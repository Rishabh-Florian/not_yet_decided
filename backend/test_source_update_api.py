"""Tests for `POST /api/source/{source_file}/{record_id}` — push-mode source update.

Unit tests use a fake GraphStore with a real ConflictStore over in-memory
SQLite + a mocked Ingestor. The integration test that exercises the full
ingest → graph → conflict surface against live Neo4j lives in
`test_conflict_integration.py`.
"""
from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from backend.api.app import (
    app,
    get_context_engine,
    get_ingest_store,
    get_ingestor,
    get_store,
    _build_default_engine,
)
from backend.conflict import Candidate, ConflictStore, Verdict
from backend.graph.store import GraphStore
from backend.ingest import IngestStore
from backend.ingest.ingestor import ApplyRecordReport, Ingestor, RecordError
from backend.models.graph import FactConfidence


import sqlite3
from pathlib import Path

SCHEMA_PATH = Path(__file__).parent / "graph" / "schema.sql"


def _new_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    with open(SCHEMA_PATH, "r") as f:
        conn.executescript(f.read())
    return conn


_MINIMAL_SPEC_YAML_TEMPLATE = """
spec_version: 1
tenant: demo
source: {{ file_pattern: "{source_pattern}", format: json, record_path: '$[*]' }}
nodes:
  - name: row
    canonical_type: Person
    id_template: "person:{{emp_id}}"
    fields:
      - {{ attribute: emp_id, source: '$.emp_id' }}
edges: []
"""


def _seed_active_spec(conn: sqlite3.Connection, source_pattern: str) -> None:
    yaml_text = _MINIMAL_SPEC_YAML_TEMPLATE.format(source_pattern=source_pattern)
    conn.execute(
        """INSERT INTO mapping_specs
           (tenant, source_pattern, version, yaml_text, status, created_at)
           VALUES ('demo', ?, 1, ?, 'active', '2026-01-01T00:00:00+00:00')""",
        (source_pattern, yaml_text),
    )
    conn.commit()


def _cand(value: object, src: str = "x.json", rid: str = "x:1") -> Candidate:
    return Candidate(
        value=value, confidence=FactConfidence.EXACT,
        source_file=src, source_record_id=rid,
    )


@pytest.fixture
def fake_store_and_ingestor() -> tuple[MagicMock, MagicMock, sqlite3.Connection]:
    conn = _new_conn()
    cs = ConflictStore(conn)
    store = MagicMock(spec=GraphStore)
    store.conflicts = cs
    store._conn = conn

    ingestor = MagicMock(spec=Ingestor)
    return store, ingestor, conn


@pytest.fixture
def client(fake_store_and_ingestor: tuple[MagicMock, MagicMock, sqlite3.Connection]) -> Iterator[TestClient]:
    store, ingestor, conn = fake_store_and_ingestor
    ing_store = IngestStore(conn)
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[get_ingestor] = lambda: ingestor
    app.dependency_overrides[get_ingest_store] = lambda: ing_store
    app.dependency_overrides[get_context_engine] = _build_default_engine
    yield TestClient(app)
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Happy path — content changed, no conflicts surfaced
# ---------------------------------------------------------------------------


