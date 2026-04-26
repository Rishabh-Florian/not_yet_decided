"""AgenticTier — Tier 4 of the retrieval cascade (Gemini function-calling).

Acts as the destination of the `route_to="agentic"` directive emitted
by `RouterTier` on `analytical` intent. It is also reachable via the
default cascade fallthrough — but its 10s wall-clock budget makes it
expensive enough that we want the router (or a `prefer_tier="agentic"`
override) to be the typical entry path.

How it works
------------

A single Gemini function-calling loop, bounded on three axes:

* `max_iterations` (default 6) — hard cap on number of tool calls per
  query. The issue mandates this; on overshoot the loop exits with the
  last partial result and `relevance=0.0`.
* `wall_clock_budget_ms` (default 10_000) — wall-clock guard. If the
  cumulative time across LLM turns + tool calls exceeds the budget we
  exit early. Honors `QueryContext.max_latency_ms` when stricter.
* Tool-call results are bounded by per-tool size caps in `tools.py`
  (`_MAX_K`, `_MAX_DEPTH`, `_MAX_NEIGHBORS`).

The loop driver:

1. Send the system prompt + the user query to the LLM with
   `tool_definitions()` attached.
2. The LLM either returns text (final answer) or a tool-call request.
3. On tool-call: dispatch through `ToolBox.call`, wrap the result (or
   error message) into a function-response message, and continue.
4. On text answer: stop, return the answer + accumulated citations +
   computed `Hit.score` and `relevance`.

Scoring
-------

Per the project's "no magic numbers" rule (`models.py` contract +
issue #10): `Hit.score` and `QueryResult.relevance` are NOT the
agent's own self-rated confidence. The agent's self-rating, if any,
goes into `Provenance.model_self_score` (audit-only, not used here).

Instead, the algorithmic recipe (mirrors the issue spec):

* `relevance = 0.7` if the agent emitted a final answer AND the
  citation collector accumulated >= 1 unique citation during the
  loop. Justification: a grounded LLM answer is empirically more
  trustworthy than ungrounded; one citation is the minimum
  threshold the issue defines.
* `relevance = 0.3` if the agent emitted a final answer with zero
  citations. The answer exists but cannot be audited; we surface
  it but mark it weakly.
* `relevance = 0.0` on timeout, max-iteration overshoot, or any
  exception during the loop. Empty / partial result; the cascade
  orchestrator escalates past.

`Hit.score` mirrors `relevance` — there is one synthetic `Hit` per
agentic answer, kind=`"node"`, id=`"agentic:answer"`, with the same
score as the overall relevance. The actual evidence the agent
gathered lives in `citations`, not `items` (the items list is a
single envelope around the prose answer).

Deep module: `AgenticTier.search` is the only public method. The loop
driver, prompt builder, LLM client, and tool dispatch are all
internal.
"""
from __future__ import annotations

import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from backend.graph.store import GraphStore

from .embedder import Embedder
from .models import Citation, Hit, QueryContext, QueryResult
from .tiers import Tier
from .tools import CitationCollector, ToolBox, ToolDefinition, tool_definitions


# Defaults per issue spec. Tunable via the constructor.
_DEFAULT_MAX_ITERATIONS: int = 6
_DEFAULT_WALL_CLOCK_BUDGET_MS: int = 10_000
_DEFAULT_MODEL: str = "gemini-2.5-flash"

# Algorithmic relevance values — see module docstring for the recipe.
# Pinned as named constants so the eval harness can reference them
# without re-reading the loop driver.
RELEVANCE_GROUNDED: float = 0.7
RELEVANCE_UNGROUNDED: float = 0.3
RELEVANCE_FAILED: float = 0.0


_SYSTEM_PROMPT = (
    "You are a knowledge graph reasoning assistant. You answer "
    "natural-language analytical questions over an enterprise "
    "knowledge graph by issuing tool calls.\n\n"
    "Rules:\n"
    "1. Always ground your answer in tool-call evidence. If you cannot "
    "find evidence, say so explicitly.\n"
    "2. Prefer typed pattern_query over fulltext when you know the "
    "node and relation types involved.\n"
    "3. Use get_source_record to surface the original ingested record "
    "for any non-trivial claim — this is what produces a citation.\n"
    "4. Keep tool calls under 6 total. Each call costs latency; plan "
    "before calling.\n"
    "5. When you have enough evidence, emit the final answer as plain "
    "text — do NOT call another tool."
)


