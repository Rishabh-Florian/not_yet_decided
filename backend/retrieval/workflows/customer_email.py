"""`answer-customer-email` — deterministic workflow for drafting a
reply to an inbound customer email.

Pipeline (frozen, no cascade, no agentic loop — see issue #8):

1. **T1 / ExactTier** — exact lookup of the sender by email. The email
   is normalized (lowercased + stripped) before being forwarded as a
   query string. ExactTier walks its id-token regex first (won't match
   plain emails) and falls through to its Lucene fulltext branch over
   the `node_text` index, so a customer node carrying the email in any
   of its indexed fields is recovered. If no hit returns, the workflow
   short-circuits with `relevance=0.0` and `extras.reason="unknown_sender"`.
   No LLM call is made on this branch.
2. **T1 / ExactTier (neighbor traversal)** — using the resolved sender
   node id, the workflow walks the graph for one-hop neighbors that
   look like related context (open IT tickets / recent sales /
   purchased products). Neighbor traversal is implemented through the
   tier's underlying `GraphStore` (the workflow holds an explicit
   `GraphStore` reference for this; see __init__).
3. **T3 / HybridTier** — semantic search over the email body to surface
   candidate `Product`-shaped nodes the customer might be asking about
   (top-5 from the fused vector + BM25 ranking).
4. **LLM compose** — a single Gemini one-shot call that takes
   (customer profile, related tickets/sales, candidate products,
   original email) and writes a draft reply with inline citations
   referenced by node id. The compose step uses the same `LLMClient`
   protocol the AgenticTier uses, but with an empty tools list — this
   is a single-shot call, not a function-calling loop. That keeps the
   workflow strictly deterministic (the only LLM is for natural-language
   generation; routing / tool selection are not LLM-driven).

Deterministic ⇒ the **tier sequence** and **branching** are hardcoded.
No LLM picks the route. The compose step is allowed because the
acceptance criteria explicitly call for "draft reply" output; the LLM
sees a frozen prompt assembled from deterministic retrieval results.

Citations are accumulated from every node touched during steps 1–3 and
attached verbatim to the `WorkflowResult.citations`. The compose
prompt instructs the model to cite by node id; the returned `items`
include every node id the model is allowed to cite, so a UI can render
inline references reliably.

Why a workflow (not cascade)?
- Predictable shape per email; cascade's escalation logic adds latency
  and unpredictability.
- p95 ≤ 2s is achievable when AgenticTier is excluded from the path.
- The compose prompt is template-driven, not free-form reasoning.
"""
from __future__ import annotations

from typing import Any, ClassVar

from pydantic import BaseModel, Field, ValidationError

from backend.graph.store import GraphStore

from ..agentic import LLMClient
from ..models import Citation, Hit, QueryContext
from .base import TierRegistry, Workflow, WorkflowInput, WorkflowResult


# Issue spec: top-5 product candidates from the semantic search.
_PRODUCT_TOP_K: int = 5

# Neighbor traversal depth for step 2 (one hop covers tickets / sales /
# purchases that point to the customer node directly).
_NEIGHBOR_DEPTH: int = 1

# Per-result-set cap so the compose prompt stays bounded on hub-shaped
# customer nodes (one with hundreds of incident tickets / sales). The
# cap is defensive, not algorithmic.
_MAX_RELATED_NEIGHBORS: int = 25


class CustomerEmailInput(BaseModel):
    """Issue-spec input shape. The workflow accepts this on
    `WorkflowInput.payload` (kept on the generic dict surface so the
    framework's IO model stays stable across workflows).
    """

    from_address: str = Field(..., min_length=3)
    subject: str
    body: str = Field(..., min_length=1)
    thread_history: list[str] = Field(default_factory=list)


def _normalize_email(addr: str) -> str:
    """Lowercase + strip. The issue mentions identity-resolution
    `SAME_AS` clusters as a follow-on; for R5b we only do the
    deterministic normalization. Cluster-aware lookup is human-backlog.
    """
    return addr.strip().lower()