class TestSourceUpdate:
    def test_applies_record_returns_report(
        self,
        client: TestClient,
        fake_store_and_ingestor: tuple[MagicMock, MagicMock, sqlite3.Connection],
    ) -> None:
        store, ingestor, conn = fake_store_and_ingestor
        _seed_active_spec(conn, "hr/employees.json")

        ingestor.apply_record.return_value = ApplyRecordReport(
            source_record_id="person:emp_1002",
            content_changed=True,
            nodes_touched=["person:emp_1002"],
            skipped=False,
        )

        resp = client.post(
            "/api/source/hr/employees.json/person:emp_1002",
            json={"emp_id": "emp_1002", "name": "Alice", "title": "Staff Engineer"},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["source_file"] == "hr/employees.json"
        assert body["source_record_id"] == "person:emp_1002"
        assert body["content_changed"] is True
        assert body["skipped"] is False
        assert body["nodes_touched"] == ["person:emp_1002"]
        assert body["conflicts_open"] == []
        assert body["spec_version"] == 1

        # Apply was called with the active spec + raw record + URL-derived expected id.
        ingestor.apply_record.assert_called_once()
        kwargs = ingestor.apply_record.call_args.kwargs
        assert kwargs["expected_record_id"] == "person:emp_1002"
        args = ingestor.apply_record.call_args.args
        assert args[1] == {"emp_id": "emp_1002", "name": "Alice", "title": "Staff Engineer"}

    def test_idempotent_skip_returns_skipped_true(
        self,
        client: TestClient,
        fake_store_and_ingestor: tuple[MagicMock, MagicMock, sqlite3.Connection],
    ) -> None:
        store, ingestor, conn = fake_store_and_ingestor
        _seed_active_spec(conn, "hr/employees.json")

        ingestor.apply_record.return_value = ApplyRecordReport(
            source_record_id="person:emp_1002",
            content_changed=False,
            nodes_touched=[],
            skipped=True,
        )

        resp = client.post(
            "/api/source/hr/employees.json/person:emp_1002",
            json={"emp_id": "emp_1002"},
        )
        assert resp.status_code == 200
        assert resp.json()["skipped"] is True
        assert resp.json()["content_changed"] is False


# ---------------------------------------------------------------------------
# Conflicts on touched nodes are surfaced
# ---------------------------------------------------------------------------


class TestSourceUpdateConflicts:
    def test_open_conflicts_on_touched_nodes_returned(
        self,
        client: TestClient,
        fake_store_and_ingestor: tuple[MagicMock, MagicMock, sqlite3.Connection],
    ) -> None:
        store, ingestor, conn = fake_store_and_ingestor
        _seed_active_spec(conn, "hr/employees.json")

        # Pre-seed an open conflict on a node about to be touched. This
        # simulates "the update lands on a node that already has unresolved
        # disagreement"; the response surfaces it so the demo can show
        # "look, this update touched a node with an open conflict."
        store.conflicts.record(
            node_id="person:emp_1002", attribute="title",
            existing=_cand("Senior Engineer", "hr.json", "h1"),
            incoming=_cand("Lead Engineer", "crm.json", "c1"),
            verdict=Verdict.ESCALATE, reason="tied_at_confident_rung",
        )
        store.conflicts.record(
            node_id="person:other", attribute="title",
            existing=_cand("X"), incoming=_cand("Y"),
            verdict=Verdict.ESCALATE, reason="tied_at_confident_rung",
        )

        ingestor.apply_record.return_value = ApplyRecordReport(
            source_record_id="person:emp_1002",
            content_changed=True,
            nodes_touched=["person:emp_1002"],
            skipped=False,
        )

        resp = client.post(
            "/api/source/hr/employees.json/person:emp_1002",
            json={"emp_id": "emp_1002", "title": "Staff"},
        )
        body = resp.json()
        # Only the conflict on the touched node returns; person:other is unrelated.
        assert len(body["conflicts_open"]) == 1
        assert body["conflicts_open"][0]["node_id"] == "person:emp_1002"


# ---------------------------------------------------------------------------
# Failure modes
# ---------------------------------------------------------------------------


class TestSourceUpdateFailures:
    def test_no_active_spec_returns_404(
        self, client: TestClient, fake_store_and_ingestor: tuple[MagicMock, MagicMock, sqlite3.Connection],
    ) -> None:
        # No spec seeded → 404
        resp = client.post(
            "/api/source/unknown.json/rec_1",
            json={"x": 1},
        )
        assert resp.status_code == 404
        assert "no active spec" in resp.json()["detail"].lower()

    def test_record_id_mismatch_returns_400(
        self,
        client: TestClient,
        fake_store_and_ingestor: tuple[MagicMock, MagicMock, sqlite3.Connection],
    ) -> None:
        store, ingestor, conn = fake_store_and_ingestor
        _seed_active_spec(conn, "hr/employees.json")
        ingestor.apply_record.side_effect = RecordError(
            "record id mismatch: expected 'person:wrong', spec id_template renders 'person:emp_1002'"
        )
        resp = client.post(
            "/api/source/hr/employees.json/person:wrong",
            json={"emp_id": "emp_1002"},
        )
        assert resp.status_code == 400
        assert "record id mismatch" in resp.json()["detail"]

    def test_missing_required_field_returns_400(
        self,
        client: TestClient,
        fake_store_and_ingestor: tuple[MagicMock, MagicMock, sqlite3.Connection],
    ) -> None:
        store, ingestor, conn = fake_store_and_ingestor
        _seed_active_spec(conn, "hr/employees.json")
        ingestor.apply_record.side_effect = RecordError(
            "required field 'emp_id' missing"
        )
        resp = client.post(
            "/api/source/hr/employees.json/person:emp_1002",
            json={"name": "Alice"},
        )
        assert resp.status_code == 400
        assert "required field" in resp.json()["detail"]