# ---------------------------------------------------------------------------
# LLMClient Protocol + implementations
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolCall:
    """One LLM-requested tool invocation. SDK-agnostic shape."""

    name: str
    args: dict[str, Any]


@dataclass(frozen=True)
class ToolResult:
    """One server-side tool response, fed back to the LLM next turn."""

    name: str
    content: Any  # JSON-serializable; the LLM client converts to its native shape


@dataclass(frozen=True)
class LLMTurn:
    """One LLM response. Either `text` (final) is set OR `tool_calls`
    is non-empty — never both, never neither. The driver uses the
    presence of `tool_calls` to decide between dispatching and
    terminating.
    """

    text: str | None
    tool_calls: list[ToolCall]

    def __post_init__(self) -> None:
        # `text` and `tool_calls` are mutually exclusive *signals*. An
        # empty string text is treated as no text. `frozen=True` keeps
        # the LLMTurn immutable post-validation.
        has_text = self.text is not None and self.text.strip() != ""
        has_calls = len(self.tool_calls) > 0
        if has_text and has_calls:
            raise ValueError(
                "LLMTurn must carry either text or tool_calls, not both"
            )
        if not has_text and not has_calls:
            raise ValueError(
                "LLMTurn must carry at least one of text / tool_calls"
            )


class LLMClient(ABC):
    """Pluggable backend for the AgenticTier function-calling loop.

    Implementations are stateless across queries — the AgenticTier
    creates a fresh conversation per `search()` call and feeds it
    through `start` + `respond_to_tool_results`. This mirrors how
    `Embedder` and `EntityRouter` are isolated behind Protocols so
    `StubLLMClient` can be swapped in for tests with no network.
    """

    @abstractmethod
    def start(
        self,
        *,
        system_prompt: str,
        user_query: str,
        tools: list[ToolDefinition],
    ) -> LLMTurn:
        """Open a new conversation. Returns the model's first turn —
        either a final answer or a list of tool calls.
        """

    @abstractmethod
    def respond_to_tool_results(self, results: list[ToolResult]) -> LLMTurn:
        """Continue the conversation after dispatching tool calls.
        Returns the model's next turn (final or further tool calls).
        """


class StubLLMClient(LLMClient):
    """Deterministic LLM stand-in for unit tests / no-network fallback.

    Driven by a scripted list of `LLMTurn`s. The first call to `start`
    pops index 0, each subsequent `respond_to_tool_results` pops the
    next one. Raises `RuntimeError` if the script runs out before the
    loop terminates — that's a test bug, not a runtime condition.

    Carries NO semantic intelligence: the script is what makes the
    loop converge. Tests build the script to exercise specific paths
    (one tool call then answer / six tool calls then bust the cap /
    pattern_query that raises validation / etc.).
    """

    def __init__(self, scripted_turns: list[LLMTurn]) -> None:
        if not scripted_turns:
            raise ValueError("StubLLMClient requires at least one scripted turn")
        for t in scripted_turns:
            if not isinstance(t, LLMTurn):
                raise TypeError(
                    f"scripted turn must be LLMTurn, got {type(t).__name__}"
                )
        self._script = list(scripted_turns)
        self._cursor = 0
        # Recorded for test assertions on what the agent actually sent.
        self.calls_received: list[list[ToolResult]] = []
        self.starts_received: list[tuple[str, str, tuple[str, ...]]] = []

    def _pop(self) -> LLMTurn:
        if self._cursor >= len(self._script):
            raise RuntimeError(
                "StubLLMClient script exhausted; the test did not script enough turns"
            )
        turn = self._script[self._cursor]
        self._cursor += 1
        return turn

    def start(
        self,
        *,
        system_prompt: str,
        user_query: str,
        tools: list[ToolDefinition],
    ) -> LLMTurn:
        if not isinstance(system_prompt, str) or not system_prompt:
            raise ValueError("system_prompt must be non-empty string")
        if not isinstance(user_query, str) or not user_query.strip():
            raise ValueError("user_query must be non-empty string")
        self.starts_received.append(
            (system_prompt, user_query, tuple(t.name for t in tools))
        )
        return self._pop()

    def respond_to_tool_results(self, results: list[ToolResult]) -> LLMTurn:
        self.calls_received.append(list(results))
        return self._pop()


