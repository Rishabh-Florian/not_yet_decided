"""Tests for `RouterTier`, `EntityRouter` protocol, and the
GLiNER2 output parser.

Unit tests use `StubEntityRouter` (deterministic regex backend) and a
`MagicMock(spec=GraphStore)` for the underlying ExactTier — we never
touch a real Neo4j or load a real GLiNER2 model. Integration tests
gated on `GLINER2_MODEL_PATH` exercise the live fine-tuned model.
"""
from __future__ import annotations

import json
import os
from unittest.mock import MagicMock

import pytest

from backend.graph.store import GraphStore
from backend.models.graph import FactConfidence, Provenance
from backend.retrieval import (
    ExactTier,
    QueryContext,
    RouterDecision,
    RouterTier,
    StubEntityRouter,
)
from backend.retrieval.router import (
    ENTITY_TYPES,
    INTENTS,
    GLiNER2EntityRouter,
    _parse_gliner2_output,
)


# ---------------------------------------------------------------------------
# RouterDecision dataclass validation
# ---------------------------------------------------------------------------


class TestRouterDecision:
    def test_valid_construction(self) -> None:
        d = RouterDecision(intent="lookup", confidence=0.9, entities={"emp_id": ["emp_1002"]})
        assert d.intent == "lookup"
        assert d.confidence == 0.9
        assert d.entities == {"emp_id": ["emp_1002"]}

    def test_rejects_unknown_intent(self) -> None:
        with pytest.raises(ValueError, match="intent must be one of"):
            RouterDecision(intent="bogus", confidence=0.5, entities={})  # type: ignore[arg-type]

    def test_rejects_out_of_range_confidence(self) -> None:
        with pytest.raises(ValueError, match=r"confidence must be in \[0, 1\]"):
            RouterDecision(intent="lookup", confidence=1.5, entities={})

    def test_rejects_unknown_entity_type(self) -> None:
        with pytest.raises(ValueError, match="unknown entity type"):
            RouterDecision(intent="lookup", confidence=0.9, entities={"weirdtype": ["x"]})

    def test_rejects_empty_span(self) -> None:
        with pytest.raises(ValueError, match="non-empty strings"):
            RouterDecision(intent="lookup", confidence=0.9, entities={"emp_id": [""]})


# ---------------------------------------------------------------------------
# StubEntityRouter — deterministic regex classifier
# ---------------------------------------------------------------------------


class TestStubEntityRouter:
    def test_emp_id_routes_to_lookup(self) -> None:
        r = StubEntityRouter()
        d = r.classify("Send a message about emp_1002 to the team")
        assert d.intent == "lookup"
        assert d.confidence == 1.0
        assert "emp_1002" in d.entities["emp_id"]

    def test_clnt_id_routes_to_lookup(self) -> None:
        r = StubEntityRouter()
        d = r.classify("status of CLNT-0042")
        assert d.intent == "lookup"
        assert "CLNT-0042" in d.entities["customer_id"]

    def test_asin_routes_to_lookup_as_product(self) -> None:
        r = StubEntityRouter()
        d = r.classify("Tell me about B0BQ3K23Y1 pricing")
        assert d.intent == "lookup"
        assert "B0BQ3K23Y1" in d.entities["product"]

    def test_ticket_prefix_routes_to_lookup(self) -> None:
        r = StubEntityRouter()
        d = r.classify("ticket-4226 needs review")
        assert d.intent == "lookup"
        assert "ticket-4226" in d.entities["ticket_id"]

    def test_analytical_hint_routes_analytical(self) -> None:
        r = StubEntityRouter()
        d = r.classify("how many tickets did we close last week")
        assert d.intent == "analytical"

    def test_count_keyword_routes_analytical(self) -> None:
        r = StubEntityRouter()
        d = r.classify("count of vpn issues over the last quarter")
        assert d.intent == "analytical"

    def test_short_query_routes_ambiguous(self) -> None:
        r = StubEntityRouter()
        d = r.classify("vpn outage")
        assert d.intent == "ambiguous"

    def test_long_natural_language_routes_search(self) -> None:
        r = StubEntityRouter()
        d = r.classify("send a message to Anil Rathore regarding project timelines")
        assert d.intent == "search"

    def test_deterministic(self) -> None:
        r = StubEntityRouter()
        q = "find emp_0431 contact info"
        assert r.classify(q) == r.classify(q)

    def test_rejects_non_string(self) -> None:
        r = StubEntityRouter()
        with pytest.raises(TypeError, match="query must be str"):
            r.classify(42)  # type: ignore[arg-type]

    def test_rejects_empty(self) -> None:
        r = StubEntityRouter()
        with pytest.raises(ValueError, match="non-empty"):
            r.classify("   ")

    def test_dedups_repeated_spans(self) -> None:
        r = StubEntityRouter()
        d = r.classify("emp_1002 talked to emp_1002 again")
        assert d.entities["emp_id"] == ["emp_1002"]


