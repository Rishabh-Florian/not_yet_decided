# Better Context — System Architecture

> **Status as of 2026-04-26 (Big Berlin Hack submission)**

---

## Implementation status

| Subsystem | Status | Location |
|-----------|--------|----------|
| Knowledge graph store (Neo4j + SQLite hybrid) | **done** | `backend/graph/store.py` |
| Fact-level provenance + raw record store | **done** | SQLite `provenance` + `source_records` tables |
| Adaptive ingestion (MappingSpec / Onboarder / Ingestor) | **done** | `backend/ingest/` |
| Identity resolution (deterministic email-match → SAME_AS) | **done** (light) | fuzzy/LLM triage stubbed |
| LLM-extraction blocks at ingest time | **done** | opt-in per-spec, cached |
| Org subgraph (Department + Location nodes + MEMBER_OF edges) | **done** | `scripts/bootstrap_subgraph_nodes.py` |
| REST API — graph read endpoints | **done** | 7 GET endpoints in `backend/api/app.py` |
| REST API — pattern query DSL | **done** | `POST /api/graph/query` |
| REST API — edit API (human-in-the-loop) | **done** | `PUT /api/graph/node/{id}` |
| VFS (ls/cat/grep/find/stat/tree) | **done** (tools-only) | `backend/vfs/`, surfaced as Gemini tools |
| 4-tier retrieval cascade (exact/router/hybrid/agentic) | **done** | `backend/retrieval/` |
| Fine-tuned Pioneer GLiNER2 router (Round 1) | **done** | `pioneer/weights/inazuma-gliner2-v2` |
| Workflow framework + 2 built-in workflows | **done** | `backend/retrieval/workflows.py` |
| Eval harness | **done** | `backend/eval/` |
| Web UI (Next.js 14) — 4 views + subgraph filters + saved views | **done** | `frontend/` |
| Cross-encoder rerank | not yet | one model call post-HybridTier |
| Conflict resolution engine (detect + auto-route + REST) | **done** | `backend/conflict.py`, `GET/POST /api/conflicts*` |
| Conflict resolution UI (inbox) | partial | engine + REST done; visual queue page not yet |
| MCP server | not yet | thin wrappers over existing API |

---

## User flows

| Flow | Description | Status |
|------|-------------|--------|
| **F1** | AI agent retrieves context via VFS | **done** — 6 VFS tools in AgenticTier function-calling loop |
| **F2** | AI agent answers complex question via pattern query | **done** — `POST /api/graph/query` + `vfs_cat` enrichment |
| **F3** | Human browses company memory (web UI) | **done** — `/app/graph`, `/app/nodes`, `/app/query`, `/app/edit/:id` |
| **F4** | Human resolves data conflict | **partial** — engine detects + auto-routes at MERGE time; `GET/POST /api/conflicts*` exposes the queue; UI inbox not yet built |
| **F5** | Human edits a fact (with provenance tracking) | **done** — `PUT /api/graph/node/{id}` with `extraction_method="human"` |
| **F6** | Human explores department/location subgraph | **done** — Dim/Isolate/Expand view modes in FilterPanel |

---

## High-level architecture

```
┌───────────────────────────────────────────────────────────┐
│                    PRESENTATION LAYER                      │
│                                                           │
│  ┌────────────────┐  ┌──────────────┐  ┌───────────────┐  │
│  │  Web UI         │  │  VFS Tools   │  │  REST API     │  │
│  │  (Next.js 14)   │  │  (Gemini FC) │  │  /api/*       │  │
│  └───────┬─────────┘  └──────┬───────┘  └──────┬────────┘  │
└──────────┼───────────────────┼─────────────────┼───────────┘
           │                   │                 │
┌──────────▼───────────────────▼─────────────────▼───────────┐
│                    CONTEXT API LAYER                        │
│                      (FastAPI)                              │
│                                                             │
│  ┌──────────────┐  ┌────────────┐  ┌───────────────────┐   │
│  │  Graph Query  │  │  Retrieval │  │  Workflow         │   │
│  │  Engine       │  │  Cascade   │  │  Framework        │   │
│  │  (pattern DSL)│  │  (5 tiers) │  │  (frozen-policy)  │   │
│  └──────┬───────┘  └─────┬──────┘  └────────┬──────────┘   │
└─────────┼────────────────┼───────────────────┼─────────────┘
          │                │                   │
┌─────────▼────────────────▼───────────────────▼─────────────┐
│                    KNOWLEDGE LAYER                          │
│                                                             │
│  ┌─────────────────────────┐  ┌───────────────────────────┐  │
│  │  Neo4j                   │  │  SQLite                    │  │
│  │  :Entity nodes           │  │  source_records (L4)       │  │
│  │  typed relationships     │  │  provenance traces (L3)    │  │
│  │  attributes_json (L2)    │  │  ingestion control         │  │
│  │  :Entity.vector (HNSW)   │  │  mapping_specs             │  │
│  └─────────────────────────┘  └───────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
          ▲
┌─────────┴─────────────────────────────────────────────────┐
│                    INGESTION LAYER                         │
│                                                            │
│  ┌───────────┐  ┌───────────┐  ┌──────────┐  ┌─────────┐  │
│  │  Onboarder │  │  Ingestor  │  │  Identity│  │  Embed  │  │
│  │  (Gemini)  │  │  (determ.) │  │  Resolve │  │  Pass   │  │
│  └───────────┘  └───────────┘  └──────────┘  └─────────┘  │
└────────────────────────────────────────────────────────────┘
```