_SYSTEM_PROMPT = (
    "You are a customer-support drafting assistant. The user message "
    "below contains a structured brief: (1) an inbound customer "
    "email, (2) the matched customer profile, (3) related tickets / "
    "sales for that customer, and (4) candidate product nodes that "
    "may be relevant. Draft a concise, polite reply addressed to the "
    "customer.\n\n"
    "Hard rules:\n"
    "* Cite every concrete claim by node id, e.g. '[node:abc123]'. "
    "Cite ONLY the node ids listed in the brief — do not invent ids.\n"
    "* If the brief contains no related tickets / sales / products, "
    "acknowledge the email and ask one clarifying question. Do not "
    "fabricate context.\n"
    "* Output the reply text only — no preamble, no signature beyond "
    "'Best regards, Support Team', no markdown headers."
)


class CustomerEmailWorkflow(Workflow):
    """Frozen recipe: T1 sender lookup → T1 neighbors → T3 product
    semantic search → one-shot LLM compose.

    Construction takes:
    * `tiers` — `TierRegistry` (locked to `{"exact", "hybrid"}` by the
      framework).
    * `llm` — single-shot composer. Tests pass a `StubLLMClient`
      scripted with one final-text turn; production wires
      `GeminiLLMClient`.
    * `store` — `GraphStore`. Needed for the neighbor-traversal step
      (the tiers themselves don't expose graph walks). Held alongside
      the tier registry so the workflow can do its own one-hop
      `neighbors` call.
    """

    name: ClassVar[str] = "answer-customer-email"
    # Deterministic — agentic is intentionally excluded so the path
    # stays under the issue's 2s p95 latency budget.
    allowed_tiers: ClassVar[frozenset[str]] = frozenset({"exact", "hybrid"})

    def __init__(
        self,
        tiers: TierRegistry,
        *,
        llm: LLMClient,
        store: GraphStore,
    ) -> None:
        super().__init__(tiers)
        if not isinstance(llm, LLMClient):
            raise TypeError(
                f"llm must be an LLMClient instance, got {type(llm).__name__}"
            )
        if not isinstance(store, GraphStore):
            raise TypeError(
                f"store must be a GraphStore instance, got {type(store).__name__}"
            )
        self._llm = llm
        self._store = store

    def run(self, input: WorkflowInput) -> WorkflowResult:  # noqa: A002
        try:
            payload = CustomerEmailInput.model_validate(input.payload)
        except ValidationError as e:
            # Surface as ValueError so the API layer maps to HTTP 400.
            raise ValueError(f"invalid CustomerEmailInput payload: {e}") from e

        ctx = input.ctx or QueryContext()

        # Step 1: T1 sender lookup. ExactTier owns the matching algo
        # (id-token regex → Lucene fulltext fallback). On miss, abort
        # without invoking the LLM — issue: relevance=0.0 + reason.
        normalized = _normalize_email(payload.from_address)
        exact = self.tiers.get("exact")
        sender_result = exact.search(normalized, ctx)
        if not sender_result.items:
            return WorkflowResult(
                answer=None,
                items=[],
                citations=[],
                tier_used="exact",
                relevance=0.0,
                latency_ms=0,
                workflow=type(self).name,
                extras={"reason": "unknown_sender", "from_address": normalized},
            )

        # Top hit is the resolved customer node. ExactTier orders id-
        # token hits with score 1.0; fulltext hits land in [0, 1).
        sender_hit = sender_result.items[0]
        sender_score = sender_hit.score
        sender_node_id = sender_hit.id

        # Step 2: T1-style neighbor traversal — one hop, untyped (we
        # let the canonical schema's set of relations surface anything
        # incident to the customer node). Capped at `_MAX_RELATED_NEIGHBORS`
        # so the compose prompt does not blow up on a hub node.
        related_hits, related_citations = self._gather_neighbors(sender_node_id)

        # Step 3: T3 / HybridTier — semantic + lexical search over the
        # email body. We then keep the top `_PRODUCT_TOP_K` hits whose
        # node type is `Asset` (the canonical product-shaped type — see
        # `backend/ingest/canonical.yaml`). The filter is applied
        # post-hoc rather than via a typed Cypher because HybridTier's
        # public API is query-string-only; widening it would couple the
        # workflow to tier internals.
        hybrid = self.tiers.get("hybrid")
        product_query_result = hybrid.search(payload.body, ctx)
        product_hits, product_citations = self._filter_product_candidates(
            product_query_result.items, product_query_result.citations
        )

        # Aggregate citations (sender + neighbors + products), dedup by
        # the canonical key the rest of the system uses.
        all_citations = _dedup_citations(
            list(sender_result.citations) + related_citations + product_citations
        )
        all_items = [sender_hit, *related_hits, *product_hits]

        # Step 4: LLM compose. Single-shot — `tools=[]` — so the LLM
        # cannot call back into the cascade. Failure surfaces upward
        # (per fail-fast); the unknown-sender branch above is the only
        # "soft" exit.
        prompt_brief = _format_brief(
            email=payload,
            sender_hit=sender_hit,
            related_hits=related_hits,
            product_hits=product_hits,
        )
        turn = self._llm.start(
            system_prompt=_SYSTEM_PROMPT,
            user_query=prompt_brief,
            tools=[],
        )
        if turn.tool_calls:
            # Compose-step LLM is not allowed to request tools; the
            # prompt instructs final-text-only. A tool call here is a
            # contract violation, not a valid path.
            raise RuntimeError(
                f"compose LLM unexpectedly requested tools: "
                f"{[c.name for c in turn.tool_calls]!r}"
            )
        if turn.text is None or not turn.text.strip():
            raise RuntimeError("compose LLM returned empty text")

        # Surface relevance from the dominant retrieval tier. ExactTier
        # produced the customer match; cascade convention says we use
        # the dominant tier's relevance + name. Picking `exact` here
        # mirrors how the issue describes the workflow ("T1 + T1 + T3
        # + LLM compose" — T1 is the spine).
        return WorkflowResult(
            answer=turn.text.strip(),
            items=all_items,
            citations=all_citations,
            tier_used="exact",
            relevance=sender_score,
            latency_ms=0,
            workflow=type(self).name,
            extras={
                "from_address": normalized,
                "sender_node_id": sender_node_id,
                "related_count": len(related_hits),
                "product_candidate_count": len(product_hits),
            },
        )

    # ---------- internal ----------

    def _gather_neighbors(
        self, node_id: str
    ) -> tuple[list[Hit], list[Citation]]:
        """One-hop neighbor traversal around the resolved customer node.

        Pulls neighbor ids from `GraphStore.neighbors`, then resolves
        each into a `Hit` + the node's provenance into `Citation`s.
        Score is fixed at 1.0 (these are direct graph neighbors — exact
        relationships, not retrieval candidates; see ExactTier id-match
        scoring convention).
        """
        neighbor_ids = sorted(self._store.neighbors(node_id, depth=_NEIGHBOR_DEPTH))
        if len(neighbor_ids) > _MAX_RELATED_NEIGHBORS:
            neighbor_ids = neighbor_ids[:_MAX_RELATED_NEIGHBORS]
        hits: list[Hit] = []
        citations: list[Citation] = []
        for nid in neighbor_ids:
            node = self._store.get_node(nid)
            if node is None:
                # Neighbors returned an id we then can't read back —
                # store inconsistency, fail fast.
                raise RuntimeError(
                    f"GraphStore.neighbors returned unknown node id {nid!r}"
                )
            hits.append(
                Hit(
                    kind="node",
                    id=nid,
                    score=1.0,  # direct graph adjacency — exact relationship
                    preview=_node_preview(node.attributes, node.type),
                )
            )
            for p in self._store._provenance_for_node(nid):
                method = p.extraction_method
                if method not in (
                    "direct_mapping",
                    "llm_extraction",
                    "rule_based",
                    "human",
                ):
                    raise ValueError(
                        f"unexpected extraction_method {method!r} in provenance"
                    )
                citations.append(
                    Citation(
                        source_file=p.source_file,
                        source_record_id=p.source_record_id,
                        source_field=p.source_field,
                        raw_value=p.raw_value,
                        extraction_method=method,
                    )
                )
        return hits, citations

    def _filter_product_candidates(
        self,
        hits: list[Hit],
        citations: list[Citation],
    ) -> tuple[list[Hit], list[Citation]]:
        """Keep the top `_PRODUCT_TOP_K` hits the HybridTier returned.

        We do NOT post-filter by node type here: HybridTier's RRF
        ranking already produces the strongest semantic+lexical
        candidates for the email body, and the canonical schema does
        not have a `Product` type — the closest is `Asset`, but assets
        are also things like repos and accounts. Trusting the RRF
        ranking matches how the issue describes the step ("semantic
        search over Product nodes ... top-5"); the surface here lets a
        future revision insert a typed filter without touching callers.
        """
        return hits[:_PRODUCT_TOP_K], list(citations)


