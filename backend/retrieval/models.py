"""Pydantic v2 result/context models for the retrieval cascade.

Two scoring concepts live here. Both are algorithmic — never magic numbers
(see issue #10):

* `Hit.score` — per-hit relevance from the tier that produced it.
* `QueryResult.relevance` — overall result-set relevance the orchestrator
  used to decide whether to escalate.

Each tier MUST document the algorithm it uses to produce these scores in
its own docstring (cosine sim / BM25 / cross-encoder rerank / exact-match
indicator / etc.). The names `confidence` and `score` are reserved for
algorithmic outputs only; categorical fact-trust labels live on
`Provenance.confidence` (`FactConfidence` enum) and are a different concept.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Citation(BaseModel):
    """Pointer back to the original raw record a hit was derived from.

    Mirrors the fields of `backend.models.graph.Provenance` that a UI
    needs to render a "where did this come from?" affordance. Carrying
    citations on the result keeps the retrieval API self-contained — a
    caller can render trust without a second round-trip to the store.
    """

    source_file: str
    source_record_id: str
    source_field: str
    raw_value: str
    extraction_method: Literal[
        "direct_mapping", "llm_extraction", "rule_based", "human"
    ]


class Hit(BaseModel):
    """One retrieved item.

    `score` is an algorithmic relevance value produced by the tier
    that emitted this hit. Each tier documents which algorithm it uses
    (e.g. cosine similarity, BM25, cross-encoder rerank, exact-match
    indicator). The number is comparable only within a single tier's
    output; the orchestrator does not arithmetically combine scores
    across tiers.
    """

    kind: Literal["node", "edge", "source_record"]
    id: str
    score: float = Field(
        ...,
        description=(
            "Tier-specific algorithmic relevance score. The producing "
            "tier documents the algorithm. Higher is better; range is "
            "tier-defined (typically [0, 1])."
        ),
    )
    preview: str


class QueryContext(BaseModel):
    """Optional caller hints. Tiers may consult any field; the orchestrator
    only consults `prefer_tier` and `max_latency_ms`.
    """

    prefer_tier: str | None = None
    max_latency_ms: int | None = Field(default=None, ge=1)
    caller_id: str | None = None


class QueryResult(BaseModel):
    """Result of a single `ContextEngine.query` call.

    `answer` is filled by LLM-backed tiers (R3+). For raw-retrieval tiers
    (R1/R2) it is `None` — the caller composes the answer.
    """

    answer: str | None
    items: list[Hit]
    citations: list[Citation]
    tier_used: str
    relevance: float = Field(
        ...,
        description=(
            "Overall result-set relevance from the tier that produced "
            "this result. Algorithmic (mean / max of `Hit.score` per the "
            "tier's documented rule). Used by the orchestrator to decide "
            "whether to escalate to a slower tier."
        ),
    )
    latency_ms: int = Field(..., ge=0)
