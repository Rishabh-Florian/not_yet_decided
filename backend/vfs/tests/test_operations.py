"""Tests for `backend.vfs.operations` — the VFS tool surface.

Unit tests use `MagicMock(spec=GraphStore)` so we never touch a real Neo4j.
Per-method tests cover input validation (fail-fast) + happy-path dispatch +
the canonical-type registry coupling.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from backend.graph.store import GraphStore
from backend.models.graph import (
    FactConfidence,
    GraphNode,
    Provenance,
    SourceRecord,
)
from backend.vfs.operations import VFS, _matches_where, _parse_path


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _mock_store() -> MagicMock:
    store = MagicMock(spec=GraphStore)
    store._driver = MagicMock()
    store._database = "neo4j"
    return store


def _attach_session(store: MagicMock, run_results: list[list[dict]]) -> MagicMock:
    iter_results = iter(run_results)

    def _run(*_args, **_kwargs):  # noqa: ANN001
        return next(iter_results)

    sess = MagicMock()
    sess.run.side_effect = _run
    cm = MagicMock()
    cm.__enter__.return_value = sess
    cm.__exit__.return_value = None
    store._session.return_value = cm
    return store


def _prov(field: str = "name", source_file: str = "HR/employees.json") -> Provenance:
    return Provenance(
        source_file=source_file,
        source_record_id="row:0",
        source_field=field,
        extraction_method="direct_mapping",
        extraction_model="rule:hr_v1",
        confidence=FactConfidence.EXACT,
        raw_value="Alice",
    )


# ---------------------------------------------------------------------------
# _parse_path
# ---------------------------------------------------------------------------


class TestParsePath:
    def test_root(self) -> None:
        assert _parse_path("/") == ("root", None, None)
        assert _parse_path("") == ("root", None, None)

    def test_directory(self) -> None:
        assert _parse_path("/Person") == ("dir", "Person", None)
        assert _parse_path("/Person/") == ("dir", "Person", None)

    def test_node(self) -> None:
        assert _parse_path("/Person/person:emp_0431") == (
            "node",
            "Person",
            "person:emp_0431",
        )

    def test_rejects_non_string(self) -> None:
        with pytest.raises(TypeError, match="path must be a string"):
            _parse_path(42)  # type: ignore[arg-type]

    def test_rejects_relative_path(self) -> None:
        with pytest.raises(ValueError, match="path must start with"):
            _parse_path("Person")

    def test_rejects_unknown_canonical_type(self) -> None:
        with pytest.raises(ValueError, match="unknown canonical type"):
            _parse_path("/Bogus/x")

    def test_rejects_three_segment_path(self) -> None:
        # No "directories within a directory" — VFS is two-level.
        with pytest.raises(ValueError, match="more than two segments"):
            _parse_path("/Person/some/deep")


# ---------------------------------------------------------------------------
# _matches_where
# ---------------------------------------------------------------------------


class TestMatchesWhere:
    def test_equality_match(self) -> None:
        assert _matches_where({"category": "engineering"}, {"category": "engineering"})

    def test_mismatch(self) -> None:
        assert not _matches_where({"category": "hr"}, {"category": "engineering"})

    def test_missing_attribute(self) -> None:
        assert not _matches_where({"name": "Alice"}, {"category": "engineering"})

    def test_explicit_none_matches_missing(self) -> None:
        # `attrs.get(missing) -> None`; expected None matches it.
        assert _matches_where({"name": "Alice"}, {"category": None})


# ---------------------------------------------------------------------------
# VFS construction
# ---------------------------------------------------------------------------


class TestVFSConstruction:
    def test_rejects_non_store(self) -> None:
        with pytest.raises(TypeError, match="store must be GraphStore"):
            VFS("not a store")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# ls
# ---------------------------------------------------------------------------


class TestLsRoot:
    def test_returns_canonical_types(self) -> None:
        store = _mock_store()
        _attach_session(
            store,
            run_results=[
                [
                    {"t": "Person", "c": 3},
                    {"t": "Message", "c": 5},
                    {"t": "Document", "c": 1},
                ],
            ],
        )
        vfs = VFS(store)
        entries = vfs.ls("/")
        # All seven canonical types are listed (even those with 0 nodes).
        type_names = {e.name for e in entries}
        assert {"Person", "Message", "Document", "Asset", "Topic", "Event", "Organization"} <= type_names
        # Counts come from the COUNT cypher.
        person = next(e for e in entries if e.name == "Person")
        assert person.kind == "dir"
        assert person.child_count == 3
        assert person.path == "/Person/"

    def test_unknown_type_rejected(self) -> None:
        store = _mock_store()
        vfs = VFS(store)
        with pytest.raises(ValueError, match="unknown canonical type"):
            vfs.ls("/Unicorn/")


class TestLsType:
    def test_returns_node_entries(self) -> None:
        store = _mock_store()
        _attach_session(
            store,
            run_results=[
                [
                    {
                        "id": "person:emp_0431",
                        "attrs": json.dumps({"name": "Raj Patel", "category": "engineering"}),
                        "version": 2,
                        "updated_at": "2026-04-01T10:00:00+00:00",
                    },
                    {
                        "id": "person:emp_0106",
                        "attrs": json.dumps({"name": "Anita", "category": "engineering"}),
                        "version": 1,
                        "updated_at": None,
                    },
                ],
            ],
        )
        vfs = VFS(store)
        entries = vfs.ls("/Person/")
        assert len(entries) == 2
        assert entries[0].kind == "node"
        assert entries[0].path == "/Person/person:emp_0431"
        assert entries[0].preview == "Raj Patel"
        assert entries[0].version == 2
        # ISO string parses into datetime.
        assert entries[0].updated_at is not None
        # Null updated_at survives.
        assert entries[1].updated_at is None

    def test_rejects_node_path(self) -> None:
        store = _mock_store()
        vfs = VFS(store)
        with pytest.raises(ValueError, match="is a node \\(file\\)"):
            vfs.ls("/Person/person:emp_0431")

    def test_rejects_bad_limit(self) -> None:
        store = _mock_store()
        vfs = VFS(store)
        with pytest.raises(ValueError, match="limit must be int"):
            vfs.ls("/Person/", limit=999)


# ---------------------------------------------------------------------------
# cat
# ---------------------------------------------------------------------------


class TestCat:
    def test_assembles_filebody(self) -> None:
        store = _mock_store()
        node = GraphNode(
            id="person:emp_0431",
            type="Person",
            attributes={"name": "Raj Patel", "category": "engineering"},
            provenance=[_prov("name"), _prov("category")],
        )
        store.get_node.return_value = node
        # Neighbor cypher result + raw_record fetches.
        _attach_session(
            store,
            run_results=[
                [
                    {
                        "mid": "person:emp_0106",
                        "mtype": "Person",
                        "mattrs": json.dumps({"name": "Anita"}),
                        "rt": "REPORTS_TO",
                        "eid": "edge_xyz",
                        "src": "person:emp_0106",  # incoming
                    },
                ],
            ],
        )
        store.get_source_record.return_value = SourceRecord(
            source_file="HR/employees.json",
            source_record_id="row:0",
            raw_record={"emp_id": "emp_0431", "Name": "Raj Patel"},
            content_hash="deadbeef",
            ingested_at=datetime.now(timezone.utc),
        )

        vfs = VFS(store)
        body = vfs.cat("/Person/person:emp_0431")

        assert body.path == "/Person/person:emp_0431"
        assert body.frontmatter["id"] == "person:emp_0431"
        assert body.frontmatter["type"] == "Person"
        assert body.frontmatter["source_files"] == ["HR/employees.json"]
        assert body.attributes["name"] == "Raj Patel"
        # Neighbor grouped under REPORTS_TO with direction "in".
        assert "REPORTS_TO" in body.relations
        assert body.relations["REPORTS_TO"][0].direction == "in"
        # Two provenance rows but a single distinct (source_file, source_record_id).
        assert len(body.raw_evidence) == 1
        assert body.raw_evidence[0].source_file == "HR/employees.json"

    def test_rejects_directory_path(self) -> None:
        store = _mock_store()
        vfs = VFS(store)
        with pytest.raises(ValueError, match="is a directory"):
            vfs.cat("/Person/")

    def test_404_when_node_missing(self) -> None:
        store = _mock_store()
        store.get_node.return_value = None
        vfs = VFS(store)
        with pytest.raises(KeyError, match="no node at"):
            vfs.cat("/Person/person:nope")

    def test_404_when_type_mismatch(self) -> None:
        # An id that exists under a different canonical type must not
        # leak across the path tree.
        store = _mock_store()
        store.get_node.return_value = GraphNode(
            id="msg_1",
            type="Message",
            attributes={"subject": "Hi"},
            provenance=[],
        )
        vfs = VFS(store)
        with pytest.raises(KeyError, match="no node at"):
            vfs.cat("/Person/msg_1")


# ---------------------------------------------------------------------------
# stat
# ---------------------------------------------------------------------------


class TestStat:
    def test_root(self) -> None:
        store = _mock_store()
        _attach_session(
            store,
            run_results=[[{"t": "Person", "c": 3}, {"t": "Message", "c": 5}]],
        )
        vfs = VFS(store)
        info = vfs.stat("/")
        assert info.kind == "dir"
        assert info.child_count == 8
        assert info.type is None

    def test_directory(self) -> None:
        store = _mock_store()
        _attach_session(
            store,
            run_results=[[{"t": "Person", "c": 3}, {"t": "Message", "c": 5}]],
        )
        vfs = VFS(store)
        info = vfs.stat("/Person/")
        assert info.kind == "dir"
        assert info.type == "Person"
        assert info.child_count == 3

    def test_node(self) -> None:
        store = _mock_store()
        store.get_node.return_value = GraphNode(
            id="person:emp_0431",
            type="Person",
            attributes={"name": "Raj"},
            provenance=[_prov("name", "HR/a.json"), _prov("name", "HR/b.json")],
            version=4,
        )
        vfs = VFS(store)
        info = vfs.stat("/Person/person:emp_0431")
        assert info.kind == "node"
        assert info.version == 4
        assert sorted(info.source_files) == ["HR/a.json", "HR/b.json"]
        assert info.provenance_count == 2


# ---------------------------------------------------------------------------
# grep
# ---------------------------------------------------------------------------


class TestGrep:
    def test_validates_query(self) -> None:
        store = _mock_store()
        vfs = VFS(store)
        with pytest.raises(ValueError, match="query must be a non-empty"):
            vfs.grep("   ")

    def test_rejects_node_path(self) -> None:
        store = _mock_store()
        vfs = VFS(store)
        with pytest.raises(ValueError, match="must be a directory"):
            vfs.grep("VPN", path="/Person/person:abc")

    def test_dispatches_typed_scope(self) -> None:
        store = _mock_store()
        _attach_session(
            store,
            run_results=[
                [
                    {
                        "id": "msg_42",
                        "type": "Message",
                        "attrs": json.dumps({"subject": "VPN issue"}),
                        "score": 3.0,
                    },
                ],
            ],
        )
        vfs = VFS(store)
        hits = vfs.grep("VPN", path="/Message/")
        assert len(hits) == 1
        assert hits[0].type == "Message"
        assert hits[0].path == "/Message/msg_42"
        # 3 / (1+3) = 0.75
        assert hits[0].score == pytest.approx(0.75)


# ---------------------------------------------------------------------------
# find
# ---------------------------------------------------------------------------


class TestFind:
    def test_filters_by_where(self) -> None:
        store = _mock_store()
        _attach_session(
            store,
            run_results=[
                [
                    {
                        "id": "person:emp_0431",
                        "type": "Person",
                        "attrs": json.dumps({"name": "Raj", "category": "engineering"}),
                        "version": 1,
                        "updated_at": None,
                    },
                    {
                        "id": "person:emp_0042",
                        "type": "Person",
                        "attrs": json.dumps({"name": "Bob", "category": "hr"}),
                        "version": 1,
                        "updated_at": None,
                    },
                ],
            ],
        )
        vfs = VFS(store)
        entries = vfs.find("/Person/", where={"category": "engineering"})
        # Python-side filter drops the HR row.
        assert len(entries) == 1
        assert entries[0].node_id == "person:emp_0431"

    def test_rejects_node_path(self) -> None:
        store = _mock_store()
        vfs = VFS(store)
        with pytest.raises(ValueError, match="must be a directory"):
            vfs.find("/Person/person:abc")

    def test_rejects_bad_where(self) -> None:
        store = _mock_store()
        vfs = VFS(store)
        with pytest.raises(TypeError, match="where must be"):
            vfs.find("/Person/", where=["not", "a", "dict"])  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# tree
# ---------------------------------------------------------------------------


class TestTree:
    def test_root_depth_1(self) -> None:
        store = _mock_store()
        _attach_session(
            store,
            run_results=[
                [{"t": "Person", "c": 3}, {"t": "Message", "c": 5}],
            ],
        )
        vfs = VFS(store)
        tree = vfs.tree("/", depth=1)
        assert tree.kind == "dir"
        assert tree.path == "/"
        # Children are the canonical types, no expansion below.
        assert all(child.kind == "dir" for child in tree.children)
        assert all(child.children == [] for child in tree.children)

    def test_rejects_bad_depth(self) -> None:
        store = _mock_store()
        vfs = VFS(store)
        with pytest.raises(ValueError, match="depth must be int"):
            vfs.tree("/", depth=99)