# ---------------------------------------------------------------------------
# RouterTier construction guards
# ---------------------------------------------------------------------------


def _mock_store() -> MagicMock:
    store = MagicMock(spec=GraphStore)
    store._driver = MagicMock()
    store._database = "neo4j"
    store._driver.session.return_value.__enter__.return_value.run.return_value = None
    return store


def _attach_session(store: MagicMock, run_results: list[list[dict]]) -> MagicMock:
    iter_results = iter(run_results)

    def _run(*_args, **_kwargs):  # noqa: ANN001
        return next(iter_results)

    sess = MagicMock()
    sess.run.side_effect = _run
    cm = MagicMock()
    cm.__enter__.return_value = sess
    cm.__exit__.return_value = None
    store._session.return_value = cm
    return store


class TestRouterTierConstruction:
    def test_rejects_non_router(self) -> None:
        store = _mock_store()
        exact = ExactTier(store)
        with pytest.raises(TypeError, match="EntityRouter protocol"):
            RouterTier(router="x", exact_tier=exact)  # type: ignore[arg-type]

    def test_rejects_non_exact_tier(self) -> None:
        with pytest.raises(TypeError, match="exact_tier must be ExactTier"):
            RouterTier(router=StubEntityRouter(), exact_tier="x")  # type: ignore[arg-type]

    def test_rejects_bad_name(self) -> None:
        store = _mock_store()
        exact = ExactTier(store)
        with pytest.raises(ValueError, match="lowercase identifier"):
            RouterTier(StubEntityRouter(), exact, name="ROUTER")

    def test_rejects_out_of_range_min_conf(self) -> None:
        store = _mock_store()
        exact = ExactTier(store)
        with pytest.raises(ValueError, match=r"min_intent_conf must be in \[0, 1\]"):
            RouterTier(StubEntityRouter(), exact, min_intent_conf=1.5)

    def test_rejects_unknown_intent_in_routing_table(self) -> None:
        store = _mock_store()
        exact = ExactTier(store)
        with pytest.raises(ValueError, match="not a valid intent"):
            RouterTier(
                StubEntityRouter(),
                exact,
                next_tier_for={"bogus": "hybrid"},  # type: ignore[dict-item]
            )

    def test_rejects_uppercase_routing_target(self) -> None:
        store = _mock_store()
        exact = ExactTier(store)
        with pytest.raises(ValueError, match="lowercase tier"):
            RouterTier(
                StubEntityRouter(),
                exact,
                next_tier_for={"search": "HYBRID"},
            )

    def test_default_name(self) -> None:
        store = _mock_store()
        exact = ExactTier(store)
        tier = RouterTier(StubEntityRouter(), exact)
        assert tier.name == "router"


# ---------------------------------------------------------------------------
# RouterTier behavior
# ---------------------------------------------------------------------------


class _FakeRouter:
    """Test double: returns a pre-canned RouterDecision regardless of input."""

    def __init__(self, decision: RouterDecision) -> None:
        self._decision = decision

    def classify(self, query: str) -> RouterDecision:
        if not query:
            raise ValueError("empty")
        return self._decision


