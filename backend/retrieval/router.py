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
points at the fine-tuned weights (see `pioneer/README.md`).

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
# `pioneer/prompt.md`; if you add a label, update both places.
RouterIntent = Literal["lookup", "search", "analytical", "ambiguous"]
INTENTS: tuple[RouterIntent, ...] = ("lookup", "search", "analytical", "ambiguous")

# Canonical NER entity types. Mirrors the schema in
# `pioneer/prompt.md` and the GLiNER2 multi-task config used at
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
# `pioneer/README.md` § Calibration).
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

    See `backend/retrieval/pioneer/README.md` for the end-to-end
    Pioneer.ai workflow.
    """

    MODEL_PATH_ENV: str = "GLINER2_MODEL_PATH"
    MODEL_ID_ENV: str = "PIONEER_MODEL_ID"
    API_KEY_ENV: str = "PIONEER_API_KEY"
    API_BASE_ENV: str = "PIONEER_API_BASE"
    DEFAULT_API_BASE: str = "https://api.pioneer.ai/v1"

    def __init__(
        self,
        *,
        model_path: str | None = None,
        pioneer_model_id: str | None = None,
        pioneer_api_key: str | None = None,
        threshold: float = 0.95,
    ) -> None:
        path = model_path if model_path is not None else os.environ.get(self.MODEL_PATH_ENV)
        endpoint = (
            pioneer_model_id
            if pioneer_model_id is not None
            else os.environ.get(self.MODEL_ID_ENV)
        )
        api_key = (
            pioneer_api_key
            if pioneer_api_key is not None
            else os.environ.get(self.API_KEY_ENV)
        )
        # Two paths: hosted (PIONEER_MODEL_ID + PIONEER_API_KEY) or local
        # (GLINER2_MODEL_PATH + gliner installed). Hosted wins if both
        # are present — same accuracy, no PyTorch dependency to load.
        self._hosted = bool(endpoint and api_key)
        self._local = bool(path) and not self._hosted
        if not self._hosted and not self._local:
            raise RuntimeError(
                "GLiNER2EntityRouter requires either:\n"
                f"  (a) hosted: {self.MODEL_ID_ENV}=<id> + {self.API_KEY_ENV}=<key>, OR\n"
                f"  (b) local:  {self.MODEL_PATH_ENV}=<weights-dir> (requires `uv add gliner`).\n"
                "See pioneer/README.md. Use StubEntityRouter for tests / no-model fallback."
            )
        if self._local:
            try:
                import gliner  # type: ignore[import-not-found]  # noqa: F401
            except ImportError as e:
                raise ImportError(
                    "Local GLiNER2EntityRouter requires the `gliner` package. "
                    "Install with `uv add gliner`, or switch to hosted mode by "
                    f"setting {self.MODEL_ID_ENV} + {self.API_KEY_ENV}."
                ) from e
            # Pioneer ships weights as LoRA adapters (adapter_config.json +
            # adapter_weights.safetensors). gliner.from_pretrained can't load
            # those directly — we detect the adapter shape here and apply it
            # via peft on top of the base model in `_ensure_local_model`.
            assert path is not None
            import pathlib
            p = pathlib.Path(path)
            self._is_lora_adapter = (
                (p / "adapter_config.json").is_file()
                and not (p / "config.json").is_file()
            )
            if self._is_lora_adapter:
                try:
                    import peft  # type: ignore[import-not-found]  # noqa: F401
                except ImportError as e:
                    raise ImportError(
                        f"{path!r} is a LoRA adapter — loading needs `peft` "
                        "(installs with gliner already; if you removed it, "
                        "run `uv add 'peft<0.19'`)."
                    ) from e
        self._model_path = path
        self._model_id = endpoint
        self._api_key = api_key
        self._api_base = os.environ.get(self.API_BASE_ENV, self.DEFAULT_API_BASE)
        self._threshold = threshold
        self._model: object | None = None
        self._http_client: object | None = None

    BASE_MODEL_ID: str = "fastino/gliner2-base-v1"

    def _ensure_local_model(self) -> object:
        if self._model is None:
            from gliner import GLiNER  # type: ignore[import-not-found]
            assert self._model_path is not None
            if self._is_lora_adapter:
                # Two-step: load base from HF, then apply LoRA adapter via peft.
                # Pioneer's adapter targets the encoder submodule; gliner stores
                # its torch model on the wrapper — find which attribute holds
                # the nn.Module and wrap THAT in PeftModel.
                from peft import PeftModel  # type: ignore[import-not-found]

                base = GLiNER.from_pretrained(self.BASE_MODEL_ID)
                attached = False
                for attr in ("model", "encoder", "base_model", "net"):
                    if hasattr(base, attr):
                        sub = getattr(base, attr)
                        import torch.nn as nn
                        if isinstance(sub, nn.Module):
                            setattr(base, attr, PeftModel.from_pretrained(sub, self._model_path))
                            attached = True
                            break
                if not attached:
                    raise RuntimeError(
                        "Could not find a torch.nn.Module attribute on the "
                        "base GLiNER wrapper to attach the LoRA adapter to. "
                        "gliner's internal layout may have changed — switch "
                        "to BETTER_CONTEXT_ROUTER=two-model (hosted) as a "
                        "fallback."
                    )
                self._model = base
            else:
                # Full GLiNER2 model directory.
                self._model = GLiNER.from_pretrained(self._model_path)
        return self._model

    def _ensure_http_client(self) -> object:
        if self._http_client is None:
            import httpx
            self._http_client = httpx.Client(timeout=30.0)
        return self._http_client

    def _classify_hosted(self, query: str) -> RouterDecision:
        """Call Pioneer's hosted inference endpoint. Response shape:
        ``{"entities": {<type>: [<spans>...], ...}, "intent": "<label>", ...}``.
        Note: hosted endpoint does not return a calibrated intent score — we
        emit 1.0 since the API has already applied its abstain threshold.
        """
        client = self._ensure_http_client()
        url = f"{self._api_base}/chat/completions"
        payload = {
            "model": self._model_id,
            "task": "schema",
            "input": query,
            "schema": {
                "entities": list(ENTITY_TYPES),
                "classifications": {"intent": list(INTENTS)},
            },
            "threshold": self._threshold,
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        resp = client.post(url, json=payload, headers=headers)  # type: ignore[attr-defined]
        if resp.status_code != 200:
            raise RuntimeError(
                f"Pioneer inference HTTP {resp.status_code}: {resp.text[:200]}"
            )
        body = resp.json()
        # Pioneer wraps the actual prediction either at top level or under
        # `result` depending on the API version — handle both.
        raw = body.get("result", body) if isinstance(body, dict) else body
        return _parse_pioneer_hosted_output(raw)

    def classify(self, query: str) -> RouterDecision:
        if not isinstance(query, str):
            raise TypeError(f"query must be str, got {type(query).__name__}")
        if not query.strip():
            raise ValueError("query must be non-empty / non-whitespace")
        if self._hosted:
            return self._classify_hosted(query)
        # Local path: GLiNER2 multi-task forward pass.
        model = self._ensure_local_model()
        raw = model.predict(  # type: ignore[attr-defined]
            query,
            classifications={"intent": list(INTENTS)},
            entities={etype: f"{etype} entity" for etype in ENTITY_TYPES},
        )
        return _parse_gliner2_output(raw)


def _parse_pioneer_hosted_output(raw: object) -> RouterDecision:
    """Convert Pioneer's hosted-endpoint schema-task response into a
    `RouterDecision`. Shape:

        {"entities": {"emp_id": ["emp_0990"], "customer_id": [], ...},
         "intent": "analytical",
         "input_tokens": ..., "output_tokens": ..., "token_usage": ...}

    Differs from local gliner output: intent at top level (no nested
    `classifications.intent` list), entities as dict-of-lists (no
    label/text/score objects). The hosted API has already applied its
    abstain threshold, so we emit confidence=1.0 — the local stub-router
    threshold (`min_intent_conf=0.5`) is therefore always satisfied.
    """
    if not isinstance(raw, dict):
        raise RuntimeError(f"Pioneer hosted returned non-dict: {type(raw).__name__}")
    intent = raw.get("intent")
    if intent not in INTENTS:
        raise RuntimeError(
            f"Pioneer hosted returned unknown intent {intent!r}; "
            f"expected one of {INTENTS}"
        )
    entities_raw = raw.get("entities", {})
    if not isinstance(entities_raw, dict):
        raise RuntimeError("Pioneer hosted 'entities' must be a dict")
    spans: dict[str, list[str]] = {}
    for etype, vals in entities_raw.items():
        if etype not in ENTITY_TYPES:
            continue
        if not isinstance(vals, list):
            raise RuntimeError(f"Pioneer hosted entities[{etype!r}] must be a list")
        clean = [v for v in vals if isinstance(v, str) and v]
        if clean:
            spans[etype] = clean
    return RouterDecision(intent=intent, confidence=1.0, entities=spans)


def _parse_gliner2_output(raw: object) -> RouterDecision:
    """Convert a GLiNER2 multi-task forward-pass result into a `RouterDecision`.

    Pulled out of `GLiNER2EntityRouter.classify` so it can be unit-tested
    without the model. The expected raw shape is documented in the
    `pioneer/README.md` § Output format.
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


