"""Tests for `ExactTier`.

Unit tests use a `MagicMock(spec=GraphStore)` so we never touch a real
Neo4j. Integration tests gated on `RUN_INTEGRATION=1` exercise the live
fulltext index against a populated graph (matching the pattern in
`backend/test_graph_query_edit.py`).
"""
from __future__ import annotations

import json
import os
from unittest.mock import MagicMock

import pytest

from backend.graph.store import GraphStore
from backend.models.graph import FactConfidence, Provenance
from backend.retrieval import ExactTier, QueryContext
from backend.retrieval.exact import (
    _escape_lucene,
    _extract_id_tokens,
    _normalize_bm25,
    _preview,
)


# ---------------------------------------------------------------------------
# Pure-function tests — no Neo4j, no GraphStore
# ---------------------------------------------------------------------------


class TestExtractIdTokens:
    def test_emp_id(self) -> None:
        assert _extract_id_tokens("who is emp_0431?") == ["emp_0431"]

    def test_clnt_id(self) -> None:
        assert _extract_id_tokens("ticket for CLNT-0042") == ["CLNT-0042"]

    def test_asin_id(self) -> None:
        assert _extract_id_tokens("Tell me about B0BQ3K23Y1") == ["B0BQ3K23Y1"]

    def test_uuid(self) -> None:
        q = "see record 4226322d-0ea5-4b7c-9a31-7c1d8e0f1234"
        assert _extract_id_tokens(q) == ["4226322d-0ea5-4b7c-9a31-7c1d8e0f1234"]

    def test_multiple_unique(self) -> None:
        out = _extract_id_tokens("emp_1002 reports to emp_1003")
        assert out == ["emp_1002", "emp_1003"]

    def test_dedup(self) -> None:
        assert _extract_id_tokens("emp_1002 emp_1002") == ["emp_1002"]

    def test_no_match(self) -> None:
        assert _extract_id_tokens("who handles vpn?") == []

    def test_ticket_prefix(self) -> None:
        assert _extract_id_tokens("ticket-4226 status") == ["ticket-4226"]


class TestEscapeLucene:
    def test_special_chars_escaped(self) -> None:
        out = _escape_lucene('hello (world) +foo')
        assert "\\(" in out
        assert "\\)" in out
        assert "\\+" in out

    def test_plain_text_unchanged(self) -> None:
        assert _escape_lucene("plain phrase") == "plain phrase"


class TestNormalizeBM25:
    def test_zero(self) -> None:
        assert _normalize_bm25(0.0) == 0.0

    def test_one(self) -> None:
        assert _normalize_bm25(1.0) == 0.5

    def test_monotonic(self) -> None:
        assert _normalize_bm25(2.0) > _normalize_bm25(1.0)
        assert _normalize_bm25(10.0) > _normalize_bm25(5.0)

    def test_bounded(self) -> None:
        assert _normalize_bm25(1e9) < 1.0

    def test_negative_raises(self) -> None:
        with pytest.raises(ValueError, match="must be >= 0"):
            _normalize_bm25(-0.1)


class TestPreview:
    def test_picks_name(self) -> None:
        assert _preview({"name": "Raj Patel", "title": "Director"}) == "Raj Patel"

    def test_picks_title_if_no_name(self) -> None:
        assert _preview({"title": "Director", "category": "Eng"}) == "Director"

    def test_falls_back_to_json(self) -> None:
        out = _preview({"foo": "bar"})
        assert "foo" in out
        assert "bar" in out

    def test_truncates(self) -> None:
        assert len(_preview({"name": "x" * 500})) == 200


# ---------------------------------------------------------------------------
# ExactTier — mocked GraphStore (no Neo4j)
# ---------------------------------------------------------------------------


def _mock_store() -> MagicMock:
    """A `MagicMock(spec=GraphStore)` with a session context manager
    callable that records `run` invocations.
    """
    store = MagicMock(spec=GraphStore)
    # Bypass `ensure_indexes` (which calls `driver.session(...)`) by using
    # a driver mock that yields a session whose `run` is a no-op.
    store._driver = MagicMock()
    store._database = "neo4j"
    store._driver.session.return_value.__enter__.return_value.run.return_value = None
    return store


