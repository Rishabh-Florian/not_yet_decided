"""Tests for the public `ContextEngine` surface."""
from __future__ import annotations

import pytest

from backend.retrieval import (
    CascadeOrchestrator,
    ContextEngine,
    QueryContext,
    StubTier,
    TierConfig,
)


def _engine_with_stub() -> ContextEngine:
    tier = StubTier(name="stub")
    orch = CascadeOrchestrator(
        tiers=[tier],
        configs=[TierConfig(name="stub", escalate_below=0.0)],
    )
    return ContextEngine(orch)


class TestContextEngineConstruction:
    def test_rejects_non_orchestrator(self) -> None:
        with pytest.raises(TypeError, match="orchestrator must be CascadeOrchestrator"):
            ContextEngine(orchestrator="not-an-orchestrator")  # type: ignore[arg-type]

    def test_accepts_orchestrator(self) -> None:
        engine = _engine_with_stub()
        assert engine.tier_names == ["stub"]


class TestContextEngineQuery:
    def test_returns_stub_result(self) -> None:
        engine = _engine_with_stub()
        result = engine.query("who handles VPN?")
        assert result.tier_used == "stub"
        assert result.items == []
        assert result.citations == []
        assert result.relevance == 0.0
        assert result.answer is None
        assert result.latency_ms >= 0

    def test_default_query_context(self) -> None:
        engine = _engine_with_stub()
        result = engine.query("hello")
        assert result.tier_used == "stub"

    def test_explicit_query_context(self) -> None:
        engine = _engine_with_stub()
        ctx = QueryContext(prefer_tier="stub", max_latency_ms=100)
        result = engine.query("hello", ctx)
        assert result.tier_used == "stub"

    def test_rejects_non_string_query(self) -> None:
        engine = _engine_with_stub()
        with pytest.raises(TypeError, match="query must be str"):
            engine.query(42)  # type: ignore[arg-type]

    def test_rejects_empty_query(self) -> None:
        engine = _engine_with_stub()
        with pytest.raises(ValueError, match="non-empty"):
            engine.query("")

    def test_rejects_whitespace_query(self) -> None:
        engine = _engine_with_stub()
        with pytest.raises(ValueError, match="non-empty"):
            engine.query("   ")
