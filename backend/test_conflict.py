"""Tests for `backend.conflict` — decision logic + persistence + API.

Pure unit tests only: no Neo4j. The `decide` decision table, the
`reconcile` MERGE-time helper, the `ConflictStore` SQLite CRUD, and the
REST endpoints (via FastAPI TestClient with a stubbed GraphStore) all
live here. Live-Neo4j coverage is in `test_conflict_integration.py`.

Run:  uv run pytest backend/test_conflict.py -v
"""
from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from backend.api.app import app, get_context_engine, get_store, _build_default_engine
from backend.conflict import (
    Candidate,
    Conflict,
    ConflictStore,
    Decision,
    Verdict,
    decide,
    reconcile,
)
from backend.graph.store import GraphStore
from backend.models.graph import FactConfidence, Provenance


SCHEMA_PATH = Path(__file__).parent / "graph" / "schema.sql"

HUMAN = FactConfidence.HUMAN
EXACT = FactConfidence.EXACT
GROUNDED = FactConfidence.GROUNDED
INFERRED = FactConfidence.INFERRED


# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------


def _new_conn(*, threadsafe: bool = False) -> sqlite3.Connection:
    """In-memory SQLite with the production schema applied.

    `threadsafe=True` is required for FastAPI TestClient: handlers run on
    a worker thread distinct from the test thread that built the connection.
    """
    conn = sqlite3.connect(":memory:", check_same_thread=not threadsafe)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    with open(SCHEMA_PATH, "r") as f:
        conn.executescript(f.read())
    return conn


@pytest.fixture
def store() -> ConflictStore:
    return ConflictStore(_new_conn())


# Counter so each `_cand()` call without explicit src/rid produces a unique
# (source_file, source_record_id) — by default we want every Candidate to
# look like it came from a different source, which is the common test
# scenario (cross-source conflict). Tests that need same-source semantics
# (TestDecideSameSourceUpdated, reconcile auto_merge cases) pass src+rid
# explicitly.
_cand_counter = [0]


def _cand(value: object, conf: FactConfidence = EXACT,
          src: str | None = None, rid: str | None = None) -> Candidate:
    _cand_counter[0] += 1
    return Candidate(
        value=value, confidence=conf,
        source_file=src if src is not None else f"src_{_cand_counter[0]}.json",
        source_record_id=rid if rid is not None else f"rec_{_cand_counter[0]}",
    )


def _prov(attribute: str, conf: FactConfidence,
          source_file: str = "src.json", source_record_id: str = "rec_1") -> Provenance:
    return Provenance(
        source_file=source_file,
        source_record_id=source_record_id,
        source_field=f"$.{attribute}",
        attribute=attribute,
        extraction_method="direct_mapping",
        extraction_model="spec:v1",
        confidence=conf,
        raw_value="",
    )


def _seed_record(conn: sqlite3.Connection, source_file: str, record_id: str) -> None:
    """Provenance has a FK to source_records; seed before any test that
    writes provenance via the store API."""
    conn.execute(
        """INSERT OR IGNORE INTO source_records
           (source_file, source_record_id, raw_record, content_hash, ingested_at)
           VALUES (?, ?, '{}', 'h', '2026-01-01T00:00:00+00:00')""",
        (source_file, record_id),
    )


# ===========================================================================
# decide() — pure decision table
# ===========================================================================


class TestDecideAutoMerge:
    @pytest.mark.parametrize("ex_val,in_val", [
        ("alice", "alice"),                  # exact match
        ("alice", "ALICE"),                  # case
        ("  alice  ", "alice"),              # whitespace
        ("Alice Smith", "alice smith"),      # case + space
        (42, "42"),                          # type coercion via str()
        ("alice", "Alice "),                 # case + trailing space
    ])
    @pytest.mark.parametrize("ex_conf,in_conf", [
        (EXACT, EXACT),
        (EXACT, GROUNDED),                   # confidences differ but values agree → still merge
        (HUMAN, INFERRED),
        (INFERRED, INFERRED),
    ])
    def test_equal_after_normalize_merges(
        self, ex_val: object, in_val: object,
        ex_conf: FactConfidence, in_conf: FactConfidence,
    ) -> None:
        d = decide(_cand(ex_val, ex_conf), _cand(in_val, in_conf))
        assert d.verdict == Verdict.AUTO_MERGE
        assert d.reason == "equal_after_normalize"
        # Default to "existing" winner so the canonical form in the graph
        # doesn't churn on every ingest.
        assert d.winner == "existing"