def _attach_session(store: MagicMock, run_results: list[list[dict]]) -> MagicMock:
    """Wire `store._session()` to a context manager whose `run` returns the
    next result list on each call.
    """
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


class TestExactTierConstruction:
    def test_rejects_non_store(self) -> None:
        with pytest.raises(TypeError, match="store must be GraphStore"):
            ExactTier(store="not-a-store")  # type: ignore[arg-type]

    def test_rejects_bad_name(self) -> None:
        store = _mock_store()
        with pytest.raises(ValueError, match="lowercase identifier"):
            ExactTier(store, name="EXACT")

    def test_rejects_bad_limit(self) -> None:
        store = _mock_store()
        with pytest.raises(ValueError, match="fulltext_limit"):
            ExactTier(store, fulltext_limit=0)

    def test_default_name(self) -> None:
        store = _mock_store()
        tier = ExactTier(store)
        assert tier.name == "exact"

    def test_calls_ensure_indexes(self) -> None:
        store = _mock_store()
        ExactTier(store)
        # `ensure_indexes` opens a driver session and runs the CREATE.
        assert store._driver.session.called


class TestExactIdLookup:
    def test_id_hit_returns_relevance_one(self) -> None:
        store = _mock_store()
        _attach_session(
            store,
            run_results=[
                [{"id": "emp_0431", "attrs": json.dumps({"name": "Raj"})}],
            ],
        )
        store._provenance_for_node.return_value = [
            Provenance(
                source_file="HR/employees.json",
                source_record_id="row:0",
                source_field="emp_id",
                extraction_method="direct_mapping",
                extraction_model="rule:hr_v1",
                confidence=FactConfidence.EXACT,
                raw_value="emp_0431",
            )
        ]
        tier = ExactTier(store)
        result = tier.search("who is emp_0431?", QueryContext())
        assert result.tier_used == "exact"
        assert result.relevance == 1.0
        assert len(result.items) == 1
        assert result.items[0].id == "emp_0431"
        assert result.items[0].score == 1.0
        assert result.items[0].preview == "Raj"
        assert len(result.citations) == 1
        assert result.citations[0].source_file == "HR/employees.json"

    def test_id_miss_falls_through_to_fulltext(self) -> None:
        store = _mock_store()
        # First run: id-lookup returns no rows. Second run: fulltext.
        _attach_session(
            store,
            run_results=[
                [],
                [{"id": "emp_9", "attrs": json.dumps({"name": "Alice"}), "score": 1.5}],
            ],
        )
        store._provenance_for_node.return_value = []
        tier = ExactTier(store)
        # Id-shaped token AND a short phrase: id-lookup misses, fulltext
        # fires. Avoid `?` / `*` (they short-circuit the fulltext branch).
        result = tier.search("emp_999 owner", QueryContext())
        assert result.tier_used == "exact"
        assert len(result.items) == 1
        assert result.items[0].id == "emp_9"
        # 1.5 / (1 + 1.5) = 0.6
        assert result.items[0].score == pytest.approx(0.6)
        assert result.relevance == pytest.approx(0.6)


