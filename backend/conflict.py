"""Fact-level conflict resolution for the knowledge graph.

A *conflict* is what we call it when two facts about the same
`(node_id, attribute)` disagree. Conflicts arise at MERGE time inside
`GraphStore.add_node`: an existing attribute value is about to be
overwritten by a different incoming value.

This module is the deep choke point for "what do we do about it?".
Public surface (kept small on purpose):

  * :func:`decide` — pure decision over two `Candidate`s, returns a
    `Decision`. No IO, fully deterministic, parametrized-test friendly.
  * :class:`Candidate` — one side of a conflict (value + provenance
    metadata sufficient to tier the fact).
  * :class:`Decision` / :class:`Verdict` — the outcome the store acts on.
  * :class:`Conflict` — a persisted record of a queued conflict (used
    for LLM_TRIAGE / ESCALATE verdicts; AUTO_MERGE / AUTO_PICK never
    land here, the existing append-only `provenance` already audits them).
  * :class:`ConflictStore` — SQLite CRUD over the `conflicts` table.

The decision table, in order (first match wins):

  1. equal after normalize (`strip + casefold`)              → AUTO_MERGE
  2. same `(source_file, source_record_id)` on both sides    → AUTO_PICK (incoming wins)
  3. either side is `FactConfidence.HUMAN`                   → AUTO_PICK (HUMAN side wins)
  4. different rungs of the confidence ladder                → AUTO_PICK (higher rung wins)
  5. both `FactConfidence.INFERRED`                          → LLM_TRIAGE
  6. otherwise (same rung at EXACT/GROUNDED/HUMAN/HUMAN)     → ESCALATE

Why these specific lanes:

  * Rule 2 is what makes the push-mode source-update endpoint work: a
    record re-ingested from the same source-of-truth is not a conflict,
    it's an authoritative correction from the system that owns that fact.
    HUMAN-vs-HUMAN from the same `human_edits` record is also a self-update
    (a fresh resolution overwriting a stale one), so this rule sits above
    the HUMAN-overrides rule.
  * HUMAN is sacred — humans can override any machine-derived fact, and a
    HUMAN-vs-HUMAN conflict from DIFFERENT edit sessions needs another human.
  * EXACT-vs-EXACT across DIFFERENT sources literally means two structured
    fields disagree — a model cannot break that tie truthfully, so escalate.
  * Free-text INFERRED-vs-INFERRED conflicts ("Acme Corp" vs "Acme Inc.")
    are where an LLM can plausibly resolve; they get the LLM_TRIAGE lane.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Iterator, Literal

from pydantic import BaseModel

from backend.models.graph import FactConfidence, Provenance


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


class Verdict(StrEnum):
    """The four lanes a conflict can be routed to. See module docstring."""

    AUTO_MERGE = "auto_merge"
    AUTO_PICK = "auto_pick"
    LLM_TRIAGE = "llm_triage"
    ESCALATE = "escalate"


class Candidate(BaseModel):
    """One side of a conflict: a proposed value plus the provenance metadata
    that produced it. Two candidates with different values constitute a
    conflict; their `confidence` is the primary tiebreaker.
    """

    value: Any
    confidence: FactConfidence
    source_file: str
    source_record_id: str


class Decision(BaseModel):
    """Outcome of :func:`decide`.

    * ``verdict`` — which lane the conflict was routed to.
    * ``winner`` — for AUTO_MERGE / AUTO_PICK, the side whose value the
      store should write. ``None`` for LLM_TRIAGE and ESCALATE — those keep
      the existing value and queue a conflict row for downstream handling.
    * ``reason`` — short stable string identifying the rule that fired
      (used as the audit-log key, never user-facing copy).
    """

    verdict: Verdict
    winner: Literal["existing", "incoming"] | None
    reason: str


# ---------------------------------------------------------------------------
# Decision logic
# ---------------------------------------------------------------------------


# Higher number = higher rung. HUMAN is its own rung above EXACT because a
# human edit is a deliberate override, not a structured-source read.
_LADDER: dict[FactConfidence, int] = {
    FactConfidence.HUMAN: 3,
    FactConfidence.EXACT: 2,
    FactConfidence.GROUNDED: 1,
    FactConfidence.INFERRED: 0,
}


def _normalize(value: Any) -> str:
    """Cheap-and-honest normalize for v1: stringify, strip, casefold.

    We deliberately do NOT do email/phone/date normalization here. Those
    transformers exist in `backend/ingest/runtime.py` and may be wired in
    later per-attribute, but for v1 we accept the extra escalations rather
    than ship hidden type-aware logic that's harder to reason about.
    """
    return str(value).strip().casefold()


def decide(existing: Candidate, incoming: Candidate) -> Decision:
    """Apply the decision table to a pair of competing facts.

    Pure function: no IO, no side effects, no global state. The store calls
    this once per attribute that differs between an existing node and an
    incoming MERGE; the returned :class:`Decision` tells the store whether
    to write through (AUTO_MERGE / AUTO_PICK), keep the existing value and
    enqueue a conflict (LLM_TRIAGE / ESCALATE).
    """
    if _normalize(existing.value) == _normalize(incoming.value):
        # winner=existing keeps the canonical form stable across re-ingests.
        return Decision(
            verdict=Verdict.AUTO_MERGE,
            winner="existing",
            reason="equal_after_normalize",
        )

    # The source-of-truth is correcting itself — the push-mode update flow
    # depends on this rule. Sits above the HUMAN-overrides + ladder rules
    # because a self-update is not a cross-source conflict. Excludes the
    # `<unattributed>` sentinel so two legacy provenance-less candidates
    # don't accidentally self-merge.
    if (existing.source_file == incoming.source_file
            and existing.source_record_id == incoming.source_record_id
            and existing.source_file != _UNATTRIBUTED_SENTINEL):
        return Decision(verdict=Verdict.AUTO_PICK, winner="incoming", reason="same_source_updated")

    existing_human = existing.confidence == FactConfidence.HUMAN
    incoming_human = incoming.confidence == FactConfidence.HUMAN
    if existing_human and not incoming_human:
        return Decision(verdict=Verdict.AUTO_PICK, winner="existing", reason="human_overrides")
    if incoming_human and not existing_human:
        return Decision(verdict=Verdict.AUTO_PICK, winner="incoming", reason="human_overrides")
    # Both HUMAN with different values falls through to ESCALATE — two humans
    # disagreeing needs a third human, not a ladder rule.

    e_rung = _LADDER[existing.confidence]
    i_rung = _LADDER[incoming.confidence]
    if e_rung > i_rung:
        return Decision(verdict=Verdict.AUTO_PICK, winner="existing", reason="higher_confidence_wins")
    if i_rung > e_rung:
        return Decision(verdict=Verdict.AUTO_PICK, winner="incoming", reason="higher_confidence_wins")

    if existing.confidence == FactConfidence.INFERRED:
        return Decision(verdict=Verdict.LLM_TRIAGE, winner=None, reason="both_inferred")

    return Decision(verdict=Verdict.ESCALATE, winner=None, reason="tied_at_confident_rung")


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


# AUTO verdicts never reach the store — they're handled silently in
# `add_node`. Only LLM_TRIAGE and ESCALATE persist as queueable rows.
_PERSISTABLE_VERDICTS: frozenset[Verdict] = frozenset({
    Verdict.LLM_TRIAGE,
    Verdict.ESCALATE,
})


class Conflict(BaseModel):
    """A persisted conflict awaiting (or having received) a resolution."""

    id: int
    node_id: str
    attribute: str
    existing: Candidate
    incoming: Candidate
    verdict: Verdict
    reason: str
    status: Literal["open", "resolved"]
    detected_at: datetime
    resolved_at: datetime | None = None
    resolved_by: str | None = None
    chosen_value: Any | None = None
    resolution_method: Literal["human", "llm"] | None = None


# HTTP shapes — kept here next to `Conflict` so the bounded context owns
# its own surface; FastAPI consumes these directly as `response_model`
# without a separate marshalling helper.


class ConflictListResponse(BaseModel):
    conflicts: list[Conflict]
    status: Literal["open", "resolved"]
    total: int


class ResolveConflictRequest(BaseModel):
    value: Any
    editor: str


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


def _parse_iso(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s) if s else None


class ConflictStore:
    """SQLite CRUD over the `conflicts` table.

    Constructed from a sqlite3 connection (typically `GraphStore._conn`,
    so a single transaction context covers graph writes + conflict-row
    writes). The schema lives in `backend/graph/schema.sql`; this class
    assumes it has already been applied.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        # Methods do not commit. Callers (GraphStore.add_node, the REST
        # endpoint resolve handler) own transaction lifecycle so that
        # conflict writes can compose with the staged graph atomicity in
        # `add_node` (provenance staged → Neo4j MERGE → commit).
        self._conn = conn

    # ---------- record / get ----------

    def record(
        self,
        *,
        node_id: str,
        attribute: str,
        existing: Candidate,
        incoming: Candidate,
        verdict: Verdict,
        reason: str,
    ) -> int:
        """Insert (or replace incoming side of) an open conflict.

        Idempotent on `(node_id, attribute)` while the row is open: if a
        previous open conflict exists for the same key, this updates its
        incoming side and returns the existing id (newer data wins, no
        duplicate queue rows). After a row is resolved, recording another
        conflict for the same key creates a new row.

        Raises `ValueError` if `verdict` is not persistable (only
        LLM_TRIAGE and ESCALATE land in the queue; auto-resolutions are
        silent).
        """
        if verdict not in _PERSISTABLE_VERDICTS:
            raise ValueError(
                f"verdict {verdict!r} is not persistable; "
                f"only {sorted(v.value for v in _PERSISTABLE_VERDICTS)} reach the queue"
            )

        existing_open = self._conn.execute(
            """SELECT id FROM conflicts
               WHERE node_id = ? AND attribute = ? AND status = 'open'""",
            (node_id, attribute),
        ).fetchone()

        if existing_open is not None:
            cid = int(existing_open["id"])
            self._conn.execute(
                """UPDATE conflicts
                   SET incoming_value = ?,
                       incoming_confidence = ?,
                       incoming_source_file = ?,
                       incoming_source_record_id = ?,
                       verdict = ?,
                       reason = ?,
                       detected_at = ?
                   WHERE id = ?""",
                (
                    str(incoming.value),
                    incoming.confidence.value,
                    incoming.source_file,
                    incoming.source_record_id,
                    verdict.value,
                    reason,
                    _iso(_now()),
                    cid,
                ),
            )
            return cid

        cur = self._conn.execute(
            """INSERT INTO conflicts (
                   node_id, attribute,
                   existing_value, existing_confidence,
                   existing_source_file, existing_source_record_id,
                   incoming_value, incoming_confidence,
                   incoming_source_file, incoming_source_record_id,
                   verdict, reason, status, detected_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)""",
            (
                node_id, attribute,
                str(existing.value), existing.confidence.value,
                existing.source_file, existing.source_record_id,
                str(incoming.value), incoming.confidence.value,
                incoming.source_file, incoming.source_record_id,
                verdict.value, reason, _iso(_now()),
            ),
        )
        assert cur.lastrowid is not None
        return int(cur.lastrowid)

    def get(self, conflict_id: int) -> Conflict | None:
        row = self._conn.execute(
            "SELECT * FROM conflicts WHERE id = ?", (conflict_id,),
        ).fetchone()
        return _row_to_conflict(row) if row is not None else None

    # ---------- list ----------

    def list(
        self,
        *,
        status: Literal["open", "resolved"] = "open",
        node_id: str | None = None,
        attribute: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Iterator[Conflict]:
        # Build the WHERE clause from optional filters. SQL parameters are
        # always bound — no string interpolation of caller input.
        clauses = ["status = ?"]
        params: list[Any] = [status]
        if node_id is not None:
            clauses.append("node_id = ?")
            params.append(node_id)
        if attribute is not None:
            clauses.append("attribute = ?")
            params.append(attribute)
        where = " AND ".join(clauses)
        params.extend([int(limit), int(offset)])
        rows = self._conn.execute(
            f"SELECT * FROM conflicts WHERE {where} "
            f"ORDER BY id ASC LIMIT ? OFFSET ?",
            params,
        ).fetchall()
        for row in rows:
            yield _row_to_conflict(row)

    # ---------- resolve ----------

    def resolve(
        self,
        conflict_id: int,
        *,
        chosen_value: Any,
        resolution_method: Literal["human", "llm"],
        resolved_by: str,
    ) -> Conflict:
        """Mark a conflict resolved with a chosen value.

        Raises `KeyError` if the id is unknown, `ValueError` if the row is
        already resolved (resolutions are append-only — re-resolving would
        clobber the audit trail; surface a fresh edit through the edit API
        instead).
        """
        row = self._conn.execute(
            "SELECT status FROM conflicts WHERE id = ?", (conflict_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"conflict {conflict_id} not found")
        if row["status"] == "resolved":
            raise ValueError(
                f"conflict {conflict_id} is already resolved; "
                "use the edit API for further updates"
            )

        self._conn.execute(
            """UPDATE conflicts
               SET status = 'resolved',
                   chosen_value = ?,
                   resolution_method = ?,
                   resolved_by = ?,
                   resolved_at = ?
               WHERE id = ?""",
            (
                str(chosen_value),
                resolution_method,
                resolved_by,
                _iso(_now()),
                conflict_id,
            ),
        )
        result = self.get(conflict_id)
        assert result is not None  # we just updated it
        return result


# ---------------------------------------------------------------------------
# reconcile — the seam GraphStore.add_node calls at MERGE time
# ---------------------------------------------------------------------------


# When a provenance row carries no `attribute` (legacy data, or LLM blocks
# pre-dating the attribute field), the safe default is EXACT: it makes the
# rare conflict against unattributed data fall to ESCALATE rather than be
# silently auto-picked. Conservative, honest, unsurprising.
_DEFAULT_CONFIDENCE_FOR_LEGACY: FactConfidence = FactConfidence.EXACT

# Sentinel `Candidate.source_file` for legacy provenance with no `attribute`
# match. The `same_source_updated` rule deliberately ignores this sentinel —
# two unattributed candidates are NOT a self-update, just two facts whose
# source identity we couldn't recover.
_UNATTRIBUTED_SENTINEL: str = "<unattributed>"


def _confidence_for(provenance: list[Provenance], attribute: str) -> FactConfidence:
    """Find the most-recent provenance row covering `attribute` and return
    its confidence. Falls back to EXACT for legacy rows that pre-date the
    `attribute` column.

    "Most recent" = last matching row by list order. The store reads
    provenance ordered by id ASC, so the last matching entry is the
    freshest write.
    """
    matches = [p for p in provenance if p.attribute == attribute]
    if not matches:
        return _DEFAULT_CONFIDENCE_FOR_LEGACY
    return matches[-1].confidence


def _candidate_for(
    provenance: list[Provenance],
    attribute: str,
    value: Any,
) -> Candidate:
    """Build a Candidate for `attribute` from the (matching subset of)
    provenance. Source identifiers default to a placeholder when no
    matching provenance exists — that path only fires for unattributed
    legacy traces, never for fresh ingest writes.
    """
    matches = [p for p in provenance if p.attribute == attribute]
    if matches:
        latest = matches[-1]
        return Candidate(
            value=value,
            confidence=latest.confidence,
            source_file=latest.source_file,
            source_record_id=latest.source_record_id,
        )
    return Candidate(
        value=value,
        confidence=_DEFAULT_CONFIDENCE_FOR_LEGACY,
        source_file=_UNATTRIBUTED_SENTINEL,
        source_record_id=_UNATTRIBUTED_SENTINEL,
    )


def reconcile(
    *,
    node_id: str,
    existing_attrs: dict[str, Any],
    existing_provenance: list[Provenance],
    incoming_attrs: dict[str, Any],
    incoming_provenance: list[Provenance],
    conflict_store: ConflictStore | None,
) -> dict[str, Any]:
    """Merge `incoming_attrs` over `existing_attrs` with conflict-aware semantics.

    For each attribute present in both sides with **different** values,
    consults :func:`decide` and routes:

      * AUTO_MERGE / AUTO_PICK with winner=incoming → incoming value kept
      * AUTO_MERGE / AUTO_PICK with winner=existing → existing value kept
      * LLM_TRIAGE / ESCALATE                       → existing value kept
        and a row is recorded via `conflict_store` (if provided)

    Returns the resolved attribute dict ready for the graph MERGE. Disjoint
    attributes are unioned (existing-only attributes survive even if not
    present in incoming).

    `conflict_store` may be None for callers that want decision behavior
    without persistence (dry-run ingest, tests, etc.). When None, queueable
    conflicts simply keep the existing value with no audit row.
    """
    final = dict(existing_attrs)

    for attr_name, new_value in incoming_attrs.items():
        if attr_name not in existing_attrs:
            final[attr_name] = new_value
            continue

        existing_value = existing_attrs[attr_name]
        if existing_value == new_value:
            final[attr_name] = new_value  # idempotent — values agree
            continue

        existing_cand = _candidate_for(existing_provenance, attr_name, existing_value)
        incoming_cand = _candidate_for(incoming_provenance, attr_name, new_value)
        decision = decide(existing_cand, incoming_cand)

        if decision.winner == "incoming":
            final[attr_name] = new_value
        elif decision.winner == "existing":
            final[attr_name] = existing_value
        else:
            # LLM_TRIAGE or ESCALATE — keep existing, queue conflict if we have a store.
            final[attr_name] = existing_value
            if conflict_store is not None:
                conflict_store.record(
                    node_id=node_id,
                    attribute=attr_name,
                    existing=existing_cand,
                    incoming=incoming_cand,
                    verdict=decision.verdict,
                    reason=decision.reason,
                )

    return final


def _row_to_conflict(row: sqlite3.Row) -> Conflict:
    return Conflict(
        id=row["id"],
        node_id=row["node_id"],
        attribute=row["attribute"],
        existing=Candidate(
            value=row["existing_value"],
            confidence=FactConfidence(row["existing_confidence"]),
            source_file=row["existing_source_file"],
            source_record_id=row["existing_source_record_id"],
        ),
        incoming=Candidate(
            value=row["incoming_value"],
            confidence=FactConfidence(row["incoming_confidence"]),
            source_file=row["incoming_source_file"],
            source_record_id=row["incoming_source_record_id"],
        ),
        verdict=Verdict(row["verdict"]),
        reason=row["reason"],
        status=row["status"],
        detected_at=_parse_iso(row["detected_at"]) or _now(),
        resolved_at=_parse_iso(row["resolved_at"]),
        resolved_by=row["resolved_by"],
        chosen_value=row["chosen_value"],
        resolution_method=row["resolution_method"],
    )