class TestDecideHumanOverrides:
    def test_existing_human_beats_incoming_exact(self) -> None:
        d = decide(_cand("Alice", HUMAN), _cand("Bob", EXACT))
        assert d.verdict == Verdict.AUTO_PICK
        assert d.winner == "existing"
        assert d.reason == "human_overrides"

    def test_incoming_human_beats_existing_exact(self) -> None:
        d = decide(_cand("Alice", EXACT), _cand("Bob", HUMAN))
        assert d.verdict == Verdict.AUTO_PICK
        assert d.winner == "incoming"
        assert d.reason == "human_overrides"

    @pytest.mark.parametrize("loser_conf", [EXACT, GROUNDED, INFERRED])
    def test_human_beats_every_machine_rung(self, loser_conf: FactConfidence) -> None:
        d = decide(_cand("Alice", HUMAN), _cand("Bob", loser_conf))
        assert d.verdict == Verdict.AUTO_PICK
        assert d.winner == "existing"
        assert d.reason == "human_overrides"


class TestDecideSameSourceUpdated:
    """A source updating its own previously-ingested record must not escalate.

    Without this rule, every push-mode `POST /api/source/.../{id}` re-ingest
    where any attribute value changed would queue an ESCALATE conflict
    against itself — breaking the "edit source → graph reflects it" loop.
    """

    def test_same_source_record_self_update_picks_incoming(self) -> None:
        # Same (source_file, source_record_id), different value → incoming wins.
        d = decide(
            _cand("Senior Engineer", EXACT, "hr.json", "h1"),
            _cand("Lead Engineer",   EXACT, "hr.json", "h1"),
        )
        assert d.verdict == Verdict.AUTO_PICK
        assert d.winner == "incoming"
        assert d.reason == "same_source_updated"

    def test_different_record_in_same_file_does_not_self_update(self) -> None:
        # Same file, different record_id — a different record. NOT a self-update.
        # Falls through to the ladder (both EXACT → ESCALATE).
        d = decide(
            _cand("Senior Engineer", EXACT, "hr.json", "h1"),
            _cand("Lead Engineer",   EXACT, "hr.json", "h2"),
        )
        assert d.verdict == Verdict.ESCALATE

    def test_different_file_same_record_id_does_not_self_update(self) -> None:
        # Coincidental record_id collision across files. NOT a self-update.
        d = decide(
            _cand("A", EXACT, "hr.json",  "shared_id"),
            _cand("B", EXACT, "crm.json", "shared_id"),
        )
        assert d.verdict == Verdict.ESCALATE

    def test_self_update_with_equal_values_still_auto_merges(self) -> None:
        # equal_after_normalize fires before same_source_updated (cheap
        # short-circuit; result is the same — incoming written, prov appended).
        d = decide(
            _cand("Alice", EXACT, "hr.json", "h1"),
            _cand("ALICE", EXACT, "hr.json", "h1"),
        )
        assert d.verdict == Verdict.AUTO_MERGE

    def test_self_update_overrides_human_override_rule(self) -> None:
        # If existing prov is HUMAN from human_edits and incoming is from
        # the same human_edits source_record_id (a re-resolution edit on
        # the same conflict), incoming wins via same_source rather than
        # the human_overrides rule. This is the correct behavior — a fresh
        # human edit overwrites a stale one from the same edit session.
        d = decide(
            _cand("old", HUMAN, "human_edits", "edit:foo"),
            _cand("new", HUMAN, "human_edits", "edit:foo"),
        )
        assert d.verdict == Verdict.AUTO_PICK
        assert d.winner == "incoming"
        assert d.reason == "same_source_updated"


