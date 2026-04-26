"""`thread-summary` — less-deterministic workflow for summarizing
unstructured conversation threads (Slack, meeting transcripts,
email threads).

Pipeline (issue #9):

1. **T3 / HybridTier (deterministic)** — semantic search per
   participant identifier and per topic phrase extracted from the
   thread by light NER (regex over `emp_xxxx` / `cust_xxxx` style ids
   in R5c; GLiNER2 once R4 lands). Produces a "starting cluster" of
   `Person` / `Customer` / `Product` / `Ticket` nodes the agent can
   traverse from in step 2.
2. **T4 / Bounded LLM agent loop (less-deterministic)** — Gemini Flash
   2.5 driven loop with `get_node`, `get_neighbors`, `get_source_record`
   exposed as tools. Hard-capped at 6 tool calls. The LLM decides
   *which* nodes from the T3 cluster to drill into and *what* extra
   evidence to pull from the source records. Tool surface is
   intentionally narrower than `AgenticTier`: no `pattern_query` /
   `fulltext_search` / `vector_search`. The starting context already
   contains the recall step's output; the agent's job is to traverse,
   not to re-search.
3. **LLM compose (single-shot)** — final structured summary turn:
   one-line gist, decisions / action items, open questions, linked
   entities. Same `LLMClient` as the loop; `tools=[]`. Output is the
   `WorkflowResult.answer` markdown body.

Why "less-deterministic"?
- Step 1 is deterministic recall (HybridTier RRF).
- Step 2 lets the model pick which traversal to perform — the *what*
  and *how-deep* of the walk is model-driven.
- Step 3 is deterministic single-shot composition.

Citation accumulation
- All node touches (T3 cluster + every `get_node` / `get_neighbors`
  hit + every `get_source_record` pull) feed a single
  `CitationCollector`. Final result citations are deduped on
  `(source_file, source_record_id, source_field)` — same key used by
  every other tier / workflow.

Algorithmic relevance
- `relevance = 0.7` (`RELEVANCE_GROUNDED`) when the loop produced a
  non-empty summary AND >= 1 unique citation. Mirrors the
  `AgenticTier` recipe.
- `relevance = 0.3` (`RELEVANCE_UNGROUNDED`) when the summary exists
  but no citation was harvested.
- `relevance = 0.0` on empty thread short-circuit, max-iteration
  overshoot (partial summary surfaced), or any LLM exception.

Tool budget
- ≤ 6 tool calls across the loop (issue acceptance criterion).
  Overshoot returns the last partial summary text with reduced
  relevance — never crashes.
"""
from __future__ import annotations

import re
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, Field, ValidationError

from backend.graph.store import GraphStore

from ..agentic import (
    RELEVANCE_FAILED,
    RELEVANCE_GROUNDED,
    RELEVANCE_UNGROUNDED,
    LLMClient,
    ToolResult,
)
from ..models import Citation, Hit, QueryContext
from ..tools import CitationCollector, ToolDefinition, _attrs_preview, _node_to_dict
from .base import TierRegistry, Workflow, WorkflowInput, WorkflowResult


# Per-participant / per-topic top-k for the T3 starting cluster. Small
# because the agent will widen via traversal in step 2; the cluster only
# needs to cover the obvious anchors.
_T3_PER_QUERY_TOP_K: int = 5

# Issue spec: bounded ≤ 6 tool calls.
_MAX_TOOL_CALLS: int = 6

# Defensive cap on neighbor result size per `get_neighbors` call —
# mirrors `tools._MAX_NEIGHBORS` so the agent's context cannot blow up
# on a hub node.
_MAX_NEIGHBORS_PER_CALL: int = 50

# Cap on per-call traversal depth.
_MAX_DEPTH: int = 3

# Light-NER regex: matches the `emp_NNNN` / `cust_NNNN` / `prod_NNNN`
# style ids that appear inline in the dataset's conversation text.
# R5c keeps this to a regex; once R4 (GLiNER2) lands the docstring's
# implementation note says to swap it in here.
_ID_TOKEN_RE: re.Pattern[str] = re.compile(
    r"\b(?:emp|cust|customer|prod|product|ticket|order|sale)_[A-Za-z0-9]+\b"
)


