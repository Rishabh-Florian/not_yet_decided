"""Tests for `CascadeOrchestrator`."""
from __future__ import annotations

import time

import pytest

from backend.retrieval import (
    CascadeOrchestrator,
    Hit,
    QueryContext,
    QueryResult,
    StubTier,
    Tier,
    TierConfig,
)
from backend.retrieval.orchestrator import build_default_orchestrator


class _FixedTier(Tier):
    """Test double that returns a configured QueryResult."""

    def __init__(self, name: str, relevance: float, items: list[Hit] | None = None) -> None:
        self._name = name
        self._relevance = relevance
        self._items = items or []
        self.calls: int = 0

    @property
    def name(self) -> str:
        return self._name

    def search(self, query: str, ctx: QueryContext) -> QueryResult:
        self.calls += 1
        return QueryResult(
            answer=None,
            items=self._items,
            citations=[],
            tier_used=self._name,
            relevance=self._relevance,
            latency_ms=0,
        )


class _SlowTier(_FixedTier):
    def search(self, query: str, ctx: QueryContext) -> QueryResult:
        time.sleep(0.01)
        return super().search(query, ctx)


class _MislabelingTier(Tier):
    """Returns a result whose `tier_used` does not match `self.name`."""

    @property
    def name(self) -> str:
        return "real_name"

    def search(self, query: str, ctx: QueryContext) -> QueryResult:
        return QueryResult(
            answer=None,
            items=[],
            citations=[],
            tier_used="liar",
            relevance=1.0,
            latency_ms=0,
        )


class _RaisingTier(Tier):
    @property
    def name(self) -> str:
        return "raises"

    def search(self, query: str, ctx: QueryContext) -> QueryResult:
        raise RuntimeError("upstream blew up")


class TestConstruction:
    def test_empty_tiers_raises(self) -> None:
        with pytest.raises(ValueError, match="at least one tier"):
            CascadeOrchestrator(tiers=[], configs=[])

    def test_length_mismatch_raises(self) -> None:
        with pytest.raises(ValueError, match="tier/config length mismatch"):
            CascadeOrchestrator(
                tiers=[StubTier(name="a"), StubTier(name="b")],
                configs=[TierConfig(name="a", escalate_below=0.0)],
            )

    def test_duplicate_tier_names_raises(self) -> None:
        with pytest.raises(ValueError, match="tier names must be unique"):
            CascadeOrchestrator(
                tiers=[StubTier(name="a"), StubTier(name="a")],
                configs=[
                    TierConfig(name="a", escalate_below=0.0),
                    TierConfig(name="a", escalate_below=0.0),
                ],
            )

    def test_name_mismatch_raises(self) -> None:
        with pytest.raises(ValueError, match="tier/config name mismatch"):
            CascadeOrchestrator(
                tiers=[StubTier(name="a")],
                configs=[TierConfig(name="b", escalate_below=0.0)],
            )

    def test_negative_escalate_threshold_raises(self) -> None:
        with pytest.raises(ValueError, match="escalate_below must be >= 0.0"):
            CascadeOrchestrator(
                tiers=[StubTier(name="a")],
                configs=[TierConfig(name="a", escalate_below=-0.1)],
            )


class TestCascade:
    def test_first_tier_wins_when_above_threshold(self) -> None:
        fast = _FixedTier(name="fast", relevance=0.9)
        slow = _FixedTier(name="slow", relevance=0.95)
        orch = CascadeOrchestrator(
            tiers=[fast, slow],
            configs=[
                TierConfig(name="fast", escalate_below=0.5),
                TierConfig(name="slow", escalate_below=0.5),
            ],
        )
        result = orch.run("anything", QueryContext())
        assert result.tier_used == "fast"
        assert fast.calls == 1
        assert slow.calls == 0

    def test_escalation_walks_to_next_tier(self) -> None:
        fast = _FixedTier(name="fast", relevance=0.1)
        slow = _FixedTier(name="slow", relevance=0.9)
        orch = CascadeOrchestrator(
            tiers=[fast, slow],
            configs=[
                TierConfig(name="fast", escalate_below=0.5),
                TierConfig(name="slow", escalate_below=0.5),
            ],
        )
        result = orch.run("anything", QueryContext())
        assert result.tier_used == "slow"
        assert fast.calls == 1
        assert slow.calls == 1

    def test_returns_last_tier_when_all_escalate(self) -> None:
        a = _FixedTier(name="a", relevance=0.0)
        b = _FixedTier(name="b", relevance=0.0)
        orch = CascadeOrchestrator(
            tiers=[a, b],
            configs=[
                TierConfig(name="a", escalate_below=1.0),
                TierConfig(name="b", escalate_below=1.0),
            ],
        )
        result = orch.run("anything", QueryContext())
        assert result.tier_used == "b"
        assert a.calls == 1
        assert b.calls == 1

    def test_orchestrator_overwrites_latency(self) -> None:
        slow = _SlowTier(name="slow", relevance=1.0)
        orch = CascadeOrchestrator(
            tiers=[slow],
            configs=[TierConfig(name="slow", escalate_below=0.5)],
        )
        result = orch.run("anything", QueryContext())
        assert result.latency_ms >= 1  # _SlowTier sleeps 10ms but Windows clocks vary

    def test_tier_lying_about_name_raises(self) -> None:
        orch = CascadeOrchestrator(
            tiers=[_MislabelingTier()],
            configs=[TierConfig(name="real_name", escalate_below=0.5)],
        )
        with pytest.raises(RuntimeError, match="tiers must self-identify"):
            orch.run("q", QueryContext())

    def test_tier_exception_propagates(self) -> None:
        orch = CascadeOrchestrator(
            tiers=[_RaisingTier()],
            configs=[TierConfig(name="raises", escalate_below=0.5)],
        )
        with pytest.raises(RuntimeError, match="upstream blew up"):
            orch.run("q", QueryContext())

    def test_empty_query_raises(self) -> None:
        orch = CascadeOrchestrator(
            tiers=[StubTier(name="stub")],
            configs=[TierConfig(name="stub", escalate_below=0.0)],
        )
        with pytest.raises(ValueError, match="non-empty"):
            orch.run("", QueryContext())


class TestPreferTier:
    def test_prefer_tier_jumps_cascade(self) -> None:
        a = _FixedTier(name="a", relevance=1.0)
        b = _FixedTier(name="b", relevance=1.0)
        orch = CascadeOrchestrator(
            tiers=[a, b],
            configs=[
                TierConfig(name="a", escalate_below=0.5),
                TierConfig(name="b", escalate_below=0.5),
            ],
        )
        result = orch.run("q", QueryContext(prefer_tier="b"))
        assert result.tier_used == "b"
        assert b.calls == 1
        assert a.calls == 0

    def test_unknown_prefer_tier_raises(self) -> None:
        orch = CascadeOrchestrator(
            tiers=[StubTier(name="stub")],
            configs=[TierConfig(name="stub", escalate_below=0.0)],
        )
        with pytest.raises(ValueError, match="prefer_tier"):
            orch.run("q", QueryContext(prefer_tier="ghost"))


class TestDefaultBuilder:
    def test_default_builder_walks_all_tiers(self) -> None:
        a = _FixedTier(name="a", relevance=0.99)
        b = _FixedTier(name="b", relevance=0.5)
        orch = build_default_orchestrator([a, b])
        # escalate_below=1.01 forces escalation past any score <= 1.0
        result = orch.run("q", QueryContext())
        assert result.tier_used == "b"

    def test_default_builder_empty_raises(self) -> None:
        with pytest.raises(ValueError):
            build_default_orchestrator([])
