"""Tests for `AgenticTier`, `LLMClient` Protocol, and the loop driver.

Unit tests use `StubLLMClient` (scripted, no network) and a
`MagicMock(spec=GraphStore)`. Integration tests gated on
`GEMINI_API_KEY` exercise the live Gemini Flash 2.5 backend.
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest

from backend.graph.store import GraphStore
from backend.models.graph import FactConfidence, GraphEdge, GraphNode, Provenance
from backend.retrieval import (
    AgenticTier,
    GeminiLLMClient,
    LLMTurn,
    NoopLLMClient,
    QueryContext,
    StubEmbedder,
    StubLLMClient,
    ToolCall,
    ToolResult,
)
from backend.retrieval.agentic import (
    RELEVANCE_FAILED,
    RELEVANCE_GROUNDED,
    RELEVANCE_UNGROUNDED,
)


# ---------------------------------------------------------------------------
# LLMTurn dataclass invariants
# ---------------------------------------------------------------------------


class TestLLMTurn:
    def test_text_only_ok(self) -> None:
        t = LLMTurn(text="final answer", tool_calls=[])
        assert t.text == "final answer"
        assert t.tool_calls == []

    def test_tool_calls_only_ok(self) -> None:
        t = LLMTurn(
            text=None,
            tool_calls=[ToolCall(name="get_node", args={"node_id": "x"})],
        )
        assert t.text is None
        assert len(t.tool_calls) == 1

    def test_empty_text_only_treated_as_no_text(self) -> None:
        # An empty/whitespace string is *not* a valid final answer; it
        # must come paired with at least one tool call.
        with pytest.raises(ValueError, match="at least one of"):
            LLMTurn(text="   ", tool_calls=[])

    def test_both_rejected(self) -> None:
        with pytest.raises(ValueError, match="not both"):
            LLMTurn(
                text="hello",
                tool_calls=[ToolCall(name="get_node", args={"node_id": "x"})],
            )

    def test_neither_rejected(self) -> None:
        with pytest.raises(ValueError, match="at least one of"):
            LLMTurn(text=None, tool_calls=[])


# ---------------------------------------------------------------------------
# StubLLMClient script-driver
# ---------------------------------------------------------------------------


class TestStubLLMClient:
    def test_rejects_empty_script(self) -> None:
        with pytest.raises(ValueError, match="at least one scripted"):
            StubLLMClient(scripted_turns=[])

    def test_pops_in_order(self) -> None:
        s = StubLLMClient(
            scripted_turns=[
                LLMTurn(text="first", tool_calls=[]),
                LLMTurn(text="second", tool_calls=[]),
            ]
        )
        t1 = s.start(system_prompt="sp", user_query="q", tools=[])
        t2 = s.respond_to_tool_results([])
        assert t1.text == "first"
        assert t2.text == "second"

    def test_exhaustion_raises(self) -> None:
        s = StubLLMClient(
            scripted_turns=[LLMTurn(text="only", tool_calls=[])]
        )
        s.start(system_prompt="sp", user_query="q", tools=[])
        with pytest.raises(RuntimeError, match="script exhausted"):
            s.respond_to_tool_results([])

    def test_records_calls(self) -> None:
        s = StubLLMClient(
            scripted_turns=[LLMTurn(text="ans", tool_calls=[])]
        )
        s.start(system_prompt="sp", user_query="hello", tools=[])
        assert s.starts_received == [("sp", "hello", ())]


# ---------------------------------------------------------------------------
# NoopLLMClient — reusable across queries
# ---------------------------------------------------------------------------


class TestNoopLLMClient:
    def test_default_text(self) -> None:
        n = NoopLLMClient()
        t = n.start(system_prompt="sp", user_query="q", tools=[])
        assert "not configured" in (t.text or "")

    def test_reusable_across_calls(self) -> None:
        n = NoopLLMClient()
        for _ in range(3):
            assert n.start(system_prompt="sp", user_query="q", tools=[]).text


# ---------------------------------------------------------------------------
# AgenticTier construction
# ---------------------------------------------------------------------------


def _mock_store() -> MagicMock:
    store = MagicMock(spec=GraphStore)
    store._driver = MagicMock()
    store._database = "neo4j"
    return store


def _stub_llm(turns: list[LLMTurn]) -> StubLLMClient:
    return StubLLMClient(scripted_turns=turns)


class TestAgenticTierConstruction:
    def test_rejects_non_store(self) -> None:
        with pytest.raises(TypeError, match="store must be GraphStore"):
            AgenticTier(
                "x",  # type: ignore[arg-type]
                StubEmbedder(),
                _stub_llm([LLMTurn(text="ok", tool_calls=[])]),
            )

    def test_rejects_non_embedder(self) -> None:
        with pytest.raises(TypeError, match="Embedder protocol"):
            AgenticTier(
                _mock_store(),
                "no",  # type: ignore[arg-type]
                _stub_llm([LLMTurn(text="ok", tool_calls=[])]),
            )

    def test_rejects_non_llm(self) -> None:
        with pytest.raises(TypeError, match="LLMClient"):
            AgenticTier(
                _mock_store(),
                StubEmbedder(),
                "not an llm",  # type: ignore[arg-type]
            )

    def test_rejects_uppercase_name(self) -> None:
        with pytest.raises(ValueError, match="lowercase identifier"):
            AgenticTier(
                _mock_store(),
                StubEmbedder(),
                _stub_llm([LLMTurn(text="ok", tool_calls=[])]),
                name="AGENTIC",
            )

    def test_rejects_zero_max_iterations(self) -> None:
        with pytest.raises(ValueError, match="max_iterations must be >= 1"):
            AgenticTier(
                _mock_store(),
                StubEmbedder(),
                _stub_llm([LLMTurn(text="ok", tool_calls=[])]),
                max_iterations=0,
            )

    def test_rejects_zero_budget(self) -> None:
        with pytest.raises(ValueError, match="wall_clock_budget_ms"):
            AgenticTier(
                _mock_store(),
                StubEmbedder(),
                _stub_llm([LLMTurn(text="ok", tool_calls=[])]),
                wall_clock_budget_ms=0,
            )

    def test_default_name(self) -> None:
        tier = AgenticTier(
            _mock_store(),
            StubEmbedder(),
            _stub_llm([LLMTurn(text="ok", tool_calls=[])]),
        )
        assert tier.name == "agentic"


# ---------------------------------------------------------------------------
# AgenticTier query input validation
# ---------------------------------------------------------------------------


class TestAgenticTierInputValidation:
    def _tier(self) -> AgenticTier:
        return AgenticTier(
            _mock_store(),
            StubEmbedder(),
            _stub_llm([LLMTurn(text="ok", tool_calls=[])]),
        )

    def test_non_string(self) -> None:
        with pytest.raises(TypeError, match="query must be str"):
            self._tier().search(42, QueryContext())  # type: ignore[arg-type]

    def test_empty_string(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            self._tier().search("   ", QueryContext())


# ---------------------------------------------------------------------------
# Loop behavior: zero-tool answer (ungrounded)
# ---------------------------------------------------------------------------


class TestAgenticLoopUngrounded:
    def test_immediate_answer_no_tools_is_ungrounded(self) -> None:
        store = _mock_store()
        tier = AgenticTier(
            store,
            StubEmbedder(),
            _stub_llm([LLMTurn(text="42 is the answer", tool_calls=[])]),
        )
        result = tier.search("what is 6 times 7", QueryContext())
        assert result.tier_used == "agentic"
        assert result.answer == "42 is the answer"
        assert result.relevance == RELEVANCE_UNGROUNDED
        assert result.items[0].score == RELEVANCE_UNGROUNDED
        assert result.citations == []


# ---------------------------------------------------------------------------
# Loop behavior: tool call -> answer (grounded)
# ---------------------------------------------------------------------------


class TestAgenticLoopGrounded:
    def test_one_tool_call_then_answer(self) -> None:
        store = _mock_store()
        node = GraphNode(
            id="emp_1002",
            type="Person",
            attributes={"name": "Alice"},
            provenance=[
                Provenance(
                    source_file="HR/employees.json",
                    source_record_id="row:0",
                    source_field="name",
                    extraction_method="direct_mapping",
                    extraction_model="rule:hr_v1",
                    confidence=FactConfidence.EXACT,
                    raw_value="Alice",
                )
            ],
        )
        store.get_node.return_value = node
        store._provenance_for_node.return_value = node.provenance

        script = [
            LLMTurn(
                text=None,
                tool_calls=[ToolCall(name="get_node", args={"node_id": "emp_1002"})],
            ),
            LLMTurn(text="emp_1002 is Alice", tool_calls=[]),
        ]
        tier = AgenticTier(store, StubEmbedder(), _stub_llm(script))
        result = tier.search("who is emp_1002", QueryContext())
        assert result.tier_used == "agentic"
        assert result.answer == "emp_1002 is Alice"
        assert result.relevance == RELEVANCE_GROUNDED
        assert len(result.citations) == 1
        assert result.citations[0].source_field == "name"

    def test_pattern_query_multi_hop_grounded(self) -> None:
        """Issue acceptance criterion 1: multi-hop query lands an answer
        with >= 1 citation."""
        store = _mock_store()
        person = GraphNode(
            id="emp_1002",
            type="Person",
            attributes={"name": "Alice"},
            provenance=[
                Provenance(
                    source_file="HR/employees.json",
                    source_record_id="row:0",
                    source_field="name",
                    extraction_method="direct_mapping",
                    extraction_model="rule:hr_v1",
                    confidence=FactConfidence.EXACT,
                    raw_value="Alice",
                )
            ],
        )
        msg = GraphNode(
            id="msg_1",
            type="Message",
            attributes={"subject": "support case 42"},
            provenance=[],
        )
        edge = GraphEdge(
            source_node_id="emp_1002",
            target_node_id="msg_1",
            relation_type="ASSIGNED_TO",
        )
        store.pattern_query.return_value = ([(person, edge, msg)], 1)
        store._provenance_for_node.side_effect = lambda nid: (
            person.provenance if nid == "emp_1002" else []
        )

        script = [
            LLMTurn(
                text=None,
                tool_calls=[
                    ToolCall(
                        name="pattern_query",
                        args={
                            "src_type": "Person",
                            "rel_type": "ASSIGNED_TO",
                            "tgt_type": "Message",
                            "limit": 5,
                        },
                    )
                ],
            ),
            LLMTurn(
                text="emp_1002 (Alice) is assigned to msg_1 (support case 42).",
                tool_calls=[],
            ),
        ]
        tier = AgenticTier(store, StubEmbedder(), _stub_llm(script))
        result = tier.search(
            "which messages is emp_1002 assigned to", QueryContext()
        )
        assert result.relevance == RELEVANCE_GROUNDED
        assert len(result.citations) >= 1


# ---------------------------------------------------------------------------
# Loop behavior: iteration cap
# ---------------------------------------------------------------------------


class TestAgenticLoopIterationCap:
    def test_max_iterations_overshoot_returns_failed(self) -> None:
        """Issue acceptance criterion 2: synthetic query forcing >6 calls
        aborts cleanly with `confidence=0.0`."""
        store = _mock_store()
        node = GraphNode(id="emp_1002", type="Person", attributes={"name": "A"})
        store.get_node.return_value = node
        store._provenance_for_node.return_value = []

        # 7 tool-call turns (the cap is 6) — the 7th should trigger the
        # overshoot branch BEFORE the model gets to dispatch it.
        tool_call_turn = LLMTurn(
            text=None,
            tool_calls=[ToolCall(name="get_node", args={"node_id": "emp_1002"})],
        )
        script = [tool_call_turn for _ in range(7)]
        tier = AgenticTier(
            store,
            StubEmbedder(),
            _stub_llm(script),
            max_iterations=6,
        )
        result = tier.search("loop forever", QueryContext())
        assert result.tier_used == "agentic"
        assert result.relevance == RELEVANCE_FAILED
        assert result.answer is None

    def test_one_iteration_cap(self) -> None:
        store = _mock_store()
        node = GraphNode(id="emp_1002", type="Person", attributes={})
        store.get_node.return_value = node
        store._provenance_for_node.return_value = []
        script = [
            LLMTurn(
                text=None,
                tool_calls=[ToolCall(name="get_node", args={"node_id": "emp_1002"})],
            ),
            LLMTurn(
                text=None,
                tool_calls=[ToolCall(name="get_node", args={"node_id": "emp_1002"})],
            ),
        ]
        tier = AgenticTier(
            store, StubEmbedder(), _stub_llm(script), max_iterations=1
        )
        result = tier.search("anything", QueryContext())
        assert result.relevance == RELEVANCE_FAILED


# ---------------------------------------------------------------------------
# Tool-call error surfaces back to the model (acceptance criterion 3)
# ---------------------------------------------------------------------------


class TestAgenticToolErrorPassthrough:
    def test_invalid_pattern_surfaces_error_no_crash(self) -> None:
        """Issue acceptance criterion 3: pattern_query with unknown
        node/relation type → tool surfaces validation error to model,
        no crash."""
        store = _mock_store()
        # First call: unknown relation → validation error.
        # Second turn: model recovers by issuing a final answer.
        script = [
            LLMTurn(
                text=None,
                tool_calls=[
                    ToolCall(
                        name="pattern_query",
                        args={
                            "src_type": "Person",
                            "rel_type": "TOTALLY_FAKE_REL",
                            "tgt_type": "Message",
                        },
                    )
                ],
            ),
            LLMTurn(
                text="I tried but the relation does not exist.",
                tool_calls=[],
            ),
        ]
        tier = AgenticTier(store, StubEmbedder(), _stub_llm(script))
        result = tier.search("foo", QueryContext())
        # Did not crash; ungrounded prose answer (no citations were
        # collected because the only tool call failed validation).
        assert result.tier_used == "agentic"
        assert result.relevance == RELEVANCE_UNGROUNDED
        # Look at what the LLM stub *received* on its second turn —
        # the error message must have been forwarded.
        stub: StubLLMClient = tier._llm  # type: ignore[assignment]
        assert len(stub.calls_received) == 1
        forwarded = stub.calls_received[0]
        assert len(forwarded) == 1
        assert isinstance(forwarded[0], ToolResult)
        assert "error" in (forwarded[0].content or {})

    def test_unknown_tool_name_surfaces_error(self) -> None:
        store = _mock_store()
        script = [
            LLMTurn(
                text=None,
                tool_calls=[ToolCall(name="not_a_tool", args={})],
            ),
            LLMTurn(text="recovered answer", tool_calls=[]),
        ]
        tier = AgenticTier(store, StubEmbedder(), _stub_llm(script))
        result = tier.search("foo", QueryContext())
        assert result.relevance == RELEVANCE_UNGROUNDED
        stub: StubLLMClient = tier._llm  # type: ignore[assignment]
        assert "error" in (stub.calls_received[0][0].content or {})


# ---------------------------------------------------------------------------
# LLM client failure → fail_result
# ---------------------------------------------------------------------------


class TestAgenticLLMFailure:
    def test_start_failure_raises(self) -> None:
        # Fail-fast: an LLM-client failure on `start()` (e.g. bad
        # GEMINI_API_KEY, network outage) must surface as RuntimeError so
        # the orchestrator returns a 500 with a useful message — never
        # be silently masked as relevance=0.0 (which is indistinguishable
        # from "no relevant context found").
        class _BoomLLM(StubLLMClient):
            def start(self, **kwargs):  # type: ignore[no-untyped-def]
                raise RuntimeError("network down")

        tier = AgenticTier(
            _mock_store(),
            StubEmbedder(),
            _BoomLLM(scripted_turns=[LLMTurn(text="placeholder", tool_calls=[])]),
        )
        with pytest.raises(RuntimeError, match="agentic LLM call failed"):
            tier.search("foo", QueryContext())


# ---------------------------------------------------------------------------
# GeminiLLMClient construction guards (no network)
# ---------------------------------------------------------------------------


class TestGeminiLLMClientConstruction:
    def test_raises_without_api_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(GeminiLLMClient.API_KEY_ENV, raising=False)
        with pytest.raises(RuntimeError, match="requires"):
            GeminiLLMClient()

    def test_explicit_api_key_does_not_require_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(GeminiLLMClient.API_KEY_ENV, raising=False)
        # Construction should reach the SDK import — which is present
        # in this project's deps. We pass a fake key; we never actually
        # call out to the API.
        c = GeminiLLMClient(api_key="fake-not-used")
        assert c is not None


# ---------------------------------------------------------------------------
# Cascade integration: route_to="agentic" hits AgenticTier
# ---------------------------------------------------------------------------


class TestAgenticInCascade:
    """End-to-end: a router-emitted `route_to="agentic"` directive must
    land on an AgenticTier registered with `name="agentic"`."""

    def test_router_routes_analytical_to_agentic_grounded(self) -> None:
        """When the agent grounds its answer (relevance >= escalate_below
        floor), the cascade terminates at AgenticTier — proving the
        `route_to` directive landed on the right tier."""
        from backend.retrieval import (
            CascadeOrchestrator,
            ExactTier,
            QueryResult,
            RouterDecision,
            RouterTier,
            StubTier,
            Tier,
            TierConfig,
        )

        class _AnalyticalRouter:
            def classify(self, query: str) -> RouterDecision:
                return RouterDecision(
                    intent="analytical", confidence=0.9, entities={}
                )

        store = _mock_store()
        # ExactTier session: empty → relevance 0.0 → escalates.
        sess = MagicMock()
        sess.run.return_value = []
        cm = MagicMock()
        cm.__enter__.return_value = sess
        cm.__exit__.return_value = None
        store._session.return_value = cm
        node = GraphNode(
            id="emp_1002",
            type="Person",
            attributes={"name": "Alice"},
            provenance=[
                Provenance(
                    source_file="HR/employees.json",
                    source_record_id="row:0",
                    source_field="name",
                    extraction_method="direct_mapping",
                    extraction_model="rule:hr_v1",
                    confidence=FactConfidence.EXACT,
                    raw_value="Alice",
                )
            ],
        )
        store.get_node.return_value = node
        store._provenance_for_node.return_value = node.provenance

        exact = ExactTier(store)
        router = RouterTier(_AnalyticalRouter(), exact)
        # Scripted: tool call to ground the answer → final answer.
        agentic = AgenticTier(
            store,
            StubEmbedder(),
            _stub_llm(
                [
                    LLMTurn(
                        text=None,
                        tool_calls=[
                            ToolCall(
                                name="get_node",
                                args={"node_id": "emp_1002"},
                            )
                        ],
                    ),
                    LLMTurn(
                        text="grounded analytical answer",
                        tool_calls=[],
                    ),
                ]
            ),
        )

        class _HybridProbe(Tier):
            def __init__(self) -> None:
                self.calls = 0

            @property
            def name(self) -> str:
                return "hybrid"

            def search(self, query: str, ctx: QueryContext) -> QueryResult:
                self.calls += 1
                return QueryResult(
                    answer=None,
                    items=[],
                    citations=[],
                    tier_used="hybrid",
                    relevance=0.0,
                    latency_ms=0,
                )

        hybrid = _HybridProbe()
        orch = CascadeOrchestrator(
            tiers=[exact, router, hybrid, agentic, StubTier(name="stub")],
            configs=[
                TierConfig(name="exact", escalate_below=0.5),
                TierConfig(name="router", escalate_below=0.5),
                TierConfig(name="hybrid", escalate_below=0.3),
                TierConfig(name="agentic", escalate_below=0.5),
                TierConfig(name="stub", escalate_below=0.0),
            ],
        )
        result = orch.run(
            "how many tickets did we close last week", QueryContext()
        )
        # Agentic's grounded answer (0.7) clears its 0.5 floor so the
        # cascade terminates here. Hybrid was skipped via the route_to
        # directive AND the early termination.
        assert result.tier_used == "agentic"
        assert result.relevance == RELEVANCE_GROUNDED
        assert hybrid.calls == 0


# ---------------------------------------------------------------------------
# Integration tests — require GEMINI_API_KEY
# ---------------------------------------------------------------------------


gemini_integration = pytest.mark.skipif(
    not os.environ.get(GeminiLLMClient.API_KEY_ENV),
    reason=(
        f"set {GeminiLLMClient.API_KEY_ENV} to run AgenticTier "
        "Gemini integration tests"
    ),
)


@gemini_integration
class TestAgenticTierGeminiIntegration:
    def test_constructor_loads_with_env(self) -> None:
        GeminiLLMClient()