# ---------- IO models ----------


class ThreadMessage(BaseModel):
    """One message in the thread. `author` may be an email, an emp_id,
    or a display name; the workflow does not normalize it (the agent
    sees it verbatim and disambiguates via the T3 cluster).
    """

    author: str = Field(..., min_length=1)
    ts: str = Field(..., min_length=1)
    text: str = Field(..., min_length=1)


class ThreadSummaryInput(BaseModel):
    """Issue-spec input shape. `kind` distinguishes meeting / Slack /
    email so the compose prompt can adapt the framing (e.g. action items
    are more prominent in meetings).
    """

    kind: Literal["meeting", "slack", "email_thread"]
    participants: list[str] = Field(default_factory=list)
    messages: list[ThreadMessage] = Field(default_factory=list)


_LOOP_SYSTEM_PROMPT = (
    "You are a thread-summarization assistant. The user message contains "
    "a structured brief: (1) the kind of thread (slack / meeting / "
    "email), (2) declared participants, (3) the verbatim message log, "
    "and (4) a starting cluster of related entity nodes recovered by "
    "semantic search.\n\n"
    "Your goal: write a structured summary of the thread.\n\n"
    "Tool budget: at most 6 tool calls. Use them to disambiguate "
    "participants, traverse to neighbors of starting-cluster nodes, "
    "or pull a source record when you need verbatim grounding. Plan "
    "before calling — each call costs latency.\n\n"
    "Available tools:\n"
    "* `get_node(node_id)` — fetch a node + provenance.\n"
    "* `get_neighbors(node_id, relation_type=None, depth=1)` — walk "
    "from a known node.\n"
    "* `get_source_record(source_file, record_id)` — pull the raw "
    "ingested record for an evidence-grade citation.\n\n"
    "When you have enough evidence, emit the FINAL summary as plain "
    "text with these sections (markdown):\n"
    "## Gist\n"
    "<one sentence>\n\n"
    "## Decisions / Action items\n"
    "* <action item> [node:<id>]\n"
    "...\n\n"
    "## Open questions\n"
    "* <question>\n"
    "...\n\n"
    "## Linked entities\n"
    "* <entity short label> [node:<id>]\n"
    "...\n\n"
    "Citation rules: every action item and every linked entity MUST "
    "carry a `[node:<id>]` reference. Cite ONLY ids that appear in the "
    "starting cluster or in a tool response — do not invent ids."
)


