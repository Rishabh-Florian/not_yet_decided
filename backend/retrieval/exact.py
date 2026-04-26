"""ExactTier — Tier 1 of the retrieval cascade.

Two paths, walked in order:

1. **Cypher exact id match.** Tokens in the query that look like an
   entity id (e.g. `emp_1002`, `CLNT-0042`, ten-char ASIN-shaped
   product ids, hex UUIDs) are looked up against `:Entity {id: $id}`.
   Any match returns `Hit.score = 1.0` — there is no ranking, the
   identifier is a primary key.

2. **Neo4j fulltext index.** If no id-shaped token hit, the query is
   forwarded to the shared `node_text` fulltext index (Lucene-backed,
   BM25-similar scoring). The top hit's raw Lucene score is normalized
   into `[0, 1]` via `score / (1 + score)` — a monotonic squash that
   keeps relative ordering and lets the cascade orchestrator compare it
   against an `escalate_below` threshold without per-tier tuning of the
   raw Lucene range.

Deep module: `ExactTier.search()` is the only public method. Token
extraction, Cypher building, and fulltext invocation are private.
"""
from __future__ import annotations

import json
import re

from backend.graph.store import GraphStore

from ._util import _citations_from_provenance, _escape_lucene, _preview
from .index import NODE_TEXT_INDEX, ensure_indexes
from .models import Citation, Hit, QueryContext, QueryResult
from .tiers import Tier

# Top-N hits returned by the fulltext fallback. Five is enough to give
# the cascade orchestrator something to escalate-or-not on; we are not a
# ranking surface, just a fast first-pass filter.
_FULLTEXT_LIMIT: int = 5

# ID-shaped tokens we recognize. Sourced from EnterpriseBench: employee ids
# `emp_1234`, customer/client ids like `CLNT-0042`, ten-char ASIN-style
# product ids `B0BQ3K23Y1`, lowercase shortname customer ids (`arout`,
# `queen`), repo names (alnum + `_`/`-`), and hex UUIDs. Order matters —
# the more specific patterns must come before the catchall hex/uuid one
# so we don't accidentally classify `emp_1002` as just hex.
_ID_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bemp_\d+\b"),                                # emp_0431
    re.compile(r"\b(?:CLNT|CUST|VEND|ORG)-\d+\b"),             # CLNT-0042
    re.compile(r"\b[A-Z][0-9A-Z]{9}\b"),                       # ASIN B0BQ3K23Y1
    re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"),  # UUID
    re.compile(r"\b(?:ticket|conv|conversation|order|sale|product)[-_:][\w-]+\b", re.IGNORECASE),
)


def _extract_id_tokens(query: str) -> list[str]:
    seen: set[str] = set()
    tokens: list[str] = []
    for pattern in _ID_PATTERNS:
        for m in pattern.finditer(query):
            tok = m.group(0)
            if tok not in seen:
                seen.add(tok)
                tokens.append(tok)
    return tokens


def _normalize_bm25(raw_score: float) -> float:
    """Squash an unbounded Lucene/BM25 score into [0, 1).

    `score / (1 + score)`: monotonic, keeps ordering, unit-free. Avoids
    divide-by-zero (raw_score >= 0 always per Lucene contract).
    """
    if raw_score < 0:
        raise ValueError(f"Lucene score must be >= 0, got {raw_score}")
    return raw_score / (1.0 + raw_score)