class TestRouterTierLookupDelegates:
    def test_lookup_with_emp_id_delegates_to_exact(self) -> None:
        store = _mock_store()
        # ExactTier id-lookup will query for `emp_1002` and find it.
        _attach_session(
            store,
            run_results=[
                [{"id": "emp_1002", "attrs": json.dumps({"name": "Alice"})}],
            ],
        )
        store._provenance_for_node.return_value = [
            Provenance(
                source_file="HR/employees.json",
                source_record_id="row:0",
                source_field="emp_id",
                extraction_method="direct_mapping",
                extraction_model="rule:hr_v1",
                confidence=FactConfidence.EXACT,
                raw_value="emp_1002",
            )
        ]
        exact = ExactTier(store)
        # The fake router classifies as `lookup` and returns one emp_id span.
        decision = RouterDecision(
            intent="lookup", confidence=0.95, entities={"emp_id": ["emp_1002"]}
        )
        tier = RouterTier(_FakeRouter(decision), exact)

        result = tier.search("send a message to Alice (emp_1002)", QueryContext())
        # Forwarded to ExactTier; relevance = 1.0 (id-token hit).
        assert result.tier_used == "router"
        assert result.relevance == 1.0
        assert len(result.items) == 1
        assert result.items[0].id == "emp_1002"
        assert result.items[0].score == 1.0  # ExactTier id-token Hit.score
        assert len(result.citations) == 1
        assert result.route_to is None

    def test_lookup_without_id_bearing_entity_falls_back_to_full_query(self) -> None:
        store = _mock_store()
        # id lookup tries our literal query string; no id token => falls
        # through to fulltext. We mock both calls.
        _attach_session(
            store,
            run_results=[
                # ExactTier first does fulltext (no id-token in the
                # forwarded query "Alice"); single-token short-phrase
                # case actually skips id lookup and goes straight to
                # fulltext. We supply one fulltext row.
                [{"id": "p1", "attrs": json.dumps({"name": "Alice"}), "score": 2.0}],
            ],
        )
        store._provenance_for_node.return_value = []
        exact = ExactTier(store)
        # classifier says lookup but the entities dict has only a
        # `department` (not id-bearing); router falls back to passing
        # the original query through to ExactTier.
        decision = RouterDecision(
            intent="lookup", confidence=0.9, entities={"department": ["Sales"]}
        )
        tier = RouterTier(_FakeRouter(decision), exact)

        result = tier.search("Alice", QueryContext())
        assert result.tier_used == "router"
        # 2 / (1+2) = 0.666... → relevance from fulltext.
        assert result.relevance == pytest.approx(2.0 / 3.0)


class TestRouterTierAbstainsAndRoutes:
    def _make_tier(self, decision: RouterDecision) -> RouterTier:
        store = _mock_store()
        exact = ExactTier(store)
        return RouterTier(_FakeRouter(decision), exact)

    def test_search_intent_emits_route_to_hybrid(self) -> None:
        tier = self._make_tier(
            RouterDecision(intent="search", confidence=0.9, entities={})
        )
        result = tier.search("VPN issues last quarter for the EU team", QueryContext())
        assert result.tier_used == "router"
        assert result.items == []
        assert result.relevance == 0.0
        assert result.route_to == "hybrid"

    def test_analytical_intent_emits_route_to_agentic(self) -> None:
        tier = self._make_tier(
            RouterDecision(intent="analytical", confidence=0.9, entities={})
        )
        result = tier.search("how many tickets per region last month", QueryContext())
        assert result.tier_used == "router"
        assert result.items == []
        assert result.relevance == 0.0
        assert result.route_to == "agentic"

    def test_ambiguous_intent_no_route_directive(self) -> None:
        tier = self._make_tier(
            RouterDecision(intent="ambiguous", confidence=0.9, entities={})
        )
        result = tier.search("weather", QueryContext())
        assert result.tier_used == "router"
        assert result.items == []
        assert result.relevance == 0.0
        assert result.route_to is None

    def test_low_confidence_overrides_to_ambiguous(self) -> None:
        # min_intent_conf default is 0.5; a 0.4 search confidence
        # should be downgraded to ambiguous (no `route_to`).
        store = _mock_store()
        exact = ExactTier(store)
        tier = RouterTier(
            _FakeRouter(
                RouterDecision(intent="search", confidence=0.4, entities={})
            ),
            exact,
            min_intent_conf=0.5,
        )
        result = tier.search("noisy query", QueryContext())
        assert result.tier_used == "router"
        assert result.items == []
        assert result.route_to is None

    def test_custom_routing_table_override(self) -> None:
        store = _mock_store()
        exact = ExactTier(store)
        tier = RouterTier(
            _FakeRouter(RouterDecision(intent="search", confidence=0.9, entities={})),
            exact,
            next_tier_for={"search": "rerank"},
        )
        result = tier.search("anything", QueryContext())
        assert result.route_to == "rerank"


class TestRouterTierInputValidation:
    def test_rejects_non_string_query(self) -> None:
        store = _mock_store()
        exact = ExactTier(store)
        tier = RouterTier(StubEntityRouter(), exact)
        with pytest.raises(TypeError, match="query must be str"):
            tier.search(42, QueryContext())  # type: ignore[arg-type]

    def test_rejects_empty_query(self) -> None:
        store = _mock_store()
        exact = ExactTier(store)
        tier = RouterTier(StubEntityRouter(), exact)
        with pytest.raises(ValueError, match="non-empty"):
            tier.search("   ", QueryContext())


# ---------------------------------------------------------------------------
# Cascade integration: orchestrator honors router's route_to directive
# ---------------------------------------------------------------------------


