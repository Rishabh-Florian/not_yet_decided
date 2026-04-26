"""Workflow ABC + frozen tier policy.

A `Workflow` is a hard-coded orchestration policy that bypasses the
cascade. It declares — at class definition time — which tiers it is
allowed to call (`allowed_tiers`), composes their results in a fixed
sequence, and returns a `WorkflowResult`.

Why bypass cascade? Cascade is the right default for ad-hoc queries.
But some flows have a known shape: "answer a customer email" always
wants customer + order + product context, never agentic reasoning.
Hard-coding the recipe per workflow cuts latency and cost predictably.

The `TierRegistry` passed into `run()` enforces the frozen-policy
contract at call time: `registry.get("agentic")` raises if `"agentic"`
is not in the workflow's `allowed_tiers`. A workflow author cannot
quietly reach for an arbitrary tier — the locked subset is checked on
every access. Fail-fast at the boundary.

`WorkflowResult` extends `QueryResult` so workflow output is structurally
indistinguishable from cascade output: same `items` / `citations` /
`tier_used` / `relevance` shape. The orchestration framework wraps tiers,
it does not invent a new relevance shape (per harness/PRINCIPLES.md: algorithmic
relevance only). The `tier_used` field on a workflow result is set to
the producing tier's name — when a workflow fuses multiple tiers, it
picks the dominant one (workflow-defined) and surfaces that.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from .. import models as retrieval_models
from ..tiers import Tier


class WorkflowInput(BaseModel):
    """Input to a workflow. `payload` is workflow-specific (validated
    inside `Workflow.run`); `ctx` is the same `QueryContext` cascade
    callers use, passed through to underlying tiers verbatim.
    """

    payload: dict[str, Any]
    ctx: retrieval_models.QueryContext | None = None


class WorkflowResult(retrieval_models.QueryResult):
    """Output of a workflow.

    Same shape as `QueryResult` (so a UI can render workflow output
    and cascade output the same way) plus:

    * `workflow` — the workflow's `name`. Distinct from `tier_used`
      because a workflow may compose results from several tiers.
    * `extras` — workflow-specific structured data that does not fit
      into `Hit` / `Citation` (e.g. a rendered email-reply template,
      a thread-summary headline). Schema is per-workflow; the field
      is intentionally open.
    """

    workflow: str
    extras: dict[str, Any] = Field(default_factory=dict)


class TierRegistry:
    """Locked view onto a subset of tiers.

    Constructed by the workflow framework from the full tier set of a
    `CascadeOrchestrator`, restricted to the workflow's `allowed_tiers`.
    `get(name)` raises `KeyError` for any tier outside the allowed set
    OR not registered at all — the workflow cannot tell the difference,
    and that is intentional. The frozen policy is enforced here, not in
    the workflow body.
    """

    def __init__(self, tiers: dict[str, Tier], allowed: frozenset[str]) -> None:
        if not isinstance(allowed, frozenset):
            raise TypeError(
                f"allowed must be frozenset[str], got {type(allowed).__name__}"
            )
        if not allowed:
            raise ValueError("a workflow must declare at least one allowed tier")
        missing = allowed - tiers.keys()
        if missing:
            raise ValueError(
                f"workflow declares tiers not registered in the engine: {sorted(missing)!r}"
            )
        # Snapshot so subsequent mutations to the source dict cannot
        # widen the policy.
        self._tiers: dict[str, Tier] = {n: tiers[n] for n in allowed}
        self._allowed: frozenset[str] = allowed

    @property
    def allowed(self) -> frozenset[str]:
        return self._allowed

    def get(self, name: str) -> Tier:
        if name not in self._tiers:
            raise KeyError(
                f"tier {name!r} not in workflow allowed set {sorted(self._allowed)!r}"
            )
        return self._tiers[name]

    def __contains__(self, name: object) -> bool:
        return name in self._tiers


class Workflow(ABC):
    """Frozen-policy orchestration over a locked subset of tiers.

    Subclasses MUST set:

    * `name` — stable, unique workflow identifier (lowercase,
      hyphen/underscore, no whitespace). Used for the registry key
      and the `/api/workflow/{name}` URL.
    * `allowed_tiers` — frozenset of tier names this workflow may
      call. Anything outside this set is unreachable at runtime.

    Subclasses implement `run(input)` and call `self.tiers.get(...)`
    to reach the underlying tiers. The framework injects `tiers` at
    construction time; subclasses do not own tier wiring.
    """

    name: ClassVar[str]
    allowed_tiers: ClassVar[frozenset[str]]

    def __init__(self, tiers: TierRegistry) -> None:
        # ClassVar presence checks — fail fast when a subclass forgets
        # the contract (a typo in `name` or missing `allowed_tiers` is
        # a bug, not a runtime condition).
        if not isinstance(getattr(type(self), "name", None), str):
            raise TypeError(
                f"{type(self).__name__} must set ClassVar `name: str`"
            )
        cls_name = type(self).name
        if not cls_name or cls_name != cls_name.strip() or " " in cls_name:
            raise ValueError(
                f"workflow `name` must be a non-empty whitespace-free identifier, "
                f"got {cls_name!r}"
            )
        cls_allowed = getattr(type(self), "allowed_tiers", None)
        if not isinstance(cls_allowed, frozenset):
            raise TypeError(
                f"{type(self).__name__} must set ClassVar "
                f"`allowed_tiers: frozenset[str]`"
            )
        # The `tiers.allowed == cls.allowed_tiers` invariant is owned by
        # `TierRegistry.__init__` (which is the only constructor for the
        # registry passed in here); re-asserting it here would duplicate
        # framework guarantees.
        self._tiers = tiers

    @property
    def tiers(self) -> TierRegistry:
        return self._tiers

    @abstractmethod
    def run(self, input: WorkflowInput) -> WorkflowResult:  # noqa: A002
        """Execute the workflow against `input` and return a result.

        Implementations:

        * Validate `input.payload` upfront and raise `ValueError` /
          `TypeError` on malformed input (fail fast).
        * Reach tiers exclusively via `self.tiers.get(name)`.
        * Set `WorkflowResult.workflow = type(self).name`.
        * Set `WorkflowResult.tier_used` to the dominant tier whose
          relevance the workflow surfaces (or the workflow's name if
          the result is a fusion that does not map to a single tier —
          in that case set `tier_used = type(self).name` AND ensure
          the workflow is also registered as a synthetic tier name in
          docs; the standard convention is "use the dominant tier").
        """