class ThreadSummaryWorkflow(Workflow):
    """`thread-summary` — T3 semantic recall + T4 bounded agent loop +
    single-shot LLM compose.

    Construction:

    * `tiers` — locked to `{"hybrid"}` (T3 only — T1 / agentic are
      explicitly out of scope per the issue: "Skip T1 entirely — id
      matches are rare in conversational text").
    * `llm` — drives both the bounded tool loop AND the final compose
      turn. Tests pass a `StubLLMClient` scripted with the desired
      sequence; production wires `GeminiLLMClient`.
    * `store` — `GraphStore`. Used by the workflow's tool dispatcher
      (the workflow exposes a narrower tool surface than the standard
      `ToolBox`, so it dispatches directly).
    """

    name: ClassVar[str] = "thread-summary"
    # T3 only. Per the issue: "Skip T1 entirely — id matches are rare
    # in conversational text." T4 (agentic) is invoked via the workflow's
    # own LLM driver, not via the cascade's `AgenticTier` — so it is
    # not declared here.
    allowed_tiers: ClassVar[frozenset[str]] = frozenset({"hybrid"})

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

    # ------------------------------------------------------------------

    def run(self, input: WorkflowInput) -> WorkflowResult:  # noqa: A002
        try:
            payload = ThreadSummaryInput.model_validate(input.payload)
        except ValidationError as e:
            raise ValueError(f"invalid ThreadSummaryInput payload: {e}") from e

        ctx = input.ctx or QueryContext()

        # Empty thread → low-confidence return, no LLM call. Issue
        # acceptance criterion: "Empty messages list = low-confidence
        # return (not error)."
        if not payload.messages:
            return WorkflowResult(
                answer=None,
                items=[],
                citations=[],
                tier_used="hybrid",
                relevance=RELEVANCE_FAILED,
                latency_ms=0,
                workflow=type(self).name,
                extras={
                    "reason": "empty_thread",
                    "kind": payload.kind,
                    "tool_calls_used": 0,
                    "action_items": [],
                    "linked_entity_ids": [],
                },
            )

        cites = CitationCollector()

        # ---------- Step 1: T3 recall ----------
        cluster_hits = self._t3_starting_cluster(payload, ctx, cites)

        # ---------- Step 2: bounded agent loop ----------
        # The loop drives the same `LLMClient` protocol the AgenticTier
        # uses but with a 3-tool subset. Tool calls dispatched by the
        # workflow's `_dispatch_tool` (no `ToolBox` — keeps the surface
        # exactly the 3 the issue specifies and avoids pulling an
        # `Embedder` dependency the workflow has no other use for).
        loop_brief = _format_loop_brief(payload, cluster_hits)
        tools = _thread_summary_tool_definitions()
        try:
            turn = self._llm.start(
                system_prompt=_LOOP_SYSTEM_PROMPT,
                user_query=loop_brief,
                tools=tools,
            )
        except Exception as e:
            return self._partial_result(
                cluster_hits=cluster_hits,
                cites=cites,
                summary_text=None,
                tool_calls_used=0,
                kind=payload.kind,
                reason=f"llm_start_failed: {type(e).__name__}",
            )

        tool_calls_used = 0
        last_text: str | None = None
        loop_overshot = False

        while True:
            if turn.text is not None and turn.text.strip():
                last_text = turn.text.strip()
                break
            if not turn.tool_calls:
                # `LLMTurn.__post_init__` rejects this case, but defend
                # explicitly anyway — fail-fast.
                raise RuntimeError(
                    "loop LLM returned a turn with neither text nor tool_calls"
                )

            results: list[ToolResult] = []
            for call in turn.tool_calls:
                tool_calls_used += 1
                if tool_calls_used > _MAX_TOOL_CALLS:
                    loop_overshot = True
                    break
                try:
                    content = self._dispatch_tool(call.name, call.args, cites)
                    results.append(ToolResult(name=call.name, content=content))
                except Exception as e:
                    # Tool exception → surface to model so it can self-
                    # correct on the next turn (mirrors AgenticTier's
                    # one allowed catch-all in the agent path).
                    results.append(
                        ToolResult(
                            name=call.name,
                            content={"error": f"{type(e).__name__}: {e}"},
                        )
                    )
            if loop_overshot:
                break
            try:
                turn = self._llm.respond_to_tool_results(results)
            except Exception as e:
                return self._partial_result(
                    cluster_hits=cluster_hits,
                    cites=cites,
                    summary_text=last_text,
                    tool_calls_used=tool_calls_used,
                    kind=payload.kind,
                    reason=f"llm_followup_failed: {type(e).__name__}",
                )

        if loop_overshot:
            # Issue acceptance criterion: "Tool budget exceeded → returns
            # partial summary with reduced confidence (no crash)".
            return self._partial_result(
                cluster_hits=cluster_hits,
                cites=cites,
                summary_text=last_text,
                tool_calls_used=tool_calls_used,
                kind=payload.kind,
                reason="tool_budget_exceeded",
            )

        # `last_text` is non-None here (loop only exits on text or
        # overshoot, and overshoot was handled above).
        assert last_text is not None
        action_items = _extract_action_items(last_text)
        linked_entity_ids = _extract_cited_node_ids(last_text)
        # Items: cluster hits the agent had access to, deduped. The
        # actual citations on the result come from `cites`.
        result_items = _dedup_hits(cluster_hits)
        relevance = (
            RELEVANCE_GROUNDED if cites.citations else RELEVANCE_UNGROUNDED
        )
        return WorkflowResult(
            answer=last_text,
            items=result_items,
            citations=list(cites.citations),
            tier_used="hybrid",
            relevance=relevance,
            latency_ms=0,
            workflow=type(self).name,
            extras={
                "kind": payload.kind,
                "tool_calls_used": tool_calls_used,
                "action_items": action_items,
                "linked_entity_ids": linked_entity_ids,
            },
        )

    # ---------- internal: T3 recall ----------

    def _t3_starting_cluster(
        self,
        payload: ThreadSummaryInput,
        ctx: QueryContext,
        cites: CitationCollector,
    ) -> list[Hit]:
        """Run HybridTier once per participant identifier and once per
        extracted id-token from the messages. Aggregate hits, dedup by
        node id (keep best score), and harvest citations.

        Topic phrases beyond raw id tokens are NOT surfaced as separate
        queries in R5c — the issue's implementation note pins free-form
        topic NER on R4 / GLiNER2 ("once R4 lands, swap to GLiNER2
        inference here for richer entity extraction"). Until then, the
        regex-based id tokens cover the high-precision recall step and
        the agent loop in step 2 is responsible for any further widening
        via traversal.
        """
        hybrid = self.tiers.get("hybrid")
        queries: list[str] = []
        for p in payload.participants:
            p_clean = p.strip()
            if p_clean:
                queries.append(p_clean)
        for token in _light_ner(payload.messages):
            queries.append(token)

        # Dedup queries while preserving order — keeps the LLM brief
        # stable (same input → same cluster).
        seen_q: set[str] = set()
        ordered_queries: list[str] = []
        for q in queries:
            key = q.lower()
            if key in seen_q:
                continue
            seen_q.add(key)
            ordered_queries.append(q)

        # Aggregate hits keyed by node id; keep the best score seen.
        best_hit: dict[str, Hit] = {}
        for q in ordered_queries:
            res = hybrid.search(q, ctx)
            for h in res.items[:_T3_PER_QUERY_TOP_K]:
                cur = best_hit.get(h.id)
                if cur is None or h.score > cur.score:
                    best_hit[h.id] = h
                # Pull provenance for every cluster node (citations on
                # the final result reflect the whole T3 cluster, not
                # just what the agent later touched).
                cites.add_node(self._store, h.id)
            # The HybridTier may also surface its own citations (it
            # does — see hybrid.py); merge them in.
            for c in res.citations:
                _maybe_add_citation(cites, c)
        return [best_hit[k] for k in sorted(best_hit)]

    # ---------- internal: tool dispatch ----------

    def _dispatch_tool(
        self,
        name: str,
        args: dict[str, Any],
        cites: CitationCollector,
    ) -> dict[str, Any]:
        """Three tools — `get_node`, `get_neighbors`, `get_source_record`.
        Anything else from the model is rejected (the LLM was told the
        surface; an out-of-scope name is a violation, not a soft path).
        """
        if not isinstance(args, dict):
            raise TypeError(f"tool args must be dict, got {type(args).__name__}")
        if name == "get_node":
            node_id = args["node_id"]
            if not isinstance(node_id, str) or not node_id:
                raise ValueError("get_node: node_id must be a non-empty string")
            cites.add_node(self._store, node_id)
            return _node_to_dict(self._store, node_id)
        if name == "get_neighbors":
            node_id = args["node_id"]
            if not isinstance(node_id, str) or not node_id:
                raise ValueError(
                    "get_neighbors: node_id must be a non-empty string"
                )
            relation_type = args.get("relation_type")
            if relation_type is not None and (
                not isinstance(relation_type, str) or not relation_type
            ):
                raise ValueError(
                    "get_neighbors: relation_type must be None or non-empty string"
                )
            depth = args.get("depth", 1)
            if not isinstance(depth, int) or not (1 <= depth <= _MAX_DEPTH):
                raise ValueError(
                    f"get_neighbors: depth must be int in [1, {_MAX_DEPTH}], got {depth!r}"
                )
            if self._store.get_node(node_id) is None:
                raise KeyError(f"node {node_id!r} not found")
            ids = sorted(self._store.neighbors(node_id, relation_type, depth))
            if len(ids) > _MAX_NEIGHBORS_PER_CALL:
                ids = ids[:_MAX_NEIGHBORS_PER_CALL]
            out: list[dict[str, Any]] = []
            for nid in ids:
                n = self._store.get_node(nid)
                if n is None:
                    # `neighbors` returned an id we then can't read back —
                    # store inconsistency, fail-fast (mirrors the same
                    # check in `customer_email._gather_neighbors`).
                    raise RuntimeError(
                        f"thread_summary: get_node returned None for known id {nid!r}"
                    )
                cites.add_node(self._store, nid)
                out.append(
                    {
                        "id": n.id,
                        "type": n.type,
                        "preview": _attrs_preview(n.attributes),
                    }
                )
            return {"node_id": node_id, "neighbors": out, "total": len(out)}
        if name == "get_source_record":
            source_file = args["source_file"]
            record_id = args["record_id"]
            if not isinstance(source_file, str) or not source_file:
                raise ValueError(
                    "get_source_record: source_file must be a non-empty string"
                )
            if not isinstance(record_id, str) or not record_id:
                raise ValueError(
                    "get_source_record: record_id must be a non-empty string"
                )
            rec = self._store.get_source_record(source_file, record_id)
            if rec is None:
                raise KeyError(
                    f"source record not found: {source_file!r} / {record_id!r}"
                )
            cites.add_source_record(source_file, record_id)
            return {
                "source_file": rec.source_file,
                "source_record_id": rec.source_record_id,
                "raw_record": rec.raw_record,
                "content_hash": rec.content_hash,
            }
        raise ValueError(
            f"unknown tool {name!r}; thread-summary only exposes "
            f"get_node / get_neighbors / get_source_record"
        )

    # ---------- internal: result helpers ----------

    def _partial_result(
        self,
        *,
        cluster_hits: list[Hit],
        cites: CitationCollector,
        summary_text: str | None,
        tool_calls_used: int,
        kind: str,
        reason: str,
    ) -> WorkflowResult:
        """Build a `WorkflowResult` for failure / overshoot paths.
        Surfaces whatever partial state was accumulated (cluster items,
        citations gathered before the failure, last text seen). Mirrors
        `AgenticTier._fail_result` shape: `relevance=0.0`.
        """
        return WorkflowResult(
            answer=summary_text,
            items=_dedup_hits(cluster_hits),
            citations=list(cites.citations),
            tier_used="hybrid",
            relevance=RELEVANCE_FAILED,
            latency_ms=0,
            workflow=type(self).name,
            extras={
                "kind": kind,
                "tool_calls_used": tool_calls_used,
                "reason": reason,
                "action_items": (
                    _extract_action_items(summary_text)
                    if summary_text is not None
                    else []
                ),
                "linked_entity_ids": (
                    _extract_cited_node_ids(summary_text)
                    if summary_text is not None
                    else []
                ),
            },
        )


