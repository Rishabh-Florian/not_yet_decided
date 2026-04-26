"""Cascade orchestrator — try fast tiers first, escalate on miss.

Cascade is the **default** path. Two override hooks exist:

1. `QueryContext.prefer_tier` — caller-supplied hint. The orchestrator
   reorders the cascade so the named tier runs first; the rest follow
   in the original order. Set this when the caller already knows which
   tier to run (e.g. `prefer_tier="exact"` for an id-only request).
2. `QueryResult.route_to` — pre-route directive emitted by a routing
   tier (R4 `RouterTier`, issue #6 — Pioneer.ai GLiNER2). When the
   router produces a low-relevance result that names a downstream
   tier, the orchestrator skips ahead to that tier instead of walking
   to the next one in cascade order. The directive fires at most once
   per query (no chained re-routing) so a misclassification cannot
   send the cascade into a cycle.

Escalation rule (per-tier configurable):

* Run tier ``T``.
* If ``T.search()`` raises, the orchestrator re-raises (fail fast).
* Otherwise compare ``result.relevance`` against ``T``'s configured
  ``escalate_below`` threshold. If ``relevance < escalate_below``:
  - if the result carries `route_to=X` and `X` is registered AND has
    not already run, jump to `X` next;
  - else fall through to the next tier in cascade order.
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

    @property
    def tiers_by_name(self) -> dict[str, Tier]:
        """Snapshot of registered tiers keyed by `tier.name`.

        Returned dict is a fresh copy — mutating it does not change the
        orchestrator's internal state. Used by the workflow framework
        to build per-workflow `TierRegistry` views over a locked subset.
        """
        return {t.name: t for t in self._tiers}

    def run(self, query: str, ctx: QueryContext) -> QueryResult:
        if not query:
            raise ValueError("query must be a non-empty string")

        ordered = self._order_for(ctx)
        last_result: QueryResult | None = None
        executed: set[str] = set()
        # Pre-route directives are honored at most once per query so a
        # router misclassification cannot cycle the cascade.
        reroute_consumed = False
        i = 0
        while i < len(ordered):
            tier_name = ordered[i]
            if tier_name in executed:
                i += 1
                continue
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
            executed.add(tier_name)
            if result.relevance >= cfg.escalate_below:
                return result
            # Pre-route directive (R4 RouterTier). Honor at most once per
            # query: a router that asks for an unknown / already-run tier
            # is treated as an abstain (fall through to the next tier in
            # cascade order).
            if (
                not reroute_consumed
                and result.route_to is not None
                and result.route_to in self._configs
                and result.route_to not in executed
            ):
                reroute_consumed = True
                ordered = ordered[: i + 1] + [result.route_to] + [
                    n for n in ordered[i + 1 :] if n != result.route_to
                ]
            i += 1
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


def build_orchestrator_with_store(store: object) -> CascadeOrchestrator:
    """Production cascade for `POST /api/query`: `[ExactTier, RouterTier, HybridTier, AgenticTier, StubTier]`.

    Tier order:
      1. `exact`   — Cypher id lookup + Neo4j fulltext (R1, issue #3)
      2. `router`  — Pioneer.ai GLiNER2 pre-route (R4, issue #6)
      3. `hybrid`  — vector + fulltext fused by RRF (R2, issue #4)
      4. `agentic` — Gemini function-calling loop (R3, issue #5)
      5. `stub`    — terminal no-op so the cascade always returns a result

    Why is `router` after `exact`? Pure id queries (`emp_1002`) are
    already caught by ExactTier's regex; running an NER model on them
    is wasted latency. The router earns its 200ms budget on natural
    language queries that contain entities buried in prose
    (`"Send a message to Anil Rathore..."`). On an exact id miss the
    router runs, may extract an id ExactTier missed, and either
    delegates back to ExactTier inline (`lookup` intent) or emits a
    `route_to` directive that skips the cascade ahead.

    Why is `agentic` after `hybrid`? AgenticTier's wall-clock budget
    is 10s — an order of magnitude more expensive than every other
    tier. Cascade fallthrough to it is a last-resort path; the
    intended entry is the router's `route_to="agentic"` directive on
    `analytical` intent (multi-hop reasoning queries). The
    orchestrator's pre-route mechanism honors that directive and
    jumps `hybrid` for analytical queries, so `agentic` only runs
    after `hybrid` for queries that the router classified as
    `search` but which then produced a poor RRF score.

    Escalation thresholds:
      * `exact.escalate_below = 0.5` — id-token hits (`relevance == 1.0`)
        terminate immediately; weak fulltext hits (normalized BM25 <
        0.5) escalate to the router. The 0.5 cutoff corresponds to
        a raw Lucene score of 1.0 after `score / (1 + score)`
        normalization.
      * `router.escalate_below = 0.5` — `lookup` decisions return
        ExactTier's relevance (1.0 for id-token hits) and terminate;
        `search`/`analytical`/`ambiguous` decisions return relevance=0
        plus an optional `route_to` directive and the orchestrator
        skips ahead per that directive (or falls through to the next
        tier in cascade order if absent).
      * `hybrid.escalate_below = 0.3` — RRF scores are normalized so
        rank-1-in-both-arms == 1.0; in practice realistic queries score
        between 0.4 and 0.8 (single-arm hits land at ~0.5 max). 0.3 is
        a permissive floor that escalates only when both arms missed
        outright. Tune downward if recall is acceptable but escalation
        is too aggressive.
      * `agentic.escalate_below = 0.5` — AgenticTier's algorithmic
        relevance is one of {0.0, 0.3, 0.7} (failed / ungrounded /
        grounded). 0.5 terminates on grounded answers and escalates
        past failed/ungrounded so the cascade falls back to `stub`
        rather than returning a low-trust prose answer as the final
        result. Tune to 0.2 if "any answer beats no answer" matches
        the deployment's latency tolerance.
      * `stub.escalate_below = 0.0` — terminal: never escalates past.

    Embedder selection: defaults to `StubEmbedder` so the cascade boots
    without any optional model dependency. Set `QONTEXT_EMBEDDER=bge`
    in the environment to use `BgeSmallEmbedder` (`BAAI/bge-small-en-v1.5`,
    requires `sentence-transformers`). Without the BGE backend the
    hybrid tier is wired but its semantic recall is non-functional —
    real embeddings need either the BGE local model or a remote API
    key (e.g. GEMINI_API_KEY for a future Gemini-backed embedder).
    The vector index population is a separate one-shot pass (run
    `uv run python -m backend.retrieval.embed` once Neo4j has data).

    Router backend selection: defaults to `StubEntityRouter` (regex
    fallback, deterministic, no model). Set `QONTEXT_ROUTER=gliner2`
    AND one of `GLINER2_MODEL_PATH` (local weights) or
    `PIONEER_AI_MODEL_ID` to use the fine-tuned GLiNER2 backend.
    Without those, the stub fallback keeps the cascade green but does
    NOT produce real NER spans — see
    `backend/retrieval/router_train/README.md` for the Pioneer.ai
    fine-tune workflow.

    AgenticTier LLM backend: selected via `QONTEXT_AGENTIC=gemini`
    (requires `GEMINI_API_KEY`). Default is `noop` — a `StubLLMClient`
    scripted with a single `"agentic backend not configured"` prose
    answer so the cascade still returns a typed result without
    pretending to reason. Real analytical queries need
    `QONTEXT_AGENTIC=gemini` plus a working API key. See
    `ralph/plans/human-backlog.txt` for the env-setup checklist.

    `store` is typed as `object` to dodge an import cycle
    (`backend.graph.store` imports `backend.models`). The runtime check
    enforces the real type.
    """
    # Local imports break the import cycle with backend.graph.store.
    import os

    from backend.graph.store import GraphStore

    from .agentic import (
        AgenticTier,
        GeminiLLMClient,
        LLMClient,
        NoopLLMClient,
    )
    from .embedder import BgeSmallEmbedder, Embedder, StubEmbedder
    from .exact import ExactTier
    from .hybrid import HybridTier
    from .router import (
        EntityRouter,
        GLiNER2EntityRouter,
        RouterTier,
        StubEntityRouter,
    )
    from .tiers import StubTier

    if not isinstance(store, GraphStore):
        raise TypeError(f"store must be GraphStore, got {type(store).__name__}")
    embedder_kind = os.environ.get("QONTEXT_EMBEDDER", "stub").lower()
    if embedder_kind == "bge":
        embedder: Embedder = BgeSmallEmbedder()
    elif embedder_kind == "stub":
        embedder = StubEmbedder()
    else:
        raise ValueError(
            f"QONTEXT_EMBEDDER must be 'bge' or 'stub', got {embedder_kind!r}"
        )
    router_kind = os.environ.get("QONTEXT_ROUTER", "stub").lower()
    if router_kind == "gliner2":
        entity_router: EntityRouter = GLiNER2EntityRouter()
    elif router_kind == "stub":
        entity_router = StubEntityRouter()
    else:
        raise ValueError(
            f"QONTEXT_ROUTER must be 'stub' or 'gliner2', got {router_kind!r}"
        )
    agentic_kind = os.environ.get("QONTEXT_AGENTIC", "noop").lower()
    if agentic_kind == "gemini":
        llm: LLMClient = GeminiLLMClient()
    elif agentic_kind == "noop":
        # Reusable single-turn marker. Marked ungrounded
        # (relevance=0.3) — escalation past `agentic` to `stub` is
        # the intended behavior here. Distinct from the test-only
        # `StubLLMClient` (which is single-use scripted).
        llm = NoopLLMClient()
    else:
        raise ValueError(
            f"QONTEXT_AGENTIC must be 'gemini' or 'noop', got {agentic_kind!r}"
        )
    exact = ExactTier(store)
    router_tier = RouterTier(entity_router, exact)
    hybrid = HybridTier(store, embedder)
    agentic = AgenticTier(store, embedder, llm)
    stub = StubTier(name="stub")
    return CascadeOrchestrator(
        tiers=[exact, router_tier, hybrid, agentic, stub],
        configs=[
            TierConfig(name="exact", escalate_below=0.5),
            TierConfig(name="router", escalate_below=0.5),
            TierConfig(name="hybrid", escalate_below=0.3),
            TierConfig(name="agentic", escalate_below=0.5),
            TierConfig(name="stub", escalate_below=0.0),
        ],
    )