class TestFulltextLookup:
    def test_phrase_query_returns_normalized_score(self) -> None:
        store = _mock_store()
        _attach_session(
            store,
            run_results=[
                [
                    {"id": "person_1", "attrs": json.dumps({"name": "Raj Patel"}), "score": 3.0},
                    {"id": "person_2", "attrs": json.dumps({"name": "Raj Singh"}), "score": 1.0},
                ],
            ],
        )
        store._provenance_for_node.return_value = []
        tier = ExactTier(store)
        result = tier.search("Raj Patel", QueryContext())
        assert result.tier_used == "exact"
        assert len(result.items) == 2
        # 3 / 4 = 0.75
        assert result.items[0].score == pytest.approx(0.75)
        # 1 / 2 = 0.5
        assert result.items[1].score == pytest.approx(0.5)
        # relevance == max
        assert result.relevance == pytest.approx(0.75)

    def test_long_query_skips_fulltext(self) -> None:
        store = _mock_store()
        _attach_session(store, run_results=[])
        tier = ExactTier(store)
        # 5 tokens, no id => skip fulltext, return empty.
        result = tier.search("who handles the vpn outage", QueryContext())
        assert result.items == []
        assert result.relevance == 0.0
        # Session was never opened for retrieval (no id, > 4 tokens).
        store._session.assert_not_called()

    def test_wildcard_query_skips_fulltext(self) -> None:
        store = _mock_store()
        _attach_session(store, run_results=[])
        tier = ExactTier(store)
        result = tier.search("vpn*", QueryContext())
        assert result.items == []
        assert result.relevance == 0.0
        store._session.assert_not_called()


class TestMissReturnsZeroRelevance:
    def test_id_miss_no_fulltext_match(self) -> None:
        store = _mock_store()
        _attach_session(
            store,
            run_results=[
                [],  # id-lookup miss
                [],  # fulltext miss
            ],
        )
        tier = ExactTier(store)
        # Short query (no `?`/`*`) so fulltext is also attempted.
        result = tier.search("emp_999 ghost", QueryContext())
        assert result.items == []
        assert result.citations == []
        assert result.relevance == 0.0
        assert result.tier_used == "exact"


class TestSearchInputValidation:
    def test_rejects_non_string_query(self) -> None:
        store = _mock_store()
        tier = ExactTier(store)
        with pytest.raises(TypeError, match="query must be str"):
            tier.search(42, QueryContext())  # type: ignore[arg-type]

    def test_rejects_empty_query(self) -> None:
        store = _mock_store()
        tier = ExactTier(store)
        with pytest.raises(ValueError, match="non-empty"):
            tier.search("   ", QueryContext())


# ---------------------------------------------------------------------------
# Integration tests — require a running Neo4j with ingested data
# ---------------------------------------------------------------------------

integration = pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION", "").lower() not in ("1", "true", "yes"),
    reason="set RUN_INTEGRATION=1 to run integration tests",
)


def _make_store(tmp_path) -> GraphStore:  # noqa: ANN001
    return GraphStore(
        db_path=tmp_path / "test.sqlite",
        neo4j_uri=os.environ.get("NEO4J_URI", "bolt://localhost:7687"),
        neo4j_user=os.environ.get("NEO4J_USER", "neo4j"),
        neo4j_password=os.environ.get("NEO4J_PASSWORD", "better_context"),
        neo4j_database=os.environ.get("NEO4J_DATABASE", "neo4j"),
    )


@integration
class TestExactTierIntegration:
    def test_index_is_idempotent(self, tmp_path) -> None:  # noqa: ANN001
        store = _make_store(tmp_path)
        try:
            ExactTier(store)
            ExactTier(store)  # second construction must not raise
        finally:
            store.close()

    def test_unknown_id_returns_zero_relevance(self, tmp_path) -> None:  # noqa: ANN001
        store = _make_store(tmp_path)
        try:
            tier = ExactTier(store)
            result = tier.search("emp_99999999", QueryContext())
            assert result.tier_used == "exact"
            assert result.items == []
            assert result.relevance == 0.0
        finally:
            store.close()

    def test_first_known_emp_id(self, tmp_path) -> None:  # noqa: ANN001
        store = _make_store(tmp_path)
        try:
            with store._session() as s:
                rec = s.run(
                    "MATCH (n:Entity {type: 'Person'}) RETURN n.id AS id LIMIT 1"
                ).single()
            if rec is None:
                pytest.skip("no Person nodes in the graph")
            node_id = rec["id"]
            tier = ExactTier(store)
            result = tier.search(node_id, QueryContext())
            assert result.tier_used == "exact"
            # If the id matches our id-token regex it lands as relevance=1.0;
            # otherwise it falls through to fulltext (still > 0).
            assert result.relevance > 0.0
            assert any(h.id == node_id for h in result.items)
        finally:
            store.close()
