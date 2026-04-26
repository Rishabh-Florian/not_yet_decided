"""Tier ABC + a stub implementation.

A `Tier` is one retrieval strategy: exact Cypher / hybrid vector+BM25 /
agentic LLM / etc. The cascade orchestrator composes them.

Each concrete tier is responsible for documenting **which algorithm**
produces the `Hit.score` and `QueryResult.relevance` values it emits
(cosine similarity, BM25, cross-encoder rerank, exact-match indicator,
etc.). See `backend/retrieval/models.py`.

R0 ships only `StubTier` so the cascade orchestrator and eval harness
can run end-to-end without Neo4j or an LLM. Real tiers (#3-#6) implement
this same ABC behind their own modules.
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod

from .models import QueryContext, QueryResult


class Tier(ABC):
    """A single retrieval strategy.

    Implementations MUST be deterministic w.r.t. their declared inputs
    (the query string and `QueryContext`). They MUST raise on unrecoverable
    errors — never return an empty result to mask a failure (see
    PRINCIPLES.md §1: fail fast).
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Stable, lowercase identifier (e.g. ``"exact"``, ``"hybrid"``).

        Used by `QueryResult.tier_used`, the eval harness, and
        `QueryContext.prefer_tier`. Must be unique across registered
        tiers in one orchestrator.
        """

    @abstractmethod
    def search(self, query: str, ctx: QueryContext) -> QueryResult:
        """Run retrieval. Returns a populated `QueryResult`.

        Implementations set `tier_used = self.name` and `relevance` per
        their documented scoring algorithm. The orchestrator handles
        latency timing and escalation; tiers should not.
        """


class StubTier(Tier):
    """Deterministic empty tier.

    Used by R0 tests and the eval harness so the full pipeline can run
    before any real retrieval lands. Always returns zero hits and
    `relevance == 0.0` so the orchestrator escalates past it whenever
    a more capable tier is registered after it. The `relevance` value
    is the trivially correct one for an empty hit list — there is no
    algorithm to document because there is nothing to score.
    """

    def __init__(self, name: str = "stub") -> None:
        if not name or not name.islower():
            raise ValueError(
                f"StubTier name must be a non-empty lowercase identifier, got {name!r}"
            )
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def search(self, query: str, ctx: QueryContext) -> QueryResult:
        if not isinstance(query, str):
            raise TypeError(f"query must be str, got {type(query).__name__}")
        start = time.perf_counter()
        latency_ms = int((time.perf_counter() - start) * 1000)
        return QueryResult(
            answer=None,
            items=[],
            citations=[],
            tier_used=self._name,
            relevance=0.0,
            latency_ms=latency_ms,
        )