class TestDecideConfidenceLadder:
    @pytest.mark.parametrize("ex_conf,in_conf,winner", [
        (EXACT, GROUNDED, "existing"),
        (EXACT, INFERRED, "existing"),
        (GROUNDED, INFERRED, "existing"),
        (GROUNDED, EXACT, "incoming"),
        (INFERRED, EXACT, "incoming"),
        (INFERRED, GROUNDED, "incoming"),
    ])
    def test_higher_rung_wins(
        self, ex_conf: FactConfidence, in_conf: FactConfidence, winner: str,
    ) -> None:
        d = decide(_cand("Alice", ex_conf), _cand("Bob", in_conf))
        assert d.verdict == Verdict.AUTO_PICK
        assert d.winner == winner
        assert d.reason == "higher_confidence_wins"


class TestDecideLLMTriage:
    def test_both_inferred_routes_to_llm(self) -> None:
        d = decide(_cand("Acme Corp", INFERRED), _cand("Acme Inc.", INFERRED))
        assert d.verdict == Verdict.LLM_TRIAGE
        assert d.winner is None
        assert d.reason == "both_inferred"


class TestDecideEscalate:
    def test_both_exact_escalates(self) -> None:
        d = decide(_cand("Senior Engineer", EXACT), _cand("Lead Engineer", EXACT))
        assert d.verdict == Verdict.ESCALATE
        assert d.winner is None
        assert d.reason == "tied_at_confident_rung"

    def test_both_grounded_escalates(self) -> None:
        d = decide(_cand("Acme Corp", GROUNDED), _cand("Acme GmbH", GROUNDED))
        assert d.verdict == Verdict.ESCALATE
        assert d.reason == "tied_at_confident_rung"

    def test_both_human_escalates(self) -> None:
        # Two humans disagreeing must not silently overwrite each other —
        # the honest answer is to surface the disagreement to a third human.
        d = decide(_cand("Alice", HUMAN), _cand("Alicia", HUMAN))
        assert d.verdict == Verdict.ESCALATE


class TestDecideLaneOrdering:
    def test_normalize_match_short_circuits_ladder(self) -> None:
        d = decide(_cand("alice", INFERRED), _cand("ALICE", EXACT))
        assert d.verdict == Verdict.AUTO_MERGE

    def test_normalize_match_short_circuits_human_override(self) -> None:
        d = decide(_cand("alice", HUMAN), _cand("ALICE", INFERRED))
        assert d.verdict == Verdict.AUTO_MERGE


def test_decision_is_pydantic_serializable() -> None:
    payload = decide(_cand("a", EXACT), _cand("a", EXACT)).model_dump()
    assert payload == {
        "verdict": "auto_merge",
        "winner": "existing",
        "reason": "equal_after_normalize",
    }


def test_decision_type_is_pydantic() -> None:
    assert isinstance(decide(_cand("a", EXACT), _cand("a", EXACT)), Decision)


# ===========================================================================
# reconcile() — MERGE-time helper
# ===========================================================================


class TestReconcileNoExisting:
    def test_returns_incoming_attrs_verbatim(self, store: ConflictStore) -> None:
        result = reconcile(
            node_id="person:1",
            existing_attrs={},
            existing_provenance=[],
            incoming_attrs={"name": "Alice", "email": "alice@x"},
            incoming_provenance=[_prov("name", EXACT), _prov("email", EXACT)],
            conflict_store=store,
        )
        assert result == {"name": "Alice", "email": "alice@x"}
        assert list(store.list()) == []


class TestReconcileMerge:
    def test_disjoint_attrs_unioned(self, store: ConflictStore) -> None:
        result = reconcile(
            node_id="person:1",
            existing_attrs={"name": "Alice"},
            existing_provenance=[_prov("name", EXACT, "hr.json", "h1")],
            incoming_attrs={"email": "alice@x"},
            incoming_provenance=[_prov("email", EXACT, "crm.json", "c1")],
            conflict_store=store,
        )
        assert result == {"name": "Alice", "email": "alice@x"}
        assert list(store.list()) == []

    def test_same_value_no_conflict(self, store: ConflictStore) -> None:
        result = reconcile(
            node_id="person:1",
            existing_attrs={"name": "Alice"},
            existing_provenance=[_prov("name", EXACT, "hr.json", "h1")],
            incoming_attrs={"name": "Alice"},
            incoming_provenance=[_prov("name", EXACT, "crm.json", "c1")],
            conflict_store=store,
        )
        assert result == {"name": "Alice"}
        assert list(store.list()) == []

    def test_auto_merge_keeps_existing_canonical_form(self, store: ConflictStore) -> None:
        result = reconcile(
            node_id="person:1",
            existing_attrs={"name": "Alice"},
            existing_provenance=[_prov("name", EXACT, "hr.json", "h1")],
            incoming_attrs={"name": "ALICE"},
            incoming_provenance=[_prov("name", EXACT, "crm.json", "c1")],
            conflict_store=store,
        )
        assert result == {"name": "Alice"}
        assert list(store.list()) == []


