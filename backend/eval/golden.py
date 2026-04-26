"""Golden-set extraction from EnterpriseBench tasks.

Each line in `dataset/EnterpriseBench/tasks.jsonl` is a multi-turn
conversation. We treat:

* the first ``role == "user"`` message's `content` as the **query**, and
* every entity id appearing in subsequent assistant tool-call arguments
  as **expected node ids** the retrieval cascade should surface.

Recognized id keys (matching what the dataset's tools accept):
    emp_id, sender_emp_id, recipient_emp_id, product_id,
    conversation_id, customer_id, client_id, repo_name, vendor_id

Tasks with no extractable id are skipped (acceptance criteria of issue #2).
The format `(query, expected_node_ids)` is intentionally simple: a tier
is "correct" on a task if any expected id appears in its `Hit.id` list.
This is recall-only, not precision — appropriate for R0 where precision
metrics depend on real ranking.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator

# Keys whose string values name an entity in the EnterpriseBench dataset.
# Frozen at the boundary; if a tool adds a new id key, add it here.
ID_KEYS: frozenset[str] = frozenset(
    {
        "emp_id",
        "sender_emp_id",
        "recipient_emp_id",
        "product_id",
        "conversation_id",
        "customer_id",
        "client_id",
        "repo_name",
        "vendor_id",
    }
)


@dataclass(frozen=True)
class GoldenItem:
    """One eval row.

    `expected_node_ids` is an unordered set of entity identifiers (e.g.
    ``"emp_0431"``, ``"B0BQ3K23Y1"``). A tier is considered to have hit
    the item if any of its `Hit.id` values match any expected id.
    """

    task_index: int
    query: str
    expected_node_ids: frozenset[str]


def extract_golden_item(task_index: int, raw: dict[str, Any]) -> GoldenItem | None:
    """Extract one `GoldenItem` from a raw `tasks.jsonl` entry.

    Returns `None` (skip) iff there is no first user message OR no id can
    be harvested from any tool-call argument. Raises on structurally
    malformed entries (missing ``messages`` key, non-list ``messages``,
    non-dict message) — fail fast.
    """
    if "messages" not in raw:
        raise ValueError(f"task {task_index}: missing 'messages' key")
    msgs = raw["messages"]
    if not isinstance(msgs, list):
        raise TypeError(f"task {task_index}: 'messages' must be list, got {type(msgs).__name__}")

    query: str | None = None
    for m in msgs:
        if not isinstance(m, dict):
            raise TypeError(f"task {task_index}: message entry not a dict")
        if m.get("role") == "user":
            content = m.get("content")
            if not isinstance(content, str) or not content.strip():
                continue
            query = content.strip()
            break
    if query is None:
        return None

    ids: set[str] = set()
    for m in msgs:
        tool_calls = m.get("tool_calls")
        if not tool_calls:
            continue
        if not isinstance(tool_calls, list):
            raise TypeError(f"task {task_index}: tool_calls not list")
        for tc in tool_calls:
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function")
            if not isinstance(fn, dict):
                continue
            args = fn.get("arguments")
            if not isinstance(args, dict):
                continue
            for k, v in args.items():
                if k in ID_KEYS and isinstance(v, str) and v:
                    ids.add(v)

    if not ids:
        return None
    return GoldenItem(task_index=task_index, query=query, expected_node_ids=frozenset(ids))


def load_golden_set(
    tasks_path: Path | str,
    *,
    limit: int | None = None,
) -> list[GoldenItem]:
    """Read every line of `tasks_path` and yield `GoldenItem`s.

    `limit` caps the number of *yielded* (post-skip) items, useful for
    smoke tests. Raises `FileNotFoundError` if the path does not exist.
    """
    path = Path(tasks_path)
    if not path.is_file():
        raise FileNotFoundError(f"tasks file not found: {path}")
    items: list[GoldenItem] = []
    for item in _iter_items(path):
        items.append(item)
        if limit is not None and len(items) >= limit:
            break
    return items


def _iter_items(path: Path) -> Iterator[GoldenItem]:
    with path.open(encoding="utf-8") as f:
        for i, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            raw = json.loads(line)
            item = extract_golden_item(i, raw)
            if item is not None:
                yield item


def coverage_stats(items: Iterable[GoldenItem]) -> dict[str, int]:
    """Quick descriptive stats over a golden set."""
    items_list = list(items)
    total_ids = sum(len(g.expected_node_ids) for g in items_list)
    return {
        "items": len(items_list),
        "total_expected_ids": total_ids,
        "max_ids_per_item": max((len(g.expected_node_ids) for g in items_list), default=0),
    }