class NoopLLMClient(LLMClient):
    """Always-returns-one-fixed-text LLM stand-in.

    Used by `build_orchestrator_with_store` when `QONTEXT_AGENTIC` is
    `noop` (the default). Cascade fallthrough to AgenticTier with this
    backend produces a marker answer + ungrounded relevance (0.3) so
    the cascade escalates past to `stub`. This keeps the cascade
    typed end-to-end without pretending to reason.

    Distinct from `StubLLMClient` (which is scripted, single-use, for
    tests): `NoopLLMClient` is reusable across queries.
    """

    DEFAULT_TEXT: str = (
        "AgenticTier is not configured (set QONTEXT_AGENTIC=gemini and "
        "GEMINI_API_KEY). No analytical reasoning was performed."
    )

    def __init__(self, text: str = DEFAULT_TEXT) -> None:
        if not isinstance(text, str) or not text.strip():
            raise ValueError("NoopLLMClient text must be a non-empty string")
        self._text = text

    def start(
        self,
        *,
        system_prompt: str,
        user_query: str,
        tools: list[ToolDefinition],
    ) -> LLMTurn:
        return LLMTurn(text=self._text, tool_calls=[])

    def respond_to_tool_results(self, results: list[ToolResult]) -> LLMTurn:
        return LLMTurn(text=self._text, tool_calls=[])


class GeminiLLMClient(LLMClient):
    """Gemini Flash 2.5 backend via the `google-genai` SDK.

    Imports `google.genai` lazily (the SDK is heavy on Windows). Raises
    on `__init__` if `GEMINI_API_KEY` (or override `api_key`) is not
    set — fail-fast per the project rule. The class refuses to fall
    back silently to the stub.

    Uses `function_declarations` for tool surface so each `LLMTurn`
    parses cleanly into our SDK-agnostic dataclasses. Temperature
    pinned at 0.0 for determinism (the eval harness needs reproducible
    runs); raise the floor if creative phrasing is desired.
    """

    API_KEY_ENV: str = "GEMINI_API_KEY"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = _DEFAULT_MODEL,
        temperature: float = 0.0,
    ) -> None:
        key = api_key if api_key is not None else os.environ.get(self.API_KEY_ENV)
        if not key:
            raise RuntimeError(
                f"GeminiLLMClient requires {self.API_KEY_ENV} (or api_key kwarg). "
                "Use StubLLMClient for tests / no-network fallback."
            )
        try:
            from google import genai  # type: ignore[import-not-found]
        except ImportError as e:
            raise ImportError(
                "GeminiLLMClient requires the `google-genai` SDK. "
                "Install with `uv add google-genai`."
            ) from e
        self._client = genai.Client(api_key=key)
        self._model = model
        self._temperature = temperature
        # Lazy state: the conversation is opened in `start()` so each
        # AgenticTier query gets a fresh context.
        self._chat: Any = None
        self._tool_decls: list[Any] = []

    def _build_function_declarations(
        self, tools: list[ToolDefinition]
    ) -> list[Any]:
        from google.genai import types  # type: ignore[import-not-found]

        decls: list[Any] = []
        for t in tools:
            # google-genai accepts plain JSON-Schema-style dicts on
            # FunctionDeclaration.parameters at runtime even though
            # the type annotation is `Schema`. Bypass the static check
            # by passing via dict-unpack, which mirrors how the SDK's
            # own examples use plain dicts.
            decls.append(
                types.FunctionDeclaration(
                    name=t.name,
                    description=t.description,
                    parameters=t.parameters,  # type: ignore[arg-type]
                )
            )
        return decls

    def _parse_turn(self, response: Any) -> LLMTurn:
        # Gemini's response shape varies slightly across SDK versions.
        # We extract function calls + text from the first candidate's
        # parts. The SDK exposes `function_call` and `text` on each
        # part; we coalesce.
        tool_calls: list[ToolCall] = []
        text_parts: list[str] = []
        candidates = getattr(response, "candidates", None) or []
        if not candidates:
            raise RuntimeError("Gemini response has no candidates")
        content = getattr(candidates[0], "content", None)
        if content is None:
            raise RuntimeError("Gemini candidate has no content")
        parts = getattr(content, "parts", None) or []
        for part in parts:
            fc = getattr(part, "function_call", None)
            if fc is not None:
                name = getattr(fc, "name", None)
                if not name:
                    raise RuntimeError("Gemini function_call missing name")
                args = dict(getattr(fc, "args", {}) or {})
                tool_calls.append(ToolCall(name=name, args=args))
                continue
            text = getattr(part, "text", None)
            if isinstance(text, str) and text.strip():
                text_parts.append(text)
        joined_text = "\n".join(text_parts).strip() or None
        if tool_calls and joined_text:
            # Gemini occasionally emits both prose and a tool call. We
            # prioritize the tool call: the prose is "thinking out
            # loud", not a final answer. Drop the text.
            joined_text = None
        if not tool_calls and not joined_text:
            raise RuntimeError(
                "Gemini turn had neither text nor function_call — empty response"
            )
        return LLMTurn(text=joined_text, tool_calls=tool_calls)

    def start(
        self,
        *,
        system_prompt: str,
        user_query: str,
        tools: list[ToolDefinition],
    ) -> LLMTurn:
        from google.genai import types  # type: ignore[import-not-found]

        self._tool_decls = self._build_function_declarations(tools)
        cfg = types.GenerateContentConfig(
            system_instruction=system_prompt,
            tools=[types.Tool(function_declarations=self._tool_decls)],
            temperature=self._temperature,
        )
        # `chats` keeps the conversation history server-side per
        # SDK semantics; `send_message` is the per-turn primitive.
        self._chat = self._client.chats.create(model=self._model, config=cfg)
        response = self._chat.send_message(user_query)
        return self._parse_turn(response)

    def respond_to_tool_results(self, results: list[ToolResult]) -> LLMTurn:
        if self._chat is None:
            raise RuntimeError("respond_to_tool_results called before start()")
        from google.genai import types  # type: ignore[import-not-found]

        parts: list[Any] = []
        for r in results:
            parts.append(
                types.Part.from_function_response(
                    name=r.name,
                    response=(
                        r.content
                        if isinstance(r.content, dict)
                        else {"result": r.content}
                    ),
                )
            )
        response = self._chat.send_message(parts)
        return self._parse_turn(response)