class TestRouterIntegrationWithOrchestrator:
    """End-to-end through the cascade: the orchestrator must honor
    `route_to` so a router-emitted directive jumps the cascade."""

    def test_router_routes_to_hybrid_skipping_agentic_slot(self) -> None:
        """With a cascade [exact, router, agentic, hybrid], a
        `search`-intent directive should jump to `hybrid` even though
        `agentic` is registered earlier in the next-tier slot."""
        from backend.retrieval import (
            CascadeOrchestrator,
            Hit,
            QueryResult,
            StubTier,
            Tier,
            TierConfig,
        )

        class _OneHitTier(Tier):
            def __init__(self, name: str, relevance: float) -> None:
                self._name = name
                self._relevance = relevance
                self.calls = 0

            @property
            def name(self) -> str:
                return self._name

            def search(self, query: str, ctx: QueryContext) -> QueryResult:
                self.calls += 1
                return QueryResult(
                    answer=None,
                    items=[
                        Hit(kind="node", id=f"{self._name}-1", score=self._relevance, preview="x")
                    ],
                    citations=[],
                    tier_used=self._name,
                    relevance=self._relevance,
                    latency_ms=0,
                )

        store = _mock_store()
        # ExactTier misses (empty fulltext, no id token in the long
        # query) → relevance 0.0 → escalates to router.
        _attach_session(store, run_results=[])
        exact = ExactTier(store)
        # Router returns search → route_to="hybrid".
        router = RouterTier(
            _FakeRouter(RouterDecision(intent="search", confidence=0.9, entities={})),
            exact,
        )
        agentic = _OneHitTier("agentic", relevance=1.0)
        hybrid = _OneHitTier("hybrid", relevance=1.0)

        orch = CascadeOrchestrator(
            tiers=[exact, router, agentic, hybrid, StubTier(name="stub")],
            configs=[
                TierConfig(name="exact", escalate_below=0.5),
                TierConfig(name="router", escalate_below=0.5),
                TierConfig(name="agentic", escalate_below=0.5),
                TierConfig(name="hybrid", escalate_below=0.3),
                TierConfig(name="stub", escalate_below=0.0),
            ],
        )
        result = orch.run(
            "this is a long natural language semantic query", QueryContext()
        )
        assert result.tier_used == "hybrid"
        # Agentic must have been skipped over.
        assert agentic.calls == 0
        assert hybrid.calls == 1

    def test_router_lookup_terminates_cascade(self) -> None:
        """`lookup` intent: router delegates to ExactTier, returns
        relevance=1.0 — orchestrator must stop, never reaching hybrid."""
        from backend.retrieval import (
            CascadeOrchestrator,
            QueryResult,
            StubTier,
            Tier,
            TierConfig,
        )

        store = _mock_store()
        # First call: ExactTier (cascade slot) misses on the long
        # natural-language query (no id-token, > 4 tokens → fulltext
        # branch is skipped). Second call: ExactTier called again from
        # inside RouterTier, this time on the forwarded id-only query
        # — finds emp_1002.
        _attach_session(
            store,
            run_results=[
                # RouterTier delegates to ExactTier with "emp_1002" → id hit.
                [{"id": "emp_1002", "attrs": json.dumps({"name": "Alice"})}],
            ],
        )
        store._provenance_for_node.return_value = []
        exact = ExactTier(store)
        router = RouterTier(
            _FakeRouter(
                RouterDecision(
                    intent="lookup", confidence=0.95, entities={"emp_id": ["emp_1002"]}
                )
            ),
            exact,
        )

        class _UnreachableTier(Tier):
            def __init__(self, name: str) -> None:
                self._name = name
                self.calls = 0

            @property
            def name(self) -> str:
                return self._name

            def search(self, query: str, ctx: QueryContext) -> QueryResult:
                self.calls += 1
                return QueryResult(
                    answer=None,
                    items=[],
                    citations=[],
                    tier_used=self._name,
                    relevance=0.0,
                    latency_ms=0,
                )

        hybrid = _UnreachableTier("hybrid")

        orch = CascadeOrchestrator(
            # Note: no `exact` slot first this time — we want the
            # router to be the first thing that runs, so the test
            # assertion is unambiguous about who produced the result.
            tiers=[router, hybrid, StubTier(name="stub")],
            configs=[
                TierConfig(name="router", escalate_below=0.5),
                TierConfig(name="hybrid", escalate_below=0.3),
                TierConfig(name="stub", escalate_below=0.0),
            ],
        )
        result = orch.run("anything", QueryContext())
        assert result.tier_used == "router"
        assert result.relevance == 1.0
        assert hybrid.calls == 0


# ---------------------------------------------------------------------------
# GLiNER2 output parser (pure-function, no model needed)
# ---------------------------------------------------------------------------


