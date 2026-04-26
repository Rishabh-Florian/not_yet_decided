"""Eval harness — run a `ContextEngine` over a golden set, score it.

Metrics produced:

* `recall_at_5`, `recall_at_10` — fraction of golden items where the
  top-k `Hit.id`s overlap with `expected_node_ids`. This is recall, not
  precision: we are measuring whether the cascade *surfaces* the right
  entities at all. Precision metrics depend on tier ranking, which is a
  per-tier concern.
* `latency_p50_ms`, `latency_p95_ms` — wall-clock per-query latency the
  orchestrator measured. Median + tail.
* `escalation_rate` — fraction of queries where the final `tier_used`
  was *not* the first tier in the cascade (i.e. cascade had to escalate).

Output: a Markdown table written to
``backend/eval/reports/<UTC-timestamp>.md`` so successive runs can be
diffed. Stdout receives the same table.

Run end-to-end with the bundled stub tier:

    uv run python -m backend.eval.harness
"""
from __future__ import annotations

import argparse
import statistics
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from backend.retrieval import (
    CascadeOrchestrator,
    ContextEngine,
    QueryContext,
    StubTier,
    TierConfig,
)

from .golden import GoldenItem, load_golden_set

DEFAULT_TASKS_PATH = Path("dataset/EnterpriseBench/tasks.jsonl")
DEFAULT_REPORT_DIR = Path("backend/eval/reports")
RECALL_KS = (5, 10)


@dataclass
class _PerQueryRow:
    task_index: int
    tier_used: str
    latency_ms: int
    hit_ids: list[str]
    expected_ids: frozenset[str]


@dataclass
class EvalReport:
    """Aggregated eval result.

    `per_tier_counts` maps tier name -> number of queries whose cascade
    terminated on that tier (so the sum equals `total_queries`).
    """

    total_queries: int
    first_tier: str
    recall_at: dict[int, float]
    latency_p50_ms: float
    latency_p95_ms: float
    escalation_rate: float
    per_tier_counts: dict[str, int] = field(default_factory=dict)


def run_eval(
    engine: ContextEngine,
    golden: list[GoldenItem],
    *,
    ks: tuple[int, ...] = RECALL_KS,
) -> EvalReport:
    if not golden:
        raise ValueError("golden set is empty; nothing to evaluate")
    if not ks:
        raise ValueError("ks must contain at least one cutoff")
    if any(k < 1 for k in ks):
        raise ValueError(f"every k in ks must be >= 1, got {ks}")

    rows: list[_PerQueryRow] = []
    for item in golden:
        result = engine.query(item.query, QueryContext())
        rows.append(
            _PerQueryRow(
                task_index=item.task_index,
                tier_used=result.tier_used,
                latency_ms=result.latency_ms,
                hit_ids=[h.id for h in result.items],
                expected_ids=item.expected_node_ids,
            )
        )

    recall_at: dict[int, float] = {}
    for k in ks:
        hit_count = sum(
            1 for r in rows if any(hid in r.expected_ids for hid in r.hit_ids[:k])
        )
        recall_at[k] = hit_count / len(rows)

    latencies = [r.latency_ms for r in rows]
    p50 = statistics.median(latencies)
    p95 = _percentile(latencies, 95)

    first_tier = engine.tier_names[0]
    escalations = sum(1 for r in rows if r.tier_used != first_tier)
    per_tier_counts: dict[str, int] = {name: 0 for name in engine.tier_names}
    for r in rows:
        if r.tier_used not in per_tier_counts:
            raise RuntimeError(
                f"engine emitted unknown tier_used={r.tier_used!r}; "
                f"registered tiers are {list(per_tier_counts)}"
            )
        per_tier_counts[r.tier_used] += 1

    return EvalReport(
        total_queries=len(rows),
        first_tier=first_tier,
        recall_at=recall_at,
        latency_p50_ms=p50,
        latency_p95_ms=p95,
        escalation_rate=escalations / len(rows),
        per_tier_counts=per_tier_counts,
    )


def _percentile(values: list[int], pct: int) -> float:
    if not values:
        raise ValueError("cannot compute percentile of empty list")
    if not 0 <= pct <= 100:
        raise ValueError(f"pct must be in [0, 100], got {pct}")
    sorted_v = sorted(values)
    if len(sorted_v) == 1:
        return float(sorted_v[0])
    rank = (pct / 100.0) * (len(sorted_v) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(sorted_v) - 1)
    frac = rank - lo
    return sorted_v[lo] * (1 - frac) + sorted_v[hi] * frac


def format_report_markdown(report: EvalReport) -> str:
    lines: list[str] = []
    lines.append("# Retrieval eval report")
    lines.append("")
    lines.append(f"- total queries: {report.total_queries}")
    lines.append(f"- first tier: `{report.first_tier}`")
    lines.append("")
    lines.append("## Metrics")
    lines.append("")
    lines.append("| metric | value |")
    lines.append("|---|---|")
    for k in sorted(report.recall_at):
        lines.append(f"| recall@{k} | {report.recall_at[k]:.4f} |")
    lines.append(f"| latency p50 (ms) | {report.latency_p50_ms:.2f} |")
    lines.append(f"| latency p95 (ms) | {report.latency_p95_ms:.2f} |")
    lines.append(f"| escalation rate | {report.escalation_rate:.4f} |")
    lines.append("")
    lines.append("## Per-tier termination counts")
    lines.append("")
    lines.append("| tier | count |")
    lines.append("|---|---|")
    for name, count in report.per_tier_counts.items():
        lines.append(f"| `{name}` | {count} |")
    lines.append("")
    return "\n".join(lines)


def write_report(report: EvalReport, *, report_dir: Path = DEFAULT_REPORT_DIR) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = report_dir / f"{ts}.md"
    path.write_text(format_report_markdown(report), encoding="utf-8")
    return path


def _build_stub_engine() -> ContextEngine:
    """Default engine for `python -m backend.eval.harness`: one stub tier.

    Lets the full pipeline run end-to-end before any real tier lands.
    """
    tier = StubTier(name="stub")
    orch = CascadeOrchestrator(
        tiers=[tier],
        configs=[TierConfig(name="stub", escalate_below=0.0)],
    )
    return ContextEngine(orch)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tasks",
        type=Path,
        default=DEFAULT_TASKS_PATH,
        help="path to tasks.jsonl",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="cap number of golden items (smoke testing)",
    )
    parser.add_argument(
        "--report-dir",
        type=Path,
        default=DEFAULT_REPORT_DIR,
        help="directory to write the markdown report",
    )
    args = parser.parse_args(argv)

    golden = load_golden_set(args.tasks, limit=args.limit)
    if not golden:
        print(f"no golden items extracted from {args.tasks}", file=sys.stderr)
        return 1
    engine = _build_stub_engine()
    report = run_eval(engine, golden)
    md = format_report_markdown(report)
    sys.stdout.write(md)
    out_path = write_report(report, report_dir=args.report_dir)
    print(f"\nwrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