class TestReconcileAutoPick:
    def test_existing_higher_confidence_wins(self, store: ConflictStore) -> None:
        result = reconcile(
            node_id="person:1",
            existing_attrs={"title": "Senior Engineer"},
            existing_provenance=[_prov("title", EXACT, "hr.json", "h1")],
            incoming_attrs={"title": "Lead Engineer"},
            incoming_provenance=[_prov("title", INFERRED, "emails.json", "e1")],
            conflict_store=store,
        )
        assert result == {"title": "Senior Engineer"}
        assert list(store.list()) == []

    def test_incoming_higher_confidence_wins(self, store: ConflictStore) -> None:
        result = reconcile(
            node_id="person:1",
            existing_attrs={"title": "Lead Engineer"},
            existing_provenance=[_prov("title", INFERRED, "emails.json", "e1")],
            incoming_attrs={"title": "Senior Engineer"},
            incoming_provenance=[_prov("title", EXACT, "hr.json", "h1")],
            conflict_store=store,
        )
        assert result == {"title": "Senior Engineer"}
        assert list(store.list()) == []

    def test_human_overrides_machine(self, store: ConflictStore) -> None:
        result = reconcile(
            node_id="person:1",
            existing_attrs={"title": "Lead"},
            existing_provenance=[_prov("title", HUMAN, "human_edits", "edit:1")],
            incoming_attrs={"title": "Senior"},
            incoming_provenance=[_prov("title", EXACT, "hr.json", "h1")],
            conflict_store=store,
        )
        assert result == {"title": "Lead"}
        assert list(store.list()) == []


class TestReconcileQueueing:
    def test_escalate_records_conflict_and_keeps_existing(self, store: ConflictStore) -> None:
        result = reconcile(
            node_id="person:1",
            existing_attrs={"title": "Senior Engineer"},
            existing_provenance=[_prov("title", EXACT, "hr.json", "h1")],
            incoming_attrs={"title": "Lead Engineer"},
            incoming_provenance=[_prov("title", EXACT, "crm.json", "c1")],
            conflict_store=store,
        )
        assert result == {"title": "Senior Engineer"}
        conflicts = list(store.list())
        assert len(conflicts) == 1
        c = conflicts[0]
        assert c.verdict == Verdict.ESCALATE
        assert c.existing.value == "Senior Engineer"
        assert c.incoming.value == "Lead Engineer"

    def test_llm_triage_records_conflict(self, store: ConflictStore) -> None:
        result = reconcile(
            node_id="org:1",
            existing_attrs={"legal_name": "Acme Corp"},
            existing_provenance=[_prov("legal_name", INFERRED, "emails.json", "e1")],
            incoming_attrs={"legal_name": "Acme Inc."},
            incoming_provenance=[_prov("legal_name", INFERRED, "emails.json", "e2")],
            conflict_store=store,
        )
        assert result == {"legal_name": "Acme Corp"}
        conflicts = list(store.list())
        assert len(conflicts) == 1
        assert conflicts[0].verdict == Verdict.LLM_TRIAGE