# ---------------------------------------------------------------------------
# AgenticTier
# ---------------------------------------------------------------------------


class AgenticTier(Tier):
    """Tier 4: bounded Gemini function-calling loop over the graph store.

    Constructor signature mirrors HybridTier — store + a pluggable
    backend (here `LLMClient`) — so wiring in `build_orchestrator_with_store`
    follows the same pattern. Defaults to `StubLLMClient` is NOT
    appropriate (a stub script per query is meaningless); callers
    must pass an explicit `LLMClient`. The orchestrator factory
    handles the env-driven selection.

    Confidence semantics: see module docstring. Briefly:

    * answer + >= 1 citation → relevance 0.7 (`RELEVANCE_GROUNDED`)
    * answer + zero citations → relevance 0.3 (`RELEVANCE_UNGROUNDED`)
    * timeout / overshoot / exception → relevance 0.0 (`RELEVANCE_FAILED`)
    """

    def __init__(
        self,
        store: GraphStore,
        embedder: Embedder,
        llm: LLMClient,
        *,
        name: str = "agentic",
        max_iterations: int = _DEFAULT_MAX_ITERATIONS,
        wall_clock_budget_ms: int = _DEFAULT_WALL_CLOCK_BUDGET_MS,
        system_prompt: str = _SYSTEM_PROMPT,
    ) -> None:
        if not isinstance(store, GraphStore):
            raise TypeError(f"store must be GraphStore, got {type(store).__name__}")
        if not isinstance(embedder, Embedder):
            raise TypeError(
                f"embedder must implement Embedder protocol, got {type(embedder).__name__}"
            )
        if not isinstance(llm, LLMClient):
            raise TypeError(
                f"llm must be an LLMClient instance, got {type(llm).__name__}"
            )
        if not name or not name.islower():
            raise ValueError(
                f"AgenticTier name must be a non-empty lowercase identifier, got {name!r}"
            )
        if max_iterations < 1:
            raise ValueError(f"max_iterations must be >= 1, got {max_iterations}")
        if wall_clock_budget_ms < 1:
            raise ValueError(
                f"wall_clock_budget_ms must be >= 1, got {wall_clock_budget_ms}"
            )
        if not isinstance(system_prompt, str) or not system_prompt.strip():
            raise ValueError("system_prompt must be a non-empty string")
        self._store = store
        self._toolbox = ToolBox(store, embedder)
        self._llm = llm
        self._name = name
        self._max_iterations = max_iterations
        self._wall_clock_budget_ms = wall_clock_budget_ms
        self._system_prompt = system_prompt

    @property
    def name(self) -> str:
        return self._name

    def search(self, query: str, ctx: QueryContext) -> QueryResult:
        if not isinstance(query, str):
            raise TypeError(f"query must be str, got {type(query).__name__}")
        if not query.strip():
            raise ValueError("query must be non-empty / non-whitespace")

        budget_ms = self._wall_clock_budget_ms
        if ctx.max_latency_ms is not None:
            # Honor the stricter of the tier's own budget vs the
            # caller's hint — never exceed the caller.
            budget_ms = min(budget_ms, ctx.max_latency_ms)

        cites = CitationCollector()
        tools = tool_definitions()
        start_time = time.perf_counter()

        def elapsed_ms() -> int:
            return int((time.perf_counter() - start_time) * 1000)

        try:
            turn = self._llm.start(
                system_prompt=self._system_prompt,
                user_query=query,
                tools=tools,
            )
        except Exception as e:
            # Fail-fast: a misconfigured key / network outage / bad model id
            # must surface as 500 with a useful message — not be silently
            # masked as "no relevant context found" (relevance=0.0).
            raise RuntimeError(f"agentic LLM call failed: {e}") from e

        iterations = 0
        last_text: str | None = None

        while True:
            if turn.text is not None:
                last_text = turn.text
                break
            iterations += 1
            if iterations > self._max_iterations:
                # Overshoot — issue spec says: return relevance=0.0
                # with the last partial result. We have no final
                # answer at this point.
                return self._fail_result(
                    answer=None, citations=cites.citations
                )
            if elapsed_ms() > budget_ms:
                return self._fail_result(
                    answer=last_text, citations=cites.citations
                )
            results: list[ToolResult] = []
            for call in turn.tool_calls:
                # Per-call dispatch: convert exceptions into a tool
                # response the model can read on the next turn (issue
                # acceptance criterion 3). This is the only catch-all
                # in the agent path; the underlying tool functions
                # themselves still fail-fast.
                try:
                    content = self._toolbox.call(call.name, call.args, cites)
                    results.append(
                        ToolResult(name=call.name, content=_jsonable(content))
                    )
                except Exception as e:
                    results.append(
                        ToolResult(
                            name=call.name,
                            content={"error": f"{type(e).__name__}: {e}"},
                        )
                    )
            try:
                turn = self._llm.respond_to_tool_results(results)
            except Exception as e:
                # Fail-fast on LLM-client failure (see `start()` rationale
                # above). Tool-call exceptions are caught separately and
                # surfaced back to the model as `{"error": ...}` results.
                raise RuntimeError(
                    f"agentic LLM call failed: {e}"
                ) from e

        # Final answer in `last_text`. Score per the algorithmic recipe.
        if last_text is None or not last_text.strip():
            return self._fail_result(citations=cites.citations)
        relevance = (
            RELEVANCE_GROUNDED if cites.citations else RELEVANCE_UNGROUNDED
        )
        item = Hit(
            kind="node",
            id="agentic:answer",
            score=relevance,
            preview=last_text[:200],
        )
        return QueryResult(
            answer=last_text,
            items=[item],
            citations=cites.citations,
            tier_used=self._name,
            relevance=relevance,
            latency_ms=0,  # orchestrator overwrites
        )

    # ---------- internal ----------

    def _fail_result(
        self,
        *,
        answer: str | None = None,
        citations: list[Citation] | None = None,
    ) -> QueryResult:
        """Build the canonical failure / overshoot result.

        `relevance=0.0` so the cascade orchestrator escalates past
        AgenticTier (back to `stub` — the terminal fallback). Any
        partial citations gathered before the failure are still
        surfaced so the UI can show what evidence the agent did
        manage to find.
        """
        return QueryResult(
            answer=answer,
            items=[],
            citations=list(citations) if citations else [],
            tier_used=self._name,
            relevance=RELEVANCE_FAILED,
            latency_ms=0,
        )


def _jsonable(value: Any) -> Any:
    """Convert tool return values into JSON-friendly structures so the
    LLM client can serialize them. Lists/dicts pass through; pydantic
    `Hit`s are dumped via `model_dump`.
    """
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, Hit):
        return value.model_dump()
    return value
