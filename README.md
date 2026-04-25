# not_yet_decided

Workspace for **Qontext** — a system that turns fragmented enterprise data
(email, CRM, HR, IT tickets, chat, code, policies) into a structured,
inspectable, editable company memory backed by a knowledge graph with
fact-level provenance. See `docs/ARCHITECTURE.md` for the full design.

## Layout

```
backend/      Python: knowledge graph + (eventually) ingestion, API, MCP server
frontend/     Next.js + React UI (scaffolded next)
dataset/      EnterpriseBench source data
docs/         Architecture and dataset documentation
```

## Knowledge graph

The graph is the **source of truth** for the system. Every entity is a node,
every relationship is an edge, and every fact carries provenance metadata
linking it back to the exact original record it came from.

The store keeps four layers strictly separated:

| Layer | Where it lives | What it holds |
|---|---|---|
| **1. Graph** | Neo4j `:Entity` nodes + typed relationships | Entities and relationships (the structure) |
| **2. Content (metadata)** | Neo4j `attributes_json` property on each node and relationship | Typed, normalized fields for each node/edge |
| **3. Traces** | SQLite `provenance` | Fact-level extraction history: which source field, which extractor, which model, confidence |
| **4. Raw data** | SQLite `source_records` | The original ingested records, stored verbatim, with content hash |

A trace cannot exist without the raw record it points at — provenance has a
foreign key to `source_records`, so the graph can never claim a fact whose
origin has been lost. Conversely, raw records can sit unused (e.g. before
extraction has run on them) without polluting the graph. Provenance refers to
graph elements by `node_id` / `edge_id`; the graph is owned by Neo4j, so the
store cascades those deletes manually when a node or edge is removed.

### Node

```python
GraphNode(
    id,              # globally unique (e.g. "node_a1b2c3d4...")
    type,            # "Employee" | "Customer" | "Product" | "Email" | ...
    attributes,      # dict of typed fields (the "content/metadata" layer)
    provenance,      # list[Provenance] — one per source field that contributed
    confidence,      # 0.0 – 1.0
    vfs_path,        # path in the materialized virtual filesystem
    created_at, updated_at, version,
)
```

Expected node types (counts estimated from `dataset/EnterpriseBench`):

| Type | Count | Source |
|---|---|---|
| Employee | ~1,260 | HR |
| Customer / Client / Vendor | ~890 | CRM |
| Product | ~1,351 | Business |
| EmailThread / Email | ~16,300 | Mail |
| Conversation | ~2,897 | Collaboration |
| ITTicket | ~163 | ITSM |
| Repository | ~726 | Workspace |
| Policy | ~24 | Policy docs |
| SocialPost | ~971 | Social platform |
| Department / Skill / Topic / Category | ~360 | Cross-cutting |

### Edge

```python
GraphEdge(
    id,
    source_node_id, target_node_id,
    relation_type,   # "REPORTS_TO" | "PURCHASED" | "MENTIONS" | "OWNS" | ...
    attributes,      # edge-specific data (date, amount, channel, ...)
    provenance,      # list[Provenance]
    confidence,
    valid_from, valid_to,   # temporal validity (None = still valid)
    version,
)
```

Neo4j is multi-relational by default, so the same pair of nodes may be
connected by several edges of different `relation_type` (or the same type
across different time windows). `relation_type` is used as the actual Neo4j
relationship type and must match `[A-Za-z_][A-Za-z0-9_]*`.

### Provenance (trace)

Every node and every edge carries a list of `Provenance` records — one per
distinct source field that contributed to that fact:

```python
Provenance(
    source_file,         # "Enterprise_mail_system/emails.json"
    source_record_id,    # "email_id:4226322d-0ea5-..."
    source_field,        # "sender_emp_id" (dotted paths supported, e.g. "role.title")
    extraction_method,   # "direct_mapping" | "llm_extraction" | "rule_based" | "human"
    extraction_model,    # "claude-sonnet-4-6" or "rule:email_parser_v1"
    confidence,
    raw_value,           # the original value before normalization
    extracted_at,
)
```

`GraphStore.resolve_provenance(p)` looks up the original raw record and
returns both the record and the value at `source_field`, so the UI / agent can
display "this fact came from _here_" with the actual byte-for-byte source.

### Raw data

Original records are stored verbatim in `source_records` keyed by
`(source_file, source_record_id)`, with a sha256 `content_hash` for change
detection. `add_source_record` is idempotent: re-ingesting the same record is
a no-op; an updated record bumps `ingested_at`.

## Backend usage

```bash
cd backend
pip install -r requirements.txt
```

A running Neo4j is required (e.g. `docker run -p 7687:7687 -p 7474:7474 -e NEO4J_AUTH=neo4j/neo4j neo4j:5`). Configure the connection via env vars (defaults shown):

```bash
export NEO4J_URI=bolt://localhost:7687
export NEO4J_USER=neo4j
export NEO4J_PASSWORD=neo4j
export NEO4J_DATABASE=neo4j
```

```python
from backend.graph import GraphStore
from backend.models import GraphNode, GraphEdge, Provenance

store = GraphStore("data/qontext.sqlite")  # SQLite path for traces + raw data;
                                            # Neo4j connection comes from env

# 1. Ingest the raw record first.
store.add_source_record(
    source_file="Human_Resource_Management/employees.json",
    source_record_id="emp_id:42",
    raw_record={"emp_id": "42", "name": "Alice", "direct_manager_id": "17"},
)

# 2. Extract entities and edges, attaching provenance back to that record.
prov = Provenance(
    source_file="Human_Resource_Management/employees.json",
    source_record_id="emp_id:42",
    source_field="direct_manager_id",
    extraction_method="direct_mapping",
    extraction_model="rule:employee_parser_v1",
    confidence=1.0,
    raw_value="17",
)
alice = store.add_node(GraphNode(type="Employee", attributes={"name": "Alice"}))
bob   = store.add_node(GraphNode(type="Employee", attributes={"name": "Bob"}))
store.add_edge(GraphEdge(
    source_node_id=alice.id, target_node_id=bob.id,
    relation_type="REPORTS_TO", provenance=[prov],
))

# 3. Query the graph and resolve a trace back to the raw record.
store.neighbors(alice.id, relation_type="REPORTS_TO")     # {bob.id}
store.shortest_path(alice.id, bob.id)                     # [alice.id, bob.id]
store.stats()                                             # {graph, traces, raw}

edge = store.get_edge(...)
record, value = store.resolve_provenance(edge.provenance[0])
# record.raw_record["direct_manager_id"] == value == "17"
```

## Layout (backend)

```
backend/
├── models/
│   └── graph.py        GraphNode, GraphEdge, Provenance, SourceRecord
├── graph/
│   ├── schema.sql      provenance, source_records (SQLite)
│   └── store.py        GraphStore (Neo4j for graph, SQLite for traces + raw)
└── requirements.txt
```