# ---------- module-level helpers ----------


def _light_ner(messages: list[ThreadMessage]) -> list[str]:
    """Pull id-shaped tokens (`emp_NNNN`, `cust_NNNN`, ...) out of every
    message's text. Order-preserving, deduped, lowercase comparison.
    """
    seen: set[str] = set()
    out: list[str] = []
    for m in messages:
        for match in _ID_TOKEN_RE.findall(m.text):
            key = match.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(match)
    return out


_NODE_ID_RE: re.Pattern[str] = re.compile(r"\[node:([A-Za-z0-9_:\-./]+)\]")


def _extract_cited_node_ids(text: str) -> list[str]:
    """Order-preserving dedup of `[node:<id>]` markers in the summary."""
    seen: set[str] = set()
    out: list[str] = []
    for m in _NODE_ID_RE.findall(text):
        if m in seen:
            continue
        seen.add(m)
        out.append(m)
    return out


_ACTION_BULLET_RE: re.Pattern[str] = re.compile(
    r"^\s*[*\-]\s+(.+?)\s*$", re.MULTILINE
)


def _extract_action_items(text: str) -> list[str]:
    """Pull bullet items under the `## Decisions / Action items` heading.
    The compose prompt pins this header verbatim; if the LLM strays we
    return an empty list rather than guessing.
    """
    # Locate the section by heading + the next `##` (or end of text).
    sec_match = re.search(
        r"##\s*Decisions\s*/\s*Action items\s*\n(.*?)(?=\n##\s|\Z)",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if sec_match is None:
        return []
    section = sec_match.group(1)
    items: list[str] = []
    for m in _ACTION_BULLET_RE.finditer(section):
        item = m.group(1).strip()
        if item:
            items.append(item)
    return items


def _maybe_add_citation(cites: CitationCollector, c: Citation) -> None:
    """Merge a `Citation` produced by a tier into the collector,
    respecting its dedup key. We do not have a node id at this layer,
    so this is a thin direct add.
    """
    key = (c.source_file, c.source_record_id, c.source_field)
    if key in cites._seen:
        return
    cites._seen.add(key)
    cites.citations.append(c)


def _dedup_hits(hits: list[Hit]) -> list[Hit]:
    """Dedup hits by id, keep first occurrence (which is best-score by
    construction in `_t3_starting_cluster`)."""
    seen: set[str] = set()
    out: list[Hit] = []
    for h in hits:
        if h.id in seen:
            continue
        seen.add(h.id)
        out.append(h)
    return out


def _format_loop_brief(
    payload: ThreadSummaryInput, cluster_hits: list[Hit]
) -> str:
    """Build the brief the loop LLM sees on its first turn. Bullets
    rather than raw JSON for readability — same convention as
    `customer_email._format_brief`.
    """
    lines: list[str] = []
    lines.append(f"=== THREAD ({payload.kind}) ===")
    lines.append("")
    lines.append("--- Participants ---")
    if payload.participants:
        for p in payload.participants:
            lines.append(f"- {p}")
    else:
        lines.append("- (none declared)")
    lines.append("")
    lines.append("--- Messages ---")
    for i, m in enumerate(payload.messages, start=1):
        lines.append(f"[{i}] ({m.ts}) {m.author}: {m.text}")
    lines.append("")
    lines.append("--- Starting cluster (T3 semantic recall) ---")
    if cluster_hits:
        for h in cluster_hits:
            lines.append(
                f"- node id: {h.id}  (score={h.score:.3f})  preview: {h.preview}"
            )
    else:
        lines.append("- (no cluster — recall returned no candidates)")
    lines.append("")
    lines.append(
        "Plan tool calls before issuing them. Stop calling and emit the "
        "final summary as soon as you have enough evidence."
    )
    return "\n".join(lines)


def _thread_summary_tool_definitions() -> list[ToolDefinition]:
    """Three-tool surface — narrower than the standard `tool_definitions()`.
    Authored fresh here (rather than filtering the standard list) so the
    descriptions are tuned for the thread-summary context.
    """
    return [
        ToolDefinition(
            name="get_node",
            description=(
                "Fetch a single node by id, including all attributes "
                "and provenance. Use to disambiguate a participant or "
                "expand on a starting-cluster entity."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "node_id": {
                        "type": "string",
                        "description": "Canonical node id from the cluster or a prior tool result.",
                    },
                },
                "required": ["node_id"],
            },
        ),
        ToolDefinition(
            name="get_neighbors",
            description=(
                "Fetch direct (or up to depth-3) neighbors of a node, "
                "optionally filtered by relation type. Use to traverse "
                "from a starting-cluster node to related entities the "
                "thread implicitly references."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "node_id": {
                        "type": "string",
                        "description": "Source node id to walk from.",
                    },
                    "relation_type": {
                        "type": "string",
                        "description": "Optional canonical relation filter.",
                    },
                    "depth": {
                        "type": "integer",
                        "description": f"Hop depth (1..{_MAX_DEPTH}); default 1.",
                    },
                },
                "required": ["node_id"],
            },
        ),
        ToolDefinition(
            name="get_source_record",
            description=(
                "Fetch the original ingested record verbatim. Use to "
                "ground a non-trivial claim in raw evidence; this also "
                "produces a high-grade citation."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "source_file": {"type": "string"},
                    "record_id": {"type": "string"},
                },
                "required": ["source_file", "record_id"],
            },
        ),
    ]


__all__ = [
    "ThreadMessage",
    "ThreadSummaryInput",
    "ThreadSummaryWorkflow",
]