class TestReconcileMultiAttribute:
    def test_independent_per_attribute(self, store: ConflictStore) -> None:
        # Three attributes, three different fates: same / escalate / human.
        result = reconcile(
            node_id="person:1",
            existing_attrs={
                "name": "Alice",
                "title": "Senior Engineer",
                "phone": "+49 30 123",
            },
            existing_provenance=[
                _prov("name",  EXACT, "hr.json", "h1"),
                _prov("title", EXACT, "hr.json", "h1"),
                _prov("phone", EXACT, "hr.json", "h1"),
            ],
            incoming_attrs={
                "name": "Alice",
                "title": "Lead Engineer",
                "phone": "+49 030 99999",
            },
            incoming_provenance=[
                _prov("name",  EXACT, "crm.json", "c1"),
                _prov("title", EXACT, "crm.json", "c1"),
                _prov("phone", HUMAN, "human_edits", "edit:1"),
            ],
            conflict_store=store,
        )
        assert result == {
            "name": "Alice",
            "title": "Senior Engineer",          # ESCALATE → existing kept
            "phone": "+49 030 99999",            # HUMAN incoming wins
        }
        conflicts = list(store.list())
        assert len(conflicts) == 1
        assert conflicts[0].attribute == "title"


class TestReconcileLegacyProvenance:
    def test_attribute_match_among_many_traces(self, store: ConflictStore) -> None:
        result = reconcile(
            node_id="person:1",
            existing_attrs={"title": "Senior Engineer"},
            existing_provenance=[
                _prov("name",  EXACT,    "hr.json", "h1"),
                _prov("email", EXACT,    "hr.json", "h1"),
                _prov("title", INFERRED, "emails.json", "e1"),     # the relevant one
            ],
            incoming_attrs={"title": "Lead Engineer"},
            incoming_provenance=[_prov("title", EXACT, "hr.json", "h2")],
            conflict_store=store,
        )
        # title: existing INFERRED vs incoming EXACT → incoming wins.
        assert result == {"title": "Lead Engineer"}
        assert list(store.list()) == []

    def test_unattributed_provenance_defaults_to_exact(self, store: ConflictStore) -> None:
        def legacy() -> Provenance:
            # `confidence=INFERRED` is ignored: with `attribute=None` the
            # reconcile lookup falls through to the EXACT default.
            return Provenance(
                source_file="legacy.json", source_record_id="l1",
                source_field="$.title", attribute=None,
                extraction_method="direct_mapping", extraction_model="spec:v0",
                confidence=INFERRED,
                raw_value="",
            )
        result = reconcile(
            node_id="person:1",
            existing_attrs={"title": "Senior"},
            existing_provenance=[legacy()],
            incoming_attrs={"title": "Lead"},
            incoming_provenance=[legacy()],
            conflict_store=store,
        )
        # Both default to EXACT → tied → escalate. Conservative.
        assert result == {"title": "Senior"}
        conflicts = list(store.list())
        assert len(conflicts) == 1
        assert conflicts[0].verdict == Verdict.ESCALATE


# ===========================================================================
# ConflictStore — SQLite CRUD
# ===========================================================================


class TestConflictStoreRecord:
    def test_inserts_open_conflict(self, store: ConflictStore) -> None:
        cid = store.record(
            node_id="person:1", attribute="title",
            existing=_cand("Senior Engineer"), incoming=_cand("Lead Engineer"),
            verdict=Verdict.ESCALATE, reason="tied_at_confident_rung",
        )
        c = store.get(cid)
        assert c is not None
        assert c.status == "open"
        assert c.verdict == Verdict.ESCALATE
        assert c.existing.value == "Senior Engineer"
        assert c.incoming.value == "Lead Engineer"
        assert c.detected_at is not None
        assert c.resolved_at is None
        assert c.chosen_value is None

    def test_idempotent_on_open_same_node_attr(self, store: ConflictStore) -> None:
        cid1 = store.record(
            node_id="person:1", attribute="title",
            existing=_cand("Senior Engineer"), incoming=_cand("Lead Engineer"),
            verdict=Verdict.ESCALATE, reason="tied_at_confident_rung",
        )
        cid2 = store.record(
            node_id="person:1", attribute="title",
            existing=_cand("Senior Engineer"), incoming=_cand("Staff Engineer"),
            verdict=Verdict.ESCALATE, reason="tied_at_confident_rung",
        )
        # Same id; incoming side replaced (newer data wins).
        assert cid1 == cid2
        c = store.get(cid1)
        assert c is not None and c.incoming.value == "Staff Engineer"
        assert len(list(store.list())) == 1

    def test_allows_new_open_after_resolved(self, store: ConflictStore) -> None:
        cid1 = store.record(
            node_id="person:1", attribute="title",
            existing=_cand("Senior"), incoming=_cand("Lead"),
            verdict=Verdict.ESCALATE, reason="tied_at_confident_rung",
        )
        store.resolve(cid1, chosen_value="Staff",
                      resolution_method="human", resolved_by="florian")
        cid2 = store.record(
            node_id="person:1", attribute="title",
            existing=_cand("Staff"), incoming=_cand("Principal"),
            verdict=Verdict.ESCALATE, reason="tied_at_confident_rung",
        )
        assert cid2 != cid1
        assert len(list(store.list(status="open"))) == 1
        assert len(list(store.list(status="resolved"))) == 1

    def test_rejects_non_persistable_verdict(self, store: ConflictStore) -> None:
        with pytest.raises(ValueError, match="not persistable"):
            store.record(
                node_id="x", attribute="y",
                existing=_cand("a"), incoming=_cand("b"),
                verdict=Verdict.AUTO_MERGE, reason="...",
            )


