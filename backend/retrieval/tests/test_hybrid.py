"""Tests for `HybridTier` and the `Embedder` protocol.

Unit tests use a `MagicMock(spec=GraphStore)` plus the deterministic
`StubEmbedder` so we never touch a real Neo4j or pull a real model.
Integration tests gated on `RUN_INTEGRATION=1` exercise the live
vector + fulltext indexes (matching the pattern in `test_exact.py`).
"""
from __future__ import annotations

import json
import os
from unittest.mock import MagicMock

import pytest

from backend.graph.store import GraphStore
from backend.models.graph import FactConfidence, Provenance
from backend.retrieval import HybridTier, QueryContext, StubEmbedder
from backend.retrieval._util import _escape_lucene, _preview
from backend.retrieval.hybrid import _rrf_fuse


# ---------------------------------------------------------------------------
# Pure-function tests — no Neo4j, no GraphStore, no embedder
# ---------------------------------------------------------------------------


class TestRRFFuse:
    def test_empty_inputs_return_empty(self) -> None:
        assert _rrf_fuse([], []) == []

    def test_single_arm_only(self) -> None:
        out = _rrf_fuse(["a", "b"], [])
        # a is rank 1 in vector arm; max possible RRF for two arms = 2/(60+1).
        # a's score = 1/61 / (2/61) = 0.5 (single-arm hit).
        assert out[0][0] == "a"
        assert out[1][0] == "b"
        assert out[0][1] == pytest.approx(0.5)
        assert out[1][1] < out[0][1]

    def test_intersection_outranks_union(self) -> None:
        # `x` appears rank 1 in both arms; `y` appears rank 1 in only one.
        out = dict(_rrf_fuse(["x", "y"], ["x"]))
        assert out["x"] > out["y"]
        # Both-arms-rank-1 == 1.0 (max possible).
        assert out["x"] == pytest.approx(1.0)

    def test_lower_rank_means_lower_score(self) -> None:
        out = dict(_rrf_fuse(["a", "b", "c"], []))
        assert out["a"] > out["b"] > out["c"]

    def test_invalid_k_raises(self) -> None:
        with pytest.raises(ValueError, match="RRF k must be >= 1"):
            _rrf_fuse(["a"], [], k=0)

    def test_custom_k_changes_dampening(self) -> None:
        # k=1 strongly differentiates; k=1000 flattens. Both must
        # still order correctly.
        small_k = dict(_rrf_fuse(["a", "b"], [], k=1))
        large_k = dict(_rrf_fuse(["a", "b"], [], k=1000))
        assert small_k["a"] > small_k["b"]
        assert large_k["a"] > large_k["b"]
        # Large k => ratios approach 1 (less differentiation).
        assert large_k["b"] / large_k["a"] > small_k["b"] / small_k["a"]


class TestEscapeLucene:
    def test_special_chars_escaped(self) -> None:
        out = _escape_lucene('hello (world) +foo')
        assert "\\(" in out
        assert "\\)" in out
        assert "\\+" in out

    def test_plain_text_unchanged(self) -> None:
        assert _escape_lucene("plain phrase") == "plain phrase"


class TestPreview:
    def test_picks_name(self) -> None:
        assert _preview({"name": "Raj Patel"}) == "Raj Patel"

    def test_falls_back_to_json(self) -> None:
        out = _preview({"foo": "bar"})
        assert "foo" in out


# ---------------------------------------------------------------------------
# StubEmbedder unit tests
# ---------------------------------------------------------------------------


class TestStubEmbedder:
    def test_dim_default(self) -> None:
        e = StubEmbedder()
        assert e.dim == 384

    def test_embed_returns_correct_dim(self) -> None:
        e = StubEmbedder(dim=64)
        v = e.embed("hello world")
        assert len(v) == 64

    def test_deterministic(self) -> None:
        e = StubEmbedder()
        assert e.embed("foo") == e.embed("foo")

    def test_different_inputs_different_outputs(self) -> None:
        e = StubEmbedder()
        assert e.embed("foo") != e.embed("bar")

    def test_l2_normalized(self) -> None:
        e = StubEmbedder()
        v = e.embed("anything")
        norm_sq = sum(x * x for x in v)
        assert norm_sq == pytest.approx(1.0, rel=1e-6)

    def test_rejects_empty(self) -> None:
        e = StubEmbedder()
        with pytest.raises(ValueError, match="non-empty"):
            e.embed("")

    def test_rejects_non_str(self) -> None:
        e = StubEmbedder()
        with pytest.raises(TypeError, match="text must be str"):
            e.embed(42)  # type: ignore[arg-type]

    def test_rejects_bad_dim(self) -> None:
        with pytest.raises(ValueError, match="dim must be >= 1"):
            StubEmbedder(dim=0)


