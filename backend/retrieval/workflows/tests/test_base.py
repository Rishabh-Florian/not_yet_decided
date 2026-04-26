"""Tests for the workflow framework: ABC, registry, frozen-policy enforcement."""
from __future__ import annotations

from collections.abc import Iterator
from typing import ClassVar

import pytest

from backend.retrieval import (
    Hit,
    QueryContext,
    QueryResult,
    StubTier,
    Tier,
)
from backend.retrieval.workflows import (
    TierRegistry,
    Workflow,
    WorkflowInput,
    WorkflowResult,
    build_workflow,
    clear_registry,
    get_workflow,
    list_workflows,
    register_workflow,
)


class _RecordingTier(Tier):
    """Test double that records every call and returns a fixed result."""

    def __init__(self, name: str, relevance: float = 1.0) -> None:
        self._name = name
        self._relevance = relevance
        self.calls: int = 0

    @property
    def name(self) -> str:
        return self._name

    def search(self, query: str, ctx: QueryContext) -> QueryResult:
        self.calls += 1
        return QueryResult(
            answer=None,
            items=[Hit(kind="node", id=f"{self._name}-1", score=self._relevance, preview="ok")],
            citations=[],
            tier_used=self._name,
            relevance=self._relevance,
            latency_ms=1,
        )


@pytest.fixture(autouse=True)
def _isolate_registry() -> Iterator[None]:
    """Each test starts with an empty registry and leaves it empty."""
    clear_registry()
    yield
    clear_registry()


def _engine_tiers() -> dict[str, Tier]:
    return {
        "exact": _RecordingTier("exact"),
        "hybrid": _RecordingTier("hybrid", relevance=0.6),
        "stub": StubTier(name="stub"),
    }


# ---------- TierRegistry frozen-policy enforcement ----------


class TestTierRegistry:
    def test_get_returns_allowed_tier(self) -> None:
        tiers = _engine_tiers()
        reg = TierRegistry(tiers, frozenset({"exact"}))
        assert reg.get("exact") is tiers["exact"]
        assert "exact" in reg

    def test_get_unknown_in_allowed_raises(self) -> None:
        reg = TierRegistry(_engine_tiers(), frozenset({"exact"}))
        with pytest.raises(KeyError, match="not in workflow allowed set"):
            reg.get("hybrid")

    def test_get_completely_unknown_raises(self) -> None:
        reg = TierRegistry(_engine_tiers(), frozenset({"exact"}))
        with pytest.raises(KeyError):
            reg.get("ghost")

    def test_missing_required_tier_in_engine_raises(self) -> None:
        with pytest.raises(ValueError, match="not registered in the engine"):
            TierRegistry(_engine_tiers(), frozenset({"exact", "agentic"}))

    def test_empty_allowed_raises(self) -> None:
        with pytest.raises(ValueError, match="at least one allowed tier"):
            TierRegistry(_engine_tiers(), frozenset())

    def test_allowed_must_be_frozenset(self) -> None:
        with pytest.raises(TypeError, match="frozenset"):
            TierRegistry(_engine_tiers(), {"exact"})  # type: ignore[arg-type]

    def test_snapshot_isolation(self) -> None:
        tiers = _engine_tiers()
        reg = TierRegistry(tiers, frozenset({"exact"}))
        # Mutating the source dict must NOT widen the registry's view.
        tiers["hybrid"] = _RecordingTier("hybrid")
        with pytest.raises(KeyError):
            reg.get("hybrid")


# ---------- Workflow ABC contract ----------


class _OkWorkflow(Workflow):
    name: ClassVar[str] = "ok"
    allowed_tiers: ClassVar[frozenset[str]] = frozenset({"exact"})

    def run(self, input: WorkflowInput) -> WorkflowResult:  # noqa: A002
        tier = self.tiers.get("exact")
        result = tier.search(str(input.payload.get("q", "")), input.ctx or QueryContext())
        return WorkflowResult(
            answer=result.answer,
            items=result.items,
            citations=result.citations,
            tier_used=result.tier_used,
            relevance=result.relevance,
            latency_ms=result.latency_ms,
            workflow=type(self).name,
            extras={"echo": input.payload},
        )


