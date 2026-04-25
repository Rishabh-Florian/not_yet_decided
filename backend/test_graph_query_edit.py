"""Tests for graph pattern query (Flow 2) and edit API (Flow 5).

Parser tests run without external services. Integration tests that touch
Neo4j/SQLite are marked with @pytest.mark.integration and use a real
GraphStore pointed at the live database.

Run all:   uv run pytest backend/test_graph_query_edit.py -v
Run fast:  uv run pytest backend/test_graph_query_edit.py -v -m "not integration"
"""
from __future__ import annotations

import os

import pytest

from backend.graph.store import GraphStore, parse_pattern
from backend.ingest.spec import CANONICAL_NODE_TYPES, CANONICAL_RELATION_TYPES


# ---------------------------------------------------------------------------
# parse_pattern — pure-function tests, no IO
# ---------------------------------------------------------------------------


class TestParsePattern:
    def test_valid_pattern(self) -> None:
        src, rel, tgt = parse_pattern("(Person)-[SENT]->(Message)")
        assert src == "Person"
        assert rel == "SENT"
        assert tgt == "Message"

    def test_valid_pattern_with_whitespace(self) -> None:
        src, rel, tgt = parse_pattern("  (Person)-[SENT]->(Message)  ")
        assert src == "Person"
        assert rel == "SENT"
        assert tgt == "Message"

    def test_all_canonical_combinations(self) -> None:
        for nt in sorted(CANONICAL_NODE_TYPES):
            for rt in sorted(CANONICAL_RELATION_TYPES):
                src, rel, tgt = parse_pattern(f"({nt})-[{rt}]->({nt})")
                assert src == nt
                assert rel == rt
                assert tgt == nt

    def test_invalid_syntax_no_arrow(self) -> None:
        with pytest.raises(ValueError, match="invalid pattern"):
            parse_pattern("(Person)-[SENT]-(Message)")

    def test_invalid_syntax_missing_brackets(self) -> None:
        with pytest.raises(ValueError, match="invalid pattern"):
            parse_pattern("Person-[SENT]->Message")

    def test_invalid_syntax_empty_string(self) -> None:
        with pytest.raises(ValueError, match="invalid pattern"):
            parse_pattern("")

    def test_unknown_source_node_type(self) -> None:
        with pytest.raises(ValueError, match="unknown source node type"):
            parse_pattern("(Alien)-[SENT]->(Message)")

    def test_unknown_target_node_type(self) -> None:
        with pytest.raises(ValueError, match="unknown target node type"):
            parse_pattern("(Person)-[SENT]->(Alien)")

    def test_unknown_relation_type(self) -> None:
        with pytest.raises(ValueError, match="unknown relation type"):
            parse_pattern("(Person)-[FLIES_TO]->(Person)")

    def test_sql_injection_in_node_type(self) -> None:
        with pytest.raises(ValueError):
            parse_pattern("(Person'; DROP TABLE--)-[SENT]->(Message)")

    def test_sql_injection_in_rel_type(self) -> None:
        with pytest.raises(ValueError):
            parse_pattern("(Person)-[SENT; DROP]->(Message)")


# ---------------------------------------------------------------------------
# Integration tests — require Neo4j + SQLite
# ---------------------------------------------------------------------------

integration = pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION", "").lower() not in ("1", "true", "yes"),
    reason="set RUN_INTEGRATION=1 to run integration tests",
)


def _make_store(tmp_path) -> GraphStore:
    return GraphStore(
        db_path=tmp_path / "test.sqlite",
        neo4j_uri=os.environ.get("NEO4J_URI", "bolt://localhost:7687"),
        neo4j_user=os.environ.get("NEO4J_USER", "neo4j"),
        neo4j_password=os.environ.get("NEO4J_PASSWORD", "better_context"),
        neo4j_database=os.environ.get("NEO4J_DATABASE", "neo4j"),
    )


