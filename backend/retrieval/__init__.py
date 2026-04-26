"""Retrieval package — public surface is intentionally tiny.

Callers import only `ContextEngine` and the result/context models. The
cascade orchestrator, individual tiers, and per-tier scoring algorithms
live behind that wall (deep module).
"""
from __future__ import annotations

from .engine import ContextEngine
from .models import Citation, Hit, QueryContext, QueryResult
from .orchestrator import CascadeOrchestrator, TierConfig
from .tiers import StubTier, Tier

__all__ = [
    "CascadeOrchestrator",
    "Citation",
    "ContextEngine",
    "Hit",
    "QueryContext",
    "QueryResult",
    "StubTier",
    "Tier",
    "TierConfig",
]
