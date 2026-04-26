"""Tests for the `answer-customer-email` workflow.

Stubs every external dependency (tiers, LLM, GraphStore) so the tests
exercise the workflow's frozen recipe end-to-end without Neo4j /
network. Real-graph integration is left for the manual smoke step
called out in the issue's acceptance criteria.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from backend.graph.store import GraphStore
from backend.models.graph import FactConfidence, GraphNode, Provenance
from backend.retrieval import (
    Citation,
    Hit,
    QueryContext,
    QueryResult,
    StubLLMClient,
    Tier,
)
from backend.retrieval.agentic import LLMTurn
from backend.retrieval.workflows import (
    CustomerEmailWorkflow,
    TierRegistry,
    WorkflowInput,
    build_workflow,
    list_workflows,
    register_workflow,
)


# ---------- helpers ----------


class _ScriptedTier(Tier):
    """Tier double whose `search` returns whatever the test queues up."""

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


def _empty_result(name: str) -> QueryResult:
    return QueryResult(
        answer=None,
        items=[],
        citations=[],
        tier_used=name,
        relevance=0.0,
        latency_ms=0,
    )


def _hit(node_id: str, score: float, preview: str = "...") -> Hit:
    return Hit(kind="node", id=node_id, score=score, preview=preview)


def _citation(field: str, raw: str = "x") -> Citation:
    return Citation(
        source_file="customers.json",
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


def _stub_store(
    *,
    neighbors: dict[str, list[str]] | None = None,
    nodes: dict[str, GraphNode] | None = None,
    provenance: dict[str, list[Provenance]] | None = None,
) -> GraphStore:
    """A `MagicMock(spec=GraphStore)` with the methods the workflow
    actually calls wired to deterministic returns. We use spec=GraphStore
    so attribute access stays type-checked (typos blow up loudly).
    """
    store = MagicMock(spec=GraphStore)
    nb = neighbors or {}
    nd = nodes or {}
    pv = provenance or {}
    store.neighbors.side_effect = lambda nid, depth: set(nb.get(nid, []))
    store.get_node.side_effect = lambda nid: nd.get(nid)
    store._provenance_for_node.side_effect = lambda nid: list(pv.get(nid, []))
    return store


def _node(node_id: str, node_type: str, **attrs: Any) -> GraphNode:
    return GraphNode(id=node_id, type=node_type, attributes=attrs)


def _provenance(node_id: str, field: str) -> Provenance:
    return Provenance(
        source_file="tickets.json",
        source_record_id=f"rec:{node_id}:{field}",
        source_field=field,
        extraction_method="direct_mapping",
        extraction_model="rule:test",
        confidence=FactConfidence.EXACT,
        raw_value=f"{node_id}:{field}",
    )


# ---------- registry / contract ----------


class TestRegistration:
    def test_workflow_is_registered_on_package_import(self) -> None:
        # The `from .customer_email import ...` line in
        # `workflows/__init__.py` triggers the `@register_workflow`
        # decorator at import time. If a sibling test (e.g.
        # `test_base.py::_isolate_registry`) cleared the registry
        # earlier in the session, re-register manually to get a clean
        # baseline — `clear_registry` does not re-fire decorators.
        if "answer-customer-email" not in list_workflows():
            register_workflow(CustomerEmailWorkflow)
        from backend.retrieval.workflows import get_workflow

        assert "answer-customer-email" in list_workflows()
        assert get_workflow("answer-customer-email") is CustomerEmailWorkflow

    def test_class_metadata_matches_issue_spec(self) -> None:
        # Deterministic workflow MUST NOT include agentic in the
        # allowed tier set — issue #8 spec mandates p95 ≤ 2s.
        assert CustomerEmailWorkflow.name == "answer-customer-email"
        assert CustomerEmailWorkflow.allowed_tiers == frozenset({"exact", "hybrid"})
        assert "agentic" not in CustomerEmailWorkflow.allowed_tiers


# ---------- happy path ----------


class TestHappyPath:
    def _build(
        self,
        *,
        sender_hit: Hit,
        sender_citations: list[Citation],
        product_hits: list[Hit],
        product_citations: list[Citation],
        neighbor_ids: list[str],
        neighbor_nodes: dict[str, GraphNode],
        neighbor_provenance: dict[str, list[Provenance]],
        llm_text: str,
    ) -> tuple[CustomerEmailWorkflow, _ScriptedTier, _ScriptedTier, StubLLMClient]:
        sender_node_id = sender_hit.id
        exact_tier = _ScriptedTier(
            "exact",
            [
                _result_with_hits(
                    "exact",
                    [sender_hit],
                    citations=sender_citations,
                    relevance=sender_hit.score,
                )
            ],
        )
        hybrid_tier = _ScriptedTier(
            "hybrid",
            [
                _result_with_hits(
                    "hybrid",
                    product_hits,
                    citations=product_citations,
                    relevance=(product_hits[0].score if product_hits else 0.0),
                )
            ],
        )
        store = _stub_store(
            neighbors={sender_node_id: neighbor_ids},
            nodes=neighbor_nodes,
            provenance=neighbor_provenance,
        )
        llm = StubLLMClient(
            scripted_turns=[LLMTurn(text=llm_text, tool_calls=[])]
        )
        registry = TierRegistry(
            {"exact": exact_tier, "hybrid": hybrid_tier},
            CustomerEmailWorkflow.allowed_tiers,
        )
        wf = CustomerEmailWorkflow(registry, llm=llm, store=store)
        return wf, exact_tier, hybrid_tier, llm

    def test_known_customer_returns_draft_with_citations(self) -> None:
        sender_hit = _hit("cust_1", 1.0, preview="ACME Corp")
        sender_citations = [_citation("email", "buyer@acme.com")]
        ticket_node = _node(
            "ticket_42", "Event", title="VPN access denied", status="open"
        )
        sale_node = _node("sale_99", "Event", item="Widget Pro", amount=299)
        product_hit_a = _hit("asset_widget", 0.85, preview="Widget Pro")
        product_hit_b = _hit("asset_gizmo", 0.4, preview="Gizmo Lite")
        product_citations = [_citation("name", "Widget Pro")]
        wf, exact_tier, hybrid_tier, llm = self._build(
            sender_hit=sender_hit,
            sender_citations=sender_citations,
            product_hits=[product_hit_a, product_hit_b],
            product_citations=product_citations,
            neighbor_ids=["ticket_42", "sale_99"],
            neighbor_nodes={"ticket_42": ticket_node, "sale_99": sale_node},
            neighbor_provenance={
                "ticket_42": [_provenance("ticket_42", "title")],
                "sale_99": [_provenance("sale_99", "item")],
            },
            llm_text=(
                "Hi, thanks for reaching out about [node:asset_widget]. "
                "I see ticket [node:ticket_42] is still open."
            ),
        )

        out = wf.run(
            WorkflowInput(
                payload={
                    "from_address": "Buyer@ACME.com",
                    "subject": "VPN issue",
                    "body": "I cannot connect to the Widget portal",
                }
            )
        )

        assert out.workflow == "answer-customer-email"
        assert out.tier_used == "exact"
        assert out.relevance == 1.0
        # Issue acceptance: the draft cites the customer's open ticket
        # and one of the candidate products by node id.
        assert "[node:asset_widget]" in (out.answer or "")
        assert "[node:ticket_42]" in (out.answer or "")
        # Items must include sender + neighbors + product candidates so
        # the UI can render every node id the LLM was allowed to cite.
        item_ids = {h.id for h in out.items}
        assert {"cust_1", "ticket_42", "sale_99", "asset_widget", "asset_gizmo"} <= item_ids
        # Citations must be populated and dedup'd (no two with the same key).
        keys = [
            (c.source_file, c.source_record_id, c.source_field)
            for c in out.citations
        ]
        assert len(keys) == len(set(keys))
        assert any(c.source_field == "email" for c in out.citations)
        assert any(c.source_field == "title" for c in out.citations)
        # ExactTier received the *normalized* (lowercased) address.
        assert exact_tier.calls == ["buyer@acme.com"]
        # HybridTier received the email body verbatim.
        assert hybrid_tier.calls == ["I cannot connect to the Widget portal"]
        # Workflow extras carry diagnostic counters.
        assert out.extras["sender_node_id"] == "cust_1"
        assert out.extras["from_address"] == "buyer@acme.com"
        assert out.extras["related_count"] == 2
        assert out.extras["product_candidate_count"] == 2

    def test_product_top_k_truncation(self) -> None:
        # HybridTier returns 8 hits; only the top 5 should land on items.
        sender_hit = _hit("cust_2", 1.0)
        product_hits = [_hit(f"asset_{i}", 1.0 - i * 0.1) for i in range(8)]
        wf, _, _, _ = self._build(
            sender_hit=sender_hit,
            sender_citations=[],
            product_hits=product_hits,
            product_citations=[],
            neighbor_ids=[],
            neighbor_nodes={},
            neighbor_provenance={},
            llm_text="ok",
        )
        out = wf.run(
            WorkflowInput(
                payload={
                    "from_address": "x@y.com",
                    "subject": "s",
                    "body": "b",
                }
            )
        )
        product_ids_in_items = [h.id for h in out.items if h.id.startswith("asset_")]
        assert product_ids_in_items == [f"asset_{i}" for i in range(5)]
        assert out.extras["product_candidate_count"] == 5


# ---------- unknown sender ----------


class TestUnknownSender:
    def test_unknown_sender_short_circuits_no_llm_call(self) -> None:
        exact_tier = _ScriptedTier("exact", [_empty_result("exact")])
        hybrid_tier = _ScriptedTier("hybrid", [])  # must NOT be called
        store = _stub_store()
        llm = StubLLMClient(scripted_turns=[LLMTurn(text="should not run", tool_calls=[])])
        registry = TierRegistry(
            {"exact": exact_tier, "hybrid": hybrid_tier},
            CustomerEmailWorkflow.allowed_tiers,
        )
        wf = CustomerEmailWorkflow(registry, llm=llm, store=store)

        out = wf.run(
            WorkflowInput(
                payload={
                    "from_address": "ghost@nowhere.com",
                    "subject": "hello?",
                    "body": "anyone there",
                }
            )
        )

        assert out.relevance == 0.0
        assert out.answer is None
        assert out.items == []
        assert out.citations == []
        assert out.extras["reason"] == "unknown_sender"
        assert out.extras["from_address"] == "ghost@nowhere.com"
        # LLM was not invoked.
        assert llm.starts_received == []
        # HybridTier was not invoked.
        assert hybrid_tier.calls == []
        # ExactTier received the lowercased query.
        assert exact_tier.calls == ["ghost@nowhere.com"]


# ---------- input validation (fail-fast) ----------


class TestInputValidation:
    def _wf(self) -> CustomerEmailWorkflow:
        exact_tier = _ScriptedTier("exact", [_empty_result("exact")])
        hybrid_tier = _ScriptedTier("hybrid", [_empty_result("hybrid")])
        store = _stub_store()
        llm = StubLLMClient(scripted_turns=[LLMTurn(text="x", tool_calls=[])])
        registry = TierRegistry(
            {"exact": exact_tier, "hybrid": hybrid_tier},
            CustomerEmailWorkflow.allowed_tiers,
        )
        return CustomerEmailWorkflow(registry, llm=llm, store=store)

    def test_missing_from_address_raises_value_error(self) -> None:
        wf = self._wf()
        with pytest.raises(ValueError, match="invalid CustomerEmailInput"):
            wf.run(
                WorkflowInput(payload={"subject": "s", "body": "b"})
            )

    def test_missing_body_raises_value_error(self) -> None:
        wf = self._wf()
        with pytest.raises(ValueError, match="invalid CustomerEmailInput"):
            wf.run(
                WorkflowInput(
                    payload={"from_address": "a@b.com", "subject": "s"}
                )
            )

    def test_empty_body_raises_value_error(self) -> None:
        wf = self._wf()
        with pytest.raises(ValueError, match="invalid CustomerEmailInput"):
            wf.run(
                WorkflowInput(
                    payload={
                        "from_address": "a@b.com",
                        "subject": "s",
                        "body": "",
                    }
                )
            )


# ---------- contract: allowed_tiers enforcement ----------


class TestAllowedTiersEnforcement:
    def test_workflow_cannot_reach_for_agentic(self) -> None:
        """Even though `allowed_tiers` is `{exact, hybrid}`, double-check
        that the framework rejects an attempt to construct the workflow
        with a wider TierRegistry."""
        exact_tier = _ScriptedTier("exact", [_empty_result("exact")])
        hybrid_tier = _ScriptedTier("hybrid", [_empty_result("hybrid")])
        agentic_tier = _ScriptedTier("agentic", [_empty_result("agentic")])
        store = _stub_store()
        llm = StubLLMClient(scripted_turns=[LLMTurn(text="x", tool_calls=[])])
        wider = TierRegistry(
            {
                "exact": exact_tier,
                "hybrid": hybrid_tier,
                "agentic": agentic_tier,
            },
            frozenset({"exact", "hybrid", "agentic"}),  # wider than allowed
        )
        with pytest.raises(ValueError, match="mirror"):
            CustomerEmailWorkflow(wider, llm=llm, store=store)

    def test_constructor_rejects_non_llm_instance(self) -> None:
        exact_tier = _ScriptedTier("exact", [_empty_result("exact")])
        hybrid_tier = _ScriptedTier("hybrid", [_empty_result("hybrid")])
        store = _stub_store()
        registry = TierRegistry(
            {"exact": exact_tier, "hybrid": hybrid_tier},
            CustomerEmailWorkflow.allowed_tiers,
        )
        with pytest.raises(TypeError, match="LLMClient"):
            CustomerEmailWorkflow(registry, llm=object(), store=store)  # type: ignore[arg-type]

    def test_constructor_rejects_non_store_instance(self) -> None:
        exact_tier = _ScriptedTier("exact", [_empty_result("exact")])
        hybrid_tier = _ScriptedTier("hybrid", [_empty_result("hybrid")])
        llm = StubLLMClient(scripted_turns=[LLMTurn(text="x", tool_calls=[])])
        registry = TierRegistry(
            {"exact": exact_tier, "hybrid": hybrid_tier},
            CustomerEmailWorkflow.allowed_tiers,
        )
        with pytest.raises(TypeError, match="GraphStore"):
            CustomerEmailWorkflow(registry, llm=llm, store=object())  # type: ignore[arg-type]


# ---------- compose-step contract ----------


class TestComposeContract:
    def test_compose_llm_requesting_tools_raises(self) -> None:
        """Compose is a single-shot call — a tool-calling response is a
        contract violation, not a valid path. Tests use a doctored
        StubLLMClient response: we cannot ask `LLMTurn` to carry a
        function-call without going through the agentic path, so this
        test injects via a direct Mock object instead.
        """
        from backend.retrieval.agentic import LLMClient as _LLMClient
        from backend.retrieval.agentic import ToolCall

        class _ToolCallingLLM(_LLMClient):
            def start(self, **_kw: Any) -> LLMTurn:
                return LLMTurn(
                    text=None,
                    tool_calls=[ToolCall(name="evil", args={})],
                )

            def respond_to_tool_results(self, results: list[Any]) -> LLMTurn:
                raise NotImplementedError

        sender_hit = _hit("cust_3", 1.0)
        exact_tier = _ScriptedTier(
            "exact", [_result_with_hits("exact", [sender_hit], relevance=1.0)]
        )
        hybrid_tier = _ScriptedTier("hybrid", [_empty_result("hybrid")])
        store = _stub_store()
        registry = TierRegistry(
            {"exact": exact_tier, "hybrid": hybrid_tier},
            CustomerEmailWorkflow.allowed_tiers,
        )
        wf = CustomerEmailWorkflow(registry, llm=_ToolCallingLLM(), store=store)
        with pytest.raises(RuntimeError, match="unexpectedly requested tools"):
            wf.run(
                WorkflowInput(
                    payload={
                        "from_address": "x@y.com",
                        "subject": "s",
                        "body": "b",
                    }
                )
            )


# ---------- build_workflow factory ----------


class TestBuildWorkflowFactory:
    def test_build_threads_extras_to_constructor(self) -> None:
        # Make sure the workflow is registered (the package import does
        # this; explicit re-register protects against an earlier
        # `clear_registry` call from another test in the same session).
        if "answer-customer-email" not in list_workflows():
            register_workflow(CustomerEmailWorkflow)

        exact_tier = _ScriptedTier("exact", [_empty_result("exact")])
        hybrid_tier = _ScriptedTier("hybrid", [_empty_result("hybrid")])
        store = _stub_store()
        llm = StubLLMClient(scripted_turns=[LLMTurn(text="x", tool_calls=[])])
        wf = build_workflow(
            "answer-customer-email",
            {"exact": exact_tier, "hybrid": hybrid_tier},
            llm=llm,
            store=store,
        )
        assert isinstance(wf, CustomerEmailWorkflow)
        assert wf.tiers.allowed == frozenset({"exact", "hybrid"})

    def test_build_missing_extras_raises_typeerror(self) -> None:
        if "answer-customer-email" not in list_workflows():
            register_workflow(CustomerEmailWorkflow)

        exact_tier = _ScriptedTier("exact", [_empty_result("exact")])
        hybrid_tier = _ScriptedTier("hybrid", [_empty_result("hybrid")])
        with pytest.raises(TypeError):
            build_workflow(
                "answer-customer-email",
                {"exact": exact_tier, "hybrid": hybrid_tier},
            )
