"""Cross-vendor agnosticism test.

Four deliberately-different CRM export shapes (HubSpot-like, Salesforce-like,
Dynamics OData-like, Pipedrive-like) ingested through ONE Ingestor with
ONE canonical type registry. The test proves that vendor heterogeneity is
absorbed entirely at the spec level — same Ingestor code, same graph types
out, regardless of input shape.

Run: uv run pytest backend/test_ingest_agnostic.py -v
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from backend.graph.store import GraphStore
from backend.ingest import Ingestor, IngestStore, MappingSpec


HUBSPOT_LIKE = [
    {
        "id": "h-1001",
        "properties": {
            "firstname": "Alice",
            "lastname": "Wong",
            "email": "ALICE@ACME.COM",
            "company": "Acme Corp",
            "createdate": "2024-09-12T14:23:00Z",
        },
        "associations": {"companies": [{"id": "co-001"}]},
        "archived": False,
    },
    {
        "id": "h-1002",
        "properties": {
            "firstname": "Bob",
            "lastname": "Singh",
            "email": "bob@globex.io",
            "company": "Globex",
            "createdate": "2024-09-13T09:11:00Z",
        },
        "associations": {"companies": [{"id": "co-002"}]},
        "archived": False,
    },
]

SALESFORCE_LIKE = [
    {
        "attributes": {"type": "Contact", "url": "/services/data/v60.0/sobjects/Contact/0030..."},
        "Id": "0030001",
        "FirstName": "Carol",
        "LastName": "Diaz",
        "Email": "carol@initech.org",
        "AccountId": "0010001",
        "CreatedDate": "2024-09-13T16:48:00.000+0000",
        "IsDeleted": False,
    },
    {
        "attributes": {"type": "Contact", "url": "/services/data/v60.0/sobjects/Contact/0030..."},
        "Id": "0030002",
        "FirstName": "Dan",
        "LastName": "Patel",
        "Email": "DAN@umbrella.com",
        "AccountId": "0010001",
        "CreatedDate": "2024-09-14T08:02:00.000+0000",
        "IsDeleted": False,
    },
]

DYNAMICS_ODATA_LIKE = [
    {
        "@odata.etag": "W/\"123456\"",
        "contactid": "abc-111",
        "fullname": "Erin Park",
        "emailaddress1": "erin@umbrella.com",
        "_parentcustomerid_value": "comp-aaa",
        "createdon": "2024-09-15T10:30:00Z",
        "statecode": 0,
    },
    {
        "@odata.etag": "W/\"123457\"",
        "contactid": "abc-112",
        "fullname": "Frank Liu",
        "emailaddress1": "frank@umbrella.com",
        "_parentcustomerid_value": "comp-aaa",
        "createdon": "2024-09-15T12:45:00Z",
        "statecode": 0,
    },
]

PIPEDRIVE_LIKE = [
    {
        "id": 5001,
        "name": "Gina Romero",
        "primary_email": [{"value": "gina@initech.org", "primary": True}],
        "org_id": {"name": "Initech"},
        "add_time": "2024-09-16 11:00:00",
    },
    {
        "id": 5002,
        "name": "Hank Wu",
        "primary_email": [{"value": "hank@globex.io", "primary": True}],
        "org_id": {"name": "Globex"},
        "add_time": "2024-09-16 14:30:00",
    },
]


HUBSPOT_SPEC = """
spec_version: 1
tenant: vendor_demo
source: { file_pattern: hubspot_contacts.json, format: json, record_path: '$[*]' }
canonical_aliases: { Contact: Person, Company: Organization }
nodes:
  - name: contact
    canonical_type: Contact
    id_template: "person:hubspot:{contact_id}"
    fields:
      - { attribute: contact_id, source: '$.id' }
      - { attribute: first_name, source: '$.properties.firstname' }
      - { attribute: last_name,  source: '$.properties.lastname' }
      - { attribute: email,      source: '$.properties.email', transform: [normalize_email] }
      - { attribute: company,    source: '$.properties.company', required: false }
      - { attribute: created_at, source: '$.properties.createdate', transform: [parse_iso_datetime] }
edges: []
"""

SALESFORCE_SPEC = """
spec_version: 1
tenant: vendor_demo
source: { file_pattern: salesforce_contacts.json, format: json, record_path: '$[*]' }
canonical_aliases: { Contact: Person }
nodes:
  - name: contact
    canonical_type: Contact
    id_template: "person:salesforce:{contact_id}"
    when: { equals: ['$.IsDeleted', false] }
    fields:
      - { attribute: contact_id, source: '$.Id' }
      - { attribute: first_name, source: '$.FirstName' }
      - { attribute: last_name,  source: '$.LastName' }
      - { attribute: email,      source: '$.Email', transform: [normalize_email] }
      - { attribute: account_id, source: '$.AccountId', required: false }
      - { attribute: created_at, source: '$.CreatedDate', transform: [parse_iso_datetime] }
edges: []
"""

DYNAMICS_SPEC = """
spec_version: 1
tenant: vendor_demo
source: { file_pattern: dynamics_contacts.json, format: json, record_path: '$[*]' }
canonical_aliases: { Contact: Person }
nodes:
  - name: contact
    canonical_type: Contact
    id_template: "person:dynamics:{contact_id}"
    when: { equals: ['$.statecode', 0] }
    fields:
      - { attribute: contact_id, source: '$.contactid' }
      - { attribute: full_name,  source: '$.fullname' }
      - { attribute: email,      source: '$.emailaddress1', transform: [normalize_email] }
      - { attribute: parent_customer, source: '$._parentcustomerid_value', required: false }
      - { attribute: created_at, source: '$.createdon', transform: [parse_iso_datetime] }
