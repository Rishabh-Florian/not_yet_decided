"""ContextEngine — single public entrypoint into retrieval.

Deep module: callers import `ContextEngine` and the result models. The
cascade, tier ABC, and per-tier scoring details are private surface area.
"""
from __future__ import annotations

from .models import QueryContext, QueryResult
from .orchestrator import CascadeOrchestrator
from .tiers import Tier


class ContextEngine:
    """Run a query through the cascade and return a single `QueryResult`."""

    def __init__(self, orchestrator: CascadeOrchestrator) -> None:
        if not isinstance(orchestrator, CascadeOrchestrator):
            raise TypeError(
                f"orchestrator must be CascadeOrchestrator, got {type(orchestrator).__name__}"
            )
        self._orchestrator = orchestrator

    def query(self, query: str, ctx: QueryContext | None = None) -> QueryResult:
        if not isinstance(query, str):
            raise TypeError(f"query must be str, got {type(query).__name__}")
        if not query.strip():
            raise ValueError("query must be a non-empty, non-whitespace string")
        return self._orchestrator.run(query, ctx or QueryContext())

    @property
    def tier_names(self) -> list[str]:
        """Tier names in cascade order — useful for logging and the eval harness."""
        return self._orchestrator.tier_names

    @property
    def tiers_by_name(self) -> dict[str, Tier]:
        """Live tier set keyed by name. Surfaced for the workflow framework
        (which needs to wire a locked-subset `TierRegistry`) — cascade
        callers do not need this.
        """
        return self._orchestrator.tiers_by_name
