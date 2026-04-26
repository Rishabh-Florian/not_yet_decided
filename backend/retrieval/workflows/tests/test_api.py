"""Tests for `/api/workflow/{name}` endpoint."""
from __future__ import annotations

from typing import ClassVar
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from backend.api.app import (
    _build_default_engine,
    app,
    get_context_engine,
    get_store,
)
from backend.graph.store import GraphStore
from backend.retrieval import Hit, QueryContext, QueryResult, StubTier, Tier
from backend.retrieval.workflows import (
    Workflow,
    WorkflowInput,
    WorkflowResult,
    clear_registry,
    register_workflow,
)


class _FixedTier(Tier):
    @property
    def name(self) -> str:
        return "fixed"

    def search(self, query: str, ctx: QueryContext) -> QueryResult:
        return QueryResult(
            answer="ans",
            items=[Hit(kind="node", id="n1", score=0.9, preview="hi")],
            citations=[],
            tier_used="fixed",
            relevance=0.9,
            latency_ms=2,
        )


class _APIWorkflow(Workflow):
    name: ClassVar[str] = "api-test"
    allowed_tiers: ClassVar[frozenset[str]] = frozenset({"fixed"})

    def run(self, input: WorkflowInput) -> WorkflowResult:  # noqa: A002
        if "q" not in input.payload:
            raise ValueError("payload must contain 'q'")
        tier = self.tiers.get("fixed")
        result = tier.search(str(input.payload["q"]), input.ctx or QueryContext())
        return WorkflowResult(
            answer=result.answer,
            items=result.items,
            citations=result.citations,
            tier_used=result.tier_used,
            relevance=result.relevance,
            latency_ms=result.latency_ms,
            workflow=type(self).name,
            extras={"q": input.payload["q"]},
        )


def _engine_with_fixed_tier():
    """Build a ContextEngine whose orchestrator exposes the `fixed` tier
    so workflows that depend on it can be wired up.
    """
    from backend.retrieval import CascadeOrchestrator, ContextEngine, TierConfig

    orch = CascadeOrchestrator(
        tiers=[_FixedTier(), StubTier(name="stub")],
        configs=[
            TierConfig(name="fixed", escalate_below=0.0),
            TierConfig(name="stub", escalate_below=0.0),
        ],
    )
    return ContextEngine(orch)


@pytest.fixture(autouse=True)
def _isolate_registry():
    clear_registry()
    register_workflow(_APIWorkflow)
    yield
    clear_registry()


class TestWorkflowEndpoint:
    def _client(self) -> TestClient:
        app.dependency_overrides[get_store] = lambda: MagicMock(spec=GraphStore)
        app.dependency_overrides[get_context_engine] = _engine_with_fixed_tier
        return TestClient(app)

    def teardown_method(self) -> None:
        app.dependency_overrides.clear()

    def test_run_known_workflow_returns_200(self) -> None:
        client = self._client()
        resp = client.post("/api/workflow/api-test", json={"payload": {"q": "hello"}})
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["workflow"] == "api-test"
        assert data["tier_used"] == "fixed"
        assert data["extras"] == {"q": "hello"}
        assert data["items"][0]["id"] == "n1"

    def test_unknown_workflow_returns_404(self) -> None:
        client = self._client()
        resp = client.post("/api/workflow/ghost", json={"payload": {}})
        assert resp.status_code == 404

    def test_invalid_body_returns_422(self) -> None:
        client = self._client()
        # Missing required `payload` field on WorkflowInput.
        resp = client.post("/api/workflow/api-test", json={"not_payload": 1})
        assert resp.status_code == 422

    def test_workflow_payload_validation_returns_400(self) -> None:
        client = self._client()
        # Workflow's `run` requires payload['q'] — raises ValueError.
        resp = client.post("/api/workflow/api-test", json={"payload": {}})
        assert resp.status_code == 400
        assert "q" in resp.json()["detail"]

    def test_default_engine_lacks_required_tier_returns_500(self) -> None:
        # Override engine to one without the `fixed` tier — the workflow
        # cannot be built. This exercises the 500 path.
        app.dependency_overrides[get_store] = lambda: MagicMock(spec=GraphStore)
        app.dependency_overrides[get_context_engine] = _build_default_engine
        client = TestClient(app)
        resp = client.post("/api/workflow/api-test", json={"payload": {"q": "x"}})
        assert resp.status_code == 500

    def test_list_workflows_endpoint(self) -> None:
        client = self._client()
        resp = client.get("/api/workflow")
        assert resp.status_code == 200
        assert resp.json() == {"workflows": ["api-test"]}