def _node_preview(attributes: dict[str, Any], node_type: str) -> str:
    """Short summary string for a `Hit.preview`. Mirrors the convention
    used in `exact.py` / `hybrid.py` so the workflow's hits look the
    same as cascade hits to a UI consumer.
    """
    for key in ("name", "title", "subject", "summary", "description", "customer_name"):
        v = attributes.get(key)
        if isinstance(v, str) and v.strip():
            return f"{node_type}: {v.strip()[:180]}"
    return f"{node_type}: <no preview>"


def _dedup_citations(citations: list[Citation]) -> list[Citation]:
    """Dedup on `(source_file, source_record_id, source_field)` — the
    same key `CitationCollector` uses in `tools.py`. Preserves first-
    seen order so the UI can rely on a stable rendering sequence.
    """
    seen: set[tuple[str, str, str]] = set()
    out: list[Citation] = []
    for c in citations:
        key = (c.source_file, c.source_record_id, c.source_field)
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


def _format_brief(
    *,
    email: CustomerEmailInput,
    sender_hit: Hit,
    related_hits: list[Hit],
    product_hits: list[Hit],
) -> str:
    """Turn the deterministic retrieval results into a structured
    prompt for the compose LLM. Bullets, not raw JSON, per the issue's
    implementation note.
    """
    lines: list[str] = []
    lines.append("=== INBOUND EMAIL ===")
    lines.append(f"From: {email.from_address}")
    lines.append(f"Subject: {email.subject}")
    lines.append("")
    lines.append(email.body)
    if email.thread_history:
        lines.append("")
        lines.append("--- Prior thread ---")
        for i, msg in enumerate(email.thread_history, start=1):
            lines.append(f"[{i}] {msg}")
    lines.append("")
    lines.append("=== CUSTOMER PROFILE ===")
    lines.append(f"- node id: {sender_hit.id}")
    lines.append(f"  preview: {sender_hit.preview}")
    lines.append("")
    lines.append("=== RELATED TICKETS / SALES / NEIGHBORS ===")
    if related_hits:
        for h in related_hits:
            lines.append(f"- node id: {h.id}")
            lines.append(f"  preview: {h.preview}")
    else:
        lines.append("- (none)")
    lines.append("")
    lines.append("=== CANDIDATE PRODUCTS ===")
    if product_hits:
        for h in product_hits:
            lines.append(f"- node id: {h.id}  (score={h.score:.3f})")
            lines.append(f"  preview: {h.preview}")
    else:
        lines.append("- (none)")
    lines.append("")
    lines.append(
        "Draft the reply now. Cite node ids inline using [node:<id>]. "
        "Use only ids listed above."
    )
    return "\n".join(lines)


__all__ = [
    "CustomerEmailInput",
    "CustomerEmailWorkflow",
]