@integration
class TestPatternQueryIntegration:
    def test_pattern_query_returns_triples(self, tmp_path) -> None:
        store = _make_store(tmp_path)
        try:
            matches, total = store.pattern_query("Person", "SENT", "Message", limit=5)
            assert isinstance(total, int)
            assert total >= 0
            for src_node, edge, tgt_node in matches:
                assert src_node.type == "Person"
                assert tgt_node.type == "Message"
                assert edge.relation_type == "SENT"
        finally:
            store.close()

    def test_pattern_query_pagination(self, tmp_path) -> None:
        store = _make_store(tmp_path)
        try:
            page1, total = store.pattern_query("Person", "SENT", "Message", limit=2, offset=0)
            page2, _ = store.pattern_query("Person", "SENT", "Message", limit=2, offset=2)
            if total >= 4:
                page1_ids = {m[1].id for m in page1}
                page2_ids = {m[1].id for m in page2}
                assert page1_ids.isdisjoint(page2_ids)
        finally:
            store.close()

    def test_pattern_query_no_matches(self, tmp_path) -> None:
        store = _make_store(tmp_path)
        try:
            matches, total = store.pattern_query("Topic", "PURCHASED", "Topic")
            assert total == 0
            assert matches == []
        finally:
            store.close()


@integration
class TestEditNodeIntegration:
    def _get_real_node_id(self, store: GraphStore) -> str:
        with store._session() as s:
            rec = s.run("MATCH (n:Entity {type: 'Person'}) RETURN n.id AS id LIMIT 1").single()
        assert rec is not None, "need at least one Person node in Neo4j"
        return rec["id"]

    def test_edit_creates_provenance(self, tmp_path) -> None:
        store = _make_store(tmp_path)
        try:
            node_id = self._get_real_node_id(store)
            original = store.get_node(node_id)
            assert original is not None
            original_prov_count = len(original.provenance)

            updated = store.edit_node(node_id, {"title": "Test Title"}, "test_editor")
            assert updated.attributes["title"] == "Test Title"

            human_prov = [
                p for p in updated.provenance if p.extraction_method == "human"
            ]
            assert len(human_prov) >= 1
            latest = human_prov[-1]
            assert latest.extraction_model == "human:test_editor"
            assert latest.confidence == 1.0
            assert latest.source_field == "title"
            assert latest.raw_value == "Test Title"
            assert latest.spec_version is None
            assert latest.source_file == "human_edits"
            assert len(updated.provenance) == original_prov_count + 1
        finally:
            store.close()

    def test_edit_bumps_version(self, tmp_path) -> None:
        store = _make_store(tmp_path)
        try:
            node_id = self._get_real_node_id(store)
            before = store.get_node(node_id)
            assert before is not None
            v_before = before.version

            store.edit_node(node_id, {"test_flag": "v_bump"}, "test_editor")
            after = store.get_node(node_id)
            assert after is not None
            assert after.version == v_before + 1
        finally:
            store.close()

    def test_edit_synthetic_source_record(self, tmp_path) -> None:
        store = _make_store(tmp_path)
        try:
            node_id = self._get_real_node_id(store)
            store.edit_node(node_id, {"sr_test": "yes"}, "test_editor")

            human_prov = [
                p for p in store._provenance_for_node(node_id)
                if p.extraction_method == "human" and p.source_field == "sr_test"
            ]
            assert len(human_prov) >= 1
            p = human_prov[-1]
            rec = store.get_source_record(p.source_file, p.source_record_id)
            assert rec is not None
            assert rec.raw_record["editor"] == "test_editor"
            assert rec.raw_record["node_id"] == node_id
            assert rec.raw_record["changes"] == {"sr_test": "yes"}
        finally:
            store.close()

    def test_edit_nonexistent_node(self, tmp_path) -> None:
        store = _make_store(tmp_path)
        try:
            with pytest.raises(KeyError):
                store.edit_node("nonexistent:node:id", {"x": 1}, "editor")
        finally:
            store.close()

    def test_edit_multiple_attributes(self, tmp_path) -> None:
        store = _make_store(tmp_path)
        try:
            node_id = self._get_real_node_id(store)
            changes = {"field_a": "alpha", "field_b": "beta", "field_c": "gamma"}
            updated = store.edit_node(node_id, changes, "multi_editor")

            for key, val in changes.items():
                assert updated.attributes[key] == val

            human_prov = [
                p for p in updated.provenance
                if p.extraction_method == "human"
                and p.source_field in changes
            ]
            edited_fields = {p.source_field for p in human_prov}
            assert edited_fields >= set(changes.keys())
        finally:
            store.close()


