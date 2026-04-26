"""Tests for the `/api/query` endpoint.

Exercises the FastAPI route end-to-end against the in-process app with
the GraphStore dependency mocked out — `/api/query` does not touch the
graph store, only the engine.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from backend.api.app import (
    _build_default_engine,
    app,
    get_context_engine,
    get_store,
)
from backend.graph.store import GraphStore


class TestQueryEndpoint:
    def _client(self) -> TestClient:
        # `/api/query` does not touch the store; the lifespan does, so we
        # mock both dependencies and skip lifespan entirely.
        app.dependency_overrides[get_store] = lambda: MagicMock(spec=GraphStore)
        app.dependency_overrides[get_context_engine] = _build_default_engine
        return TestClient(app)

    def teardown_method(self) -> None:
        app.dependency_overrides.clear()

    def test_returns_stub_result(self) -> None:
        client = self._client()
        resp = client.post("/api/query", json={"query": "hello"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["tier_used"] == "stub"
        assert data["items"] == []
        assert data["citations"] == []
        assert data["answer"] is None
        assert data["relevance"] == 0.0
        assert data["latency_ms"] >= 0

    def test_empty_query_returns_400(self) -> None:
        client = self._client()
        resp = client.post("/api/query", json={"query": ""})
        assert resp.status_code == 400

    def test_whitespace_query_returns_400(self) -> None:
        client = self._client()
        resp = client.post("/api/query", json={"query": "   "})
        assert resp.status_code == 400

    def test_with_query_context(self) -> None:
        client = self._client()
        resp = client.post(
            "/api/query",
            json={
                "query": "find vpn owner",
                "context": {"prefer_tier": "stub", "max_latency_ms": 500},
            },
        )
        assert resp.status_code == 200
        assert resp.json()["tier_used"] == "stub"

    def test_unknown_prefer_tier_returns_400(self) -> None:
        client = self._client()
        resp = client.post(
            "/api/query",
            json={"query": "q", "context": {"prefer_tier": "ghost"}},
        )
        assert resp.status_code == 400
