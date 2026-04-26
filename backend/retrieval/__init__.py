"""Retrieval package — public surface is intentionally tiny.

Callers import only `ContextEngine` and the result/context models. The
cascade orchestrator, individual tiers, and per-tier scoring algorithms
live behind that wall (deep module).
"""
from __future__ import annotations

from .engine import ContextEngine
from .exact import ExactTier
from .models import Citation, Hit, QueryContext, QueryResult
from .orchestrator import (
    CascadeOrchestrator,
    TierConfig,
    build_default_orchestrator,
    build_orchestrator_with_store,
)
from .tiers import StubTier, Tier

__all__ = [
    "CascadeOrchestrator",
    "Citation",
    "ContextEngine",
    "ExactTier",
    "Hit",
    "QueryContext",
    "QueryResult",
    "StubTier",
    "Tier",
    "TierConfig",
    "build_default_orchestrator",
    "build_orchestrator_with_store",
]
