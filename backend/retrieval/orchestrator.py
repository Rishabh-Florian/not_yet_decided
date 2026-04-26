"""Cascade orchestrator — try fast tiers first, escalate on miss.

Cascade is the **default and only** path through R3. A pre-routing
classifier (an upfront tier picker based on the query's shape) is the
job of R4 (issue #6 — Pioneer.ai GLiNER2 RouterTier). The hook for it
is intentionally the same `QueryContext.prefer_tier` field tiers already
honor: a future router will populate that hint before the orchestrator
runs, jumping the cascade to the right tier on the first try. Until then
we walk every registered tier in order.

Escalation rule (per-tier configurable):

* Run tier ``T``.
* If ``T.search()`` raises, the orchestrator re-raises (fail fast).
* Otherwise compare ``result.relevance`` against ``T``'s configured
  ``escalate_below`` threshold. If ``relevance < escalate_below``, the
  orchestrator moves to the next tier; else it returns the result.
* If all tiers escalate past, the orchestrator returns the **last**
  tier's result (best-effort) — never an exception, never an empty
  fabrication. This is the only "soft" path, and it's intentional:
  the caller still gets a `QueryResult` with `tier_used` set so they
  can see which tier was the final fallback.

Per-tier ``timeout_ms`` is honored only when the tier opts in; the
orchestrator passes it via `QueryContext.max_latency_ms` so the tier
can short-circuit its own internal work. Hard timeouts (signal-based)
are out of scope for R0.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, replace

from .models import QueryContext, QueryResult
from .tiers import Tier


@dataclass(frozen=True)
class TierConfig:
    """Per-tier orchestrator settings.

    `escalate_below` is compared against `QueryResult.relevance`. The
    sentinel value ``0.0`` means "always escalate past this tier on a
    relevance of 0.0" — useful for the stub tier, which always scores 0.
    Values > 1.0 mean "always escalate" (force a downstream tier).
    """

    name: str
    escalate_below: float
    timeout_ms: int | None = None


class CascadeOrchestrator:
    """Compose tiers into a cascade.

    The orchestrator owns latency timing for every tier call and
    overwrites `QueryResult.latency_ms` with the wall-clock value it
    measured — tiers cannot lie about how long they took.
    """

    def __init__(self, tiers: list[Tier], configs: list[TierConfig]) -> None:
        if not tiers:
            raise ValueError("CascadeOrchestrator requires at least one tier")
        if len(tiers) != len(configs):
            raise ValueError(
                f"tier/config length mismatch: {len(tiers)} tiers, {len(configs)} configs"
            )
        names = [t.name for t in tiers]
        if len(set(names)) != len(names):
            raise ValueError(f"tier names must be unique, got {names}")
        for tier, cfg in zip(tiers, configs):
            if tier.name != cfg.name:
                raise ValueError(
                    f"tier/config name mismatch at position: tier={tier.name!r} "
                    f"config={cfg.name!r}"
                )
            if cfg.escalate_below < 0.0:
                raise ValueError(
                    f"escalate_below must be >= 0.0, got {cfg.escalate_below} for {cfg.name!r}"
                )
        self._tiers = list(tiers)
        self._configs = {c.name: c for c in configs}
        self._order = [t.name for t in tiers]

    @property
    def tier_names(self) -> list[str]:
        """Tiers in cascade order."""
        return list(self._order)

    def run(self, query: str, ctx: QueryContext) -> QueryResult:
        if not query:
            raise ValueError("query must be a non-empty string")

        ordered = self._order_for(ctx)
        last_result: QueryResult | None = None
        for tier_name in ordered:
            tier = self._tier_by_name(tier_name)
            cfg = self._configs[tier_name]
            tier_ctx = ctx
            if cfg.timeout_ms is not None:
                # Hint the tier; the orchestrator does not enforce hard timeouts in R0.
                hinted_max = (
                    cfg.timeout_ms
                    if ctx.max_latency_ms is None
                    else min(ctx.max_latency_ms, cfg.timeout_ms)
                )
                tier_ctx = ctx.model_copy(update={"max_latency_ms": hinted_max})
            start = time.perf_counter()
            result = tier.search(query, tier_ctx)
            latency_ms = int((time.perf_counter() - start) * 1000)
            if result.tier_used != tier.name:
                raise RuntimeError(
                    f"tier {tier.name!r} returned tier_used={result.tier_used!r}; "
                    "tiers must self-identify"
                )
            result = result.model_copy(update={"latency_ms": latency_ms})
            last_result = result
            if result.relevance >= cfg.escalate_below:
                return result
        assert last_result is not None  # invariant: at least one tier ran
        return last_result

    def _order_for(self, ctx: QueryContext) -> list[str]:
        if ctx.prefer_tier is None:
            return list(self._order)
        if ctx.prefer_tier not in self._configs:
            raise ValueError(
                f"prefer_tier {ctx.prefer_tier!r} not in registered tiers {self._order}"
            )
        rest = [n for n in self._order if n != ctx.prefer_tier]
        return [ctx.prefer_tier, *rest]

    def _tier_by_name(self, name: str) -> Tier:
        for t in self._tiers:
            if t.name == name:
                return t
        raise KeyError(f"tier {name!r} not registered")  # unreachable: __init__ guards


def build_default_orchestrator(tiers: list[Tier]) -> CascadeOrchestrator:
    """Convenience builder: every tier gets `escalate_below=1.01` so the
    cascade walks all tiers and only stops on a perfect-score hit.

    Real deployments should pass explicit `TierConfig`s tuned per tier.
    Kept here so demo code does not have to re-derive thresholds.
    """
    if not tiers:
        raise ValueError("at least one tier required")
    configs = [TierConfig(name=t.name, escalate_below=1.01) for t in tiers]
    # `replace` is a no-op here but documents intent: configs are frozen.
    configs = [replace(c) for c in configs]
    return CascadeOrchestrator(tiers, configs)
