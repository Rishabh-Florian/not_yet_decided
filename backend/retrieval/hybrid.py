"""HybridTier — Tier 2 of the retrieval cascade.

Two retrieval arms, fused by Reciprocal Rank Fusion (RRF):

1. **Vector arm.** The query is embedded by an `Embedder` (see
   `embedder.py`) and forwarded to Neo4j's native HNSW vector index
   `entity_vec` (`db.index.vector.queryNodes`). Cosine similarity is
   computed server-side; we keep only the rank, not the raw cosine
   score, so the two arms are commensurable.
2. **Lexical arm.** The verbatim query (Lucene-escaped) is forwarded
   to the existing `node_text` fulltext index — the same index the
   `ExactTier` uses for its short-phrase fallback. We keep the rank,
   not the BM25 score.

The two ranked candidate lists are fused with **Reciprocal Rank
Fusion** (Cormack, Clarke & Buettcher, SIGIR 2009):

    rrf_score(d) = sum over arms a of  1 / (k + rank_a(d))

with the canonical constant ``k = 60``. Documents missing from one
arm contribute nothing from that arm. The maximum possible RRF score
is ``2 / (k + 1)`` (rank 1 in both arms); we divide by that maximum so
the score that lands on `Hit.score` lies in ``[0, 1]`` and is
comparable to other tiers' [0, 1] scores at the orchestrator
escalation gate.

Cross-encoder rerank (e.g. `BAAI/bge-reranker-base`) is mentioned in
the issue spec as an optional final pass over the top-N RRF
candidates. It is NOT implemented in this revision: it requires
adding the ``sentence-transformers`` dependency and downloading a
~120MB model. The fusion pipeline is structured so the rerank can be
inserted as a post-fusion transform without changing the public API.
See the README at the bottom of this docstring for what to add.

Deep module: `HybridTier.search()` is the only public method.
Embedding, vector query, fulltext query, and RRF fusion are private.
"""
from __future__ import annotations

import json
import re
from typing import Any

from backend.graph.store import GraphStore
from backend.models.graph import Provenance

from .embedder import Embedder
from .index import (
    ENTITY_VECTOR_INDEX,
    ENTITY_VECTOR_PROPERTY,
    NODE_TEXT_INDEX,
    ensure_indexes,
    ensure_vector_index,
)
from .models import Citation, Hit, QueryContext, QueryResult
from .tiers import Tier

# RRF constant. 60 is the value Cormack et al. recommend in the
# canonical SIGIR'09 paper; it strongly dampens the contribution of
# tail ranks while still differentiating top results.
_RRF_K: int = 60

# Reserved Lucene chars in the fulltext arm's query. Mirror the escape
# rule from `exact.py` rather than importing a private symbol — this
# tier owns its own Lucene escape so the two tiers can evolve
# independently without coupling.
_LUCENE_SPECIAL = re.compile(r'([+\-!(){}\[\]^"~*?:\\/]|&&|\|\|)')


def _escape_lucene(query: str) -> str:
    return _LUCENE_SPECIAL.sub(r"\\\1", query)


def _preview(attributes: dict[str, Any]) -> str:
    """One-line summary of a node for `Hit.preview` (mirrors `exact.py`)."""
    for key in ("name", "title", "subject", "summary", "description", "customer_name"):
        v = attributes.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()[:200]
    return json.dumps(attributes, ensure_ascii=False)[:200]


def _citations_from_provenance(rows: list[Provenance]) -> list[Citation]:
    cites: list[Citation] = []
    for p in rows:
        method = p.extraction_method
        if method not in ("direct_mapping", "llm_extraction", "rule_based", "human"):
            raise ValueError(f"unexpected extraction_method {method!r} in provenance")
        cites.append(
            Citation(
                source_file=p.source_file,
                source_record_id=p.source_record_id,
                source_field=p.source_field,
                raw_value=p.raw_value,
                extraction_method=method,
            )
        )
    return cites


