"""Tool functions exposed to the AgenticTier (R3) Gemini function-calling loop.

Six callable tools, all backed by the existing `GraphStore` API + sister
tiers — no new IO surface. The agent picks among them in a bounded loop;
their JSON-schema-ish definitions are surfaced to the model via
`tool_definitions()`.

Why these six (and only these six)?

* `pattern_query` — typed DSL `(SourceType)-[REL]->(TargetType)`. Wraps
  `GraphStore.pattern_query`; the validation in `parse_pattern` is what
  keeps the agent from issuing free-form Cypher (an explicit non-goal
  per the issue scope-out clause). Multi-hop reasoning composes by
  calling this several times.
* `fulltext_search` / `vector_search` — agent-driven recall paths.
  `fulltext_search` reuses `ExactTier`'s short-phrase fulltext branch;
  `vector_search` reuses `HybridTier`'s vector arm. Returning `Hit`s
  rather than raw nodes lets the agent reason about per-hit scores.
* `get_node` / `get_neighbors` — single-node drill-down for the agent
  to follow a hop manually after a pattern_query / search returned a
  candidate.
* `get_source_record` — retrieves the original ingested record, used
  to ground a final answer in raw evidence (and contributes citations).

Tool functions raise on bad input (fail-fast). The AgenticTier driver
catches exceptions per-call and surfaces the error message back to the
model as the tool result so the next turn can self-correct (this is
the only `try/except` in the agent path — the issue's acceptance
criterion explicitly calls it out: "Pattern query with unknown
node/relation type → tool surfaces validation error to model, no crash").
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from backend.graph.store import GraphStore, parse_pattern

from .embedder import Embedder
from .index import (
    ENTITY_VECTOR_INDEX,
    ENTITY_VECTOR_PROPERTY,
    NODE_TEXT_INDEX,
)
from .models import Citation, Hit


# Hard caps: defensive backstops on per-tool result size so a single
# tool call cannot blow the model's context. Tier loop applies a
# separate cap on number of *calls* (max_iterations).
_MAX_K: int = 25
_MAX_DEPTH: int = 3
_MAX_NEIGHBORS: int = 50


def _attrs_preview(attrs: dict[str, Any]) -> str:
    for key in ("name", "title", "subject", "summary", "description", "customer_name"):
        v = attrs.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()[:200]
    return json.dumps(attrs, ensure_ascii=False)[:200]


def _node_to_dict(store: GraphStore, node_id: str) -> dict[str, Any]:
    node = store.get_node(node_id)
    if node is None:
        raise KeyError(f"node {node_id!r} not found")
    return {
        "id": node.id,
        "type": node.type,
        "attributes": node.attributes,
        "vfs_path": node.vfs_path,
        "version": node.version,
        # Provenance is a list of dicts (audit-friendly). Each dict carries
        # the exact source_file/source_record_id/source_field tuple needed
        # to re-derive a `Citation` server-side at answer-assembly time.
        "provenance": [
            {
                "source_file": p.source_file,
                "source_record_id": p.source_record_id,
                "source_field": p.source_field,
                "extraction_method": p.extraction_method,
                "confidence": p.confidence.value,
                "raw_value": p.raw_value,
            }
            for p in node.provenance
        ],
    }


@dataclass(frozen=True)
class ToolDefinition:
    """One tool's schema as surfaced to the model.

    `parameters` mirrors the OpenAPI-flavored shape Gemini's
    `function_declarations` expects: a JSON-schema-style dict with
    `type: "object"`, `properties: {...}`, and `required: [...]`. Kept
    SDK-agnostic so `StubLLMClient` and `GeminiLLMClient` consume the
    same dataclass.
    """

    name: str
    description: str
    parameters: dict[str, Any]


@dataclass
class CitationCollector:
    """Accumulates `Citation`s harvested from any tool call that returned
    a node or source-record. The AgenticTier passes one of these into
    every tool call; the resulting list is what lands on
    `QueryResult.citations`.

    Citations are deduplicated on
    `(source_file, source_record_id, source_field)` so repeated tool
    calls on the same node do not double-count evidence — the
    `Hit.score` algorithm in `agentic.py` literally counts unique
    citations, so dedup matters for correctness, not just hygiene.
    """

    citations: list[Citation]
    _seen: set[tuple[str, str, str]]

    def __init__(self) -> None:
        self.citations = []
        self._seen = set()

    def add_node(self, store: GraphStore, node_id: str) -> None:
        for p in store._provenance_for_node(node_id):
            key = (p.source_file, p.source_record_id, p.source_field)
            if key in self._seen:
                continue
            method = p.extraction_method
            if method not in ("direct_mapping", "llm_extraction", "rule_based", "human"):
                raise ValueError(
                    f"unexpected extraction_method {method!r} in provenance"
                )
            self._seen.add(key)
            self.citations.append(
                Citation(
                    source_file=p.source_file,
                    source_record_id=p.source_record_id,
                    source_field=p.source_field,
                    raw_value=p.raw_value,
                    extraction_method=method,
                )
            )

    def add_source_record(self, source_file: str, source_record_id: str) -> None:
        # Synthetic "whole record" citation — the source_field is a
        # sentinel because the agent asked for the entire record, not a
        # particular field. The frontend can render this as "raw record"
        # rather than a per-field highlight.
        key = (source_file, source_record_id, "<whole_record>")
        if key in self._seen:
            return
        self._seen.add(key)
        self.citations.append(
            Citation(
                source_file=source_file,
                source_record_id=source_record_id,
                source_field="<whole_record>",
                raw_value="",
                # `direct_mapping` is the only `extraction_method`
                # literal that fits a "whole record" pull (no LLM was
                # involved in surfacing the row itself).
                extraction_method="direct_mapping",
            )
        )


class ToolBox:
    """Six tools the AgenticTier exposes to the Gemini function-calling loop.

    Constructed once per tier instance, cheap to invoke per query. Holds
    the `GraphStore` + `Embedder` references; tool methods are pure
    dispatchers into those.

    Each public tool method:
    - validates its arguments and raises `ValueError`/`TypeError` on
      bad input (fail-fast — the agent driver catches and surfaces);
    - records citations into the `CitationCollector` for any node /
      source record it returns;
    - returns a JSON-serializable dict / list. The agent driver
      forwards this verbatim as the function-call result.
    """

    def __init__(self, store: GraphStore, embedder: Embedder) -> None:
        if not isinstance(store, GraphStore):
            raise TypeError(f"store must be GraphStore, got {type(store).__name__}")
        if not isinstance(embedder, Embedder):
            raise TypeError(
                f"embedder must implement Embedder protocol, got {type(embedder).__name__}"
            )
        self._store = store
        self._embedder = embedder

    # ------------------------------------------------------------------
    # Tool 1: pattern_query
    # ------------------------------------------------------------------

    def pattern_query(
        self,
        cites: CitationCollector,
        *,
        src_type: str,
        rel_type: str,
        tgt_type: str,
        limit: int = 25,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Wraps `GraphStore.pattern_query` with the typed DSL guard.

        Re-uses `parse_pattern` (which validates against the canonical
        registry) by reconstructing the textual pattern; this guarantees
        the same validation path the `POST /api/graph/query` endpoint
        uses. Free-form Cypher is intentionally not exposed.
        """
        if not isinstance(src_type, str) or not src_type:
            raise ValueError("src_type must be a non-empty string")
        if not isinstance(rel_type, str) or not rel_type:
            raise ValueError("rel_type must be a non-empty string")
        if not isinstance(tgt_type, str) or not tgt_type:
            raise ValueError("tgt_type must be a non-empty string")
        if not isinstance(limit, int) or not (1 <= limit <= _MAX_K):
            raise ValueError(f"limit must be int in [1, {_MAX_K}], got {limit!r}")
        if not isinstance(offset, int) or offset < 0:
            raise ValueError(f"offset must be int >= 0, got {offset!r}")
        # Validate via the public DSL parser so canonical-registry
        # errors come out worded exactly like the HTTP API path.
        parse_pattern(f"({src_type})-[{rel_type}]->({tgt_type})")
        matches, total = self._store.pattern_query(
            src_type, rel_type, tgt_type, limit=limit, offset=offset
        )
        out_rows: list[dict[str, Any]] = []
        for src_node, edge, tgt_node in matches:
            cites.add_node(self._store, src_node.id)
            cites.add_node(self._store, tgt_node.id)
            out_rows.append(
                {
                    "source": {
                        "id": src_node.id,
                        "type": src_node.type,
                        "preview": _attrs_preview(src_node.attributes),
                    },
                    "edge": {
                        "id": edge.id,
                        "relation_type": edge.relation_type,
                        "attributes": edge.attributes,
                    },
                    "target": {
                        "id": tgt_node.id,
                        "type": tgt_node.type,
                        "preview": _attrs_preview(tgt_node.attributes),
                    },
                }
            )
        return {"matches": out_rows, "total": total}

    # ------------------------------------------------------------------
    # Tool 2: fulltext_search
    # ------------------------------------------------------------------

    def fulltext_search(
        self, cites: CitationCollector, *, query: str, k: int = 10
    ) -> list[Hit]:
        """Lucene-backed `node_text` index, mirrors `ExactTier`'s fulltext
        branch verbatim (BM25-similar score, normalized to [0, 1) via
        `score / (1 + score)`). The agent gets `Hit` objects so it can
        rank candidates by `score`.
        """
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be a non-empty string")
        if not isinstance(k, int) or not (1 <= k <= _MAX_K):
            raise ValueError(f"k must be int in [1, {_MAX_K}], got {k!r}")
        # Local import to keep the tools module from pulling tier code.
        from .exact import _escape_lucene, _normalize_bm25

        escaped = _escape_lucene(query)
        cypher = (
            f"CALL db.index.fulltext.queryNodes('{NODE_TEXT_INDEX}', $q) "
            f"YIELD node, score "
            f"WHERE node:Entity "
            f"RETURN node.id AS id, node.attributes_json AS attrs, score "
            f"LIMIT $limit"
        )
        with self._store._session() as s:
            rows = list(s.run(cypher, q=escaped, limit=k))  # type: ignore[arg-type]
        hits: list[Hit] = []
        for row in rows:
            node_id = row["id"]
            attrs = json.loads(row["attrs"])
            cites.add_node(self._store, node_id)
            hits.append(
                Hit(
                    kind="node",
                    id=node_id,
                    score=_normalize_bm25(float(row["score"])),
                    preview=_attrs_preview(attrs),
                )
            )
        return hits

    # ------------------------------------------------------------------
    # Tool 3: vector_search
    # ------------------------------------------------------------------

    def vector_search(
        self, cites: CitationCollector, *, query: str, k: int = 10
    ) -> list[Hit]:
        """Embeds the query via the registered `Embedder`, then runs
        `db.index.vector.queryNodes` against the `entity_vec` HNSW
        index. Mirrors `HybridTier._vector_lookup`. Cosine similarity
        is returned as `Hit.score` directly (Neo4j normalizes already).
        """
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be a non-empty string")
        if not isinstance(k, int) or not (1 <= k <= _MAX_K):
            raise ValueError(f"k must be int in [1, {_MAX_K}], got {k!r}")
        qvec = self._embedder.embed(query)
        if len(qvec) != self._embedder.dim:
            raise RuntimeError(
                f"embedder returned {len(qvec)}-dim vector, expected {self._embedder.dim}"
            )
        cypher = (
            f"CALL db.index.vector.queryNodes("
            f"'{ENTITY_VECTOR_INDEX}', $k, $qv) "
            f"YIELD node, score "
            f"WHERE node:Entity AND node.{ENTITY_VECTOR_PROPERTY} IS NOT NULL "
            f"RETURN node.id AS id, node.attributes_json AS attrs, score "
            f"ORDER BY score DESC"
        )
        with self._store._session() as s:
            rows = list(s.run(cypher, k=k, qv=qvec))  # type: ignore[arg-type]
        hits: list[Hit] = []
        for row in rows:
            node_id = row["id"]
            attrs = json.loads(row["attrs"])
            cites.add_node(self._store, node_id)
            hits.append(
                Hit(
                    kind="node",
                    id=node_id,
                    # Neo4j returns cosine similarity in [-1, 1]; for
                    # L2-normalized embeddings (what our embedders
                    # produce) it lands in [0, 1] already. We pass it
                    # straight through — clamping would lie about the
                    # score and any out-of-range value should surface.
                    score=float(row["score"]),
                    preview=_attrs_preview(attrs),
                )
            )
        return hits

    # ------------------------------------------------------------------
    # Tool 4: get_node
    # ------------------------------------------------------------------

    def get_node(
        self, cites: CitationCollector, *, node_id: str
    ) -> dict[str, Any]:
        """Single-node lookup with full provenance attached."""
        if not isinstance(node_id, str) or not node_id:
            raise ValueError("node_id must be a non-empty string")
        cites.add_node(self._store, node_id)
        return _node_to_dict(self._store, node_id)

    # ------------------------------------------------------------------
    # Tool 5: get_neighbors
    # ------------------------------------------------------------------

    def get_neighbors(
        self,
        cites: CitationCollector,
        *,
        node_id: str,
        relation_type: str | None = None,
        depth: int = 1,
    ) -> dict[str, Any]:
        """Walks `GraphStore.neighbors`. Caps `depth` at `_MAX_DEPTH` and
        the returned set at `_MAX_NEIGHBORS` to avoid context-blow-up
        on a hub node.
        """
        if not isinstance(node_id, str) or not node_id:
            raise ValueError("node_id must be a non-empty string")
        if relation_type is not None and (
            not isinstance(relation_type, str) or not relation_type
        ):
            raise ValueError("relation_type must be None or a non-empty string")
        if not isinstance(depth, int) or not (1 <= depth <= _MAX_DEPTH):
            raise ValueError(f"depth must be int in [1, {_MAX_DEPTH}], got {depth!r}")
        if self._store.get_node(node_id) is None:
            raise KeyError(f"node {node_id!r} not found")
        ids = sorted(self._store.neighbors(node_id, relation_type, depth))
        if len(ids) > _MAX_NEIGHBORS:
            ids = ids[:_MAX_NEIGHBORS]
        out: list[dict[str, Any]] = []
        for nid in ids:
            n = self._store.get_node(nid)
            if n is None:
                continue
            cites.add_node(self._store, nid)
            out.append(
                {
                    "id": n.id,
                    "type": n.type,
                    "preview": _attrs_preview(n.attributes),
                }
            )
        return {"node_id": node_id, "neighbors": out, "total": len(out)}

    # ------------------------------------------------------------------
    # Tool 6: get_source_record
    # ------------------------------------------------------------------

    def get_source_record(
        self,
        cites: CitationCollector,
        *,
        source_file: str,
        record_id: str,
    ) -> dict[str, Any]:
        """Returns the original ingested record verbatim, plus a
        synthetic citation. This is how the agent grounds prose
        answers in raw evidence.
        """
        if not isinstance(source_file, str) or not source_file:
            raise ValueError("source_file must be a non-empty string")
        if not isinstance(record_id, str) or not record_id:
            raise ValueError("record_id must be a non-empty string")
        rec = self._store.get_source_record(source_file, record_id)
        if rec is None:
            raise KeyError(
                f"source record not found: {source_file!r} / {record_id!r}"
            )
        cites.add_source_record(source_file, record_id)
        return {
            "source_file": rec.source_file,
            "source_record_id": rec.source_record_id,
            "raw_record": rec.raw_record,
            "content_hash": rec.content_hash,
        }

    # ------------------------------------------------------------------
    # Dispatch by name (used by the loop driver)
    # ------------------------------------------------------------------

    def call(
        self,
        name: str,
        args: dict[str, Any],
        cites: CitationCollector,
    ) -> Any:
        """Dispatch a function-call by tool name. Validates that `args`
        is a flat dict and that the tool name is registered. Raises on
        anything else — the agent driver catches and forwards.
        """
        if not isinstance(args, dict):
            raise TypeError(f"args must be dict, got {type(args).__name__}")
        registry = {
            "pattern_query": self.pattern_query,
            "fulltext_search": self.fulltext_search,
            "vector_search": self.vector_search,
            "get_node": self.get_node,
            "get_neighbors": self.get_neighbors,
            "get_source_record": self.get_source_record,
        }
        fn = registry.get(name)
        if fn is None:
            raise ValueError(
                f"unknown tool {name!r}; available: {sorted(registry.keys())}"
            )
        return fn(cites, **args)


