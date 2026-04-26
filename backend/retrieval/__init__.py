"""Retrieval package — public surface is intentionally tiny.

Callers import only `ContextEngine` and the result/context models. The
cascade orchestrator, individual tiers, and per-tier scoring algorithms
live behind that wall (deep module).
"""
from __future__ import annotations

from .embedder import BgeSmallEmbedder, Embedder, StubEmbedder
from .engine import ContextEngine
from .exact import ExactTier
from .hybrid import HybridTier
from .models import Citation, Hit, QueryContext, QueryResult
from .orchestrator import (
    CascadeOrchestrator,
    TierConfig,
    build_default_orchestrator,
    build_orchestrator_with_store,
)
from .router import (
    EntityRouter,
    GLiNER2EntityRouter,
    RouterDecision,
    RouterTier,
    StubEntityRouter,
)
from .tiers import StubTier, Tier

__all__ = [
    "BgeSmallEmbedder",
    "CascadeOrchestrator",
    "Citation",
    "ContextEngine",
    "Embedder",
    "EntityRouter",
    "ExactTier",
    "GLiNER2EntityRouter",
    "Hit",
    "HybridTier",
    "QueryContext",
    "QueryResult",
    "RouterDecision",
    "RouterTier",
    "StubEmbedder",
    "StubEntityRouter",
    "StubTier",
    "Tier",
    "TierConfig",
    "build_default_orchestrator",
    "build_orchestrator_with_store",
]
