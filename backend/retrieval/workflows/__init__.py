"""Workflow framework — frozen orchestration policies over the tier set.

Public surface:

* `Workflow` — ABC. Subclass, set `name` + `allowed_tiers`, implement
  `run()`.
* `WorkflowInput` / `WorkflowResult` — Pydantic v2 IO models.
* `TierRegistry` — locked-subset view over the engine's tiers.
* `register_workflow` / `get_workflow` / `list_workflows` /
  `build_workflow` — registry operations.

Specific workflow implementations land in #8 (answer-customer-email)
and #9 (thread-summary). This package ships only the framework.
"""
from __future__ import annotations

from .base import TierRegistry, Workflow, WorkflowInput, WorkflowResult
from .registry import (
    build_workflow,
    clear_registry,
    get_workflow,
    list_workflows,
    register_workflow,
)

__all__ = [
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