class TwoModelEntityRouter:
    """Splits intent classification and NER across two specialized Pioneer
    models, called in parallel on a 2-thread pool.

    Why two models: the v2 schema fine-tune nailed intent (0.978 acc) but
    its NER head was diluted to 0.430 macro F1 by the joint training. The
    v3 NER-only fine-tune hit 0.851 macro F1 in isolation. Running both
    in parallel keeps the API call budget at one round-trip
    (max(intent_latency, ner_latency)) while preserving each head's
    isolated accuracy.

    Backends are HTTP calls to Pioneer's `/v1/chat/completions` endpoint.
    Both calls go to the same host with the same auth, just different
    `model` ids and `task` types (`schema` for intent, `extract_entities`
    for NER). The class falls back to the runtime-determined intent
    confidence threshold (default 0.5) so an under-confident classifier
    flips the result to `ambiguous` upstream — same calibration story as
    `GLiNER2EntityRouter`.
    """

    INTENT_MODEL_ID_ENV: str = "PIONEER_INTENT_MODEL_ID"
    NER_MODEL_ID_ENV: str = "PIONEER_NER_MODEL_ID"
    API_KEY_ENV: str = "PIONEER_API_KEY"
    API_BASE_ENV: str = "PIONEER_API_BASE"
    DEFAULT_API_BASE: str = "https://api.pioneer.ai/v1"

    def __init__(
        self,
        *,
        intent_model_id: str | None = None,
        ner_model_id: str | None = None,
        pioneer_api_key: str | None = None,
        intent_threshold: float = 0.95,
        ner_threshold: float = 0.99,
        request_timeout_s: float = 30.0,
    ) -> None:
        intent_id = intent_model_id or os.environ.get(self.INTENT_MODEL_ID_ENV)
        ner_id = ner_model_id or os.environ.get(self.NER_MODEL_ID_ENV)
        api_key = pioneer_api_key or os.environ.get(self.API_KEY_ENV)
        missing = [
            name
            for name, val in [
                (self.INTENT_MODEL_ID_ENV, intent_id),
                (self.NER_MODEL_ID_ENV, ner_id),
                (self.API_KEY_ENV, api_key),
            ]
            if not val
        ]
        if missing:
            raise RuntimeError(
                "TwoModelEntityRouter requires all of: "
                f"{', '.join(missing)}. See pioneer/MODELS.md for model ids."
            )
        # mypy/pyright: missing-check above narrows to str but the assigner
        # doesn't infer that — assert for pyright + runtime safety.
        assert intent_id and ner_id and api_key
        self._intent_model_id: str = intent_id
        self._ner_model_id: str = ner_id
        self._api_key: str = api_key
        self._api_base = os.environ.get(self.API_BASE_ENV, self.DEFAULT_API_BASE)
        self._intent_threshold = intent_threshold
        self._ner_threshold = ner_threshold
        self._request_timeout_s = request_timeout_s
        self._http_client: object | None = None
        self._pool: object | None = None

    def _ensure_http(self) -> object:
        if self._http_client is None:
            import httpx

            self._http_client = httpx.Client(timeout=self._request_timeout_s)
        return self._http_client

    def _ensure_pool(self) -> object:
        if self._pool is None:
            from concurrent.futures import ThreadPoolExecutor

            self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="entity-router")
        return self._pool

    def _call_pioneer(self, payload: dict[str, object]) -> dict[str, object]:
        client = self._ensure_http()
        url = f"{self._api_base}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        resp = client.post(url, json=payload, headers=headers)  # type: ignore[attr-defined]
        if resp.status_code != 200:
            raise RuntimeError(
                f"Pioneer inference HTTP {resp.status_code}: {resp.text[:200]}"
            )
        body = resp.json()
        # Pioneer wraps the prediction in OpenAI chat-completion shape:
        # body["choices"][0]["message"]["content"] is a JSON string.
        if isinstance(body, dict) and "choices" in body:
            import json as _json
            content = body["choices"][0]["message"]["content"]
            return _json.loads(content) if isinstance(content, str) else content  # type: ignore[no-any-return]
        # Older API path: prediction at top level or under `result`.
        return body.get("result", body) if isinstance(body, dict) else body  # type: ignore[no-any-return]

    def _parse_intent_label(self, raw_intent: object) -> RouterIntent:
        # Pioneer returns intent either as a string (old) or
        # {"label": "...", "confidence": 0.99}. Handle both.
        if isinstance(raw_intent, dict):
            label = raw_intent.get("label")
        else:
            label = raw_intent
        if label not in INTENTS:
            raise RuntimeError(
                f"Intent model returned unknown intent {label!r}; "
                f"expected one of {INTENTS}"
            )
        return label  # type: ignore[return-value]

    def _parse_entities(self, raw_entities: object) -> dict[str, list[str]]:
        # Pioneer returns entities as a dict-of-lists. Each list element is
        # either a plain string (old) or {"text": "...", "confidence": ...,
        # "start": int, "end": int} (current). Extract just the text spans.
        if not isinstance(raw_entities, dict):
            return {}
        spans: dict[str, list[str]] = {}
        for etype, vals in raw_entities.items():
            if etype not in ENTITY_TYPES:
                continue
            if not isinstance(vals, list):
                continue
            clean: list[str] = []
            for v in vals:
                if isinstance(v, str) and v:
                    clean.append(v)
                elif isinstance(v, dict):
                    text = v.get("text")
                    if isinstance(text, str) and text:
                        clean.append(text)
            if clean:
                spans[etype] = clean
        return spans

    def _classify_intent(self, query: str) -> tuple[RouterIntent, float]:
        """Call Pioneer hosted intent model. Returns intent + confidence."""
        raw = self._call_pioneer({
            "model": self._intent_model_id,
            "messages": [{"role": "user", "content": query}],
            "task": "schema",
            "schema": {
                "entities": list(ENTITY_TYPES),
                "classifications": {"intent": list(INTENTS)},
            },
            "threshold": self._intent_threshold,
        })
        intent = self._parse_intent_label(raw.get("intent"))
        # If model returned confidence, surface it; otherwise the API has
        # already applied its threshold so we treat as fully confident.
        intent_obj = raw.get("intent")
        confidence = 1.0
        if isinstance(intent_obj, dict):
            c = intent_obj.get("confidence")
            if isinstance(c, (int, float)):
                confidence = float(c)
        return intent, confidence

    def _extract_entities(self, query: str) -> dict[str, list[str]]:
        """Call Pioneer hosted NER model."""
        raw = self._call_pioneer({
            "model": self._ner_model_id,
            "messages": [{"role": "user", "content": query}],
            "task": "extract_entities",
            "schema": list(ENTITY_TYPES),
            "threshold": self._ner_threshold,
        })
        return self._parse_entities(raw.get("entities", raw))

    def classify(self, query: str) -> RouterDecision:
        if not isinstance(query, str):
            raise TypeError(f"query must be str, got {type(query).__name__}")
        if not query.strip():
            raise ValueError("query must be non-empty / non-whitespace")
        pool = self._ensure_pool()
        # Fan out — intent + NER in parallel. Each backend raises on its own
        # bugs (HTTP error, malformed shape); .result() re-raises to the
        # caller, which is what we want (fail fast — no silent degradation
        # to a half-decision).
        intent_fut = pool.submit(self._classify_intent, query)  # type: ignore[attr-defined]
        ner_fut = pool.submit(self._extract_entities, query)  # type: ignore[attr-defined]
        intent, confidence = intent_fut.result()
        entities = ner_fut.result()
        return RouterDecision(intent=intent, confidence=confidence, entities=entities)


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