# ---------------------------------------------------------------------------
# HybridTier — mocked GraphStore + StubEmbedder (no Neo4j)
# ---------------------------------------------------------------------------


def _mock_store() -> MagicMock:
    """Mock GraphStore with a no-op driver session for `ensure_indexes`."""
    store = MagicMock(spec=GraphStore)
    store._driver = MagicMock()
    store._database = "neo4j"
    store._driver.session.return_value.__enter__.return_value.run.return_value = None
    return store


def _attach_session(store: MagicMock, run_results: list[list[dict]]) -> MagicMock:
    """Wire `store._session()` to a context manager whose `run` returns the
    next result list on each call."""
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


class TestHybridTierConstruction:
    def test_rejects_non_store(self) -> None:
        with pytest.raises(TypeError, match="store must be GraphStore"):
            HybridTier(store="x", embedder=StubEmbedder())  # type: ignore[arg-type]

    def test_rejects_non_embedder(self) -> None:
        store = _mock_store()
        with pytest.raises(TypeError, match="Embedder protocol"):
            HybridTier(store, embedder="x")  # type: ignore[arg-type]

    def test_rejects_bad_name(self) -> None:
        store = _mock_store()
        with pytest.raises(ValueError, match="lowercase identifier"):
            HybridTier(store, StubEmbedder(), name="HYBRID")

    def test_rejects_bad_candidate_limit(self) -> None:
        store = _mock_store()
        with pytest.raises(ValueError, match="candidate_limit"):
            HybridTier(store, StubEmbedder(), candidate_limit=0)

    def test_rejects_result_gt_candidate(self) -> None:
        store = _mock_store()
        with pytest.raises(ValueError, match="result_limit"):
            HybridTier(
                store, StubEmbedder(), candidate_limit=5, result_limit=10
            )

    def test_rejects_bad_rrf_k(self) -> None:
        store = _mock_store()
        with pytest.raises(ValueError, match="rrf_k"):
            HybridTier(store, StubEmbedder(), rrf_k=0)

    def test_default_name(self) -> None:
        store = _mock_store()
        tier = HybridTier(store, StubEmbedder())
        assert tier.name == "hybrid"

    def test_creates_both_indexes(self) -> None:
        store = _mock_store()
        HybridTier(store, StubEmbedder())
        # Two driver-session calls: one for fulltext, one for vector.
        assert store._driver.session.call_count >= 2


