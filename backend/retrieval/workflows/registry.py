"""Workflow registry — name -> Workflow class.

Process-global mapping from workflow `name` to `type[Workflow]`. Two
operations:

* `register_workflow(cls)` — store `cls.name -> cls`. Raises
  `ValueError` on duplicate name (fail fast — silently overwriting a
  registration would hide a real bug, e.g. two modules claiming the
  same workflow id).
* `get_workflow(name)` — return the registered class. Raises
  `KeyError` when unknown — callers (the FastAPI route) translate
  this to HTTP 404.

Registration is by class, not instance, because a workflow needs a
`TierRegistry` (which depends on the live `ContextEngine`'s tier set)
at instantiation time. The factory pattern means registry users (e.g.
the API endpoint) instantiate the workflow on demand against the
current engine.
"""
from __future__ import annotations

from typing import Any, TypeVar

from ..tiers import Tier
from .base import TierRegistry, Workflow

_REGISTRY: dict[str, type[Workflow]] = {}

_W = TypeVar("_W", bound=Workflow)


def register_workflow(cls: type[_W]) -> type[_W]:
    """Register a `Workflow` subclass by its `name` ClassVar.

    Returns the class so it can be used as a decorator:

        @register_workflow
        class MyWorkflow(Workflow):
            name = "my-workflow"
            allowed_tiers = frozenset({"exact"})
            def run(self, input): ...

    Raises `ValueError` if the name is already taken (no silent
    overwrites). Raises `TypeError` if `cls` is not a `Workflow`
    subclass or is missing the required ClassVars.
    """
    if not isinstance(cls, type) or not issubclass(cls, Workflow):
        raise TypeError(f"register_workflow expects a Workflow subclass, got {cls!r}")
    name = getattr(cls, "name", None)
    if not isinstance(name, str) or not name:
        raise TypeError(
            f"{cls.__name__} must define a non-empty ClassVar `name: str`"
        )
    allowed = getattr(cls, "allowed_tiers", None)
    if not isinstance(allowed, frozenset):
        raise TypeError(
            f"{cls.__name__} must define ClassVar `allowed_tiers: frozenset[str]`"
        )
    if name in _REGISTRY:
        existing = _REGISTRY[name]
        raise ValueError(
            f"workflow {name!r} already registered as {existing.__qualname__}; "
            f"refusing to overwrite with {cls.__qualname__}"
        )
    _REGISTRY[name] = cls
    return cls


def get_workflow(name: str) -> type[Workflow]:
    """Return the workflow class registered under `name`.

    Raises `KeyError` for unknown names — the API layer translates to
    HTTP 404. Never returns `None`.
    """
    if not isinstance(name, str):
        raise TypeError(f"workflow name must be str, got {type(name).__name__}")
    if name not in _REGISTRY:
        raise KeyError(
            f"no workflow registered under {name!r}; "
            f"known: {sorted(_REGISTRY)!r}"
        )
    return _REGISTRY[name]


def list_workflows() -> list[str]:
    """All registered workflow names, sorted. Useful for `/api/workflow`
    discovery + tests."""
    return sorted(_REGISTRY)


def clear_registry() -> None:
    """Drop all registrations. Test-only — production code never calls
    this. Provided so unit tests can isolate registration state without
    poking at `_REGISTRY` directly.
    """
    _REGISTRY.clear()


def register_builtin_workflows() -> None:
    """Register every workflow that ships with the package.

    Idempotent: skips a workflow whose name is already registered (so a
    second call after `clear_registry()` from a test fixture is safe).
    Asserts loudly if either built-in failed to register — a missing
    workflow at FastAPI startup is a deployment bug, not a runtime
    condition.
    """
    from .customer_email import CustomerEmailWorkflow
    from .thread_summary import ThreadSummaryWorkflow

    for cls in (CustomerEmailWorkflow, ThreadSummaryWorkflow):
        if cls.name not in _REGISTRY:
            register_workflow(cls)
    if "answer-customer-email" not in _REGISTRY:
        raise RuntimeError(
            "register_builtin_workflows: answer-customer-email failed to register"
        )
    if "thread-summary" not in _REGISTRY:
        raise RuntimeError(
            "register_builtin_workflows: thread-summary failed to register"
        )


def build_workflow(
    name: str,
    tiers_by_name: dict[str, Tier],
    **extras: Any,
) -> Workflow:
    """Instantiate the registered workflow, restricting its tier view to
    its declared `allowed_tiers`.

    `tiers_by_name` is the full live tier set (typically pulled from
    the orchestrator). The `TierRegistry` constructor enforces that
    every name in `cls.allowed_tiers` is present in `tiers_by_name`;
    a missing tier is a deployment bug and raises immediately.

    `extras` are forwarded as keyword arguments to the workflow
    subclass's constructor — non-trivial workflows (e.g. compose-with-
    LLM ones) need additional dependencies that the framework knows
    nothing about (an `LLMClient`, a `GraphStore`). The framework filters
    extras to those declared by the subclass's `__init__` so deployments
    can hand the framework "everything available" without forcing every
    workflow to accept every dependency. Missing required kwargs still
    raise `TypeError` at construction — fail-fast, no silent drop.
    """
    import inspect

    cls = get_workflow(name)
    registry = TierRegistry(tiers_by_name, cls.allowed_tiers)
    accepted = set(inspect.signature(cls.__init__).parameters)
    filtered = {k: v for k, v in extras.items() if k in accepted}
    return cls(registry, **filtered)