class TestParseGLiNER2Output:
    def test_parses_minimal_valid(self) -> None:
        raw = {
            "classifications": {"intent": [{"label": "lookup", "score": 0.92}]},
            "entities": [{"label": "emp_id", "text": "emp_1002", "score": 0.99}],
        }
        d = _parse_gliner2_output(raw)
        assert d.intent == "lookup"
        assert d.confidence == pytest.approx(0.92)
        assert d.entities == {"emp_id": ["emp_1002"]}

    def test_drops_unknown_entity_label(self) -> None:
        raw = {
            "classifications": {"intent": [{"label": "search", "score": 0.7}]},
            "entities": [
                {"label": "emp_id", "text": "emp_1", "score": 0.9},
                {"label": "weirdo", "text": "ignored", "score": 0.9},
            ],
        }
        d = _parse_gliner2_output(raw)
        assert d.entities == {"emp_id": ["emp_1"]}

    def test_empty_entities_ok(self) -> None:
        raw = {
            "classifications": {"intent": [{"label": "ambiguous", "score": 0.55}]},
            "entities": [],
        }
        d = _parse_gliner2_output(raw)
        assert d.entities == {}

    def test_missing_entities_key_ok(self) -> None:
        raw = {
            "classifications": {"intent": [{"label": "ambiguous", "score": 0.55}]},
        }
        d = _parse_gliner2_output(raw)
        assert d.entities == {}

    def test_rejects_unknown_intent(self) -> None:
        raw = {
            "classifications": {"intent": [{"label": "nonsense", "score": 0.9}]},
            "entities": [],
        }
        with pytest.raises(RuntimeError, match="unknown intent"):
            _parse_gliner2_output(raw)

    def test_rejects_non_dict_root(self) -> None:
        with pytest.raises(RuntimeError, match="non-dict result"):
            _parse_gliner2_output("not a dict")

    def test_rejects_missing_classifications(self) -> None:
        with pytest.raises(RuntimeError, match="missing 'classifications'"):
            _parse_gliner2_output({"entities": []})

    def test_rejects_empty_intent_list(self) -> None:
        with pytest.raises(RuntimeError, match="missing non-empty 'intent'"):
            _parse_gliner2_output({"classifications": {"intent": []}, "entities": []})


class TestEntityTypesAndIntentsFrozen:
    """The Pioneer.ai-fine-tune was trained against a fixed schema. If
    these change, you must re-train (and update `pioneer/prompt.md`).
    Pin the schema by tests so a future refactor cannot silently drift."""

    def test_intents_frozen(self) -> None:
        assert INTENTS == ("lookup", "search", "analytical", "ambiguous")

    def test_entity_types_frozen(self) -> None:
        assert ENTITY_TYPES == (
            "emp_id",
            "customer_id",
            "ticket_id",
            "date",
            "department",
            "product",
        )


# ---------------------------------------------------------------------------
# Integration tests — require GLINER2_MODEL_PATH / PIONEER_AI_MODEL_ID
# ---------------------------------------------------------------------------

gliner2_integration = pytest.mark.skipif(
    not (
        os.environ.get(GLiNER2EntityRouter.MODEL_PATH_ENV)
        or os.environ.get(GLiNER2EntityRouter.MODEL_ID_ENV)
    ),
    reason=(
        f"set {GLiNER2EntityRouter.MODEL_PATH_ENV} or "
        f"{GLiNER2EntityRouter.MODEL_ID_ENV} to run GLiNER2 integration tests"
    ),
)


@gliner2_integration
class TestGLiNER2EntityRouterIntegration:
    def test_constructor_loads_with_env(self) -> None:
        # Smoke test: just constructing must not raise when env var is set.
        GLiNER2EntityRouter()

    def test_classify_emp_id_query(self) -> None:
        r = GLiNER2EntityRouter()
        d = r.classify("send a message about emp_1002 to the team")
        assert d.intent in INTENTS
        # The fine-tuned model SHOULD identify emp_1002 as an emp_id
        # span on a lookup-intent query. If it doesn't, the fine-tune
        # needs more training data — see pioneer/README.md.
        if d.intent == "lookup":
            assert "emp_1002" in d.entities.get("emp_id", [])


class TestGLiNER2EntityRouterConstructorWithoutEnv:
    def test_raises_without_path_or_endpoint(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(GLiNER2EntityRouter.MODEL_PATH_ENV, raising=False)
        monkeypatch.delenv(GLiNER2EntityRouter.MODEL_ID_ENV, raising=False)
        with pytest.raises(RuntimeError, match="requires either a local weights path"):
            GLiNER2EntityRouter()
