"""Workflow framework — frozen orchestration policies over the tier set.

Public surface:

* `Workflow` — ABC. Subclass, set `name` + `allowed_tiers`, implement
  `run()`.
* `WorkflowInput` / `WorkflowResult` — Pydantic v2 IO models.
* `TierRegistry` — locked-subset view over the engine's tiers.
* `register_workflow` / `get_workflow` / `list_workflows` /
  `build_workflow` — registry operations.

Concrete workflows:

* `CustomerEmailWorkflow` (issue #8) — `answer-customer-email`,
  T1 sender lookup → T1 neighbors → T3 product search → LLM compose.
* `thread-summary` (issue #9) — pending.

Importing this package registers every shipped workflow as a
side effect (the `register_workflow` decorator runs at import time).
"""
from __future__ import annotations

from .base import TierRegistry, Workflow, WorkflowInput, WorkflowResult
from .customer_email import CustomerEmailInput, CustomerEmailWorkflow
from .registry import (
    build_workflow,
    clear_registry,
    get_workflow,
    list_workflows,
    register_workflow,
)

__all__ = [
    "CustomerEmailInput",
    "CustomerEmailWorkflow",
    "TierRegistry",
    "Workflow",
    "WorkflowInput",
    "WorkflowResult",
    "build_workflow",
    "clear_registry",
    "get_workflow",
    "list_workflows",
    "register_workflow",
]
