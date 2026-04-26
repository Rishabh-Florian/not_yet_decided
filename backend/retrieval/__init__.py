"""Retrieval package — public surface is intentionally tiny.

Callers import only `ContextEngine` and the result/context models. The
cascade orchestrator, individual tiers, and per-tier scoring algorithms
live behind that wall (deep module).
"""
from __future__ import annotations

from .agentic import (
    AgenticTier,
    GeminiLLMClient,
    LLMClient,
    LLMTurn,
    NoopLLMClient,
    StubLLMClient,
    ToolCall,
    ToolResult,
)
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
    "AgenticTier",
    "BgeSmallEmbedder",
    "CascadeOrchestrator",
    "Citation",
    "ContextEngine",
    "Embedder",
    "EntityRouter",
    "ExactTier",
    "GLiNER2EntityRouter",
    "GeminiLLMClient",
    "Hit",
    "HybridTier",
    "LLMClient",
    "LLMTurn",
    "NoopLLMClient",
    "QueryContext",
    "QueryResult",
    "RouterDecision",
    "RouterTier",
    "StubEmbedder",
    "StubEntityRouter",
    "StubLLMClient",
    "StubTier",
    "Tier",
    "TierConfig",
    "ToolCall",
    "ToolResult",
    "build_default_orchestrator",
    "build_orchestrator_with_store",
]