class TestWorkflowABC:
    def test_workflow_runs_against_allowed_tier(self) -> None:
        tiers = _engine_tiers()
        wf = _OkWorkflow(TierRegistry(tiers, _OkWorkflow.allowed_tiers))
        out = wf.run(WorkflowInput(payload={"q": "hello"}))
        assert isinstance(out, WorkflowResult)
        assert out.workflow == "ok"
        assert out.tier_used == "exact"
        assert out.extras == {"echo": {"q": "hello"}}
        assert tiers["exact"].calls == 1  # type: ignore[attr-defined]

    def test_workflow_cannot_reach_for_disallowed_tier(self) -> None:
        class _Sneaky(Workflow):
            name: ClassVar[str] = "sneaky"
            allowed_tiers: ClassVar[frozenset[str]] = frozenset({"exact"})

            def run(self, input: WorkflowInput) -> WorkflowResult:  # noqa: A002
                # Reaching for a tier outside the locked subset MUST fail.
                self.tiers.get("hybrid")
                raise AssertionError("unreachable: registry should have raised")

        wf = _Sneaky(TierRegistry(_engine_tiers(), _Sneaky.allowed_tiers))
        with pytest.raises(KeyError, match="not in workflow allowed set"):
            wf.run(WorkflowInput(payload={}))

    def test_subclass_missing_name_raises_at_construction(self) -> None:
        class _Bad(Workflow):  # missing `name` ClassVar
            allowed_tiers: ClassVar[frozenset[str]] = frozenset({"exact"})

            def run(self, input: WorkflowInput) -> WorkflowResult:  # noqa: A002
                raise NotImplementedError

        # `Workflow.name` declares a ClassVar but does not assign one;
        # constructing without it must fail the contract check.
        with pytest.raises((TypeError, ValueError, AttributeError)):
            _Bad(TierRegistry(_engine_tiers(), frozenset({"exact"})))

    def test_subclass_with_whitespace_name_raises(self) -> None:
        class _Bad(Workflow):
            name: ClassVar[str] = "has spaces"
            allowed_tiers: ClassVar[frozenset[str]] = frozenset({"exact"})

            def run(self, input: WorkflowInput) -> WorkflowResult:  # noqa: A002
                raise NotImplementedError

        with pytest.raises(ValueError, match="whitespace-free identifier"):
            _Bad(TierRegistry(_engine_tiers(), frozenset({"exact"})))

# ---------- Registry: register / get / duplicates / unknown ----------


class TestRegistry:
    def test_register_and_get_roundtrip(self) -> None:
        register_workflow(_OkWorkflow)
        assert get_workflow("ok") is _OkWorkflow
        assert list_workflows() == ["ok"]

    def test_register_as_decorator(self) -> None:
        @register_workflow
        class _W(Workflow):
            name: ClassVar[str] = "deco"
            allowed_tiers: ClassVar[frozenset[str]] = frozenset({"exact"})

            def run(self, input: WorkflowInput) -> WorkflowResult:  # noqa: A002
                raise NotImplementedError

        assert get_workflow("deco") is _W

    def test_duplicate_name_raises(self) -> None:
        register_workflow(_OkWorkflow)

        class _OkAgain(Workflow):
            name: ClassVar[str] = "ok"  # collision
            allowed_tiers: ClassVar[frozenset[str]] = frozenset({"hybrid"})

            def run(self, input: WorkflowInput) -> WorkflowResult:  # noqa: A002
                raise NotImplementedError

        with pytest.raises(ValueError, match="already registered"):
            register_workflow(_OkAgain)

    def test_get_unknown_raises(self) -> None:
        with pytest.raises(KeyError, match="no workflow registered"):
            get_workflow("ghost")

    def test_register_non_workflow_raises(self) -> None:
        class _NotAWorkflow:
            pass

        with pytest.raises(TypeError, match="Workflow subclass"):
            register_workflow(_NotAWorkflow)  # type: ignore[arg-type]

    def test_register_missing_classvars_raises(self) -> None:
        class _NoName(Workflow):
            allowed_tiers: ClassVar[frozenset[str]] = frozenset({"exact"})

            def run(self, input: WorkflowInput) -> WorkflowResult:  # noqa: A002
                raise NotImplementedError

        with pytest.raises(TypeError, match="name"):
            register_workflow(_NoName)


# ---------- build_workflow: end-to-end factory ----------


class TestBuildWorkflow:
    def test_build_constructs_with_locked_registry(self) -> None:
        register_workflow(_OkWorkflow)
        tiers = _engine_tiers()
        wf = build_workflow("ok", tiers)
        assert isinstance(wf, _OkWorkflow)
        # The built workflow's TierRegistry exposes only its allowed set.
        assert wf.tiers.allowed == frozenset({"exact"})
        with pytest.raises(KeyError):
            wf.tiers.get("hybrid")

    def test_build_unknown_raises_keyerror(self) -> None:
        with pytest.raises(KeyError):
            build_workflow("missing", _engine_tiers())

    def test_build_missing_engine_tier_raises(self) -> None:
        register_workflow(_OkWorkflow)
        # Engine missing the "exact" tier the workflow depends on.
        with pytest.raises(ValueError, match="not registered in the engine"):
            build_workflow("ok", {"hybrid": _RecordingTier("hybrid")})