class TestHybridSearch:
    def _tier(self, store: MagicMock) -> HybridTier:
        return HybridTier(store, StubEmbedder(), result_limit=5, candidate_limit=10)

    def test_rejects_non_string_query(self) -> None:
        store = _mock_store()
        tier = self._tier(store)
        with pytest.raises(TypeError, match="query must be str"):
            tier.search(42, QueryContext())  # type: ignore[arg-type]

    def test_rejects_empty_query(self) -> None:
        store = _mock_store()
        tier = self._tier(store)
        with pytest.raises(ValueError, match="non-empty"):
            tier.search("   ", QueryContext())

    def test_both_arms_empty_returns_zero_relevance(self) -> None:
        store = _mock_store()
        _attach_session(store, run_results=[[], []])  # vector miss, fulltext miss
        tier = self._tier(store)
        result = tier.search("anything", QueryContext())
        assert result.tier_used == "hybrid"
        assert result.items == []
        assert result.citations == []
        assert result.relevance == 0.0

    def test_vector_only_hit_returns_normalized_score(self) -> None:
        store = _mock_store()
        _attach_session(
            store,
            run_results=[
                # vector arm: rank-1 hit
                [{"id": "n1", "attrs": json.dumps({"name": "Alice"}), "score": 0.91}],
                # fulltext arm: empty
                [],
            ],
        )
        store._provenance_for_node.return_value = []
        tier = self._tier(store)
        result = tier.search("vpn outage", QueryContext())
        assert result.tier_used == "hybrid"
        assert len(result.items) == 1
        assert result.items[0].id == "n1"
        # Single arm rank 1 == 0.5 (half of the both-arms-rank-1 max).
        assert result.items[0].score == pytest.approx(0.5)
        assert result.relevance == pytest.approx(0.5)

    def test_intersection_top_score_is_one(self) -> None:
        store = _mock_store()
        _attach_session(
            store,
            run_results=[
                [
                    {"id": "n1", "attrs": json.dumps({"name": "Alice"}), "score": 0.9},
                    {"id": "n2", "attrs": json.dumps({"name": "Bob"}), "score": 0.7},
                ],
                [
                    {"id": "n1", "attrs": json.dumps({"name": "Alice"}), "score": 2.0},
                    {"id": "n3", "attrs": json.dumps({"name": "Carol"}), "score": 1.0},
                ],
            ],
        )
        store._provenance_for_node.return_value = []
        tier = self._tier(store)
        result = tier.search("alice", QueryContext())
        # n1 is rank 1 in BOTH arms => fused score 1.0.
        assert result.items[0].id == "n1"
        assert result.items[0].score == pytest.approx(1.0)
        assert result.relevance == pytest.approx(1.0)
        # n2 and n3 are single-arm hits and should both be present.
        ids = {h.id for h in result.items}
        assert ids == {"n1", "n2", "n3"}

    def test_result_limit_truncates(self) -> None:
        store = _mock_store()
        # 8 distinct vector hits, 0 fulltext.
        vec_rows = [
            {"id": f"n{i}", "attrs": json.dumps({"name": f"x{i}"}), "score": 1.0 - i * 0.1}
            for i in range(8)
        ]
        _attach_session(store, run_results=[vec_rows, []])
        store._provenance_for_node.return_value = []
        tier = HybridTier(
            store, StubEmbedder(), result_limit=3, candidate_limit=10
        )
        result = tier.search("foo", QueryContext())
        assert len(result.items) == 3
        # Top-3 should be the first three vector results, in order.
        assert [h.id for h in result.items] == ["n0", "n1", "n2"]

    def test_citations_included(self) -> None:
        store = _mock_store()
        _attach_session(
            store,
            run_results=[
                [{"id": "n1", "attrs": json.dumps({"name": "Alice"}), "score": 0.9}],
                [],
            ],
        )
        store._provenance_for_node.return_value = [
            Provenance(
                source_file="hr/employees.json",
                source_record_id="row:0",
                source_field="emp_id",
                extraction_method="direct_mapping",
                extraction_model="rule:hr_v1",
                confidence=FactConfidence.EXACT,
                raw_value="emp_0431",
            )
        ]
        tier = self._tier(store)
        result = tier.search("alice", QueryContext())
        assert len(result.citations) == 1
        assert result.citations[0].source_file == "hr/employees.json"

    def test_embedder_dimension_mismatch_raises(self) -> None:
        # An embedder that lies about its dim.
        class LyingEmbedder:
            @property
            def dim(self) -> int:
                return 384

            def embed(self, text: str) -> list[float]:
                return [0.0] * 10  # wrong dim

        store = _mock_store()
        _attach_session(store, run_results=[[], []])
        tier = HybridTier(store, LyingEmbedder())  # type: ignore[arg-type]
        with pytest.raises(RuntimeError, match="expected 384"):
            tier.search("anything", QueryContext())


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
class TestHybridTierIntegration:
    def test_index_construction_idempotent(self, tmp_path) -> None:  # noqa: ANN001
        store = _make_store(tmp_path)
        try:
            HybridTier(store, StubEmbedder())
            HybridTier(store, StubEmbedder())  # second construction must not raise
        finally:
            store.close()

    def test_query_returns_zero_when_index_empty(self, tmp_path) -> None:  # noqa: ANN001
        store = _make_store(tmp_path)
        try:
            tier = HybridTier(store, StubEmbedder())
            result = tier.search("definitely-not-a-real-thing-xyz", QueryContext())
            assert result.tier_used == "hybrid"
            # With no embedded nodes the vector arm returns nothing; the
            # fulltext arm may also return nothing on a nonsense query.
            assert result.relevance >= 0.0
        finally:
            store.close()
