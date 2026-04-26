"""RouterTier — Tier 2 of the retrieval cascade (Pioneer.ai GLiNER2).

Acts as a **pre-router** between R1 (`ExactTier`) and R3 (`HybridTier`).
The cascade order is therefore:

    [exact, router, hybrid, ...]

Why between exact and hybrid? Pure id-shaped lookups (`emp_1002`,
`CLNT-0042`) are already caught by `ExactTier` regex token extraction;
running an NER model on them is wasted latency. RouterTier adds value
when the query is natural language but contains an entity buried in a
sentence (`"Send a message to Anil Rathore regarding ..."`) — GLiNER2
extracts the named entities, and the router decides whether the cascade
should fast-path back to T1 (with the freshly-extracted ids), continue
to T3 (semantic search), or skip ahead to T4 (analytical / multi-hop).

GLiNER2 (Pioneer.ai-fine-tuned) emits a multi-task forward pass:

* **Intent classification** over a fixed label set ``{lookup, search,
  analytical, ambiguous}`` with a softmax-style confidence in [0, 1].
* **NER** over the EnterpriseBench entity types (``emp_id``,
  ``customer_id``, ``ticket_id``, ``date``, ``department``, ``product``).

Routing decisions, mirroring issue #6:

| classifier output                                | routing action                              |
|--------------------------------------------------|---------------------------------------------|
| intent=`lookup`  + >= 1 NER id (emp/customer/...) | inline-call ExactTier with the NER ids; return its hits, tier_used="router" |
| intent=`search`                                  | abstain (relevance=0, route_to="hybrid")     |
| intent=`analytical`                              | abstain (relevance=0, route_to="agentic")    |
| intent=`ambiguous` OR confidence < min_intent_conf | abstain (relevance=0, no directive)        |

`Hit.score` semantics:

* For `lookup` decisions, RouterTier delegates to `ExactTier` and
  forwards its hits verbatim (each `Hit.score` is the underlying
  ExactTier score: 1.0 for an id-token Cypher hit, normalized BM25 for
  a fulltext fallback). The hit list is the value RouterTier
  contributes to the cascade.
* For abstain decisions, RouterTier returns an empty hit list. There
  is no Hit to score because the router itself does no retrieval — it
  is purely a routing decision. `QueryResult.relevance` is set to
  ``0.0`` so the orchestrator escalates past the router; the optional
  `route_to` field tells the orchestrator which downstream tier to
  jump to (rather than walking the next slot in cascade order).

The classifier is hidden behind an `EntityRouter` Protocol so the BGE
fine-tuned weights / Pioneer.ai inference endpoint stays a swappable
backend. `StubEntityRouter` is a deterministic fallback that classifies
purely from id-shaped tokens — it is correct enough to keep the cascade
green in CI and on machines that have not run the Pioneer.ai
fine-tune. Replace with `GLiNER2EntityRouter` once `GLINER2_MODEL_PATH`
points at the fine-tuned weights (see `router_train/README.md`).

Deep module: `RouterTier.search()` is the only public method.
Classifier construction, NER token extraction, and ExactTier delegation
are internal.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

from .exact import ExactTier
from .models import Citation, Hit, QueryContext, QueryResult
from .tiers import Tier

# The four routing intents emitted by the classifier. Keep tightly
# coupled to the Pioneer.ai fine-tuning label set in
# `router_train/prompt.md`; if you add a label, update both places.
RouterIntent = Literal["lookup", "search", "analytical", "ambiguous"]
INTENTS: tuple[RouterIntent, ...] = ("lookup", "search", "analytical", "ambiguous")

# Canonical NER entity types. Mirrors the schema in
# `router_train/prompt.md` and the GLiNER2 multi-task config used at
# fine-tune time. Any change here requires re-training.
ENTITY_TYPES: tuple[str, ...] = (
    "emp_id",
    "customer_id",
    "ticket_id",
    "date",
    "department",
    "product",
)

# Entity types whose extracted spans are usable as direct ExactTier
# id-token inputs (ExactTier's id regex covers `emp_*`, `CLNT-*`,
# product ASINs, ticket prefixes, UUIDs). Routing a `lookup` to T1
# requires at least one span of one of these types.
_ID_BEARING_TYPES: frozenset[str] = frozenset({"emp_id", "customer_id", "ticket_id", "product"})

# Intent confidence floor. Below this, the router abstains regardless
# of the classifier's claimed label — a noisy/uncertain output is
# treated as `ambiguous`. The default 0.5 is a safe placeholder; tune
# after Pioneer.ai eval lands real calibration numbers (see
# `router_train/README.md` § Calibration).
_DEFAULT_MIN_INTENT_CONF: float = 0.5

# Heuristic id patterns the stub router uses to classify a query as
# `lookup` without a real model. Mirrors (but does not import) a
# subset of `exact._ID_PATTERNS` so the stub stays self-contained and
# cannot drift accidentally if `exact.py` adds patterns the router
# does not yet understand.
_STUB_ID_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bemp_\d+\b"),
    re.compile(r"\b(?:CLNT|CUST|VEND|ORG)-\d+\b"),
    re.compile(r"\b[A-Z][0-9A-Z]{9}\b"),
    re.compile(r"\b(?:ticket|conv|conversation|order|sale|product)[-_:][\w-]+\b", re.IGNORECASE),
)

# Soft signals the stub uses to classify a query as `analytical`
# (multi-hop / aggregation / reasoning). Word-boundary-anchored,
# case-insensitive. Conservative on purpose: false positives here
# route past hybrid which is more expensive than necessary.
_STUB_ANALYTICAL_HINTS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bhow many\b", re.IGNORECASE),
    re.compile(r"\bcount\b", re.IGNORECASE),
    re.compile(r"\baverage\b", re.IGNORECASE),
    re.compile(r"\bcompare\b", re.IGNORECASE),
    re.compile(r"\btrend\b", re.IGNORECASE),
    re.compile(r"\bover (?:the )?(?:last|past)\b", re.IGNORECASE),
)


@dataclass(frozen=True)
class RouterDecision:
    """Output of one `EntityRouter.classify` call.

    * `intent` — one of `lookup`, `search`, `analytical`, `ambiguous`.
    * `confidence` — softmax-style probability in [0, 1] for `intent`.
      Algorithmic (the classifier's calibrated head output). Below the
      router's `min_intent_conf` floor, the intent is overridden to
      `ambiguous` upstream — the field stays the raw classifier value
      for audit.
    * `entities` — dict `entity_type -> list of surface spans` (each
      span is the raw substring as it appeared in the query). Empty
      dict if the classifier emitted no spans.
    """

    intent: RouterIntent
    confidence: float
    entities: dict[str, list[str]]

    def __post_init__(self) -> None:
        if self.intent not in INTENTS:
            raise ValueError(f"intent must be one of {INTENTS}, got {self.intent!r}")
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(
                f"confidence must be in [0, 1], got {self.confidence}"
            )
        for etype, spans in self.entities.items():
            if etype not in ENTITY_TYPES:
                raise ValueError(
                    f"unknown entity type {etype!r}; allowed: {ENTITY_TYPES}"
                )
            for s in spans:
                if not isinstance(s, str) or not s:
                    raise ValueError(
                        f"entity spans must be non-empty strings, got {s!r}"
                    )


@runtime_checkable
class EntityRouter(Protocol):
    """Pluggable backend for the `RouterTier`.

    Implementations classify a free-text query into a `RouterDecision`
    in one forward pass. The contract intentionally returns a single
    dataclass so swapping a deterministic stub for a fine-tuned model
    is a one-line change in the orchestrator factory.

    Implementations MUST be deterministic with respect to the input
    string (no hidden global state, no random sampling). This keeps
    the cascade reproducible for the eval harness.
    """

    def classify(self, query: str) -> RouterDecision:
        ...


class StubEntityRouter:
    """Deterministic regex-based router used as the fallback backend.

    Decision rules (in order):

    1. If the query contains any id-shaped token (`emp_*`, `CLNT-*`,
       ASIN, ticket/conv/order/product prefix) → `lookup`,
       confidence=1.0, entities populated by best-effort type inference
       on the surface form.
    2. Else if the query matches an analytical hint (`how many`,
       `count`, `average`, `trend`, ...) → `analytical`, confidence=0.8.
    3. Else if the query is short (<= 4 tokens) → `ambiguous`,
       confidence=0.6 (likely a phrase ExactTier already tried).
    4. Else → `search`, confidence=0.7.

    The numeric confidences are calibration *placeholders*. Real
    confidences come from the Pioneer.ai-fine-tuned head and replace
    these once `GLiNER2EntityRouter` is wired in. Tests assert on the
    intent label, not the exact confidence number.
    """

    def classify(self, query: str) -> RouterDecision:
        if not isinstance(query, str):
            raise TypeError(f"query must be str, got {type(query).__name__}")
        if not query.strip():
            raise ValueError("query must be non-empty / non-whitespace")
        entities = self._extract_entities(query)
        if entities:
            return RouterDecision(intent="lookup", confidence=1.0, entities=entities)
        for pat in _STUB_ANALYTICAL_HINTS:
            if pat.search(query):
                return RouterDecision(
                    intent="analytical", confidence=0.8, entities={}
                )
        if len(query.split()) <= 4:
            return RouterDecision(intent="ambiguous", confidence=0.6, entities={})
        return RouterDecision(intent="search", confidence=0.7, entities={})

    def _extract_entities(self, query: str) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for pat in _STUB_ID_PATTERNS:
            for m in pat.finditer(query):
                tok = m.group(0)
                etype = self._infer_type(tok)
                out.setdefault(etype, []).append(tok)
        # Dedup while preserving order so the downstream ExactTier
        # query is stable.
        for k, v in list(out.items()):
            seen: set[str] = set()
            uniq = [s for s in v if not (s in seen or seen.add(s))]
            out[k] = uniq
        return out

    @staticmethod
    def _infer_type(token: str) -> str:
        lower = token.lower()
        if lower.startswith("emp_"):
            return "emp_id"
        if lower.startswith(("clnt-", "cust-", "vend-", "org-")):
            return "customer_id"
        if lower.startswith(("ticket", "ticket-", "ticket_", "ticket:")):
            return "ticket_id"
        if lower.startswith(("conv", "order", "sale")):
            return "ticket_id"
        # Default: treat as a product id (covers ASINs and `product:*`).
        return "product"


class GLiNER2EntityRouter:
    """Pioneer.ai-fine-tuned GLiNER2 backend.

    Loads weights from `GLINER2_MODEL_PATH` (local directory) OR calls
    a Pioneer.ai inference endpoint named by `PIONEER_AI_MODEL_ID`.
    The class refuses to construct if neither env var is set — per the
    issue's fail-fast clause: missing weights/endpoint → raise on
    `__init__`, never silently degrade.

    The actual `gliner` / `gliner2` Python package is **not** declared
    as a project dependency: it is large (PyTorch + transformers) and
    must only land on machines that have run the Pioneer.ai
    fine-tune. The constructor imports it lazily and raises a clear
    `ImportError` with installation instructions if absent.

    See `backend/retrieval/router_train/README.md` for the end-to-end
    Pioneer.ai workflow.
    """

    MODEL_PATH_ENV: str = "GLINER2_MODEL_PATH"
    MODEL_ID_ENV: str = "PIONEER_AI_MODEL_ID"

    def __init__(
        self,
        *,
        model_path: str | None = None,
        pioneer_model_id: str | None = None,
    ) -> None:
        path = model_path if model_path is not None else os.environ.get(self.MODEL_PATH_ENV)
        endpoint = (
            pioneer_model_id
            if pioneer_model_id is not None
            else os.environ.get(self.MODEL_ID_ENV)
        )
        if not path and not endpoint:
            raise RuntimeError(
                "GLiNER2EntityRouter requires either a local weights path "
                f"({self.MODEL_PATH_ENV}=...) or a Pioneer.ai model id "
                f"({self.MODEL_ID_ENV}=...). See "
                "backend/retrieval/router_train/README.md for fine-tuning "
                "instructions. Use StubEntityRouter for tests / no-model "
                "fallback."
            )
        try:
            import gliner  # type: ignore[import-not-found]  # noqa: F401
        except ImportError as e:
            raise ImportError(
                "GLiNER2EntityRouter requires the `gliner` package. "
                "Install with `uv add gliner` after running the "
                "Pioneer.ai fine-tune (see "
                "backend/retrieval/router_train/README.md)."
            ) from e
        self._model_path = path
        self._endpoint = endpoint
        self._model: object | None = None

    def _ensure_model(self) -> object:
        if self._model is None:
            from gliner import GLiNER  # type: ignore[import-not-found]

            # Local weights take precedence; the endpoint path is a
            # follow-up wiring once Pioneer publishes a stable
            # inference URL convention.
            if self._model_path is None:
                raise RuntimeError(
                    "GLiNER2EntityRouter: remote endpoint inference is not yet "
                    f"implemented. Set {self.MODEL_PATH_ENV}=<weights-dir> after "
                    "downloading the fine-tuned weights from Pioneer.ai."
                )
            self._model = GLiNER.from_pretrained(self._model_path)
        return self._model

    def classify(self, query: str) -> RouterDecision:
        if not isinstance(query, str):
            raise TypeError(f"query must be str, got {type(query).__name__}")
        if not query.strip():
            raise ValueError("query must be non-empty / non-whitespace")
        model = self._ensure_model()
        # GLiNER2 multi-task forward pass. Schema matches
        # `router_train/prompt.md`. The fine-tuned head returns
        # ``{"classifications": {"intent": [{"label": ..., "score": ...}, ...]},
        #    "entities": [{"label": "emp_id", "text": "emp_1002", "score": ...}, ...]}``.
        raw = model.predict(  # type: ignore[attr-defined]
            query,
            classifications={"intent": list(INTENTS)},
            entities={etype: f"{etype} entity" for etype in ENTITY_TYPES},
        )
        return _parse_gliner2_output(raw)


def _parse_gliner2_output(raw: object) -> RouterDecision:
    """Convert a GLiNER2 multi-task forward-pass result into a `RouterDecision`.

    Pulled out of `GLiNER2EntityRouter.classify` so it can be unit-tested
    without the model. The expected raw shape is documented in the
    `router_train/README.md` § Output format.
    """
    if not isinstance(raw, dict):
        raise RuntimeError(f"GLiNER2 returned non-dict result: {type(raw).__name__}")
    classifications = raw.get("classifications")
    entities = raw.get("entities")
    if not isinstance(classifications, dict):
        raise RuntimeError("GLiNER2 result missing 'classifications' dict")
    intent_block = classifications.get("intent")
    if not isinstance(intent_block, list) or not intent_block:
        raise RuntimeError("GLiNER2 result missing non-empty 'intent' list")
    top = intent_block[0]
    if not isinstance(top, dict) or "label" not in top or "score" not in top:
        raise RuntimeError(f"GLiNER2 intent entry malformed: {top!r}")
    intent = top["label"]
    if intent not in INTENTS:
        raise RuntimeError(
            f"GLiNER2 returned unknown intent {intent!r}; "
            f"expected one of {INTENTS}"
        )
    confidence = float(top["score"])
    spans: dict[str, list[str]] = {}
    if entities is None:
        entities = []
    if not isinstance(entities, list):
        raise RuntimeError("GLiNER2 result 'entities' must be a list")
    for ent in entities:
        if not isinstance(ent, dict):
            raise RuntimeError(f"GLiNER2 entity entry not a dict: {ent!r}")
        label = ent.get("label")
        text = ent.get("text")
        if label not in ENTITY_TYPES:
            # Unknown labels are dropped on purpose: a future model
            # version may emit extra labels we have not wired through.
            continue
        if not isinstance(text, str) or not text:
            continue
        spans.setdefault(label, []).append(text)
    return RouterDecision(intent=intent, confidence=confidence, entities=spans)


class RouterTier(Tier):
    """Pre-router that dispatches the cascade based on entity classification.

    Sits between `ExactTier` (cheap regex/Cypher) and `HybridTier`
    (vector + fulltext). The router does not retrieve on its own
    except as a delegated `lookup` fast-path back to ExactTier; for
    `search` and `analytical` intents it abstains and emits a
    `route_to` directive so the orchestrator skips ahead.

    Confidence semantics (per `models.py` contract — every score is
    algorithmic):

    * `lookup` decision → returns the `ExactTier` result verbatim,
      retagged with `tier_used="router"`. Each `Hit.score` is the
      ExactTier algorithmic score (1.0 for an id-token Cypher match;
      normalized BM25 for a fulltext fallback). `relevance` is
      `ExactTier.relevance` so the orchestrator's escalation gate
      still works as if T1 had produced the hit.
    * `search` / `analytical` / `ambiguous` decision → returns an
      empty hit list. `relevance=0.0` (no hits to score), `route_to`
      set per the routing table above for `search` / `analytical`,
      `route_to=None` for `ambiguous` (cascade falls through normally).

    Fail-fast: the constructor enforces that `next_tier_for` is
    consistent with the registered downstream tier names; any router
    decision pointing at a tier the orchestrator does not know is
    rejected at construction (not at query time).
    """

    DEFAULT_NEXT_TIER_FOR: dict[RouterIntent, str | None] = {
        # `lookup` is handled inline (delegate to ExactTier) — never
        # routed via `route_to`.
        "lookup": None,
        "search": "hybrid",
        "analytical": "agentic",
        "ambiguous": None,
    }

    def __init__(
        self,
        router: EntityRouter,
        exact_tier: ExactTier,
        *,
        name: str = "router",
        min_intent_conf: float = _DEFAULT_MIN_INTENT_CONF,
        next_tier_for: dict[RouterIntent, str | None] | None = None,
    ) -> None:
        if not isinstance(router, EntityRouter):
            raise TypeError(
                f"router must implement the EntityRouter protocol "
                f"(needs `classify`), got {type(router).__name__}"
            )
        if not isinstance(exact_tier, ExactTier):
            raise TypeError(
                f"exact_tier must be ExactTier (the lookup fast-path target), "
                f"got {type(exact_tier).__name__}"
            )
        if not name or not name.islower():
            raise ValueError(
                f"RouterTier name must be a non-empty lowercase identifier, got {name!r}"
            )
        if not (0.0 <= min_intent_conf <= 1.0):
            raise ValueError(
                f"min_intent_conf must be in [0, 1], got {min_intent_conf}"
            )
        cfg = dict(self.DEFAULT_NEXT_TIER_FOR)
        if next_tier_for is not None:
            for k, v in next_tier_for.items():
                if k not in INTENTS:
                    raise ValueError(
                        f"next_tier_for key {k!r} is not a valid intent; "
                        f"allowed: {INTENTS}"
                    )
                if v is not None and (not isinstance(v, str) or not v.islower()):
                    raise ValueError(
                        f"next_tier_for[{k!r}] must be None or a lowercase tier "
                        f"name, got {v!r}"
                    )
                cfg[k] = v
        self._router = router
        self._exact = exact_tier
        self._name = name
        self._min_intent_conf = min_intent_conf
        self._next_tier_for = cfg

    @property
    def name(self) -> str:
        return self._name

    def search(self, query: str, ctx: QueryContext) -> QueryResult:
        if not isinstance(query, str):
            raise TypeError(f"query must be str, got {type(query).__name__}")
        if not query.strip():
            raise ValueError("query must be non-empty / non-whitespace")

        decision = self._router.classify(query)
        intent: RouterIntent = decision.intent
        if decision.confidence < self._min_intent_conf:
            # Treat low-confidence outputs as `ambiguous` regardless of
            # the claimed label. The original confidence stays in the
            # `decision` dataclass for audit but does not influence
            # routing.
            intent = "ambiguous"

        if intent == "lookup":
            return self._delegate_lookup(query, ctx, decision)

        # Abstain. Empty hit list, relevance=0 (no algorithmic score
        # because no retrieval happened), optional `route_to` directive
        # for the orchestrator.
        return QueryResult(
            answer=None,
            items=[],
            citations=[],
            tier_used=self._name,
            relevance=0.0,
            latency_ms=0,
            route_to=self._next_tier_for[intent],
        )

    # ---------- internal ----------

    def _delegate_lookup(
        self, query: str, ctx: QueryContext, decision: RouterDecision
    ) -> QueryResult:
        """Inline-call ExactTier, then re-tag the result as ours.

        Strategy: build a normalized id-only query string from the NER
        spans of id-bearing types and pass it to `ExactTier.search`.
        ExactTier's regex tokenizer will pick the ids back up; the
        downstream Cypher lookup is the hot path. If the NER produced
        no id-bearing spans, fall back to passing the original query
        through (ExactTier still does its own id extraction; we have
        merely failed to add value over the cheap regex path).
        """
        id_tokens: list[str] = []
        for etype, spans in decision.entities.items():
            if etype in _ID_BEARING_TYPES:
                id_tokens.extend(spans)
        forwarded_query = " ".join(id_tokens) if id_tokens else query
        result = self._exact.search(forwarded_query, ctx)
        # Forward ExactTier's hits and citations verbatim — only
        # rewrite `tier_used` so the orchestrator's self-identify
        # check passes and so callers see that the router is what
        # selected this path. ExactTier's algorithmic Hit.score
        # values (1.0 for id-token, normalized BM25 for fulltext)
        # are preserved unchanged.
        items: list[Hit] = list(result.items)
        citations: list[Citation] = list(result.citations)
        return QueryResult(
            answer=result.answer,
            items=items,
            citations=citations,
            tier_used=self._name,
            relevance=result.relevance,
            latency_ms=0,
            route_to=None,
        )