class TestConflictStoreList:
    def _seed(self, store: ConflictStore) -> dict[str, int]:
        return {
            "p1_title": store.record(
                node_id="person:1", attribute="title",
                existing=_cand("A"), incoming=_cand("B"),
                verdict=Verdict.ESCALATE, reason="tied_at_confident_rung",
            ),
            "p2_email": store.record(
                node_id="person:2", attribute="email",
                existing=_cand("a@x"), incoming=_cand("b@x"),
                verdict=Verdict.ESCALATE, reason="tied_at_confident_rung",
            ),
            "o1_name": store.record(
                node_id="org:1", attribute="legal_name",
                existing=_cand("Acme", INFERRED),
                incoming=_cand("Acme Inc.", INFERRED),
                verdict=Verdict.LLM_TRIAGE, reason="both_inferred",
            ),
        }

    def test_default_returns_open_only(self, store: ConflictStore) -> None:
        ids = self._seed(store)
        store.resolve(ids["p2_email"], chosen_value="a@x",
                      resolution_method="human", resolved_by="alice")
        rows = list(store.list())
        assert {r.id for r in rows} == {ids["p1_title"], ids["o1_name"]}

    def test_filter_by_status_resolved(self, store: ConflictStore) -> None:
        ids = self._seed(store)
        store.resolve(ids["p1_title"], chosen_value="A",
                      resolution_method="human", resolved_by="alice")
        assert [r.id for r in store.list(status="resolved")] == [ids["p1_title"]]

    def test_filter_by_node_id(self, store: ConflictStore) -> None:
        ids = self._seed(store)
        rows = list(store.list(node_id="person:1"))
        assert [r.id for r in rows] == [ids["p1_title"]]

    def test_filter_by_attribute(self, store: ConflictStore) -> None:
        ids = self._seed(store)
        rows = list(store.list(attribute="email"))
        assert [r.id for r in rows] == [ids["p2_email"]]

    def test_pagination(self, store: ConflictStore) -> None:
        for i in range(5):
            store.record(
                node_id=f"person:{i}", attribute="title",
                existing=_cand("A"), incoming=_cand("B"),
                verdict=Verdict.ESCALATE, reason="tied_at_confident_rung",
            )
        page1 = list(store.list(limit=2, offset=0))
        page2 = list(store.list(limit=2, offset=2))
        assert len(page1) == 2 and len(page2) == 2
        assert {r.id for r in page1}.isdisjoint({r.id for r in page2})


