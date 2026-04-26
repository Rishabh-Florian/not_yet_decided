"""Eval harness for the retrieval cascade.

Public surface:

* `golden.load_golden_set` — extract `(query, expected_node_ids)` pairs
  from `dataset/EnterpriseBench/tasks.jsonl`.
* `harness.run_eval` — run a `ContextEngine` over the golden set and
  produce a `EvalReport` (recall@k, latency p50/p95, escalation rate).
"""
from __future__ import annotations

from .golden import GoldenItem, extract_golden_item, load_golden_set
from .harness import EvalReport, format_report_markdown, run_eval

__all__ = [
    "EvalReport",
    "GoldenItem",
    "extract_golden_item",
    "format_report_markdown",
    "load_golden_set",
    "run_eval",
]
