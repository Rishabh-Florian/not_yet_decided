"""Tests for `Ingestor.apply_record` — single-record ingest path.

Used by the push-mode source-update endpoint
(`POST /api/source/{source_file}/{record_id}`). No `ingest_runs` row, no
batch report — just "fire the spec for this one record, return what
changed."

Pure unit tests with a mocked GraphStore + IngestStore. The integration
test that exercises the real Neo4j wiring lives in
`test_conflict_integration.py`.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from backend.graph.store import GraphStore
from backend.ingest import Ingestor, IngestStore, MappingSpec
from backend.ingest.ingestor import ApplyRecordReport, RecordError


_SPEC = """
spec_version: 1
tenant: tenant_demo
source: { file_pattern: contacts.json, format: json, record_path: '$[*]' }
canonical_aliases: { Contact: Person }
nodes:
  - name: contact
    canonical_type: Contact
    id_template: "person:{contact_id}"
    fields:
      - { attribute: contact_id, source: '$.id' }
      - { attribute: name,       source: '$.name' }
      - { attribute: email,      source: '$.email', transform: [normalize_email] }
edges: []
"""


def _ingestor() -> tuple[Ingestor, MagicMock, MagicMock]:
    store = MagicMock(spec=GraphStore)
    store.add_node.side_effect = lambda n: n
    store.add_edge.side_effect = lambda e: e
    store.add_source_record.return_value = None
    ing_store = MagicMock(spec=IngestStore)
    ing_store.already_seen.return_value = False
    return Ingestor(store, ing_store), store, ing_store


def _spec() -> MappingSpec:
    return MappingSpec.from_yaml(_SPEC)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestApplyRecord:
    def test_returns_report_with_node_id_and_content_changed(self) -> None:
        ing, store, _ = _ingestor()
        report = ing.apply_record(
            _spec(),
            {"id": "1001", "name": "Alice", "email": "ALICE@X.COM"},
        )
        assert isinstance(report, ApplyRecordReport)
        assert report.source_record_id == "person:1001"
        assert report.content_changed is True
        assert report.skipped is False
        assert report.nodes_touched == ["person:1001"]

    def test_writes_source_record_and_node(self) -> None:
        ing, store, _ = _ingestor()
        ing.apply_record(_spec(), {"id": "1001", "name": "Alice", "email": "alice@x.com"})

        store.add_source_record.assert_called_once()
        kwargs = store.add_source_record.call_args.kwargs
        assert kwargs["source_file"] == "contacts.json"
        assert kwargs["source_record_id"] == "person:1001"
        assert kwargs["raw_record"] == {"id": "1001", "name": "Alice", "email": "alice@x.com"}

        assert store.add_node.call_count == 1
        node = store.add_node.call_args.args[0]
        assert node.id == "person:1001"
        assert node.type == "Person"
        assert node.attributes["email"] == "alice@x.com"


# ---------------------------------------------------------------------------
# Idempotency: content_hash already seen
# ---------------------------------------------------------------------------


class TestApplyRecordIdempotency:
    def test_skipped_when_already_seen(self) -> None:
        ing, store, ing_store = _ingestor()
        ing_store.already_seen.return_value = True

        report = ing.apply_record(_spec(), {"id": "1001", "name": "Alice", "email": "a@x"})

        assert report.skipped is True
        assert report.content_changed is False
        assert report.nodes_touched == []
        store.add_source_record.assert_not_called()
        store.add_node.assert_not_called()


# ---------------------------------------------------------------------------
# Caller-supplied record_id assertion
# ---------------------------------------------------------------------------


class TestApplyRecordExpectedId:
    def test_expected_id_match(self) -> None:
        ing, _, _ = _ingestor()
        report = ing.apply_record(
            _spec(),
            {"id": "1001", "name": "Alice", "email": "a@x"},
            expected_record_id="person:1001",
        )
        assert report.source_record_id == "person:1001"

    def test_expected_id_mismatch_raises(self) -> None:
        ing, _, _ = _ingestor()
        with pytest.raises(RecordError, match="record id mismatch"):
            ing.apply_record(
                _spec(),
                {"id": "1001", "name": "Alice", "email": "a@x"},
                expected_record_id="person:9999",
            )


# ---------------------------------------------------------------------------
# Failure modes
# ---------------------------------------------------------------------------


class TestApplyRecordFailures:
    def test_missing_required_field_raises(self) -> None:
        ing, _, _ = _ingestor()
        # `name` is required by default in the spec — omit it and apply_record
        # must raise (fail-fast at the API boundary, no silent dead-letter).
        with pytest.raises(RecordError, match="required field 'name' missing"):
            ing.apply_record(_spec(), {"id": "1001", "email": "a@x"})

    def test_no_id_template_resolution_raises(self) -> None:
        ing, _, _ = _ingestor()
        with pytest.raises(RecordError):
            ing.apply_record(_spec(), {"name": "Alice", "email": "a@x"})  # missing id
