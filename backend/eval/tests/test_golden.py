"""Tests for the golden-set extractor and the eval harness."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.eval.golden import (
    GoldenItem,
    coverage_stats,
    extract_golden_item,
    load_golden_set,
)
from backend.eval.harness import (
    EvalReport,
    format_report_markdown,
    run_eval,
    write_report,
)
from backend.retrieval import (
    CascadeOrchestrator,
    ContextEngine,
    Hit,
    QueryContext,
    QueryResult,
    StubTier,
    Tier,
    TierConfig,
)


# ---------------------------------------------------------------------------
# golden.extract_golden_item
# ---------------------------------------------------------------------------


def _task(messages: list[dict]) -> dict:
    return {"messages": messages}


class TestExtractGoldenItem:
    def test_basic_extraction(self) -> None:
        raw = _task(
            [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "Fix product B0BQ3K23Y1"},
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "type": "function",
                            "function": {
                                "name": "get_product",
                                "arguments": {"product_id": "B0BQ3K23Y1"},
                            },
                        }
                    ],
                },
            ]
        )
        item = extract_golden_item(0, raw)
        assert item is not None
        assert item.task_index == 0
        assert item.query == "Fix product B0BQ3K23Y1"
        assert item.expected_node_ids == frozenset({"B0BQ3K23Y1"})

    def test_multiple_id_keys(self) -> None:
        raw = _task(
            [
                {"role": "user", "content": "send a message"},
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "type": "function",
                            "function": {
                                "name": "x",
                                "arguments": {
                                    "sender_emp_id": "emp_001",
                                    "recipient_emp_id": "emp_002",
                                    "conversation_id": "conv_abc",
                                    "ignored": "value",
                                },
                            },
                        }
                    ],
                },
            ]
        )
        item = extract_golden_item(0, raw)
        assert item is not None
        assert item.expected_node_ids == frozenset({"emp_001", "emp_002", "conv_abc"})

    def test_no_user_message_returns_none(self) -> None:
        raw = _task([{"role": "system", "content": "sys"}])
        assert extract_golden_item(0, raw) is None

    def test_no_ids_returns_none(self) -> None:
        raw = _task([{"role": "user", "content": "what time is it?"}])
        assert extract_golden_item(0, raw) is None

    def test_empty_user_content_skipped(self) -> None:
        raw = _task(
            [
                {"role": "user", "content": "   "},
                {"role": "user", "content": "real query"},
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "type": "function",
                            "function": {"name": "x", "arguments": {"emp_id": "emp_999"}},
                        }
                    ],
                },
            ]
        )
        item = extract_golden_item(0, raw)
        assert item is not None
        assert item.query == "real query"

    def test_missing_messages_raises(self) -> None:
        with pytest.raises(ValueError, match="missing 'messages'"):
            extract_golden_item(0, {})

    def test_messages_not_list_raises(self) -> None:
        with pytest.raises(TypeError, match="must be list"):
            extract_golden_item(0, {"messages": "nope"})

    def test_message_not_dict_raises(self) -> None:
        with pytest.raises(TypeError, match="not a dict"):
            extract_golden_item(0, {"messages": ["nope"]})


class TestLoadGoldenSet:
    def test_loads_jsonl(self, tmp_path: Path) -> None:
        path = tmp_path / "tasks.jsonl"
        rows = [
            _task(
                [
                    {"role": "user", "content": "q1"},
                    {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "type": "function",
                                "function": {"name": "x", "arguments": {"emp_id": "emp_1"}},
                            }
                        ],
                    },
                ]
            ),
            _task([{"role": "user", "content": "no-id task"}]),
            _task(
                [
                    {"role": "user", "content": "q2"},
                    {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "type": "function",
                                "function": {"name": "x", "arguments": {"product_id": "P_42"}},
                            }
                        ],
                    },
                ]
            ),
        ]
        path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
        items = load_golden_set(path)
        assert len(items) == 2
        assert items[0].query == "q1"
        assert items[1].query == "q2"

    def test_limit(self, tmp_path: Path) -> None:
        path = tmp_path / "tasks.jsonl"
        rows = []
        for i in range(5):
            rows.append(
                _task(
                    [
                        {"role": "user", "content": f"q{i}"},
                        {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "type": "function",
                                    "function": {
                                        "name": "x",
                                        "arguments": {"emp_id": f"emp_{i}"},
                                    },
                                }
                            ],
                        },
                    ]
                )
            )
        path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
        items = load_golden_set(path, limit=2)
        assert len(items) == 2

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_golden_set(tmp_path / "nope.jsonl")

    def test_real_dataset_extraction_rate(self) -> None:
        """Sanity check against the real EnterpriseBench dataset.

        Skipped if the dataset is not available.
        """
        path = Path("dataset/EnterpriseBench/tasks.jsonl")
        if not path.is_file():
            pytest.skip(f"dataset not present at {path}")
        items = load_golden_set(path, limit=50)
        # Out of 50 the first batch should be substantially populated;
        # we observed ~97% coverage in spot-check, allow plenty of slack.
        assert len(items) >= 30
        stats = coverage_stats(items)
        assert stats["items"] == len(items)
        assert stats["total_expected_ids"] >= len(items)


# ---------------------------------------------------------------------------
# harness.run_eval
# ---------------------------------------------------------------------------


class _CannedTier(Tier):
    """Returns a fixed list of hit ids for every query."""

    def __init__(self, name: str, hit_ids: list[str], relevance: float) -> None:
        self._name = name
        self._hit_ids = hit_ids
        self._relevance = relevance

    @property
    def name(self) -> str:
        return self._name

    def search(self, query: str, ctx: QueryContext) -> QueryResult:
        return QueryResult(
            answer=None,
            items=[
                Hit(kind="node", id=hid, score=self._relevance, preview="")
                for hid in self._hit_ids
            ],
            citations=[],
            tier_used=self._name,
            relevance=self._relevance,
            latency_ms=0,
        )


def _engine(tier: Tier, escalate_below: float = 0.0) -> ContextEngine:
    orch = CascadeOrchestrator(
        tiers=[tier],
        configs=[TierConfig(name=tier.name, escalate_below=escalate_below)],
    )
    return ContextEngine(orch)


class TestRunEval:
    def test_perfect_recall(self) -> None:
        golden = [
            GoldenItem(task_index=0, query="q1", expected_node_ids=frozenset({"a"})),
            GoldenItem(task_index=1, query="q2", expected_node_ids=frozenset({"a", "b"})),
        ]
        engine = _engine(_CannedTier("perfect", ["a", "b"], relevance=1.0))
        report = run_eval(engine, golden, ks=(1, 5))
        assert report.total_queries == 2
        assert report.recall_at[1] == 1.0
        assert report.recall_at[5] == 1.0
        assert report.escalation_rate == 0.0
        assert report.per_tier_counts == {"perfect": 2}

    def test_zero_recall_with_stub(self) -> None:
        golden = [GoldenItem(task_index=0, query="q1", expected_node_ids=frozenset({"a"}))]
        engine = _engine(StubTier(name="stub"))
        report = run_eval(engine, golden)
        assert report.recall_at[5] == 0.0
        assert report.recall_at[10] == 0.0
        assert report.per_tier_counts == {"stub": 1}

    def test_recall_at_k_truncates(self) -> None:
        # expected only at position 6 — recall@5 misses, recall@10 hits.
        golden = [GoldenItem(task_index=0, query="q", expected_node_ids=frozenset({"target"}))]
        ids = ["x", "x", "x", "x", "x", "target", "y", "y", "y", "y"]
        engine = _engine(_CannedTier("t", ids, relevance=1.0))
        report = run_eval(engine, golden, ks=(5, 10))
        assert report.recall_at[5] == 0.0
        assert report.recall_at[10] == 1.0

    def test_empty_golden_raises(self) -> None:
        engine = _engine(StubTier(name="stub"))
        with pytest.raises(ValueError, match="golden set is empty"):
            run_eval(engine, [])

    def test_invalid_ks_raises(self) -> None:
        engine = _engine(StubTier(name="stub"))
        golden = [GoldenItem(task_index=0, query="q", expected_node_ids=frozenset({"a"}))]
        with pytest.raises(ValueError, match="ks must contain"):
            run_eval(engine, golden, ks=())
        with pytest.raises(ValueError, match=">= 1"):
            run_eval(engine, golden, ks=(0,))


class TestFormatAndWrite:
    def test_format_contains_metrics(self) -> None:
        report = EvalReport(
            total_queries=2,
            first_tier="stub",
            recall_at={5: 0.5, 10: 0.5},
            latency_p50_ms=1.0,
            latency_p95_ms=2.0,
            escalation_rate=0.0,
            per_tier_counts={"stub": 2},
        )
        md = format_report_markdown(report)
        assert "recall@5" in md
        assert "recall@10" in md
        assert "latency p50" in md
        assert "latency p95" in md
        assert "escalation rate" in md
        assert "stub" in md

    def test_write_creates_timestamped_file(self, tmp_path: Path) -> None:
        report = EvalReport(
            total_queries=1,
            first_tier="stub",
            recall_at={5: 0.0},
            latency_p50_ms=0.0,
            latency_p95_ms=0.0,
            escalation_rate=0.0,
            per_tier_counts={"stub": 1},
        )
        path = write_report(report, report_dir=tmp_path)
        assert path.is_file()
        assert path.suffix == ".md"
        assert "recall@5" in path.read_text(encoding="utf-8")