class TestConflictStoreResolve:
    def test_flips_status_and_sets_audit(self, store: ConflictStore) -> None:
        cid = store.record(
            node_id="person:1", attribute="title",
            existing=_cand("A"), incoming=_cand("B"),
            verdict=Verdict.ESCALATE, reason="tied_at_confident_rung",
        )
        resolved = store.resolve(
            cid, chosen_value="C",
            resolution_method="human", resolved_by="florian@co",
        )
        assert resolved.status == "resolved"
        assert resolved.chosen_value == "C"
        assert resolved.resolution_method == "human"
        assert resolved.resolved_by == "florian@co"
        assert resolved.resolved_at is not None

    def test_unknown_id_raises(self, store: ConflictStore) -> None:
        with pytest.raises(KeyError):
            store.resolve(9999, chosen_value="x",
                          resolution_method="human", resolved_by="x")

    def test_double_resolve_raises(self, store: ConflictStore) -> None:
        cid = store.record(
            node_id="person:1", attribute="title",
            existing=_cand("A"), incoming=_cand("B"),
            verdict=Verdict.ESCALATE, reason="tied_at_confident_rung",
        )
        store.resolve(cid, chosen_value="A",
                      resolution_method="human", resolved_by="alice")
        with pytest.raises(ValueError, match="already resolved"):
            store.resolve(cid, chosen_value="B",
                          resolution_method="human", resolved_by="bob")

    def test_llm_resolution_method(self, store: ConflictStore) -> None:
        cid = store.record(
            node_id="org:1", attribute="legal_name",
            existing=_cand("Acme Corp", INFERRED),
            incoming=_cand("Acme Inc.", INFERRED),
            verdict=Verdict.LLM_TRIAGE, reason="both_inferred",
        )
        resolved = store.resolve(
            cid, chosen_value="Acme Inc.",
            resolution_method="llm", resolved_by="gemini-2.5-flash",
        )
        assert resolved.resolution_method == "llm"
        assert resolved.resolved_by == "gemini-2.5-flash"


class TestConflictStoreShape:
    def test_get_returns_none_for_missing(self, store: ConflictStore) -> None:
        assert store.get(9999) is None

    def test_conflict_is_pydantic(self, store: ConflictStore) -> None:
        cid = store.record(
            node_id="person:1", attribute="title",
            existing=_cand("A"), incoming=_cand("B"),
            verdict=Verdict.ESCALATE, reason="tied_at_confident_rung",
        )
        c = store.get(cid)
        assert isinstance(c, Conflict)
        payload = c.model_dump()
        assert payload["status"] == "open"
        assert payload["existing"]["value"] == "A"


# ===========================================================================
# REST API — fake store with real ConflictStore + stub edit_node
# ===========================================================================


@pytest.fixture
def fake_store() -> MagicMock:
    """A MagicMock GraphStore that exposes a real ConflictStore over an
    in-memory SQLite connection, and stubs `resolve_conflict` to call
    through to the real `ConflictStore.resolve` (so tests can verify
    end-to-end resolve flow without live Neo4j)."""
    conn = _new_conn(threadsafe=True)
    cs = ConflictStore(conn)

    store = MagicMock(spec=GraphStore)
    store.conflicts = cs
    store._conn = conn
    store.edit_node = MagicMock(return_value=MagicMock())

    def _resolve(conflict_id: int, *, value: object, editor: str) -> Conflict:
        c = cs.get(conflict_id)
        if c is None:
            raise KeyError(f"conflict {conflict_id} not found")
        if c.status == "resolved":
            raise ValueError(f"conflict {conflict_id} is already resolved")
        store.edit_node(c.node_id, {c.attribute: value}, editor)
        resolved = cs.resolve(
            conflict_id,
            chosen_value=value,
            resolution_method="human",
            resolved_by=editor,
        )
        conn.commit()
        return resolved

    store.resolve_conflict = MagicMock(side_effect=_resolve)
    return store


@pytest.fixture
def client(fake_store: MagicMock) -> Iterator[TestClient]:
    app.dependency_overrides[get_store] = lambda: fake_store
    app.dependency_overrides[get_context_engine] = _build_default_engine
    yield TestClient(app)
    app.dependency_overrides.clear()


