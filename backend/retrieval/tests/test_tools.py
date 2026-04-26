"""Tests for `backend.retrieval.tools` — the AgenticTier's tool surface.

Unit tests use `MagicMock(spec=GraphStore)` so we never touch a real
Neo4j. Per-tool tests verify input validation (fail-fast) + happy-path
dispatch + citation accumulation.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from backend.graph.store import GraphStore
from backend.models.graph import FactConfidence, GraphEdge, GraphNode, Provenance, SourceRecord
from backend.retrieval.embedder import StubEmbedder
from backend.retrieval.tools import (
    CitationCollector,
    ToolBox,
    ToolDefinition,
    tool_definitions,
)


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


def _prov(field: str = "name") -> Provenance:
    return Provenance(
        source_file="HR/employees.json",
        source_record_id="row:0",
        source_field=field,
        extraction_method="direct_mapping",
        extraction_model="rule:hr_v1",
        confidence=FactConfidence.EXACT,
        raw_value="emp_1002",
    )


# ---------------------------------------------------------------------------
# CitationCollector
# ---------------------------------------------------------------------------


class TestCitationCollector:
    def test_dedupes_identical_provenance(self) -> None:
        store = _mock_store()
        store._provenance_for_node.return_value = [_prov("name"), _prov("name")]
        c = CitationCollector()
        c.add_node(store, "emp_1002")
        c.add_node(store, "emp_1002")
        # Same (source_file, source_record_id, source_field) → only one Citation.
        assert len(c.citations) == 1

    def test_keeps_different_fields_distinct(self) -> None:
        store = _mock_store()
        store._provenance_for_node.return_value = [_prov("name"), _prov("title")]
        c = CitationCollector()
        c.add_node(store, "emp_1002")
        assert len(c.citations) == 2

    def test_source_record_added_once(self) -> None:
        c = CitationCollector()
        c.add_source_record("HR/employees.json", "row:0")
        c.add_source_record("HR/employees.json", "row:0")
        assert len(c.citations) == 1
        assert c.citations[0].source_field == "<whole_record>"


# ---------------------------------------------------------------------------
# ToolBox construction
# ---------------------------------------------------------------------------


class TestToolBoxConstruction:
    def test_rejects_non_store(self) -> None:
        with pytest.raises(TypeError, match="store must be GraphStore"):
            ToolBox("not a store", StubEmbedder())  # type: ignore[arg-type]

    def test_rejects_non_embedder(self) -> None:
        with pytest.raises(TypeError, match="Embedder protocol"):
            ToolBox(_mock_store(), "not an embedder")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# pattern_query
# ---------------------------------------------------------------------------


class TestPatternQuery:
    def test_validates_canonical_node_type(self) -> None:
        tb = ToolBox(_mock_store(), StubEmbedder())
        cites = CitationCollector()
        with pytest.raises(ValueError, match="unknown source node type"):
            tb.pattern_query(
                cites, src_type="NotAType", rel_type="MENTIONS", tgt_type="Person"
            )

    def test_validates_canonical_relation(self) -> None:
        tb = ToolBox(_mock_store(), StubEmbedder())
        cites = CitationCollector()
        with pytest.raises(ValueError, match="unknown relation type"):
            tb.pattern_query(
                cites, src_type="Message", rel_type="BOGUS", tgt_type="Person"
            )

    def test_rejects_bad_limit(self) -> None:
        tb = ToolBox(_mock_store(), StubEmbedder())
        cites = CitationCollector()
        with pytest.raises(ValueError, match="limit must be int in"):
            tb.pattern_query(
                cites,
                src_type="Person",
                rel_type="SENT",
                tgt_type="Message",
                limit=999,
            )

    def test_dispatches_and_collects_citations(self) -> None:
        store = _mock_store()
        src_node = GraphNode(
            id="emp_1002",
            type="Person",
            attributes={"name": "Alice"},
            provenance=[_prov("name")],
        )
        tgt_node = GraphNode(
            id="msg_1",
            type="Message",
            attributes={"subject": "Hi"},
            provenance=[_prov("subject")],
        )
        edge = GraphEdge(
            source_node_id="emp_1002",
            target_node_id="msg_1",
            relation_type="SENT",
        )
        store.pattern_query.return_value = ([(src_node, edge, tgt_node)], 1)
        store._provenance_for_node.return_value = [_prov("name")]

        tb = ToolBox(store, StubEmbedder())
        cites = CitationCollector()
        out = tb.pattern_query(
            cites, src_type="Person", rel_type="SENT", tgt_type="Message"
        )
        assert out["total"] == 1
        assert len(out["matches"]) == 1
        assert out["matches"][0]["source"]["id"] == "emp_1002"
        assert out["matches"][0]["target"]["id"] == "msg_1"
        # Citations were harvested from both nodes (dedup → 1 unique tuple).
        assert len(cites.citations) >= 1


# ---------------------------------------------------------------------------
# fulltext_search / vector_search
# ---------------------------------------------------------------------------


class TestFulltextSearch:
    def test_validates_query(self) -> None:
        tb = ToolBox(_mock_store(), StubEmbedder())
        cites = CitationCollector()
        with pytest.raises(ValueError, match="query must be a non-empty"):
            tb.fulltext_search(cites, query="   ")

    def test_dispatches_returns_hits(self) -> None:
        store = _mock_store()
        _attach_session(
            store,
            run_results=[
                [{"id": "emp_1002", "attrs": json.dumps({"name": "Alice"}), "score": 2.0}],
            ],
        )
        store._provenance_for_node.return_value = [_prov("name")]
        tb = ToolBox(store, StubEmbedder())
        cites = CitationCollector()
        hits = tb.fulltext_search(cites, query="Alice", k=3)
        assert len(hits) == 1
        assert hits[0].id == "emp_1002"
        # 2 / (1+2) = 0.666... — same normalization as ExactTier.
        assert hits[0].score == pytest.approx(2.0 / 3.0)
        assert len(cites.citations) == 1


class TestVectorSearch:
    def test_validates_k_upper_bound(self) -> None:
        tb = ToolBox(_mock_store(), StubEmbedder())
        cites = CitationCollector()
        with pytest.raises(ValueError, match="k must be int in"):
            tb.vector_search(cites, query="anything", k=9999)

    def test_dispatches_with_embedder(self) -> None:
        store = _mock_store()
        _attach_session(
            store,
            run_results=[
                [{"id": "p1", "attrs": json.dumps({"name": "X"}), "score": 0.92}],
            ],
        )
        store._provenance_for_node.return_value = []
        tb = ToolBox(store, StubEmbedder())
        cites = CitationCollector()
        hits = tb.vector_search(cites, query="vpn outage in EU", k=5)
        assert len(hits) == 1
        assert hits[0].score == pytest.approx(0.92)


# ---------------------------------------------------------------------------
# get_node / get_neighbors / get_source_record
# ---------------------------------------------------------------------------


class TestGetNode:
    def test_validates_id(self) -> None:
        tb = ToolBox(_mock_store(), StubEmbedder())
        cites = CitationCollector()
        with pytest.raises(ValueError, match="node_id must be a non-empty"):
            tb.get_node(cites, node_id="")

    def test_raises_on_missing(self) -> None:
        store = _mock_store()
        store.get_node.return_value = None
        store._provenance_for_node.return_value = []
        tb = ToolBox(store, StubEmbedder())
        cites = CitationCollector()
        with pytest.raises(KeyError, match="emp_X"):
            tb.get_node(cites, node_id="emp_X")

    def test_returns_dict_with_provenance(self) -> None:
        store = _mock_store()
        node = GraphNode(
            id="emp_1002",
            type="Person",
            attributes={"name": "Alice"},
            provenance=[_prov("name")],
        )
        store.get_node.return_value = node
        store._provenance_for_node.return_value = [_prov("name")]
        tb = ToolBox(store, StubEmbedder())
        cites = CitationCollector()
        out = tb.get_node(cites, node_id="emp_1002")
        assert out["id"] == "emp_1002"
        assert out["type"] == "Person"
        assert len(out["provenance"]) == 1
        assert len(cites.citations) == 1


class TestGetNeighbors:
    def test_validates_depth_upper_bound(self) -> None:
        store = _mock_store()
        store.get_node.return_value = GraphNode(id="x", type="Person")
        tb = ToolBox(store, StubEmbedder())
        cites = CitationCollector()
        with pytest.raises(ValueError, match="depth must be int in"):
            tb.get_neighbors(cites, node_id="x", depth=99)

    def test_raises_on_missing_anchor(self) -> None:
        store = _mock_store()
        store.get_node.return_value = None
        tb = ToolBox(store, StubEmbedder())
        cites = CitationCollector()
        with pytest.raises(KeyError):
            tb.get_neighbors(cites, node_id="ghost")

    def test_returns_neighbors(self) -> None:
        store = _mock_store()
        anchor = GraphNode(id="emp_1002", type="Person")
        nbr = GraphNode(id="emp_1003", type="Person", attributes={"name": "Bob"})

        def _get(nid: str) -> GraphNode | None:
            return {"emp_1002": anchor, "emp_1003": nbr}.get(nid)

        store.get_node.side_effect = _get
        store.neighbors.return_value = {"emp_1003"}
        store._provenance_for_node.return_value = []
        tb = ToolBox(store, StubEmbedder())
        cites = CitationCollector()
        out = tb.get_neighbors(cites, node_id="emp_1002", depth=1)
        assert out["total"] == 1
        assert out["neighbors"][0]["id"] == "emp_1003"


class TestGetSourceRecord:
    def test_validates(self) -> None:
        tb = ToolBox(_mock_store(), StubEmbedder())
        cites = CitationCollector()
        with pytest.raises(ValueError, match="source_file must be a non-empty"):
            tb.get_source_record(cites, source_file="", record_id="x")
        with pytest.raises(ValueError, match="record_id must be a non-empty"):
            tb.get_source_record(cites, source_file="HR/x.json", record_id="")

    def test_raises_on_missing(self) -> None:
        store = _mock_store()
        store.get_source_record.return_value = None
        tb = ToolBox(store, StubEmbedder())
        cites = CitationCollector()
        with pytest.raises(KeyError, match="source record not found"):
            tb.get_source_record(cites, source_file="HR/x.json", record_id="row:0")

    def test_returns_record_and_adds_citation(self) -> None:
        store = _mock_store()
        rec = SourceRecord(
            source_file="HR/employees.json",
            source_record_id="row:0",
            raw_record={"emp_id": "emp_1002", "name": "Alice"},
            content_hash="abc",
        )
        store.get_source_record.return_value = rec
        tb = ToolBox(store, StubEmbedder())
        cites = CitationCollector()
        out = tb.get_source_record(
            cites, source_file="HR/employees.json", record_id="row:0"
        )
        assert out["raw_record"]["emp_id"] == "emp_1002"
        assert len(cites.citations) == 1
        assert cites.citations[0].source_field == "<whole_record>"


# ---------------------------------------------------------------------------
# Dispatch by name
# ---------------------------------------------------------------------------


class TestToolBoxCall:
    def test_unknown_tool_raises(self) -> None:
        tb = ToolBox(_mock_store(), StubEmbedder())
        cites = CitationCollector()
        with pytest.raises(ValueError, match="unknown tool"):
            tb.call("not_a_tool", {}, cites)

    def test_rejects_non_dict_args(self) -> None:
        tb = ToolBox(_mock_store(), StubEmbedder())
        cites = CitationCollector()
        with pytest.raises(TypeError, match="args must be dict"):
            tb.call("get_node", "not-a-dict", cites)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# tool_definitions schema
# ---------------------------------------------------------------------------


class TestToolDefinitions:
    def test_six_tools(self) -> None:
        defs = tool_definitions()
        names = {d.name for d in defs}
        assert names == {
            "pattern_query",
            "fulltext_search",
            "vector_search",
            "get_node",
            "get_neighbors",
            "get_source_record",
        }

    def test_each_def_well_formed(self) -> None:
        for d in tool_definitions():
            assert isinstance(d, ToolDefinition)
            assert d.parameters["type"] == "object"
            assert isinstance(d.parameters["properties"], dict)
            assert isinstance(d.parameters["required"], list)
