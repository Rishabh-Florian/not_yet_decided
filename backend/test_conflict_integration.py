"""Integration tests for conflict detection at the GraphStore.add_node seam.

These exercise real Neo4j + SQLite. Run only when RUN_INTEGRATION=1 AND
Neo4j is available at NEO4J_URI. The unit-test suites
(`test_conflict*.py`) cover the decision table and reconcile logic
without infra; this file verifies the store-level wiring.
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

from backend.conflict import Verdict
from backend.graph.store import GraphStore
from backend.models.graph import FactConfidence, GraphNode, Provenance


integration = pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION", "").lower() not in ("1", "true", "yes"),
    reason="set RUN_INTEGRATION=1 to run integration tests",
)


def _make_store(tmp_path: Path) -> GraphStore:
    return GraphStore(
        db_path=tmp_path / "test.sqlite",
        neo4j_uri=os.environ.get("NEO4J_URI", "bolt://localhost:7687"),
        neo4j_user=os.environ.get("NEO4J_USER", "neo4j"),
        neo4j_password=os.environ.get("NEO4J_PASSWORD", "better_context"),
        neo4j_database=os.environ.get("NEO4J_DATABASE", "neo4j"),
    )


def _seed_record(store: GraphStore, source_file: str, record_id: str) -> None:
    """Provenance has a FK to source_records; tests must seed both sides."""
    store.add_source_record(
        source_file=source_file,
        source_record_id=record_id,
        raw_record={"_test": True},
    )


def _prov(attribute: str, conf: FactConfidence, source_file: str, record_id: str,
          value: str = "") -> Provenance:
    return Provenance(
        source_file=source_file,
        source_record_id=record_id,
        source_field=f"$.{attribute}",
        attribute=attribute,
        extraction_method="direct_mapping",
        extraction_model="spec:v1",
        confidence=conf,
        raw_value=value,
    )


def _unique_id() -> str:
    return f"person:test:{uuid.uuid4().hex[:12]}"


@integration
class TestAddNodeConflicts:
    def test_first_ingest_creates_node_with_attrs(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        try:
            nid = _unique_id()
            _seed_record(store, "hr.json", "h1")
            store.add_node(GraphNode(
                id=nid, type="Person",
                attributes={"name": "Alice", "title": "Senior Engineer"},
                provenance=[
                    _prov("name", FactConfidence.EXACT, "hr.json", "h1", "Alice"),
                    _prov("title", FactConfidence.EXACT, "hr.json", "h1", "Senior Engineer"),
                ],
            ))
            n = store.get_node(nid)
            assert n is not None
            assert n.attributes == {"name": "Alice", "title": "Senior Engineer"}
            assert list(store.conflicts.list()) == []
            store._session().run("MATCH (n:Entity {id: $id}) DETACH DELETE n", id=nid).consume()
        finally:
            store.close()

    def test_escalate_keeps_existing_and_queues_conflict(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        try:
            nid = _unique_id()
            _seed_record(store, "hr.json", "h1")
            _seed_record(store, "crm.json", "c1")

            store.add_node(GraphNode(
                id=nid, type="Person",
                attributes={"title": "Senior Engineer"},
                provenance=[_prov("title", FactConfidence.EXACT, "hr.json", "h1",
                                  "Senior Engineer")],
            ))
            # Second source with same id, conflicting EXACT title.
            store.add_node(GraphNode(
                id=nid, type="Person",
                attributes={"title": "Lead Engineer"},
                provenance=[_prov("title", FactConfidence.EXACT, "crm.json", "c1",
                                  "Lead Engineer")],
            ))

            n = store.get_node(nid)
            assert n is not None
            assert n.attributes["title"] == "Senior Engineer"  # existing kept

            conflicts = list(store.conflicts.list(node_id=nid))
            assert len(conflicts) == 1
            c = conflicts[0]
            assert c.attribute == "title"
            assert c.verdict == Verdict.ESCALATE
            assert c.existing.value == "Senior Engineer"
            assert c.incoming.value == "Lead Engineer"
            assert c.existing.source_file == "hr.json"
            assert c.incoming.source_file == "crm.json"

            store._session().run("MATCH (n:Entity {id: $id}) DETACH DELETE n", id=nid).consume()
        finally:
            store.close()

    def test_higher_confidence_overwrites_existing(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        try:
            nid = _unique_id()
            _seed_record(store, "emails.json", "e1")
            _seed_record(store, "hr.json", "h1")

            # First write: INFERRED extraction from email body.
            store.add_node(GraphNode(
                id=nid, type="Person",
                attributes={"title": "Lead Engineer"},
                provenance=[_prov("title", FactConfidence.INFERRED, "emails.json", "e1",
                                  "Lead Engineer")],
            ))
            # Second write: EXACT structured field from HR.
            store.add_node(GraphNode(
                id=nid, type="Person",
                attributes={"title": "Senior Engineer"},
                provenance=[_prov("title", FactConfidence.EXACT, "hr.json", "h1",
                                  "Senior Engineer")],
            ))

            n = store.get_node(nid)
            assert n is not None
            # EXACT beats INFERRED → incoming wins.
            assert n.attributes["title"] == "Senior Engineer"
            # No conflict queued — the ladder broke the tie.
            assert list(store.conflicts.list(node_id=nid)) == []

            store._session().run("MATCH (n:Entity {id: $id}) DETACH DELETE n", id=nid).consume()
        finally:
            store.close()

    def test_disjoint_attrs_preserved_across_sources(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        try:
            nid = _unique_id()
            _seed_record(store, "hr.json", "h1")
            _seed_record(store, "crm.json", "c1")

            # HR contributes name + title.
            store.add_node(GraphNode(
                id=nid, type="Person",
                attributes={"name": "Alice", "title": "Engineer"},
                provenance=[
                    _prov("name", FactConfidence.EXACT, "hr.json", "h1", "Alice"),
                    _prov("title", FactConfidence.EXACT, "hr.json", "h1", "Engineer"),
                ],
            ))
            # CRM contributes only email — must NOT erase HR's name + title.
            store.add_node(GraphNode(
                id=nid, type="Person",
                attributes={"email": "alice@x"},
                provenance=[_prov("email", FactConfidence.EXACT, "crm.json", "c1",
                                  "alice@x")],
            ))

            n = store.get_node(nid)
            assert n is not None
            assert n.attributes["name"] == "Alice"
            assert n.attributes["title"] == "Engineer"
            assert n.attributes["email"] == "alice@x"

            store._session().run("MATCH (n:Entity {id: $id}) DETACH DELETE n", id=nid).consume()
        finally:
            store.close()

    def test_idempotent_reingest_does_not_duplicate_conflict(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        try:
            nid = _unique_id()
            _seed_record(store, "hr.json", "h1")
            _seed_record(store, "crm.json", "c1")

            store.add_node(GraphNode(
                id=nid, type="Person",
                attributes={"title": "Senior"},
                provenance=[_prov("title", FactConfidence.EXACT, "hr.json", "h1", "Senior")],
            ))
            for _ in range(3):
                store.add_node(GraphNode(
                    id=nid, type="Person",
                    attributes={"title": "Lead"},
                    provenance=[_prov("title", FactConfidence.EXACT, "crm.json", "c1", "Lead")],
                ))

            conflicts = list(store.conflicts.list(node_id=nid))
            assert len(conflicts) == 1, "re-ingest should not pile up duplicate open conflicts"

            store._session().run("MATCH (n:Entity {id: $id}) DETACH DELETE n", id=nid).consume()
        finally:
            store.close()