edges: []
"""

PIPEDRIVE_SPEC = """
spec_version: 1
tenant: vendor_demo
source: { file_pattern: pipedrive_persons.json, format: json, record_path: '$[*]' }
canonical_aliases: { Contact: Person }
nodes:
  - name: contact
    canonical_type: Contact
    id_template: "person:pipedrive:{contact_id}"
    fields:
      - { attribute: contact_id, source: '$.id' }
      - { attribute: full_name,  source: '$.name' }
      - { attribute: email,      source: ['$.primary_email[*].value', '$.email'], transform: [normalize_email] }
      - { attribute: org_name,   source: '$.org_id.name', required: false }
      - { attribute: created_at, source: '$.add_time', transform: [parse_iso_datetime] }
edges: []
"""


VENDOR_FIXTURES = [
    ("hubspot", HUBSPOT_LIKE, HUBSPOT_SPEC),
    ("salesforce", SALESFORCE_LIKE, SALESFORCE_SPEC),
    ("dynamics", DYNAMICS_ODATA_LIKE, DYNAMICS_SPEC),
    ("pipedrive", PIPEDRIVE_LIKE, PIPEDRIVE_SPEC),
]


def _mocked_ingestor() -> tuple[Ingestor, MagicMock]:
    store = MagicMock(spec=GraphStore)
    store.add_node.side_effect = lambda n: n
    store.add_edge.side_effect = lambda e: e
    store.add_source_record.return_value = None
    ing_store = MagicMock(spec=IngestStore)
    ing_store.already_seen.return_value = False
    ing_store.open_run.return_value = "run_test"
    return Ingestor(store, ing_store), store


def _write(tmp_path: Path, name: str, payload: list) -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


@pytest.mark.parametrize("vendor,payload,spec_yaml", VENDOR_FIXTURES)
def test_each_vendor_produces_canonical_person_nodes(
    tmp_path: Path,
    vendor: str,
    payload: list,
    spec_yaml: str,
) -> None:
    """Each vendor's records, ingested under their own spec, produce
    canonical Person nodes — same type, same attribute names, regardless
    of the original schema.
    """
    spec = MappingSpec.from_yaml(spec_yaml)
    src = _write(tmp_path, f"{vendor}_contacts.json", payload)
    ing, store = _mocked_ingestor()

    report = ing.run(spec, src, dry_run=False)

    assert report.records_in == len(payload), f"{vendor}: records_in"
    assert report.records_dead == 0, f"{vendor}: dead-letter"
    assert store.add_node.call_count == len(payload), f"{vendor}: node count"

    nodes = [c.args[0] for c in store.add_node.call_args_list]
    assert all(n.type == "Person" for n in nodes), f"{vendor}: type"
    assert all("@" in n.attributes.get("email", "") for n in nodes), f"{vendor}: email present"
    assert all(n.id.startswith(f"person:{vendor}:") for n in nodes), f"{vendor}: id template"


def test_one_ingestor_handles_all_four_vendors(tmp_path: Path) -> None:
    """Single Ingestor instance, four different vendor shapes, one canonical
    output graph. Same code path absorbs every schema.
    """
    ing, store = _mocked_ingestor()
    total_records = 0
    for vendor, payload, spec_yaml in VENDOR_FIXTURES:
        spec = MappingSpec.from_yaml(spec_yaml)
        src = _write(tmp_path, f"{vendor}_contacts.json", payload)
        report = ing.run(spec, src, dry_run=False)
        assert report.records_dead == 0
        total_records += report.records_in

    assert total_records == sum(len(p) for _, p, _ in VENDOR_FIXTURES)
    assert store.add_node.call_count == total_records

    nodes = [c.args[0] for c in store.add_node.call_args_list]
    types = {n.type for n in nodes}
    assert types == {"Person"}, f"unexpected types: {types}"

    # Email shows up across vendors despite being at $.properties.email,
    # $.Email, $.emailaddress1, and $.primary_email[*].value respectively.
    emails = sorted({n.attributes["email"] for n in nodes})
    assert all("@" in e and e == e.lower() for e in emails), emails

    # Every node carries provenance back to the original (vendor-specific)
    # source field, so downstream consumers can answer "where did this
    # email value come from?" without knowing the vendor.
    sources_by_email = {
        n.attributes["email"]: [p.source_field for p in n.provenance]
        for n in nodes
    }
    assert any("$.properties.email" in fs for fs in sources_by_email.values())
    assert any("$.Email" in fs for fs in sources_by_email.values())
    assert any("$.emailaddress1" in fs for fs in sources_by_email.values())
    assert any("$.primary_email[*].value" in fs for fs in sources_by_email.values())


def test_filter_predicate_excludes_deleted_records(tmp_path: Path) -> None:
    """when: { equals: [$.IsDeleted, false] } drops soft-deleted Salesforce
    rows without code changes — vendor-specific deletion conventions are
    expressed in the spec.
    """
    payload = SALESFORCE_LIKE + [
        {
            "attributes": {"type": "Contact"},
            "Id": "DELETED1",
            "FirstName": "Z",
            "LastName": "Z",
            "Email": "z@z.com",
            "AccountId": "0010001",
            "CreatedDate": "2024-01-01T00:00:00.000+0000",
            "IsDeleted": True,
        },
    ]
    spec = MappingSpec.from_yaml(SALESFORCE_SPEC)
    src = _write(tmp_path, "salesforce_contacts.json", payload)
    ing, store = _mocked_ingestor()
    ing.run(spec, src, dry_run=False)
    ids = {c.args[0].id for c in store.add_node.call_args_list}
    assert "person:salesforce:DELETED1" not in ids
    assert len(ids) == len(SALESFORCE_LIKE)