class TestApiList:
    def test_empty_returns_empty_list(self, client: TestClient) -> None:
        resp = client.get("/api/conflicts")
        assert resp.status_code == 200
        body = resp.json()
        assert body == {"conflicts": [], "status": "open", "total": 0}

    def test_seeded_conflict(self, client: TestClient, fake_store: MagicMock) -> None:
        cid = fake_store.conflicts.record(
            node_id="person:1", attribute="title",
            existing=_cand("Senior Engineer", src="hr.json", rid="hr:1"),
            incoming=_cand("Lead Engineer", src="crm.json", rid="crm:1"),
            verdict=Verdict.ESCALATE, reason="tied_at_confident_rung",
        )
        body = client.get("/api/conflicts").json()
        assert body["total"] == 1
        c = body["conflicts"][0]
        assert c == {
            "id": cid,
            "node_id": "person:1",
            "attribute": "title",
            "existing": {
                "value": "Senior Engineer",
                "confidence": "exact",
                "source_file": "hr.json",
                "source_record_id": "hr:1",
            },
            "incoming": {
                "value": "Lead Engineer",
                "confidence": "exact",
                "source_file": "crm.json",
                "source_record_id": "crm:1",
            },
            "verdict": "escalate",
            "reason": "tied_at_confident_rung",
            "status": "open",
            "detected_at": c["detected_at"],
            "resolved_at": None,
            "resolved_by": None,
            "chosen_value": None,
            "resolution_method": None,
        }

    def test_filter_by_node_id(self, client: TestClient, fake_store: MagicMock) -> None:
        fake_store.conflicts.record(
            node_id="person:1", attribute="title",
            existing=_cand("A"), incoming=_cand("B"),
            verdict=Verdict.ESCALATE, reason="tied_at_confident_rung",
        )
        fake_store.conflicts.record(
            node_id="person:2", attribute="title",
            existing=_cand("X"), incoming=_cand("Y"),
            verdict=Verdict.ESCALATE, reason="tied_at_confident_rung",
        )
        body = client.get("/api/conflicts?node_id=person:2").json()
        assert body["total"] == 1
        assert body["conflicts"][0]["node_id"] == "person:2"

    def test_invalid_status_returns_422(self, client: TestClient) -> None:
        assert client.get("/api/conflicts?status=bogus").status_code == 422


class TestApiGet:
    def test_existing(self, client: TestClient, fake_store: MagicMock) -> None:
        cid = fake_store.conflicts.record(
            node_id="org:1", attribute="legal_name",
            existing=_cand("Acme Corp", INFERRED),
            incoming=_cand("Acme Inc.", INFERRED),
            verdict=Verdict.LLM_TRIAGE, reason="both_inferred",
        )
        body = client.get(f"/api/conflicts/{cid}").json()
        assert body["id"] == cid
        assert body["verdict"] == "llm_triage"

    def test_missing_returns_404(self, client: TestClient) -> None:
        assert client.get("/api/conflicts/9999").status_code == 404


class TestApiResolve:
    def test_resolves_open_conflict(self, client: TestClient, fake_store: MagicMock) -> None:
        _seed_record(fake_store._conn, "hr.json", "h1")
        _seed_record(fake_store._conn, "crm.json", "c1")
        cid = fake_store.conflicts.record(
            node_id="person:1", attribute="title",
            existing=_cand("Senior Engineer", EXACT, "hr.json", "h1"),
            incoming=_cand("Lead Engineer", EXACT, "crm.json", "c1"),
            verdict=Verdict.ESCALATE, reason="tied_at_confident_rung",
        )
        resp = client.post(
            f"/api/conflicts/{cid}/resolve",
            json={"value": "Staff Engineer", "editor": "florian@co"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "resolved"
        assert body["chosen_value"] == "Staff Engineer"
        assert body["resolved_by"] == "florian@co"
        assert body["resolution_method"] == "human"
        fake_store.edit_node.assert_called_once_with(
            "person:1", {"title": "Staff Engineer"}, "florian@co",
        )

    def test_double_resolve_returns_400(self, client: TestClient, fake_store: MagicMock) -> None:
        _seed_record(fake_store._conn, "src.json", "rec_1")
        cid = fake_store.conflicts.record(
            node_id="person:1", attribute="title",
            existing=_cand("A"), incoming=_cand("B"),
            verdict=Verdict.ESCALATE, reason="tied_at_confident_rung",
        )
        client.post(f"/api/conflicts/{cid}/resolve",
                    json={"value": "C", "editor": "alice"})
        resp = client.post(f"/api/conflicts/{cid}/resolve",
                           json={"value": "D", "editor": "bob"})
        assert resp.status_code == 400

    def test_unknown_id_returns_404(self, client: TestClient) -> None:
        resp = client.post(
            "/api/conflicts/9999/resolve",
            json={"value": "x", "editor": "x"},
        )
        assert resp.status_code == 404