---

## Knowledge graph data model

### Four layers

| Layer | Storage | Content |
|-------|---------|---------|
| **L1 — Graph** | Neo4j `:Entity` + typed relationships | Node IDs, relationship types, graph topology |
| **L2 — Content** | Neo4j `attributes_json` per node/relationship | Typed, normalized attribute values |
| **L3 — Traces** | SQLite `provenance` | Per-attribute extraction history: source_file, source_record_id, source_field, extraction_method, extraction_model, confidence, spec_version, raw_value |
| **L4 — Raw data** | SQLite `source_records` | Original records verbatim, content_hash, ingested_at |

Invariant: every L3 trace has a FK to an L4 record. A trace cannot exist without its raw record. When a node is deleted, the store manually cascades to delete Neo4j entity + SQLite provenance rows (Neo4j doesn't know about SQLite FKs).

### Provenance model

```python
class Provenance:
    source_file: str          # e.g. "Enterprise_mail_system/emails.json"
    source_record_id: str     # e.g. "email:095a317c-..."
    source_field: str         # e.g. "$.sender_email"
    extraction_method: str    # direct_mapping | llm_extraction | rule_based | human
    extraction_model: str     # e.g. "gemini-2.5-flash" | "human:florian@..."
    confidence: float         # 0.0–1.0 (exact=1.0, grounded=0.9, inferred=0.7, human=1.0)
    raw_value: str            # original value before transforms
    spec_version: int         # which MappingSpec version produced this fact
```

### Canonical types

Defined in `backend/ingest/canonical.yaml` — data, not code. The ingestor validates all node types + relation types against this registry.

**Node types:** Person, Organization, Document, Message, Event, Asset, Topic

**Relation types:** MEMBER_OF, REPORTS_TO, WORKS_ON, OWNS, AUTHORED, SENT, RECEIVED, MENTIONS, PART_OF, PURCHASED, ASSIGNED_TO, TAGGED, RELATED_TO, SAME_AS

### Organizational subgraph

Departments and locations are **explicit Organization nodes**, not flat attributes. This enables graph traversal over org structure and powers the frontend subgraph filter.

```
(Person)-[:MEMBER_OF {dimension: "department"}]->(Organization {subtype: "Department", name: "Engineering"})
(Person)-[:MEMBER_OF {dimension: "location"}]->(Organization {subtype: "Location", name: "Berlin"})
```

5 locations: Berlin, Paris, San Francisco, New York, Tokyo (injected deterministically via `sha256(emp_id) % 5`).
8 departments: BPO, Business Development, Engineering, Finance, HR, Information Technology, Management, Sales.

---

## Adaptive ingestion

### MappingSpec

A YAML descriptor per (tenant, source-file) that fully defines how raw records map to graph structure:

```yaml
tenant: enterprisebench
source:
  file_pattern: Human_Resource_Management/Employees/employees.json
  format: json
  record_path: $[*]
nodes:
  - name: employee
    canonical_type: Person
    id_template: person:{emp_id}
    id_required_fields: [$.emp_id]
    fields:
      - attribute: name
        source: $.Name
      - attribute: email
        source: $.email
        transform: [normalize_email]
      - attribute: category       # department
        source: $.category
edges:
  - source_node: "@employee"
    target_node: "@manager"
    canonical_type: REPORTS_TO
spec_version: 2
```

**Transform registry** (25+ registered): `parse_iso_datetime`, `normalize_email`, `lowercase`, `strip_whitespace`, `parse_int`, `parse_float`, `split_comma`, ...

**LLM extraction blocks** (opt-in): for unstructured fields (e.g. email body → MENTIONS relationship). Cached per (record_hash, prompt_hash). Capped token budget.

**Drift detection**: `required_paths_hash` + `type_fingerprint` computed from a data sample at onboard time. Ingestor recomputes on every run and aborts if they diverge.

### Onboarding flow

```
Sample N records from source
→ Build prompt with schema context + canonical registry
→ Gemini Flash 2.5 drafts MappingSpec (response_schema = Pydantic JSON Schema)
→ 3-round validation + self-repair loop (pydantic errors fed back as correction context)
→ Persist to SQLite (status='draft')
→ Human reviews YAML → promotes: uv run python -m backend.ingest.manage promote <spec_id>
→ status='active' → Ingestor runs
```

### Idempotency

Every record ingestion is idempotent on `(spec_version, source_file, source_record_id, content_hash)`. Re-running `ingest_all.sh` is safe — records already in the ledger are skipped.

Node MERGE: Neo4j `MERGE (n:Entity {id: $id})` — update attributes on match, set on create.
Edge MERGE: deterministic id `sha256(source_id|relation_type|target_id)` — ensures no duplicate edges.

---

## Retrieval cascade

### Architecture

```
POST /api/query {"query": "..."}
         │
         ▼
  CascadeOrchestrator
         │
    ┌────┴─────────────────────────────────────────┐
    │                                              │
    ▼  if relevance > threshold: return            │
  ExactTier                                        │
    ▼  escalate                                    │
  RouterTier ──── route_to directive ──────────────►
    ▼  escalate                                    │
  HybridTier                                       │
    ▼  escalate                                    │
  AgenticTier                                      │
    ▼  always returns                              │
  StubTier  ◄─────────────────────────────────────┘
```

### Tier details

**ExactTier (R1)**
- Cypher `MATCH (n:Entity {id: $id}) RETURN n`
- Neo4j fulltext index (`CALL db.index.fulltext.queryNodes(...)`) — BM25-like, normalized to [0, 1)
- Relevance: 1.0 for id hit, BM25 score otherwise

**RouterTier (R4) — Pioneer GLiNER2 SLM**
- 205M parameter GLiNER2 model, multi-task fine-tuned on Pioneer.ai
- Task 1 (intent): 4-way classification — `lookup` / `search` / `analytical` / `unknown`
- Task 2 (NER): 6 entity types — `person` / `org` / `date` / `ticket_id` / `product` / `location`
- Single forward pass: 467ms p95 on CPU
- Round 1 benchmark vs frontier:
  | Metric | Base GLiNER2 | GPT-4o | **Fine-tuned** |
  |--------|-------------|--------|-----------------|
  | Intent accuracy | 53.3% | 86.7% | **91.1%** |
  | NER macro F1 | 0.300 | 0.337 | **0.394** |
  | Latency p95 | — | 1699ms | **467ms** |
  | Cost/1k queries | $0 | ~$5 | **$0** |
- On `lookup` intent: inline-delegates to ExactTier
- On `search` / `analytical`: emits `route_to` directive for downstream tiers

**HybridTier (R2)**
- Neo4j HNSW vector index (`CALL db.index.vector.queryNodes(...)`) — cosine similarity
- Neo4j fulltext index — BM25-like
- Reciprocal Rank Fusion (k=60): `RRF(r) = 1/(k + r)` summed over both ranked lists
- Requires embedding pass: `uv run python -m backend.retrieval.embed`

**AgenticTier (R3)**
- Bounded Gemini 2.5 Flash function-calling loop
- Max 6 tool calls, 10s wall-clock, temperature 0
- 6-tool surface:
  - `pattern_query(pattern, limit)` — typed DSL `(A)-[R]->(B)`, returns triples
  - `fulltext_search(query, limit)` — BM25 over all entity names + attributes
  - `vector_search(query, limit)` — HNSW cosine over entity embeddings
  - `get_node(id)` — full node + attributes + provenance
  - `get_neighbors(id, relation_type, depth)` — graph traversal
  - `get_source_record(source_file, record_id)` — L4 verbatim raw record
- Relevance: 0.7 (grounded hit), 0.3 (answer synthesized without grounding), 0.0 (failed)

**StubTier (R0)**
- Terminal fallback, always returns 0 items with relevance 0.0
- Ensures cascade always terminates

### Workflow framework

Frozen-policy recipes: a `Workflow` declares `allowed_tiers: frozenset[str]` at class level. The framework wraps the live tier set in a `TierRegistry` locked to that subset. Cuts latency and cost when the retrieval shape is known.

| Workflow | Tiers | Pipeline |
|----------|-------|---------|
| `answer-customer-email` | `{exact, hybrid}` | T1 sender lookup → T1 neighbors (cap 25) → T3 product search top-5 → single-shot Gemini compose |
| `thread-summary` | `{hybrid}` | T3 cluster recall (participants + regex NER id tokens) → bounded 3-tool agent loop (≤6 calls) → structured markdown |

---

## Conflict resolution

`backend/conflict.py` — the decision engine sits at the `add_node` MERGE seam inside `GraphStore`. When an incoming attribute value disagrees with the existing value, `decide(existing, incoming)` routes it through a deterministic decision table.

### Decision table

| Rule | Condition | Verdict | Effect |
|------|-----------|---------|--------|
| 1 | Values equal after `strip().casefold()` | `AUTO_MERGE` | Keep existing; both provenance records appended |
| 2 | Either side is `HUMAN` confidence | `AUTO_PICK` (HUMAN wins) | Write HUMAN side; provenance appended |
| 3 | Different rungs on the ladder | `AUTO_PICK` (higher rung wins) | Write winning side; provenance appended |
| 4 | Both `INFERRED` | `LLM_TRIAGE` | Keep existing; queue conflict row; Gemini call resolves when `BETTER_CONTEXT_AGENTIC=gemini` |
| 5 | Same rung at EXACT/GROUNDED, values differ | `ESCALATE` | Keep existing; queue conflict row for human review |

Confidence ladder: `HUMAN > EXACT > GROUNDED > INFERRED`

`AUTO_MERGE` and `AUTO_PICK` never produce a `Conflict` row — the existing append-only `provenance` table already audits them. Only `LLM_TRIAGE` and `ESCALATE` land in the queue.

### Conflict REST API

```bash
# List open conflicts
GET /api/conflicts?status=open&limit=50

# Inspect a single conflict (shows both candidate values + provenance)
GET /api/conflicts/{id}

# Resolve: human picks a value (writes FactConfidence.HUMAN provenance via edit_node)
POST /api/conflicts/{id}/resolve
{"value": "Acme Corporation", "editor": "florian@company.com"}
```

Resolutions go through `edit_node`, so they carry full `FactConfidence.HUMAN` provenance and are reversible like any other edit.

---

## Virtual file system (VFS)

Six operations, all pure Cypher:

| Operation | Description |
|-----------|-------------|
| `vfs_ls(path)` | List all entities of a canonical type |
| `vfs_cat(path)` | Full node: frontmatter + attributes + grouped neighbors + provenance + raw evidence links |
| `vfs_stat(path)` | Metadata only: type, id, attribute count, edge count, last modified |
| `vfs_grep(query, path)` | BM25 fulltext search within a type namespace |
| `vfs_find(path, where, modified_after)` | Cypher slice + Python filter |
| `vfs_tree(path, depth)` | Recursive listing, max depth 3 |

Path convention: `/{CanonicalType}/{node_id}` — derived at request time, no stored `vfs_path` column.

Surfaced as Gemini function-calling tools in `backend/retrieval/tools.py`. Used by AgenticTier and the `thread-summary` workflow. No REST endpoints intentionally — the agent path is the consumption surface.

---

## Web UI

Next.js 14 App Router, React 18, TypeScript. TanStack Query v5 for server state. Zustand for client state (filter panel).

### Routes

| Route | Component | Purpose |
|-------|-----------|---------|
| `/` | `HomePage` | Landing: animated sphere, chatbar → `POST /api/query`, result cards |
| `/app/graph` | `GraphView` | Force-directed graph + FilterPanel with subgraph modes |
| `/app/nodes` | `NodeListTable` | Paginated browser by type |
| `/app/nodes/[id]` | `NodeDetailPanel` | Attributes + ProvenanceTimeline + SourceRecordDrawer |
| `/app/query` | `QueryView` | Pattern DSL → paginated triples |
| `/app/edit/[id]` | `EditForm` | Human node editor with provenance |

### Subgraph filter (FilterPanel)

The filter panel implements the three-mode subgraph view:

- **Dim** — all nodes visible; matched nodes at full opacity, rest faded to 5%. Smooth bezier-eased CSS transition (600ms).
- **Isolate** — only matched nodes + any Organization nodes they connect to via MEMBER_OF + edges strictly between them.
- **Expand** — matched nodes + all direct neighbors passing base filters.

Filter semantics: AND across dimensions (dept filter AND location filter), OR within each dimension (`Engineering OR Sales`). Subgraph state: `departments: Set<string> | null` where `null` = no constraint. Named views saved/loaded from localStorage via Zustand persist middleware.

### Graph visualization

`react-force-graph-2d` with:
- Canvas-based custom node paint: per-node opacity animation for dim/undim transitions
- Bezier-eased opacity animation (`cubic-bezier(0.16, 1, 0.3, 1)`, Newton-solved on x)
- Hub glow effect: Person nodes with degree ≥ 12 get a radial shadow
- Auto-fit to visible bounds when filter narrows below 50 nodes
- Label rendering at zoom > 1.5× for Person nodes

Node colors:
| Type | Color | Notes |
|------|-------|-------|
| Person | `#E8E8E5` | Light grey |
| Message | `#6B7280` | Medium grey |
| Organization (Dept) | `#818CF8` | Indigo |
| Organization (Location) | `#34D399` | Emerald |

---

## Dataset

EnterpriseBench — simulated enterprise dataset from Inazuma.co / Qontext.

| Source | Format | Records | Graph output |
|--------|--------|---------|-------------|
| employees.json | JSON | 1,260 | Person nodes + REPORTS_TO + MEMBER_OF edges |
| emails.json | JSON | 11,928 | Message nodes + SENT/RECEIVED edges |
| resume_information.csv | CSV | 1,013 | Person attribute enrichment |
| conversations.json | JSON | 2,897 | Message nodes |
| posts.json | JSON | 971 | Message nodes |
| it_tickets.json | JSON | 163 | Event nodes |
| GitHub.json | JSON | 750 | Document + Event nodes |
| customers.json | JSON | 90 | Person/Organization nodes |
| products.json | JSON | 1,351 | Asset nodes |
| sales.json | JSON | 13,510 | Event nodes |
| product_sentiment.json | JSON | 13,510 | Document nodes |
| customer_support_chats.json | JSON | 1,000 | Message nodes |
| clients.json | JSON | 400 | Organization nodes |
| vendors.json | JSON | 400 | Organization nodes |
| Policy_Documents/ | PDF | 24 | Document nodes |
| Customer_orders/ | PDF | 270 | Document nodes |

**Graph stats (emails + employees ingested, 2026-04-26):**
- 13,201 nodes
- 26,937 edges
- 200,907 provenance records
- 1,873 MEMBER_OF edges (departments + locations)

---

## Engineering notes

### Why Neo4j + SQLite (not one database)

Neo4j is optimized for graph topology and vector search but is a poor relational store for append-only provenance ledgers with FK constraints. SQLite is ACID, fast for sequential writes, and trivially embeddable. The hybrid uses each for what it's good at. The `GraphStore` class manages cross-store atomicity: SQLite is staged first (FK checked), then Neo4j MERGE, then SQLite committed.

### Why a fine-tuned small model over GPT-4o for routing

The router is called on every query. At GPT-4o prices (~$5/1k queries) and latency (1699ms p95), it would be the bottleneck. The 205M GLiNER2 SLM runs locally at $0 and 467ms. After Round 1 fine-tuning it already outperforms GPT-4o on both intent accuracy and NER F1. Round 2 targets the remaining gaps (`ambiguous` intent, `ticket_id`/`date` NER coverage).

### Why MappingSpec YAML (not hardcoded parsers)

A new data source requires a new YAML file, not a new Python module. The single `Ingestor` class handles all 14 sources. The YAML is reviewable by non-engineers. The LLM drafts it once; humans promote it; the ingestor runs deterministically forever after that. This is the vendor-agnostic property the Qontext track specifically asks for.

### Why VFS is not materialized to disk

The original design stamped a `vfs_path` on every node at ingest time. This was dropped: the path is `/{canonical_type}/{node_id}` — trivially derived at request time, zero storage cost, auto-correct when the canonical registry changes. The `vfs_path` column on `:Entity` is vestigial and unused.