def tool_definitions() -> list[ToolDefinition]:
    """JSON-schema-style declarations the LLM consumes via function-calling.

    Kept in lockstep with the `ToolBox` method signatures. Returned
    fresh each call (cheap; ~6 small dicts) so callers can mutate
    without polluting a shared state.
    """
    return [
        ToolDefinition(
            name="pattern_query",
            description=(
                "Run a typed graph pattern query "
                "(SourceType)-[REL_TYPE]->(TargetType). "
                "Use this to traverse one hop with a known relation type. "
                "Node and relation types must be canonical (Person, Organization, "
                "Document, Message, Event, Asset, Topic; relations like SENT, "
                "RECEIVED, MEMBER_OF, AUTHORED, MENTIONS, ASSIGNED_TO)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "src_type": {"type": "string", "description": "Canonical source node type"},
                    "rel_type": {"type": "string", "description": "Canonical relation type"},
                    "tgt_type": {"type": "string", "description": "Canonical target node type"},
                    "limit": {"type": "integer", "description": f"Max matches (1..{_MAX_K})"},
                    "offset": {"type": "integer", "description": "Pagination offset"},
                },
                "required": ["src_type", "rel_type", "tgt_type"],
            },
        ),
        ToolDefinition(
            name="fulltext_search",
            description=(
                "BM25 fulltext search over node attributes. Use for keyword "
                "recall when you do not know an exact id."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Free-text query"},
                    "k": {"type": "integer", "description": f"Top-k (1..{_MAX_K})"},
                },
                "required": ["query"],
            },
        ),
        ToolDefinition(
            name="vector_search",
            description=(
                "Semantic vector search over node embeddings (cosine "
                "similarity). Use for paraphrased / fuzzy recall."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Free-text query"},
                    "k": {"type": "integer", "description": f"Top-k (1..{_MAX_K})"},
                },
                "required": ["query"],
            },
        ),
        ToolDefinition(
            name="get_node",
            description=(
                "Fetch a single node by id, including all attributes "
                "and provenance traces."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "node_id": {"type": "string", "description": "Canonical node id"},
                },
                "required": ["node_id"],
            },
        ),
        ToolDefinition(
            name="get_neighbors",
            description=(
                "Fetch direct (or up to depth-3) neighbors of a node, "
                "optionally filtered by relation type."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "node_id": {"type": "string", "description": "Source node id"},
                    "relation_type": {
                        "type": "string",
                        "description": "Optional canonical relation filter",
                    },
                    "depth": {
                        "type": "integer",
                        "description": f"Hop depth (1..{_MAX_DEPTH})",
                    },
                },
                "required": ["node_id"],
            },
        ),
        ToolDefinition(
            name="get_source_record",
            description=(
                "Fetch the original ingested record verbatim from layer 4 "
                "(raw data). Use to ground a final answer in raw evidence."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "source_file": {"type": "string"},
                    "record_id": {"type": "string"},
                },
                "required": ["source_file", "record_id"],
            },
        ),
    ]