def _rrf_fuse(
    vector_ranking: list[str],
    fulltext_ranking: list[str],
    *,
    k: int = _RRF_K,
) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion of two ranked id lists.

    Each ranking is a list of node ids in descending relevance order
    (rank 1 = best). The fused score for a node `d` is
    ``sum_a 1 / (k + rank_a(d))`` over the two arms; ids not in an
    arm contribute zero from that arm. The output is sorted by fused
    score descending, normalized so the max possible RRF score
    (``2 / (k + 1)`` — rank 1 in both arms) maps to 1.0.

    The normalization is purely cosmetic: it keeps `Hit.score`
    comparable to the [0, 1] convention used by other tiers, but the
    relative ordering is identical to the un-normalized RRF.
    """
    if k < 1:
        raise ValueError(f"RRF k must be >= 1, got {k}")
    scores: dict[str, float] = {}
    for rank, node_id in enumerate(vector_ranking, start=1):
        scores[node_id] = scores.get(node_id, 0.0) + 1.0 / (k + rank)
    for rank, node_id in enumerate(fulltext_ranking, start=1):
        scores[node_id] = scores.get(node_id, 0.0) + 1.0 / (k + rank)
    if not scores:
        return []
    max_possible = 2.0 / (k + 1)
    fused = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    return [(nid, s / max_possible) for nid, s in fused]


class HybridTier(Tier):
    """Vector + fulltext retrieval fused by Reciprocal Rank Fusion.

    Reuses the `GraphStore` driver — does NOT open a second Neo4j
    connection. Calls `ensure_indexes` (fulltext) and
    `ensure_vector_index` (HNSW vector) once at construction so both
    indexes exist before any query lands.

    Confidence semantics:

    * Each `Hit.score` is the document's RRF score normalized by the
      max possible RRF score (rank 1 in both arms → 1.0). The number
      is comparable across hits within one query but not across
      tiers (per `models.py`'s contract).
    * `QueryResult.relevance` is the top hit's RRF score. The
      orchestrator compares it against `escalate_below` (default
      cascade uses 0.3 — see `orchestrator.py` for the rationale).

    Embedding generation is delegated to an `Embedder` (see
    `embedder.py`). Real semantic recall requires the
    `BgeSmallEmbedder` (which needs the optional
    ``sentence-transformers`` dep + the BGE model download); tests
    use `StubEmbedder` so the pipeline is exercisable end-to-end
    without network or extra deps.

    Cross-encoder rerank pass: NOT implemented in this revision (see
    module docstring). Adding it is purely a post-fusion transform.
    """

    def __init__(
        self,
        store: GraphStore,
        embedder: Embedder,
        *,
        name: str = "hybrid",
        candidate_limit: int = 30,
        result_limit: int = 10,
        rrf_k: int = _RRF_K,
    ) -> None:
        if not isinstance(store, GraphStore):
            raise TypeError(f"store must be GraphStore, got {type(store).__name__}")
        if not isinstance(embedder, Embedder):
            raise TypeError(
                f"embedder must implement the Embedder protocol "
                f"(needs `dim` and `embed`), got {type(embedder).__name__}"
            )
        if not name or not name.islower():
            raise ValueError(
                f"HybridTier name must be a non-empty lowercase identifier, got {name!r}"
            )
        if candidate_limit < 1:
            raise ValueError(f"candidate_limit must be >= 1, got {candidate_limit}")
        if result_limit < 1:
            raise ValueError(f"result_limit must be >= 1, got {result_limit}")
        if result_limit > candidate_limit:
            raise ValueError(
                f"result_limit ({result_limit}) must be <= candidate_limit "
                f"({candidate_limit})"
            )
        if rrf_k < 1:
            raise ValueError(f"rrf_k must be >= 1, got {rrf_k}")
        self._store = store
        self._embedder = embedder
        self._name = name
        self._candidate_limit = candidate_limit
        self._result_limit = result_limit
        self._rrf_k = rrf_k
        ensure_indexes(store._driver, store._database)
        ensure_vector_index(store._driver, store._database, dimensions=embedder.dim)

    @property
    def name(self) -> str:
        return self._name

    def search(self, query: str, ctx: QueryContext) -> QueryResult:
        if not isinstance(query, str):
            raise TypeError(f"query must be str, got {type(query).__name__}")
        if not query.strip():
            raise ValueError("query must be non-empty / non-whitespace")

        # Fail-fast on a missing embedder backend: the constructor
        # already validated the protocol, but the *call* may surface
        # the lazy-loaded model error (e.g. BGE not installed) — we
        # let it propagate per the cascade's fail-fast policy.
        qvec = self._embedder.embed(query)
        if len(qvec) != self._embedder.dim:
            raise RuntimeError(
                f"embedder returned {len(qvec)}-dim vector, expected {self._embedder.dim}"
            )

        vec_rows = self._vector_lookup(qvec)
        ft_rows = self._fulltext_lookup(query)

        # Build id -> attrs map across both arms so we can render
        # previews and fetch citations once per unique id.
        attrs_by_id: dict[str, dict[str, Any]] = {}
        for nid, attrs in vec_rows:
            attrs_by_id.setdefault(nid, attrs)
        for nid, attrs in ft_rows:
            attrs_by_id.setdefault(nid, attrs)

        vector_ranking = [nid for nid, _ in vec_rows]
        fulltext_ranking = [nid for nid, _ in ft_rows]
        fused = _rrf_fuse(
            vector_ranking, fulltext_ranking, k=self._rrf_k
        )[: self._result_limit]

        if not fused:
            return QueryResult(
                answer=None,
                items=[],
                citations=[],
                tier_used=self._name,
                relevance=0.0,
                latency_ms=0,
            )

        hits: list[Hit] = []
        citations: list[Citation] = []
        for node_id, fused_score in fused:
            attrs = attrs_by_id[node_id]
            hits.append(
                Hit(
                    kind="node",
                    id=node_id,
                    # Reciprocal Rank Fusion of vector cosine + Lucene BM25 ranks,
                    # k=60 (Cormack 2009), normalized by max possible RRF (rank-1
                    # in both arms) so scores land in [0, 1].
                    score=fused_score,
                    preview=_preview(attrs),
                )
            )
            citations.extend(
                _citations_from_provenance(self._store._provenance_for_node(node_id))
            )

        return QueryResult(
            answer=None,
            items=hits,
            citations=citations,
            tier_used=self._name,
            # Top-1 RRF score (normalized) — see `Hit.score` docstring above.
            relevance=hits[0].score,
            latency_ms=0,
        )

    # ---------- internal ----------

    def _vector_lookup(self, qvec: list[float]) -> list[tuple[str, dict[str, Any]]]:
        """Run the HNSW vector arm. Returns `[(id, attrs), ...]` in score order.

        Filters out nodes that have no `vector` property — Neo4j's
        index only exposes indexed nodes anyway, but the explicit
        filter keeps the contract obvious. Uses
        `db.index.vector.queryNodes('entity_vec', $k, $qv)`.
        """
        cypher = (
            f"CALL db.index.vector.queryNodes("
            f"'{ENTITY_VECTOR_INDEX}', $k, $qv) "
            f"YIELD node, score "
            f"WHERE node:Entity AND node.{ENTITY_VECTOR_PROPERTY} IS NOT NULL "
            f"RETURN node.id AS id, node.attributes_json AS attrs, score "
            f"ORDER BY score DESC"
        )
        with self._store._session() as s:
            rows = list(
                s.run(
                    cypher,  # type: ignore[arg-type]
                    k=self._candidate_limit,
                    qv=qvec,
                )
            )
        return [(r["id"], json.loads(r["attrs"])) for r in rows]

    def _fulltext_lookup(self, query: str) -> list[tuple[str, dict[str, Any]]]:
        """Run the BM25 lexical arm against the shared `node_text` index."""
        escaped = _escape_lucene(query)
        cypher = (
            f"CALL db.index.fulltext.queryNodes('{NODE_TEXT_INDEX}', $q) "
            f"YIELD node, score "
            f"WHERE node:Entity "
            f"RETURN node.id AS id, node.attributes_json AS attrs, score "
            f"ORDER BY score DESC LIMIT $limit"
        )
        with self._store._session() as s:
            rows = list(
                s.run(
                    cypher,  # type: ignore[arg-type]
                    q=escaped,
                    limit=self._candidate_limit,
                )
            )
        return [(r["id"], json.loads(r["attrs"])) for r in rows]
