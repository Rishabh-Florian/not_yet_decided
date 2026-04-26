"""Tests for the `thread-summary` workflow (issue #9).

Stubs HybridTier, the LLM, and the GraphStore so we exercise the
workflow's recipe end-to-end without Neo4j or network.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from backend.graph.store import GraphStore, SourceRecord
from backend.models.graph import FactConfidence, GraphNode, Provenance
from backend.retrieval import (
    Citation,
    Hit,
    QueryContext,
    QueryResult,
    StubLLMClient,
    Tier,
)
from backend.retrieval.agentic import LLMTurn, ToolCall
from backend.retrieval.workflows import (
    ThreadMessage,
    ThreadSummaryInput,
    ThreadSummaryWorkflow,
    TierRegistry,
    WorkflowInput,
    build_workflow,
    list_workflows,
    register_workflow,
)


# ---------- helpers ----------


class _ScriptedTier(Tier):
    """Tier double whose `search` returns one queued result per call."""

    def __init__(self, name: str, results: list[QueryResult]) -> None:
        self._name = name
        self._results = list(results)
        self.calls: list[str] = []

    @property
    def name(self) -> str:
        return self._name

    def search(self, query: str, ctx: QueryContext) -> QueryResult:
        self.calls.append(query)
        if not self._results:
            raise RuntimeError(
                f"{self._name} tier exhausted scripted results; the test "
                "did not queue enough"
            )
        return self._results.pop(0)


def _hit(node_id: str, score: float, preview: str = "...") -> Hit:
    return Hit(kind="node", id=node_id, score=score, preview=preview)


def _citation(field: str, raw: str = "x", source_file: str = "convs.json") -> Citation:
    return Citation(
        source_file=source_file,
        source_record_id=f"rec:{field}",
        source_field=field,
        raw_value=raw,
        extraction_method="direct_mapping",
    )


def _result_with_hits(
    name: str,
    hits: list[Hit],
    citations: list[Citation] | None = None,
    relevance: float | None = None,
) -> QueryResult:
    return QueryResult(
        answer=None,
        items=hits,
        citations=citations or [],
        tier_used=name,
        relevance=relevance if relevance is not None else (hits[0].score if hits else 0.0),
        latency_ms=0,
    )


def _node(node_id: str, node_type: str, **attrs: Any) -> GraphNode:
    return GraphNode(id=node_id, type=node_type, attributes=attrs)


def _provenance(node_id: str, field: str) -> Provenance:
    return Provenance(
        source_file="convs.json",
        source_record_id=f"rec:{node_id}:{field}",
        source_field=field,
        extraction_method="direct_mapping",
        extraction_model="rule:test",
        confidence=FactConfidence.EXACT,
        raw_value=f"{node_id}:{field}",
    )


def _stub_store(
    *,
    nodes: dict[str, GraphNode] | None = None,
    neighbors: dict[str, list[str]] | None = None,
    provenance: dict[str, list[Provenance]] | None = None,
    source_records: dict[tuple[str, str], SourceRecord] | None = None,
) -> GraphStore:
    store = MagicMock(spec=GraphStore)
    nd = nodes or {}
    nb = neighbors or {}
    pv = provenance or {}
    sr = source_records or {}
    store.get_node.side_effect = lambda nid: nd.get(nid)
    store.neighbors.side_effect = lambda nid, rel=None, depth=1: set(nb.get(nid, []))
    store._provenance_for_node.side_effect = lambda nid: list(pv.get(nid, []))
    store.get_source_record.side_effect = lambda sf, rid: sr.get((sf, rid))
    return store


def _msg(author: str, text: str, ts: str = "2024-01-01T10:00:00Z") -> dict[str, str]:
    return {"author": author, "ts": ts, "text": text}


def _final_answer_turn(text: str) -> LLMTurn:
    return LLMTurn(text=text, tool_calls=[])


def _tool_call_turn(name: str, args: dict[str, Any]) -> LLMTurn:
    return LLMTurn(text=None, tool_calls=[ToolCall(name=name, args=args)])


# ---------- registration / contract ----------


class TestRegistration:
    def test_workflow_registered_on_package_import(self) -> None:
        if "thread-summary" not in list_workflows():
            register_workflow(ThreadSummaryWorkflow)
        from backend.retrieval.workflows import get_workflow

        assert "thread-summary" in list_workflows()
        assert get_workflow("thread-summary") is ThreadSummaryWorkflow

    def test_class_metadata(self) -> None:
        # Issue: Skip T1 entirely, T4 is invoked through workflow's own
        # LLM driver (not via cascade AgenticTier), so allowed_tiers is
        # exactly {hybrid}.
        assert ThreadSummaryWorkflow.name == "thread-summary"
        assert ThreadSummaryWorkflow.allowed_tiers == frozenset({"hybrid"})
        assert "exact" not in ThreadSummaryWorkflow.allowed_tiers
        assert "agentic" not in ThreadSummaryWorkflow.allowed_tiers


# ---------- happy path ----------


class TestHappyPath:
    def _build(
        self,
        *,
        hybrid_results: list[QueryResult],
        scripted_turns: list[LLMTurn],
        nodes: dict[str, GraphNode] | None = None,
        neighbors: dict[str, list[str]] | None = None,
        provenance: dict[str, list[Provenance]] | None = None,
        source_records: dict[tuple[str, str], SourceRecord] | None = None,
    ) -> tuple[ThreadSummaryWorkflow, _ScriptedTier, StubLLMClient]:
        hybrid_tier = _ScriptedTier("hybrid", hybrid_results)
        store = _stub_store(
            nodes=nodes,
            neighbors=neighbors,
            provenance=provenance,
            source_records=source_records,
        )
        llm = StubLLMClient(scripted_turns=scripted_turns)
        registry = TierRegistry(
            {"hybrid": hybrid_tier},
            ThreadSummaryWorkflow.allowed_tiers,
        )
        wf = ThreadSummaryWorkflow(registry, llm=llm, store=store)
        return wf, hybrid_tier, llm

    def test_slack_thread_with_id_tokens_and_product(self) -> None:
        # Issue acceptance: Slack thread referencing emp_ids + a product
        # → summary mentions both with citations.
        emp_hit = _hit("emp_0436", 0.92, preview="Person: Surya Reddy")
        product_hit = _hit("prod_widget", 0.81, preview="Product: Widget Pro")
        # First HybridTier call is for the participant "raj@acme.com";
        # second/third are for the id tokens emp_0436 / prod_widget
        # extracted from the message text by the regex NER. Each call
        # returns its own hit list (cluster aggregator dedups by id and
        # keeps the best score).
        cluster_results = [
            _result_with_hits(
                "hybrid",
                [emp_hit, product_hit],
                citations=[_citation("name", "Surya Reddy")],
            ),
            _result_with_hits(
                "hybrid",
                [emp_hit],
                citations=[],
            ),
            _result_with_hits(
                "hybrid",
                [product_hit],
                citations=[],
            ),
        ]
        scripted = [
            _final_answer_turn(
                "## Gist\n"
                "Surya raised a question about Widget Pro.\n\n"
                "## Decisions / Action items\n"
                "* Follow up with Surya on the Widget Pro pricing [node:emp_0436]\n"
                "\n"
                "## Open questions\n"
                "* What's the final discount?\n"
                "\n"
                "## Linked entities\n"
                "* Surya Reddy [node:emp_0436]\n"
                "* Widget Pro [node:prod_widget]\n"
            ),
        ]
        wf, hybrid, llm = self._build(
            hybrid_results=cluster_results,
            scripted_turns=scripted,
            provenance={
                "emp_0436": [_provenance("emp_0436", "name")],
                "prod_widget": [_provenance("prod_widget", "name")],
            },
        )

        out = wf.run(
            WorkflowInput(
                payload={
                    "kind": "slack",
                    "participants": ["raj@acme.com"],
                    "messages": [
                        _msg(
                            "raj@acme.com",
                            "Hey, can someone from emp_0436 weigh in on prod_widget pricing?",
                        ),
                        _msg(
                            "surya@acme.com",
                            "Looking into it — emp_0436 here.",
                        ),
                    ],
                }
            )
        )

        # Workflow contract.
        assert out.workflow == "thread-summary"
        assert out.tier_used == "hybrid"
        # Grounded result (>= 1 citation harvested) → relevance 0.7.
        assert out.relevance == 0.7
        # Summary mentions both entities (per issue acceptance).
        assert out.answer is not None
        assert "[node:emp_0436]" in out.answer
        assert "[node:prod_widget]" in out.answer
        # Items contain the cluster (deduped).
        item_ids = {h.id for h in out.items}
        assert {"emp_0436", "prod_widget"} <= item_ids
        # Citations populated and deduped.
        keys = [
            (c.source_file, c.source_record_id, c.source_field)
            for c in out.citations
        ]
        assert len(keys) == len(set(keys))
        # Extras: action items + linked entities extracted from summary.
        assert out.extras["kind"] == "slack"
        assert out.extras["tool_calls_used"] == 0
        assert any("Widget Pro pricing" in a for a in out.extras["action_items"])
        assert "emp_0436" in out.extras["linked_entity_ids"]
        assert "prod_widget" in out.extras["linked_entity_ids"]
        # HybridTier was queried once per participant + once per id token.
        # raj@acme.com / emp_0436 / prod_widget = 3 distinct queries.
        assert sorted(hybrid.calls) == sorted(
            ["raj@acme.com", "emp_0436", "prod_widget"]
        )
        # LLM was invoked once (final answer in turn 1, no tool calls).
        assert len(llm.starts_received) == 1
        assert llm.calls_received == []

    def test_loop_with_one_get_neighbors_call(self) -> None:
        """Loop dispatches a `get_neighbors` call, surfaces the result,
        then the LLM emits the final summary. Verifies neighbor result
        is properly forwarded and citations from the traversed neighbors
        land on the result.
        """
        emp_hit = _hit("emp_0436", 0.92, preview="Person: Surya")
        cluster_results = [
            _result_with_hits("hybrid", [emp_hit], citations=[]),
        ]
        neighbor_node = _node("ticket_42", "Event", title="VPN issue")
        scripted = [
            _tool_call_turn("get_neighbors", {"node_id": "emp_0436"}),
            _final_answer_turn(
                "## Gist\nA VPN issue was discussed.\n\n"
                "## Decisions / Action items\n"
                "* Resolve VPN issue [node:ticket_42]\n\n"
                "## Open questions\n"
                "* (none)\n\n"
                "## Linked entities\n"
                "* Ticket [node:ticket_42]\n"
            ),
        ]
        wf, _, llm = self._build(
            hybrid_results=cluster_results,
            scripted_turns=scripted,
            nodes={
                "emp_0436": _node("emp_0436", "Person", name="Surya"),
                "ticket_42": neighbor_node,
            },
            neighbors={"emp_0436": ["ticket_42"]},
            provenance={
                "emp_0436": [_provenance("emp_0436", "name")],
                "ticket_42": [_provenance("ticket_42", "title")],
            },
        )

        out = wf.run(
            WorkflowInput(
                payload={
                    "kind": "slack",
                    "participants": ["surya@acme.com"],
                    "messages": [_msg("surya", "what's up with the VPN?")],
                }
            )
        )

        assert out.relevance == 0.7
        assert out.extras["tool_calls_used"] == 1
        # Citation from the traversed neighbor must be present.
        assert any(
            c.source_record_id == "rec:ticket_42:title" for c in out.citations
        )
        # Tool call was forwarded to the LLM as a function response.
        assert len(llm.calls_received) == 1
        assert llm.calls_received[0][0].name == "get_neighbors"
        # Neighbor list inside the response includes ticket_42.
        forwarded = llm.calls_received[0][0].content
        assert isinstance(forwarded, dict)
        assert any(n["id"] == "ticket_42" for n in forwarded["neighbors"])

    def test_loop_with_get_node_and_get_source_record(self) -> None:
        emp_hit = _hit("emp_0436", 0.92, preview="Person: Surya")
        cluster_results = [
            _result_with_hits("hybrid", [emp_hit], citations=[]),
        ]
        from datetime import datetime, timezone

        rec = SourceRecord(
            source_file="convs.json",
            source_record_id="conv_1",
            raw_record={"text": "raw conversation text"},
            content_hash="abc",
            ingested_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )
        scripted = [
            _tool_call_turn("get_node", {"node_id": "emp_0436"}),
            _tool_call_turn(
                "get_source_record",
                {"source_file": "convs.json", "record_id": "conv_1"},
            ),
            _final_answer_turn(
                "## Gist\nGrounded.\n\n"
                "## Decisions / Action items\n"
                "* (none)\n\n"
                "## Open questions\n"
                "* (none)\n\n"
                "## Linked entities\n"
                "* Surya [node:emp_0436]\n"
            ),
        ]
        wf, _, llm = self._build(
            hybrid_results=cluster_results,
            scripted_turns=scripted,
            nodes={"emp_0436": _node("emp_0436", "Person", name="Surya")},
            provenance={"emp_0436": [_provenance("emp_0436", "name")]},
            source_records={("convs.json", "conv_1"): rec},
        )
        out = wf.run(
            WorkflowInput(
                payload={
                    "kind": "slack",
                    "participants": ["surya@acme.com"],
                    "messages": [_msg("surya", "hi")],
                }
            )
        )
        assert out.extras["tool_calls_used"] == 2
        # The whole-record citation lands.
        assert any(
            c.source_record_id == "conv_1" and c.source_field == "<whole_record>"
            for c in out.citations
        )

    def test_unknown_tool_name_returned_to_model_as_error(self) -> None:
        """An out-of-scope tool name should not crash the loop — the
        dispatcher raises, and the workflow surfaces the error to the
        next turn so the LLM can self-correct (mirrors AgenticTier
        contract). Final summary still produced.
        """
        emp_hit = _hit("emp_0436", 0.92, preview="Person: Surya")
        cluster_results = [
            _result_with_hits("hybrid", [emp_hit], citations=[]),
        ]
        scripted = [
            _tool_call_turn("pattern_query", {"src_type": "Person"}),
            _final_answer_turn(
                "## Gist\nRecovered.\n\n"
                "## Decisions / Action items\n"
                "* (none)\n\n"
                "## Open questions\n"
                "* (none)\n\n"
                "## Linked entities\n"
                "* (none)\n"
            ),
        ]
        wf, _, llm = self._build(
            hybrid_results=cluster_results,
            scripted_turns=scripted,
            provenance={"emp_0436": [_provenance("emp_0436", "name")]},
        )
        out = wf.run(
            WorkflowInput(
                payload={
                    "kind": "slack",
                    "participants": ["surya"],
                    "messages": [_msg("surya", "hi")],
                }
            )
        )
        # Loop completed; the workflow forwarded an error result to the
        # second turn rather than crashing.
        assert out.relevance == 0.7  # citations still present from T3
        assert llm.calls_received[0][0].name == "pattern_query"
        assert "error" in llm.calls_received[0][0].content


# ---------- empty thread short-circuit ----------


class TestEmptyThread:
    def test_empty_messages_returns_low_confidence_no_llm_call(self) -> None:
        # Issue acceptance: Empty thread → confidence=0.0, no LLM call.
        hybrid = _ScriptedTier("hybrid", [])  # must NOT be called
        store = _stub_store()
        llm = StubLLMClient(scripted_turns=[_final_answer_turn("nope")])
        registry = TierRegistry(
            {"hybrid": hybrid}, ThreadSummaryWorkflow.allowed_tiers
        )
        wf = ThreadSummaryWorkflow(registry, llm=llm, store=store)

        out = wf.run(
            WorkflowInput(
                payload={
                    "kind": "meeting",
                    "participants": ["a@x.com"],
                    "messages": [],
                }
            )
        )
        assert out.relevance == 0.0
        assert out.answer is None
        assert out.items == []
        assert out.citations == []
        assert out.extras["reason"] == "empty_thread"
        assert out.extras["kind"] == "meeting"
        assert llm.starts_received == []
        assert hybrid.calls == []


# ---------- tool-loop bounding ----------


class TestToolBudgetBounding:
    def test_budget_exceeded_returns_partial_with_relevance_zero(self) -> None:
        # Issue acceptance: Tool budget exceeded → returns partial summary
        # with reduced confidence (no crash).
        emp_hit = _hit("emp_0436", 0.92, preview="Person")
        cluster = [_result_with_hits("hybrid", [emp_hit], citations=[])]
        # 7 tool-call turns — exceeds the cap of 6. The 7th call will
        # trigger the overshoot path. The final answer turn would never
        # be reached, but we still need a final turn in case the loop
        # decides differently — script defensively.
        scripted = [
            _tool_call_turn("get_node", {"node_id": "emp_0436"}),
            _tool_call_turn("get_node", {"node_id": "emp_0436"}),
            _tool_call_turn("get_node", {"node_id": "emp_0436"}),
            _tool_call_turn("get_node", {"node_id": "emp_0436"}),
            _tool_call_turn("get_node", {"node_id": "emp_0436"}),
            _tool_call_turn("get_node", {"node_id": "emp_0436"}),
            _tool_call_turn("get_node", {"node_id": "emp_0436"}),
        ]
        hybrid = _ScriptedTier("hybrid", cluster)
        store = _stub_store(
            nodes={"emp_0436": _node("emp_0436", "Person", name="X")},
            provenance={"emp_0436": [_provenance("emp_0436", "name")]},
        )
        llm = StubLLMClient(scripted_turns=scripted)
        registry = TierRegistry(
            {"hybrid": hybrid}, ThreadSummaryWorkflow.allowed_tiers
        )
        wf = ThreadSummaryWorkflow(registry, llm=llm, store=store)

        out = wf.run(
            WorkflowInput(
                payload={
                    "kind": "slack",
                    "participants": ["x@y.com"],
                    "messages": [_msg("x", "hi")],
                }
            )
        )

        assert out.relevance == 0.0
        assert out.extras["reason"] == "tool_budget_exceeded"
        # 7th call was the trigger — used count is 7.
        assert out.extras["tool_calls_used"] == 7
        # No final summary text — answer is None.
        assert out.answer is None
        # Items + citations from the partial run are still surfaced.
        assert any(h.id == "emp_0436" for h in out.items)


# ---------- citation dedup ----------


class TestCitationDedup:
    def test_repeated_node_touches_dedup_to_one_citation(self) -> None:
        """Same node touched by T3 + a get_node tool call — the citation
        should appear once, not twice.
        """
        emp_hit = _hit("emp_0436", 0.92, preview="Person")
        # T3 surfaces emp_0436 twice (e.g. via participant + via id token).
        cluster = [
            _result_with_hits("hybrid", [emp_hit], citations=[]),
            _result_with_hits("hybrid", [emp_hit], citations=[]),
        ]
        scripted = [
            _tool_call_turn("get_node", {"node_id": "emp_0436"}),
            _final_answer_turn(
                "## Gist\nx\n\n"
                "## Decisions / Action items\n"
                "* (none)\n\n"
                "## Open questions\n"
                "* (none)\n\n"
                "## Linked entities\n"
                "* X [node:emp_0436]\n"
            ),
        ]
        hybrid = _ScriptedTier("hybrid", cluster)
        store = _stub_store(
            nodes={"emp_0436": _node("emp_0436", "Person", name="X")},
            provenance={"emp_0436": [_provenance("emp_0436", "name")]},
        )
        llm = StubLLMClient(scripted_turns=scripted)
        registry = TierRegistry(
            {"hybrid": hybrid}, ThreadSummaryWorkflow.allowed_tiers
        )
        wf = ThreadSummaryWorkflow(registry, llm=llm, store=store)

        out = wf.run(
            WorkflowInput(
                payload={
                    "kind": "slack",
                    "participants": ["emp_0436", "raj@acme.com"],
                    "messages": [_msg("x", "hi emp_0436")],
                }
            )
        )

        # Even though the node was touched 3+ times (twice in T3, once
        # by get_node), we should see exactly one citation per unique
        # (source_file, source_record_id, source_field) key.
        keys = [
            (c.source_file, c.source_record_id, c.source_field)
            for c in out.citations
        ]
        assert len(keys) == len(set(keys))
        # And exactly one citation in this case (the single provenance).
        assert len(out.citations) == 1


# ---------- input validation (fail-fast) ----------


class TestInputValidation:
    def _wf(self) -> ThreadSummaryWorkflow:
        hybrid = _ScriptedTier("hybrid", [])
        store = _stub_store()
        llm = StubLLMClient(scripted_turns=[_final_answer_turn("x")])
        registry = TierRegistry(
            {"hybrid": hybrid}, ThreadSummaryWorkflow.allowed_tiers
        )
        return ThreadSummaryWorkflow(registry, llm=llm, store=store)

    def test_missing_kind_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="invalid ThreadSummaryInput"):
            self._wf().run(
                WorkflowInput(payload={"participants": [], "messages": []})
            )

    def test_invalid_kind_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="invalid ThreadSummaryInput"):
            self._wf().run(
                WorkflowInput(
                    payload={
                        "kind": "unknown_kind",
                        "participants": [],
                        "messages": [],
                    }
                )
            )

    def test_message_missing_text_raises(self) -> None:
        with pytest.raises(ValueError, match="invalid ThreadSummaryInput"):
            self._wf().run(
                WorkflowInput(
                    payload={
                        "kind": "slack",
                        "participants": [],
                        "messages": [{"author": "a", "ts": "t"}],
                    }
                )
            )

    def test_constructor_rejects_non_llm_instance(self) -> None:
        hybrid = _ScriptedTier("hybrid", [])
        store = _stub_store()
        registry = TierRegistry(
            {"hybrid": hybrid}, ThreadSummaryWorkflow.allowed_tiers
        )
        with pytest.raises(TypeError, match="LLMClient"):
            ThreadSummaryWorkflow(registry, llm=object(), store=store)  # type: ignore[arg-type]

    def test_constructor_rejects_non_store_instance(self) -> None:
        hybrid = _ScriptedTier("hybrid", [])
        llm = StubLLMClient(scripted_turns=[_final_answer_turn("x")])
        registry = TierRegistry(
            {"hybrid": hybrid}, ThreadSummaryWorkflow.allowed_tiers
        )
        with pytest.raises(TypeError, match="GraphStore"):
            ThreadSummaryWorkflow(registry, llm=llm, store=object())  # type: ignore[arg-type]


# ---------- ThreadSummaryInput model ----------


class TestThreadSummaryInputModel:
    def test_message_round_trip(self) -> None:
        m = ThreadMessage(author="a", ts="2024-01-01", text="hello")
        assert m.author == "a"
        assert m.text == "hello"

    def test_input_accepts_three_kinds(self) -> None:
        for k in ("meeting", "slack", "email_thread"):
            inp = ThreadSummaryInput(kind=k, participants=[], messages=[])  # type: ignore[arg-type]
            assert inp.kind == k


# ---------- build_workflow factory ----------


class TestBuildWorkflowFactory:
    def test_build_threads_extras_to_constructor(self) -> None:
        if "thread-summary" not in list_workflows():
            register_workflow(ThreadSummaryWorkflow)
        hybrid = _ScriptedTier("hybrid", [])
        store = _stub_store()
        llm = StubLLMClient(scripted_turns=[_final_answer_turn("x")])
        wf = build_workflow(
            "thread-summary",
            {"hybrid": hybrid},
            llm=llm,
            store=store,
        )
        assert isinstance(wf, ThreadSummaryWorkflow)
        assert wf.tiers.allowed == frozenset({"hybrid"})

    def test_build_missing_extras_raises_typeerror(self) -> None:
        if "thread-summary" not in list_workflows():
            register_workflow(ThreadSummaryWorkflow)
        hybrid = _ScriptedTier("hybrid", [])
        with pytest.raises(TypeError):
            build_workflow("thread-summary", {"hybrid": hybrid})