class ExactTier(Tier):
    """Cypher exact-id + Neo4j fulltext retrieval.

    Reuses the `GraphStore` driver — does NOT open a second Neo4j
    connection. Calls `ensure_indexes` once at construction so the
    fulltext index exists before any query lands.

    Confidence semantics:

    * id-token Cypher hit -> `Hit.score = 1.0`, `QueryResult.relevance = 1.0`
    * fulltext top-1     -> `Hit.score = normalized BM25 in [0, 1)`
    * miss               -> empty hits, `relevance = 0.0` (cascade escalates)
    """

    def __init__(
        self,
        store: GraphStore,
        *,
        name: str = "exact",
    ) -> None:
        if not isinstance(store, GraphStore):
            raise TypeError(f"store must be GraphStore, got {type(store).__name__}")
        if not name or not name.islower():
            raise ValueError(
                f"ExactTier name must be a non-empty lowercase identifier, got {name!r}"
            )
        self._store = store
        self._name = name
        ensure_indexes(store._driver, store._database)

    @property
    def name(self) -> str:
        return self._name

    def search(self, query: str, ctx: QueryContext) -> QueryResult:
        if not isinstance(query, str):
            raise TypeError(f"query must be str, got {type(query).__name__}")
        if not query.strip():
            raise ValueError("query must be non-empty / non-whitespace")

        tokens = _extract_id_tokens(query)
        if tokens:
            hits, citations = self._exact_lookup(tokens)
            if hits:
                return QueryResult(
                    answer=None,
                    items=hits,
                    citations=citations,
                    tier_used=self._name,
                    relevance=1.0,
                    latency_ms=0,
                )

        # Fulltext fallback. Only run on short literal phrases (<= 4 tokens,
        # no wildcards) per the issue spec — longer queries are R2's domain.
        word_count = len(query.split())
        if word_count <= 4 and "*" not in query and "?" not in query:
            hits, citations = self._fulltext_lookup(query)
            if hits:
                relevance = max(h.score for h in hits)
                return QueryResult(
                    answer=None,
                    items=hits,
                    citations=citations,
                    tier_used=self._name,
                    relevance=relevance,
                    latency_ms=0,
                )

        return QueryResult(
            answer=None,
            items=[],
            citations=[],
            tier_used=self._name,
            relevance=0.0,
            latency_ms=0,
        )

    # ---------- internal ----------

    def _exact_lookup(self, ids: list[str]) -> tuple[list[Hit], list[Citation]]:
        hits: list[Hit] = []
        citations: list[Citation] = []
        with self._store._session() as s:
            rows = list(
                s.run(
                    "MATCH (n:Entity) WHERE n.id IN $ids "
                    "RETURN n.id AS id, n.attributes_json AS attrs",
                    ids=ids,
                )
            )
        for row in rows:
            node_id = row["id"]
            attrs = json.loads(row["attrs"])
            hits.append(
                Hit(
                    kind="node",
                    id=node_id,
                    score=1.0,  # Cypher exact-id match: identifier is a primary key.
                    preview=_preview(attrs),
                )
            )
            citations.extend(
                _citations_from_provenance(self._store._provenance_for_node(node_id))
            )
        return hits, citations

    def _fulltext_lookup(self, query: str) -> tuple[list[Hit], list[Citation]]:
        escaped = _escape_lucene(query)
        cypher = (
            f"CALL db.index.fulltext.queryNodes('{NODE_TEXT_INDEX}', $q) "
            f"YIELD node, score "
            f"WHERE node:Entity "
            f"RETURN node.id AS id, node.attributes_json AS attrs, score "
            f"LIMIT $limit"
        )
        with self._store._session() as s:
            rows = list(
                s.run(cypher, q=escaped, limit=_FULLTEXT_LIMIT)  # type: ignore[arg-type]
            )

        hits: list[Hit] = []
        citations: list[Citation] = []
        for row in rows:
            node_id = row["id"]
            attrs = json.loads(row["attrs"])
            normalized = _normalize_bm25(float(row["score"]))
            hits.append(
                Hit(
                    kind="node",
                    id=node_id,
                    score=normalized,  # Neo4j fulltext score = Lucene BM25-similar, normalized to [0,1).
                    preview=_preview(attrs),
                )
            )
            citations.extend(
                _citations_from_provenance(self._store._provenance_for_node(node_id))
            )
        return hits, citations
