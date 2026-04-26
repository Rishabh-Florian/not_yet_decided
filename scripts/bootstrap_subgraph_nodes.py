"""Bootstrap Department and Location nodes + edges in Neo4j.

Two things happen:
1. Synthetic office_location is injected onto every Person node that lacks one.
   The value is deterministically derived from emp_id so re-runs are idempotent.
   Fixed vocabulary: Berlin, Paris, San Francisco, New York, Tokyo.

2. For every unique (category, office_location) value that exists on Person nodes,
   an Organization node is created (type="Department" / type="Location" stored in
   attributes.subtype), and a MEMBER_OF edge links each Person to their Department,
   and a MEMBER_OF edge links each Person to their Location.

Run:
    uv run python scripts/bootstrap_subgraph_nodes.py
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Bootstrap path so backend package is importable.
sys.path.insert(0, str(Path(__file__).parent.parent))

import backend.config as cfg
from backend.graph.store import GraphStore
from backend.models.graph import (
    FactConfidence,
    GraphEdge,
    GraphNode,
    Provenance,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LOCATIONS = ["Berlin", "Paris", "San Francisco", "New York", "Tokyo"]
SOURCE_FILE = "bootstrap/subgraph_nodes"
NOW = datetime.now(timezone.utc)


def _prov(source_record_id: str, field: str, value: str) -> Provenance:
    return Provenance(
        source_file=SOURCE_FILE,
        source_record_id=source_record_id,
        source_field=field,
        extraction_method="synthetic",
        extraction_model="bootstrap:v1",
        confidence=FactConfidence.INFERRED,
        raw_value=value,
        extracted_at=NOW,
        spec_version=1,
    )


def _location_for_emp(emp_id: str) -> str:
    """Deterministic, stable assignment: sha256(emp_id) % len(LOCATIONS)."""
    h = int(hashlib.sha256(emp_id.encode()).hexdigest(), 16)
    return LOCATIONS[h % len(LOCATIONS)]


def _org_node_id(subtype: str, name: str) -> str:
    slug = name.lower().replace(" ", "_")
    return f"org:{subtype}:{slug}"


def _edge_id(person_id: str, org_id: str) -> str:
    key = f"{person_id}|MEMBER_OF|{org_id}"
    return "edge_" + hashlib.sha256(key.encode()).hexdigest()[:24]


def main() -> None:
    db_path = os.environ.get("SQLITE_DB", "data/better_context.sqlite")
    store = GraphStore(
        db_path=db_path,
        neo4j_uri=cfg.NEO4J_URI,
        neo4j_user=cfg.NEO4J_USER,
        neo4j_password=cfg.NEO4J_PASSWORD,
        neo4j_database=cfg.NEO4J_DATABASE,
    )

    # ------------------------------------------------------------------
    # 1. Load all Person nodes
    # ------------------------------------------------------------------
    print("Loading Person nodes …")
    persons = list(store.nodes_by_type("Person"))
    print(f"  {len(persons)} persons found")

    # ------------------------------------------------------------------
    # 2. Inject synthetic office_location where missing
    # ------------------------------------------------------------------
    print("Injecting office_location …")
    injected = 0
    for p in persons:
        if p.attributes.get("office_location"):
            continue
        emp_id = p.attributes.get("emp_id") or p.id.replace("person:", "")
        loc = _location_for_emp(emp_id)
        store.add_source_record(
            source_file=SOURCE_FILE,
            source_record_id=f"loc:{p.id}",
            raw_record={"node_id": p.id, "office_location": loc},
        )
        store.edit_node(
            p.id,
            {"office_location": loc},
            editor="bootstrap:v1",
        )
        injected += 1
    print(f"  {injected} persons updated")

    # ------------------------------------------------------------------
    # 3. Reload persons (so they have office_location)
    # ------------------------------------------------------------------
    persons = list(store.nodes_by_type("Person"))

    # ------------------------------------------------------------------
    # 4. Collect unique Department + Location values
    # ------------------------------------------------------------------
    departments: dict[str, str] = {}   # name -> org_node_id
    locations: dict[str, str] = {}     # name -> org_node_id

    for p in persons:
        dept = p.attributes.get("category")
        if dept and isinstance(dept, str):
            if dept not in departments:
                departments[dept] = _org_node_id("department", dept)

        loc = p.attributes.get("office_location")
        if loc and isinstance(loc, str):
            if loc not in locations:
                locations[loc] = _org_node_id("location", loc)

    print(f"  {len(departments)} unique departments: {sorted(departments)}")
    print(f"  {len(locations)} unique locations: {sorted(locations)}")

    # ------------------------------------------------------------------
    # 5. Upsert Department Organization nodes
    # ------------------------------------------------------------------
    print("Creating Department nodes …")
    for name, node_id in departments.items():
        rec_id = f"dept:{name}"
        store.add_source_record(SOURCE_FILE, rec_id, {"subtype": "Department", "name": name})
        prov = _prov(rec_id, "name", name)
        node = GraphNode(
            id=node_id,
            type="Organization",
            attributes={"name": name, "subtype": "Department"},
            provenance=[prov],
        )
        store.add_node(node)
    print(f"  {len(departments)} Department nodes upserted")

    # ------------------------------------------------------------------
    # 6. Upsert Location Organization nodes
    # ------------------------------------------------------------------
    print("Creating Location nodes …")
    for name, node_id in locations.items():
        rec_id = f"loc:{name}"
        store.add_source_record(SOURCE_FILE, rec_id, {"subtype": "Location", "name": name})
        prov = _prov(rec_id, "name", name)
        node = GraphNode(
            id=node_id,
            type="Organization",
            attributes={"name": name, "subtype": "Location"},
            provenance=[prov],
        )
        store.add_node(node)
    print(f"  {len(locations)} Location nodes upserted")

    # ------------------------------------------------------------------
    # 7. Create MEMBER_OF edges: Person -> Department, Person -> Location
    # ------------------------------------------------------------------
    print("Creating MEMBER_OF edges …")
    dept_edges = 0
    loc_edges = 0

    for p in persons:
        dept = p.attributes.get("category")
        if dept and dept in departments:
            edge = GraphEdge(
                id=_edge_id(p.id, departments[dept]),
                source_node_id=p.id,
                target_node_id=departments[dept],
                relation_type="MEMBER_OF",
                attributes={"dimension": "department"},
                provenance=[_prov(f"dept:{dept}", "category", dept)],
            )
            store.add_edge(edge)
            dept_edges += 1

        loc = p.attributes.get("office_location")
        if loc and loc in locations:
            edge = GraphEdge(
                id=_edge_id(p.id, locations[loc]),
                source_node_id=p.id,
                target_node_id=locations[loc],
                relation_type="MEMBER_OF",
                attributes={"dimension": "location"},
                provenance=[_prov(f"loc:{loc}", "office_location", loc)],
            )
            store.add_edge(edge)
            loc_edges += 1

    print(f"  {dept_edges} department MEMBER_OF edges")
    print(f"  {loc_edges} location MEMBER_OF edges")

    # ------------------------------------------------------------------
    # 8. Final stats
    # ------------------------------------------------------------------
    stats = store.stats()
    print("\nGraph stats after bootstrap:")
    print(f"  nodes: {stats['graph']['node_count']}")
    print(f"  edges: {stats['graph']['edge_count']}")
    print(f"  node types: {stats['graph']['node_types']}")
    print(f"  relation types: {stats['graph']['relation_types']}")

    store.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