# ---------------------------------------------------------------------------
# FastAPI endpoint tests (httpx TestClient, no real Neo4j)
# ---------------------------------------------------------------------------


class TestPatternQueryEndpoint:
    def test_bad_pattern_returns_400(self) -> None:
        from fastapi.testclient import TestClient
        from unittest.mock import MagicMock
        from backend.api.app import app, get_store

        mock_store = MagicMock(spec=GraphStore)
        app.dependency_overrides[get_store] = lambda: mock_store
        try:
            client = TestClient(app)
            resp = client.post("/api/graph/query", json={"pattern": "garbage"})
            assert resp.status_code == 400
        finally:
            app.dependency_overrides.clear()

    def test_valid_pattern_calls_store(self) -> None:
        from fastapi.testclient import TestClient
        from unittest.mock import MagicMock
        from backend.api.app import app, get_store

        mock_store = MagicMock(spec=GraphStore)
        mock_store.pattern_query.return_value = ([], 0)
        app.dependency_overrides[get_store] = lambda: mock_store
        try:
            client = TestClient(app)
            resp = client.post(
                "/api/graph/query",
                json={"pattern": "(Person)-[SENT]->(Message)", "limit": 10},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["pattern"] == "(Person)-[SENT]->(Message)"
            assert data["matches"] == []
            assert data["total"] == 0
            mock_store.pattern_query.assert_called_once_with(
                "Person", "SENT", "Message", limit=10, offset=0,
            )
        finally:
            app.dependency_overrides.clear()


class TestEditNodeEndpoint:
    def test_empty_attributes_returns_400(self) -> None:
        from fastapi.testclient import TestClient
        from unittest.mock import MagicMock
        from backend.api.app import app, get_store

        mock_store = MagicMock(spec=GraphStore)
        app.dependency_overrides[get_store] = lambda: mock_store
        try:
            client = TestClient(app)
            resp = client.put(
                "/api/graph/node/test_id",
                json={"attributes": {}, "editor": "test"},
            )
            assert resp.status_code == 400
        finally:
            app.dependency_overrides.clear()

    def test_nonexistent_node_returns_404(self) -> None:
        from fastapi.testclient import TestClient
        from unittest.mock import MagicMock
        from backend.api.app import app, get_store

        mock_store = MagicMock(spec=GraphStore)
        mock_store.edit_node.side_effect = KeyError("not_found")
        app.dependency_overrides[get_store] = lambda: mock_store
        try:
            client = TestClient(app)
            resp = client.put(
                "/api/graph/node/not_found",
                json={"attributes": {"x": 1}, "editor": "test"},
            )
            assert resp.status_code == 404
        finally:
            app.dependency_overrides.clear()

    def test_successful_edit_returns_node(self) -> None:
        from fastapi.testclient import TestClient
        from unittest.mock import MagicMock
        from backend.api.app import app, get_store
        from backend.models.graph import GraphNode, Provenance

        mock_store = MagicMock(spec=GraphStore)
        mock_store.edit_node.return_value = GraphNode(
            id="person:test",
            type="Person",
            attributes={"name": "Test", "title": "Engineer"},
            provenance=[
                Provenance(
                    source_file="human_edits",
                    source_record_id="edit:person:test:2026-01-01T00:00:00+00:00",
                    source_field="title",
                    extraction_method="human",
                    extraction_model="human:test_editor",
                    confidence=1.0,
                    raw_value="Engineer",
                    spec_version=None,
                )
            ],
            confidence=1.0,
            vfs_path="/Person/test",
            version=3,
        )
        app.dependency_overrides[get_store] = lambda: mock_store
        try:
            client = TestClient(app)
            resp = client.put(
                "/api/graph/node/person:test",
                json={"attributes": {"title": "Engineer"}, "editor": "test_editor"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["id"] == "person:test"
            assert data["attributes"]["title"] == "Engineer"
            assert data["version"] == 3
            assert len(data["provenance"]) == 1
            assert data["provenance"][0]["extraction_method"] == "human"
        finally:
            app.dependency_overrides.clear()
