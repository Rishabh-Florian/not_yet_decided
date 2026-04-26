# Better Context Track — System Architecture & Design

> **Implementation Status (2026-04-26).** This document is the original
> aspirational design. The actual implementation has diverged in one
> important way: instead of per-source hardcoded parsers + per-source LLM
> extractors (the original Components 1 + 2), Better Context uses an
> **adaptive ingestion** layer driven by a `MappingSpec` per source.
> See [Adaptive Ingestion (implemented)](#adaptive-ingestion-implemented)
> below for the design that supersedes Components 1 + 2.
>
> | Subsystem | Status | Notes |
> |---|---|---|
> | Knowledge graph store (Neo4j + SQLite hybrid) | **done** | `backend/graph/store.py` |
> | Fact-level provenance + raw record store | **done** | `source_records`, `provenance` tables |
> | Adaptive ingestion (MappingSpec / Onboarder / Ingestor) | **done** | `backend/ingest/` |
> | Identity resolution (deterministic email match -> SAME_AS) | **done** (light) | fuzzy / LLM triage stubbed |
> | LLM-extraction blocks at ingest time | **done** | opt-in per-spec, cached |
> | REST API — Graph read endpoints | **done** | `backend/api/app.py` — 7 GET endpoints |
> | REST API — Graph pattern query | **done** | `POST /api/graph/query` — typed pattern DSL |
> | REST API — Edit API (human-in-the-loop) | **done** | `PUT /api/graph/node/{id}` — provenance-tracked edits |
> | VFS API (ls, cat, grep, find, stat, tree) | not yet | VFS is a logical view (no disk materialization) — endpoints not built |
> | Search API (semantic + hybrid, Neo4j HNSW) | not yet | Vector index architecture designed, endpoints not built |
> | Conflict resolution engine + UI | not yet | Rule-based + LLM triage designed, not implemented |
> | MCP server (for Claude / AI agents) | not yet | MCP tool wrappers over existing API |
> | Web UI (React + Next.js) | not yet | No frontend code |
> | ~~VFS materialization to disk~~ | dropped | Not needed: raw records already verbatim in `source_records`, VFS is a logical view computed from `GraphNode.vfs_path` via Cypher. No re-materialization on edit. |
> | ~~ChromaDB / external vector index~~ | dropped | Replaced by Neo4j native vector indexes (5.13+, HNSW). Embeddings live on `:Entity` nodes; one database, no sync. |
>
> ### User Flow Status
>
> | Flow | Description | Status | What's working | What's missing |
> |------|-------------|--------|----------------|----------------|
> | **Flow 1** | AI Agent Retrieves Context (VFS browse) | not yet | — | VFS API endpoints (`ls`, `cat`, `grep`, `find`) |
> | **Flow 2** | AI Agent Answers Complex Question (pattern query) | **partial** | `POST /api/graph/query` returns typed pattern matches with provenance | VFS `cat` for enriching results with full entity files |
> | **Flow 3** | Human Browses Company Memory (web UI) | not yet | — | Frontend (React + Next.js), graph visualization |
> | **Flow 4** | Human Resolves Conflict (conflict queue) | not yet | — | Conflict detection engine, resolution API, queue UI |
> | **Flow 5** | Human Edits Company Memory (edit + provenance) | **done** | `PUT /api/graph/node/{id}` with synthetic source records, per-attribute human provenance, version bumps | — |

## Executive Summary

**Better Context** is a system that transforms fragmented enterprise data (email, CRM, HR, IT tickets, chat, code repos, policies) into a **structured, inspectable, editable company memory** — a virtual file system backed by a knowledge graph with fact-level provenance. It is designed for both AI agents (efficient retrieval) and humans (inspect, validate, edit, extend).

The system ingests the Inazuma.co EnterpriseBench dataset (~50K records across 13 sources), resolves conflicts, extracts entities and relationships, and exposes the result as:

1. A **virtual file system (VFS)** — Unix-style directory tree where every "file" is a structured knowledge artifact
2. A **knowledge graph** — entities and relationships with fact-level provenance linking back to source records
3. A **web interface** — for humans to browse, search, validate, edit, and extend the company memory
4. An **API layer** — for AI agents to efficiently retrieve context via file-system operations and graph queries

---

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          PRESENTATION LAYER                             │
│                                                                         │
│   ┌──────────────────┐  ┌──────────────────┐  ┌─────────────────────┐  │
│   │   Web UI          │  │   CLI / Shell    │  │   Agent API         │  │
│   │   (React + Next)  │  │   (VFS browse)   │  │   (REST + MCP)     │  │
│   └────────┬─────────┘  └────────┬─────────┘  └──────────┬──────────┘  │
│            │                     │                        │             │
└────────────┼─────────────────────┼────────────────────────┼─────────────┘
             │                     │                        │
┌────────────▼─────────────────────▼────────────────────────▼─────────────┐
│                          CONTEXT API LAYER                               │
│                         (FastAPI / Python)                                │
│                                                                          │
│   ┌────────────┐  ┌──────────────┐  ┌──────────┐  ┌──────────────────┐  │
│   │ VFS Router │  │ Graph Query  │  │ Search   │  │ Conflict         │  │
│   │ (ls/cat/   │  │ Engine       │  │ Engine   │  │ Resolution       │  │
│   │  grep/find)│  │ (traversal)  │  │ (hybrid) │  │ Engine           │  │
│   └──────┬─────┘  └──────┬───────┘  └────┬─────┘  └───────┬──────────┘  │
│          │               │               │                 │             │
└──────────┼───────────────┼───────────────┼─────────────────┼─────────────┘
           │               │               │                 │
┌──────────▼───────────────▼───────────────▼─────────────────▼─────────────┐
│                         KNOWLEDGE LAYER                                   │
│                                                                           │
│   ┌──────────────────────────────────────────────────────────────────┐  │
│   │  Knowledge Graph + Vector Index                                   │  │
│   │  Neo4j (entities, edges, native vector index on embeddings)       │  │
│   │  + SQLite (provenance, raw records, ingestion control plane)      │  │
│   │  VFS is a logical view (GraphNode.vfs_path) — no disk materialization │
│   └────────────────────────────────┬─────────────────────────────────┘  │
│                                    │                                     │
└────────────────────────────────────┼─────────────────────────────────────┘
                                     │
┌────────────────────────────────────▼─────────────────────────────────────┐
│                        INGESTION LAYER                                    │
│                                                                           │
│   ┌──────────────┐  ┌──────────────┐  ┌────────────┐  ┌───────────────┐ │
│   │ Source        │  │ Entity       │  │ Relation   │  │ Conflict      │ │
│   │ Parsers       │  │ Extractor    │  │ Linker     │  │ Detector      │ │
│   │ (JSON/CSV/PDF)│  │ (LLM + rule) │  │ (fuzzy +   │  │ (rule-based + │ │
│   │               │  │              │  │  exact)    │  │  LLM triage)  │ │
│   └──────┬───────┘  └──────┬───────┘  └──────┬─────┘  └───────┬───────┘ │
│          │                 │                  │                 │         │
└──────────┼─────────────────┼──────────────────┼─────────────────┼─────────┘
           │                 │                  │                 │
┌──────────▼─────────────────▼──────────────────▼─────────────────▼─────────┐
│                        RAW DATA SOURCES                                   │
│                                                                           │
│  employees.json │ emails.json │ conversations.json │ products.json │ ...  │
│  customers.json │ sales.json  │ it_tickets.json    │ GitHub.json   │ ...  │
│  clients.json   │ vendors.json│ posts.json         │ 24 PDFs       │ ...  │
│  resumes (CSV + 1013 PDFs)   │ 270 customer order PDFs            │      │
└───────────────────────────────────────────────────────────────────────────┘
```

---

## Tech Stack

| Layer | Technology | Why |
|---|---|---|
| **Backend API** | Python 3.11 + FastAPI | Fast to build, async, great ecosystem for data/AI |
| **Knowledge Graph + Vector Search** | Neo4j 5.13+ (graph + native HNSW vector index) + SQLite (provenance, raw records, ingestion control plane) | One database for graph and embeddings — no separate vector store to sync. ChromaDB removed. |
| **LLM** | Claude API (claude-sonnet-4-6) | Entity extraction, conflict resolution, summarization |
| **Frontend** | Next.js 14 + React + Tailwind + shadcn/ui | Fast to prototype, good file-tree components |
| **VFS** | Logical view via `GraphNode.vfs_path` + Cypher queries | Raw records already verbatim in `source_records`; no need to write a parallel tree of markdown files to disk. |
| **PDF Parsing** | PyMuPDF (fitz) | Fast, reliable PDF text extraction |
| **Data Parsing** | pandas + orjson | High-performance JSON/CSV handling |

---

## Adaptive Ingestion (implemented)

> **The pipeline is company-data agnostic by design.** A single `Ingestor`
> instance handles records from any CRM, HR, ITSM, or comms vendor as long
> as the data is readable as JSON / JSONL / NDJSON / CSV. There are no
> per-vendor parsers, no per-vendor extractors, and no per-vendor branches
> in code. Every difference between vendors is expressed in the
> `MappingSpec` YAML, never in Python.
>
> This is verified by `backend/test_ingest_agnostic.py`: four
> deliberately-different vendor payload shapes (HubSpot-like nested
> `properties`, Salesforce-like `attributes` envelope with `IsDeleted`
> flag, Microsoft Dynamics OData with `@odata.etag` and `_lookup_value`
> fields, Pipedrive-like `primary_email[*]` arrays) ingested through the
> same `Ingestor` collapse to identical canonical `Person` nodes. Every
> node carries a `Provenance` row pointing back to its original
> vendor-specific field path, so downstream queries are vendor-blind.

The original design (Components 1 + 2 below) assumed a fixed set of vendors
and one hand-coded parser per source file. That doesn't generalize: real
deployments see different companies and different departments shipping data
under different schemas, with different field names, casings, and
conventions. Hard-coding parsers per vendor is operationally fatal.

The implemented ingestion layer replaces that with a **schema-on-onboard**
design: an LLM (Gemini Flash 2.5) drafts a `MappingSpec` ONCE per
(tenant, source-file). After human review, that spec drives a fully
deterministic `Ingestor` for every record, forever. The LLM is never in the
per-record path for structured data.

```
   ┌─────────────────────────────────────────────────────────────┐
   │  ONBOARD (one-time, per source)                              │
   │                                                              │
   │   sample N records                                           │
   │       │                                                      │
   │       ▼                                                      │
   │   Onboarder ─── Gemini Flash 2.5 ──▶  MappingSpec (YAML)     │
   │       │           (response_schema = MappingSpec JSON Schema)│
   │       │                                                      │
   │       ▼                                                      │
   │   pydantic + canonical-registry validation                   │
   │   one-shot self-repair on failure                            │
   │       │                                                      │
   │       ▼                                                      │
   │   stamp `required_paths_hash` + `type_fingerprint`           │
   │   persist to mapping_specs (status='draft')                  │
   │   human reviews YAML, edits, then `promote` → status='active'│
   └─────────────────────────────────────────────────────────────┘

   ┌─────────────────────────────────────────────────────────────┐
   │  RUN (every record, deterministic)                           │
   │                                                              │
   │   active spec ──▶ drift check (paths_hash + type_fingerprint)│
   │                       │                                      │
   │            mismatch → DriftError, abort run                  │
   │                       │ ok                                   │
   │                       ▼                                      │
   │   for each record:                                           │
   │     idempotency: skip if (spec_v, file, id, hash) seen       │
   │     add_source_record  (verbatim raw)                        │
   │     apply NodeRules    → MERGE on id_template                │
   │                          (last-write-wins attrs, prov appends)│
   │     apply EdgeRules    → MERGE on sha256(src|rel|tgt)        │
   │     run LLMExtraction blocks (opt-in, cached, grounded,      │
   │                              capped)                           │
   │                       │                                      │
   │            failure on this record → dead_letter, continue    │
   │                                                              │
   │   close run; record ledger row (records_in/out/dead/...)     │
   └─────────────────────────────────────────────────────────────┘

   ┌─────────────────────────────────────────────────────────────┐
   │  RESOLVE IDENTITY (post-pass, optional)                      │
   │                                                              │
   │   IdentityResolver.resolve():                                │
   │     cluster Person nodes by normalized email                 │
   │     emit SAME_AS edges between members of each cluster       │
   │     (does NOT merge; preserves provenance per source)        │
   └─────────────────────────────────────────────────────────────┘
```

### Why this absorbs vendor heterogeneity

| Difference between two vendors | Where it's absorbed |
|---|---|
| Different field names (`sender_emp_id` vs `from_id`) | `FieldMap.source` JSONPath in the spec |
| Same field, different format (ISO date vs epoch int) | `FieldMap.transform` chain (`parse_iso_datetime`, `lowercase`, `normalize_email`, …) |
| Field optional in some sources | `coalesce` list of paths: `source: [$.dob, $.date_of_birth, $.birthDate]` |
| Same concept, different type names ("Staff", "Employee", "TeamMember") | `canonical_aliases: { Staff: Person }` |
| New unstructured field worth extracting (email body → mentions) | `llm_blocks` entry — opt-in, cached, grounded |
| Vendor changes their export format | Drift-hash mismatch aborts the run; never silent re-inference |
| Same person under multiple ids across sources | Post-pass `IdentityResolver` emits `SAME_AS` edges |

### LLM usage policy (load-bearing)

The LLM is **not** in the per-record hot path for structured data. Three
bounded uses only:

1. **Initial schema alignment** — `Onboarder.draft_spec` runs Gemini Flash
   2.5 ONCE per (tenant, source-file). Output: a YAML `MappingSpec`.
2. **Opt-in extraction on explicitly unstructured fields** — `LLMExtraction`
   blocks declared in the spec (e.g. email-body → `MENTIONS`). Cached by
   `cache_key`, grounded against the source span (`require_grounding: true`
   rejects items whose `surface_form` does not appear verbatim in the input),
   capped by `max_extractions_per_record`. The LLM's self-rated `confidence`
   number is captured into `Provenance.model_self_score` for audit but is
   never used to filter or threshold facts (see "Provenance confidence" below).
   A spec with no `llm_blocks` ⇒ zero LLM calls during ingestion.
3. **One-shot self-repair on drafted specs** — if pydantic validation of a
   Gemini-drafted spec fails, the validator error is sent back ONCE for
   repair.

Explicitly **not** used as a fallback:
- Missing required field → record goes to `dead_letter`, never to an LLM
  guess.
- Schema drift → run aborts via `required_paths_hash` /
  `type_fingerprint` mismatch, never silent LLM re-inference.
- Type coercion / casing / date-parsing → `runtime` transformer registry,
  never an LLM call.

### Canonical type registry

Anchors the type space across vendors. Lives in
`backend/ingest/canonical.yaml`, edited as data not code. Specs that
reference unknown types fail at load.

| Node types | Relation types (subset) |
|---|---|
| Person, Organization, Document, Message, Event, Asset, Topic | MEMBER_OF, REPORTS_TO, WORKS_ON, OWNS, AUTHORED, SENT, RECEIVED, MENTIONS, PART_OF, PURCHASED, ASSIGNED_TO, TAGGED, RELATED_TO, SAME_AS |

### MappingSpec shape (abridged)

```yaml
spec_version: 1
tenant: enterprisebench
source:
  file_pattern: Enterprise_mail_system/emails.json
  format: json                # json | jsonl | ndjson | csv
  record_path: $[*]
canonical_aliases:
  Email: Message
  Employee: Person
nodes:
  - name: email
    canonical_type: Message
    id_template: "email:{email_id}"
    fields:
      - { attribute: subject, source: $.subject }
      - { attribute: sent_at, source: $.date, transform: [parse_iso_datetime] }
  - name: sender
    canonical_type: Person
    id_template: "person:{sender_emp_id}"
    when: { not_null: $.sender_emp_id }
    fields:
      - { attribute: emp_id, source: $.sender_emp_id }
      - { attribute: email,  source: $.sender_email, transform: [normalize_email] }
edges:
  - { canonical_type: SENT,     source_node: "@sender", target_node: "@email" }
  - { canonical_type: RECEIVED, source_node: "@email",  target_node: "@recipient" }
llm_blocks:                     # optional, opt-in only
  - name: mentions_in_body
    input_source: $.body
    prompt_template: "Extract Person/Organization/Topic references from this email body..."
    output_schema: { type: object, properties: {...} }
    require_grounding: true
    max_extractions_per_record: 50
    cache_key: ["$.email_id"]
required_paths_hash: <sha256>   # stamped at onboarding
type_fingerprint: { ... }        # stamped at onboarding
```

### Ingestion control plane (SQLite)

| Table | Purpose |
|---|---|
| `mapping_specs` | versioned MappingSpec YAML, status ∈ {draft, active, retired} |
| `llm_cache` | cached structured outputs keyed by sha256(model | prompt | inputs); raw model output preserved alongside parsed |
| `ingest_runs` | one row per `Ingestor.run` invocation (counts, status, timestamps) |
| `ingest_runs_records` | idempotency: `(spec_version, source_file, source_record_id, content_hash)` |
| `dead_letter` | per-record failures with reason + raw record |

### Atomicity and dedup

- `add_node` and `add_edge` are now `MERGE`-on-id (not `CREATE`). Re-ingestion
  of the same record is a no-op on graph structure; provenance traces are
  appended.
- Edge ids are deterministic: `sha256(src|rel|tgt|valid_from)`. Same fact
  ingested twice doesn't create duplicate relationships.
- Per-record write order: stage SQLite provenance → run Neo4j MERGE →
  commit SQLite. On Neo4j failure, SQLite rolls back. On SQLite-commit
  failure, the just-merged Neo4j node/edge is compensated by id.
- Type-collision protection: an id with a different `canonical_type` than
  the existing node raises rather than silently merging (e.g. refuses to
  promote a Person id into an Organization).

### Files (implementation)

```
backend/
  config.py                  load_dotenv() + env constants
  graph/
    schema.sql               raw + provenance + ingestion-control-plane tables
    store.py                 GraphStore (Neo4j + SQLite, MERGE-based)
  ingest/
    canonical.yaml           canonical type registry (data, not code)
    spec.py                  pydantic MappingSpec + canonical-registry loader
    runtime.py               JSONPath + transformers + predicates + drift
    store.py                 control-plane SQLite (mapping_specs, llm_cache, runs, dead_letter)
    llm.py                   GeminiClient + JSON-Schema sanitizer + cache
    onboard.py               Onboarder.draft_spec()
    ingestor.py              Ingestor.run() + LLM-block runner
    identity.py              IdentityResolver (deterministic email match)
    __main__.py              CLI: dryrun / run / onboard / promote / resolve-identity
  test_ingest_agnostic.py    cross-vendor agnosticism proof (4 shapes, 1 Ingestor)
  test_graph_query_edit.py   pattern query DSL + edit API tests (24 tests: parser, integration, endpoint)
ingest_specs/
  enterprisebench/
    emails.yaml              hand-written reference spec
```

### Honest scope: covered formats vs. shim-required

**Works out of the box** (`Ingestor` reads natively):
- JSON arrays, JSONL, NDJSON, CSV — covers most CRM exports and most REST
  API responses once they're saved to disk.
- Arbitrarily nested JSON via JSONPath (`$.properties.email`,
  `$.contact.address.city`).
- Array-of-objects fields via `[*]` wildcards
  (`$.primary_email[*].value`).

**Needs a small shim** (~10 lines each, isolated to `_iter_records`):
- Live API ingestion (Salesforce REST, HubSpot API, Pipedrive API…) —
  fetch, dump JSON, run `Ingestor`. The spec doesn't care if records came
  from a file or HTTP.
- Excel `.xlsx` — `pandas.read_excel(...).to_csv(...)`, or extend
  `_iter_records`.
- XML / SOAP — `xmltodict` to JSON, ingest as JSON.
- SQL dumps — export per-table to CSV.

**Out of scope today**: live streaming, binary attachments (PDFs, images),
schema discovery from a database catalog. Each is a localized change to
`_iter_records`; the rest of the pipeline is format-blind.

---

## Component Design

> The components below describe the **original** monolithic design.
> Components 1 (Source Parsers) and 2 (Entity Extractor) have been
> superseded by the [Adaptive Ingestion](#adaptive-ingestion-implemented)
> layer above and are kept only as historical context.

### Component 1: Source Parsers

Each raw data source gets a dedicated parser that normalizes it into a common intermediate representation.

```python
@dataclass
class SourceRecord:
    source_id: str          # e.g., "emails.json:email_id:4226322d"
    source_type: str        # e.g., "email", "employee", "it_ticket"
    source_file: str        # e.g., "Enterprise_mail_system/emails.json"
    raw_data: dict          # original record
    parsed_at: datetime     # ingestion timestamp
    entities: list[Entity]  # extracted entities
    relations: list[Relation]  # extracted relationships
    facts: list[Fact]       # atomic facts with provenance
```

**Parser registry:**

| Parser | Source File(s) | Output Entities |
|---|---|---|
| `EmployeeParser` | employees.json, resume_information.csv | Employee, Department, Skill, OrgUnit |
| `EmailParser` | emails.json | Email, EmailThread, Topic |
| `ConversationParser` | conversations.json | Conversation, Topic |
| `CRMParser` | customers.json, products.json, sales.json | Customer, Product, Category, Sale |
| `SupportParser` | customer_support_chats.json | SupportTicket, Issue |
| `SentimentParser` | product_sentiment.json | Review, SentimentScore |
| `BusinessParser` | clients.json, vendors.json | Client, Vendor, Partnership |
| `ITParser` | it_tickets.json | ITTicket, ITIssue |
| `GitHubParser` | GitHub.json | Repository, CodeFile, GitIssue |
| `PolicyParser` | Policy_Documents/*.pdf | Policy, PolicySection, Rule |
| `PostParser` | posts.json | SocialPost, Topic |
| `OrderParser` | Customer_orders/*.pdf | Invoice, PurchaseOrder, ShippingOrder |

**Parsing strategy by data type:**
- **JSON files**: Direct field mapping with type coercion (strings → int/float/date)
- **CSV**: pandas read with schema validation
- **PDFs (policies)**: PyMuPDF text extraction → section splitting by headers → LLM-based rule extraction
- **PDFs (orders)**: PyMuPDF text extraction → regex-based field extraction (invoice #, amounts, dates)
- **Malformed records**: Error-tolerant parsing with fallback; corrupted fields logged but record not dropped

---

### Component 2: Entity Extractor

Transforms parsed source records into typed entities with normalized attributes.

**Entity types and their canonical schemas:**

```python
class Employee(Entity):
    emp_id: str              # primary key
    name: str
    email: str
    department: str
    level: str
    seniority_tier: int      # extracted from level code
    skills: list[str]
    date_of_joining: date
    date_of_leaving: date | None
    is_active: bool
    salary: float
    age: int
    performance_rating: int
    gender: str
    marital_status: str
    manager_id: str | None
    reportee_ids: list[str]
    leave_balance: dict

class Customer(Entity):
    customer_id: str
    name: str
    document_paths: dict     # invoice, PO, shipping order

class Product(Entity):
    product_id: str          # ASIN
    name: str
    category_path: list[str] # parsed from pipe-delimited
    discounted_price: float  # parsed from ₹ string
    actual_price: float
    discount_pct: float
    rating: float | None

class Client(Entity):
    client_id: str
    business_name: str
    industry: str
    business_type: str
    contact: dict
    monthly_revenue: float   # parsed from $ string
    poc_product: str
    poc_status: str
    representative_emp_id: str

class Vendor(Entity):
    vendor_id: str           # normalized from client_id
    business_name: str
    industry: str
    business_type: str
    relationship_desc: str
    representative_emp_id: str

# ... ITTicket, Repository, Policy, EmailThread, Conversation, SocialPost, etc.
```

**Extraction approach:**
- **Structured sources** (JSON/CSV): Direct field mapping with type normalization
- **Semi-structured** (chat transcripts, email bodies): Rule-based extraction for known patterns + LLM for open-ended extraction
- **Unstructured** (PDFs, post bodies): LLM-powered extraction with structured output schemas

---

### Component 3: Relation Linker

Discovers and materializes relationships between entities, using both explicit foreign keys and inferred connections.

**Explicit relationships (from foreign keys):**

| Relationship | Source | Confidence |
|---|---|---|
| `Employee -[REPORTS_TO]-> Employee` | employees.json reports_to | 1.0 |
| `Employee -[SENT]-> Email` | emails.json sender_emp_id | 1.0 |
| `Employee -[RECEIVED]-> Email` | emails.json recipient_emp_id | 1.0 |
| `Email -[IN_THREAD]-> EmailThread` | emails.json thread_id | 1.0 |
| `Employee -[MESSAGED]-> Conversation` | conversations.json | 1.0 |
| `Employee -[AUTHORED]-> SocialPost` | posts.json emp_id | 1.0 |
| `Employee -[OWNS]-> Repository` | GitHub.json emp_id | 1.0 |
| `Employee -[RAISED]-> ITTicket` | it_tickets.json raised_by_emp_id | 1.0 |
| `Employee -[ASSIGNED_TO]-> ITTicket` | it_tickets.json emp_id | 1.0 |
| `Employee -[HANDLES_SUPPORT]-> SupportChat` | chats emp_id | 1.0 |
| `Employee -[REPRESENTS]-> Client` | clients.json rep_employee | 1.0 |
| `Employee -[MANAGES_VENDOR]-> Vendor` | vendors.json rep_employee | 1.0 |
| `Customer -[PURCHASED]-> Product` | sales.json | 1.0 |
| `Customer -[REVIEWED]-> Product` | product_sentiment.json | 1.0 |
| `Customer -[CONTACTED_SUPPORT]-> Product` | support_chats | 1.0 |

**Inferred relationships (from content analysis):**

| Relationship | Method | Confidence |
|---|---|---|
| `Employee -[COLLABORATES_WITH]-> Employee` | Co-occurrence in emails, conversations, threads | 0.7–0.9 |
| `Employee -[WORKS_ON_TOPIC]-> Topic` | Topic extraction from emails/posts/conversations | 0.6–0.8 |
| `Employee -[HAS_SKILL]-> Skill` | Parsed from skills field + resume content | 0.8–1.0 |
| `Employee -[IN_DEPARTMENT]-> Department` | employees.json category | 1.0 |
| `Product -[IN_CATEGORY]-> Category` | Parsed category hierarchy | 1.0 |
| `Policy -[GOVERNS]-> Department` | LLM analysis of policy scope | 0.7–0.9 |
| `ITTicket -[RELATES_TO]-> Policy` | Issue text ↔ policy content matching | 0.5–0.8 |
| `Client -[IN_INDUSTRY]-> Industry` | clients.json industry field | 1.0 |

---

### Component 4: Conflict Detector & Resolver

Handles contradictions across data sources. The system uses a tiered resolution strategy.

**Conflict types in this dataset:**

| Conflict | Sources | Resolution |
|---|---|---|
| Employee name spelling variants | employees.json vs. email sender_name vs. conversation text | **Auto**: employees.json is canonical (HR system of record) |
| Email signature ≠ sender | emails.json signature vs sender_name | **Auto**: Ignore signature for identity; use sender_emp_id only |
| Thread date ordering | emails.json dates within threads | **Auto**: Re-order by email position in thread, not date |
| Duplicate emp_ids in resumes | resume_information.csv | **Auto**: Keep resume where name matches employees.json |
| Product rating corruption | products.json (`"\|"` value) | **Auto**: Set to null, flag for review |
| Customer "ADDED" placeholder | Multiple CRM files | **Auto**: Exclude from knowledge graph, mark as test data |
| Department names vs resume categories | employees.json vs resume_information.csv | **Auto**: employees.json category is canonical |
| Price format discrepancies | sales.json vs products.json | **Auto**: Parse both, flag if >5% difference |
| Self-conversations | conversations.json | **Auto**: Flag as anomaly, include but annotate |
| Ambiguous entity references in text | Email bodies, chat transcripts | **HITL**: Surface to human when confidence < 0.6 |
| Policy contradiction detection | Across 24 policy PDFs | **HITL**: Flag potential conflicts for human review |
| Client/Vendor with same business name | clients.json vs vendors.json | **HITL**: Surface for human to confirm if same entity |

**Resolution hierarchy (source authority):**

```
Priority 1 (Highest): employees.json    — HR system of record for people
Priority 2:           customers.json    — CRM system of record for customers  
Priority 3:           products.json     — Product catalog of record
Priority 4:           clients/vendors   — B2B relationship records
Priority 5:           emails.json       — Communication (structured metadata)
Priority 6:           conversations     — Communication (less structured)
Priority 7:           posts.json        — Social (lowest authority)
Priority 8 (Lowest):  Inferred content  — LLM-extracted facts
```

**Conflict resolution data flow:**

```
Source Records
     │
     ▼
┌─────────────┐     ┌──────────────────┐
│  Rule-based │────▶│ Auto-resolved    │──▶ Knowledge Graph
│  Detector   │     │ (high confidence)│
└──────┬──────┘     └──────────────────┘
       │
       │ ambiguous conflicts
       ▼
┌─────────────┐     ┌──────────────────┐
│  LLM Triage │────▶│ LLM-resolved     │──▶ Knowledge Graph
│  (Claude)   │     │ (medium conf.)   │    (with lower confidence score)
└──────┬──────┘     └──────────────────┘
       │
       │ genuinely ambiguous
       ▼
┌─────────────┐
│  Human-in-  │──▶ Conflict Queue (Web UI)
│  the-Loop   │    Human reviews, decides, result → Knowledge Graph
└─────────────┘
```

---

### Component 5: Knowledge Graph

The core data structure. Every entity is a node, every relationship is an edge, and every fact carries provenance metadata.

**Node schema:**

```python
@dataclass
class GraphNode:
    id: str                    # globally unique
    type: str                  # "Employee", "Customer", "Product", etc.
    attributes: dict           # type-specific fields
    provenance: list[Provenance]  # one per source that contributed
    created_at: datetime
    updated_at: datetime
    version: int               # incremented on each update
    vfs_path: str              # path in the virtual file system
```

**Edge schema:**

```python
@dataclass
class GraphEdge:
    id: str
    source_node_id: str
    target_node_id: str
    relation_type: str         # "REPORTS_TO", "PURCHASED", etc.
    attributes: dict           # edge-specific data (e.g., date, amount)
    provenance: list[Provenance]
    valid_from: datetime       # temporal validity
    valid_to: datetime | None  # None = still valid
    version: int
```

**Provenance schema (fact-level):**

```python
@dataclass
class Provenance:
    source_file: str           # "Enterprise_mail_system/emails.json"
    source_record_id: str      # "email_id:4226322d-0ea5-..."
    source_field: str          # "sender_emp_id"
    extraction_method: str     # "direct_mapping" | "llm_extraction" | "rule_based" | "human"
    extraction_model: str      # "claude-sonnet-4-6" or "rule:email_parser_v1"
    extracted_at: datetime
    confidence: FactConfidence # categorical: exact | grounded | inferred | human
    model_self_score: float | None  # LLM self-rated, audit-only; never used to filter
    raw_value: str             # the original value before normalization
```

**Provenance confidence — grounded, not fabricated.**
A confidence value is never a magic number. It is always grounded in a real
computation, deterministic rule, or human action. If we don't have an
algorithm, we use a categorical label — not a fabricated float. The
`FactConfidence` enum captures the four producers we actually have:

| Label       | Producer                                                                 |
|-------------|--------------------------------------------------------------------------|
| `exact`     | direct field mapping or deterministic rule (e.g. identity-by-email)      |
| `grounded`  | LLM extraction whose `surface_form` was found verbatim in the input span |
| `inferred`  | LLM extraction without a grounding match (free generation)               |
| `human`     | human edit / override via the Edit API                                   |

The LLM's self-rated `confidence` number is captured into
`Provenance.model_self_score` (audit-only) so a future calibration study
has the raw signal, but it is **never** used for filtering, thresholding,
or routing — uncalibrated self-scores are theatre. Aggregation policies
on the `:Entity` / edge level are caller-defined ("all `exact`", "any
`inferred`", etc.); there is no node-level `confidence` field anymore.
Retrieval relevance (cosine similarity, BM25, rerank score) is a separate
concept and lives on retrieval result objects, not on `Provenance`.

**Graph statistics (estimated for this dataset):**

| Metric | Count |
|---|---|
| Total nodes | ~19,000 |
| — Employee | 1,260 |
| — Customer | 90 |
| — Product | 1,351 |
| — Client | 400 |
| — Vendor | 400 |
| — EmailThread | 4,417 |
| — Email | 11,928 |
| — Conversation | 2,897 |
| — ITTicket | 163 |
| — Repository | 726 |
| — Policy | 24 |
| — SocialPost | 971 |
| — Department | 8 |
| — Category (product) | ~50 |
| — Skill | ~200 |
| — Topic | ~100 |
| Total edges | ~80,000+ |
| Provenance records | ~150,000+ |

---

### Component 6: Virtual File System (VFS)

The VFS is the **product surface** — the primary way both humans and AI agents interact with the company memory. It materializes the knowledge graph as a navigable directory tree.

**Directory structure:**

```
/company/
├── overview.md                          # Company summary, key metrics
├── org-chart.md                         # Full organizational hierarchy
│
├── people/
│   ├── _index.md                        # Department summary, headcount
│   ├── engineering/
│   │   ├── _index.md                    # Dept overview, team leads
│   │   ├── emp_0431-raj-patel.md        # Individual employee file
│   │   ├── emp_0106-anita-sharma.md
│   │   └── ...
│   ├── hr/
│   ├── sales/
│   ├── finance/
│   ├── it/
│   ├── business-development/
│   ├── bpo/
│   └── management/
│
├── customers/
│   ├── _index.md                        # Customer summary, top accounts
│   ├── arout-thomas-hardy/
│   │   ├── profile.md                   # Customer details
│   │   ├── purchases.md                 # Sales history
│   │   ├── support-history.md           # Support interactions
│   │   ├── reviews.md                   # Product reviews
│   │   └── documents/                   # Links to invoice/PO/SO PDFs
│   └── ...
│
├── products/
│   ├── _index.md                        # Product catalog summary
│   ├── by-category/
│   │   ├── electronics/
│   │   │   ├── wearable-technology/
│   │   │   │   └── smart-watches/
│   │   │   │       └── B0B82YGCF6.md
│   │   │   └── ...
│   │   ├── home-and-kitchen/
│   │   └── computers-and-accessories/
│   └── by-id/
│       ├── B07JW9H4J1.md               # Full product file
│       └── ...
│
├── business/
│   ├── clients/
│   │   ├── _index.md                    # Client portfolio summary
│   │   ├── rodriguez-figueroa.md        # Individual client file
│   │   └── ...
│   └── vendors/
│       ├── _index.md                    # Vendor portfolio summary
│       ├── CLNT-0001-castillo-inc.md
│       └── ...
│
├── communications/
│   ├── email-threads/
│   │   ├── _index.md                    # Thread summary, recent activity
│   │   ├── THR_20241104_d2b538.md       # Individual thread
│   │   └── ...
│   ├── conversations/
│   │   ├── _index.md
│   │   └── <conversation_id>.md
│   └── social-posts/
│       ├── _index.md
│       └── <post_title_slug>.md
│
├── it/
│   ├── tickets/
│   │   ├── _index.md                    # Open/closed summary
│   │   ├── 717.md
│   │   └── ...
│   └── repositories/
│       ├── _index.md                    # Repo summary by language/license
│       └── <repo_name_slug>.md
│
├── policies/
│   ├── _index.md                        # Policy catalog
│   ├── it-security/
│   │   ├── acceptable-use.md
│   │   ├── information-security.md
│   │   ├── password-policy.md
│   │   └── it-asset-management.md
│   ├── hr-employee/
│   │   ├── employee-handbook.md
│   │   ├── leave-policy.md
│   │   └── ...
│   ├── legal-compliance/
│   ├── data-privacy/
│   ├── environmental/
│   ├── development/
│   └── risk-safety/
│
├── processes/                           # Procedural knowledge (derived)
│   ├── onboarding.md                    # Extracted from handbook + HR data
│   ├── leave-request.md                 # Extracted from leave policy + data
│   ├── it-support-workflow.md           # Extracted from ticket patterns
│   ├── customer-support-workflow.md     # Extracted from chat patterns
│   ├── vendor-onboarding.md            # Extracted from vendor data + policies
│   └── code-review-process.md          # Extracted from GitHub data + SDLC
│
├── trajectories/                        # Time-series / progress tracking
│   ├── projects/                        # Inferred from comms + tickets
│   ├── sales-trends.md                  # Aggregated from sales data
│   ├── hiring-timeline.md              # From DOJ data
│   └── ticket-resolution-metrics.md    # From IT ticket data
│
└── _meta/
    ├── sources.md                       # List of all source files + ingestion status
    ├── conflicts.md                     # Unresolved conflicts pending review
    ├── provenance-log.md                # Recent provenance audit trail
    └── schema.md                        # VFS schema documentation
```

**File format — every VFS file follows this structure:**

```markdown
---
id: "node_emp_0431"
type: "Employee"
sources:
  - file: "Human_Resource_Management/Employees/employees.json"
    record: "emp_id:emp_0431"
    fields: ["Name", "email", "category", "Level", ...]
  - file: "Human_Resource_Management/Resume/resume_information.csv"
    record: "emp_id:emp_0431"
    fields: ["content", "skills"]
confidence: 0.95
last_updated: "2026-04-25T10:30:00Z"
version: 1
---

# Raj Patel

**Employee ID:** emp_0431  
**Email:** raj.patel@inazuma.com  
**Department:** [Engineering](/company/people/engineering/_index.md)  
**Level:** EN14 (Senior)  
**Status:** Active (since 2012-01-03)  
**Reports to:** None (top-level)  
**Direct reports:** [emp_0106](/company/people/engineering/emp_0106-anita-sharma.md), [emp_0920](/company/people/engineering/emp_0920-vikram-singh.md), ...

## Skills
Python, Machine Learning, System Design, ...

## Recent Activity
- **Email:** 47 threads (last: 2022-11-15) → [view threads](/company/communications/email-threads/?participant=emp_0431)
- **Conversations:** 12 chats → [view](/company/communications/conversations/?participant=emp_0431)
- **Repositories:** 2 repos → [view](/company/it/repositories/?owner=emp_0431)
- **IT Tickets:** 1 raised, 0 assigned → [view](/company/it/tickets/?raised_by=emp_0431)

## Performance
- **Rating:** 5/5
- **Salary:** ₹51,000

---
*Sources: employees.json (emp_0431), resume_information.csv (emp_0431)*
```

**Key VFS design decisions:**

1. **Every file has YAML frontmatter** with provenance — machine-readable metadata
2. **Cross-references are markdown links** to other VFS paths — both human-clickable and agent-parseable
3. **`_index.md` files** at every directory level — provide summaries and navigation
4. **Source attribution at the bottom** of every file — always visible
5. **Confidence scores in frontmatter** — agents can filter by reliability
6. **Version tracking** — every edit increments the version

---

### Component 7: Search Engine (Hybrid)

Three retrieval modes, composable:

**Mode 1: VFS operations (for agents)**

```
ls /company/people/engineering/        → list all engineering employees
cat /company/people/engineering/emp_0431-raj-patel.md  → read employee file
grep -r "VPN" /company/it/tickets/     → search across IT tickets
find /company/ -name "*.md" -newer 2022-01-01  → recent files
```

**Mode 2: Semantic search (for natural language queries)**

```
POST /api/search
{
  "query": "Who handles VPN issues in IT?",
  "scope": "/company/it/",      // optional: restrict to subtree
  "top_k": 5
}
```

Uses Neo4j's native vector index over `:Entity` node embeddings (`db.index.vector.queryNodes`). Returns ranked nodes with their `vfs_path` and provenance.

**Mode 3: Graph traversal (for multi-hop questions)**

```
POST /api/graph/query
{
  "start": "emp_0431",
  "traversal": "REPORTS_TO*..MANAGES*",  // Cypher-like pattern
  "depth": 3
}

POST /api/graph/path
{
  "from": "emp_0431",
  "to": "product:B07JW9H4J1",
  "max_hops": 4
}
```

---

## Data Flow

### Ingestion Pipeline (batch — runs once at startup, then incrementally)

```
Step 1: PARSE
  For each source file in dataset/:
    → Source Parser extracts raw records
    → Type coercion (strings → proper types)
    → Error-tolerant: log malformed records, don't drop
    → Output: list[SourceRecord]

Step 2: EXTRACT
  For each SourceRecord:
    → Entity Extractor identifies entities + attributes
    → Structured sources: direct field mapping
    → Unstructured (PDFs): LLM extraction with schema prompts
    → Output: list[Entity], list[Fact]

Step 3: LINK
  For all extracted entities:
    → Relation Linker resolves foreign keys (exact match)
    → Fuzzy matching for name-based links (Levenshtein + embedding similarity)
    → Entity resolution: deduplicate same-entity-different-source
    → Output: list[Relation]

Step 4: RESOLVE CONFLICTS
  For all facts about the same entity:
    → Source authority hierarchy determines winner
    → Auto-resolve high-confidence conflicts
    → LLM triage for medium-confidence
    → Queue genuinely ambiguous for human review
    → Output: resolved entities with confidence scores

Step 5: BUILD GRAPH
  Insert nodes + edges into knowledge graph
    → Attach provenance to every node and edge
    → Compute confidence scores
    → Build temporal validity windows

Step 6: ASSIGN VFS PATHS
  Walk the knowledge graph:
    → Set `GraphNode.vfs_path` (e.g. /company/people/<dept>/<emp_id>-<slug>)
    → No file writes — VFS is a logical view computed from this string + Cypher
    → `_index` summaries are derived on demand from queries

Step 7: INDEX FOR SEARCH
  For each :Entity node worth indexing:
    → Generate embedding from `attributes` + linked `source_records.raw_record`
    → Write to a `vector` property on the node
    → Neo4j's native HNSW vector index makes it queryable via
      db.index.vector.queryNodes(); no external store, no sync
```

### Incremental Update Flow (when source data changes)

```
1. Detect changed source records (file hash comparison or webhook)
2. Re-parse only changed records
3. Diff against existing graph nodes
4. Apply changes:
   - New entities → insert node + edges
   - Changed attributes → update node, increment version, add provenance entry
   - Deleted records → soft-delete (mark valid_to, preserve history)
5. Re-materialize only affected VFS files
6. Update search index for changed files only
7. If conflicts introduced → route through conflict resolution pipeline
```

---

## User Flow

### Flow 1: AI Agent Retrieves Context

```
Agent receives task: "What is emp_0431's team structure?"
  │
  ▼
Agent calls: GET /api/vfs/ls?path=/company/people/engineering/
  │
  ▼
Agent calls: GET /api/vfs/cat?path=/company/people/engineering/emp_0431-raj-patel.md
  │
  ▼
Agent reads frontmatter (confidence, sources) + content (reports, skills)
  │
  ▼
Agent follows cross-references to reportee files if needed
  │
  ▼
Agent composes answer with provenance citations
```

### Flow 2: AI Agent Answers Complex Question

```
User asks: "Which customers bought products that had support issues, 
            and who handled those support cases?"
  │
  ▼
Agent calls: POST /api/graph/query
  {
    "pattern": "(Customer)-[CONTACTED_SUPPORT]->(Product)<-[HANDLES_SUPPORT]-(Employee)"
  }
  │
  ▼
Graph returns: list of (customer, product, support_employee) triples with provenance
  │
  ▼
Agent calls: GET /api/vfs/cat for each relevant employee/customer file
  │
  ▼
Agent composes answer with fact-level citations:
  "Thomas Hardy (arout) contacted support about [Product X] — handled by emp_0726.
   Source: customer_support_chats.json, chat_id: 47"
```

### Flow 3: Human Browses Company Memory

```
User opens web UI → sees VFS tree in left panel
  │
  ▼
Clicks /company/people/ → sees department cards with headcounts
  │
  ▼
Clicks Engineering → sees employee list with key metrics
  │
  ▼
Clicks emp_0431 → sees full employee file with:
  - Profile info (with edit button)
  - Source attribution (clickable links to raw data)
  - Activity timeline (emails, conversations, posts)
  - Graph neighborhood (visual: who they work with)
  │
  ▼
Clicks "Sources" → sees exactly which fields came from which source file
```

### Flow 4: Human Resolves Conflict

```
System detects: Client "TechCorp" in clients.json may be same entity 
as Vendor "TechCorp Solutions" in vendors.json
  │
  ▼
Conflict appears in /company/_meta/conflicts.md and in Web UI queue
  │
  ▼
Human opens conflict → sees:
  - Entity A: TechCorp (client, UUID, healthcare, $2.3M revenue)
  - Entity B: TechCorp Solutions (vendor, CLNT-0042, technology, hardware supplier)
  - System recommendation: "Likely different entities (different industry, different relationship)"
  - Confidence: 0.4
  │
  ▼
Human decides: "Different entities" or "Same entity — merge"
  │
  ▼
Decision recorded with human provenance → graph updated → VFS re-materialized
```

### Flow 5: Human Edits Company Memory

```
User views /company/people/engineering/emp_0431-raj-patel.md
  │
  ▼
Notices incorrect info: skills list is missing "Kubernetes"
  │
  ▼
Clicks "Edit" → modifies the skills section
  │
  ▼
System creates a new provenance entry:
  {
    source: "human_edit",
    editor: "user@company.com",
    field: "skills",
    old_value: "Python, Machine Learning, System Design",
    new_value: "Python, Machine Learning, System Design, Kubernetes",
    timestamp: "2026-04-25T11:00:00Z"
  }
  │
  ▼
Graph node updated → VFS file re-rendered → search index updated
  │
  ▼
If source data later changes emp_0431's skills → conflict detected:
  "Human added 'Kubernetes' but source doesn't include it"
  → Human edit preserved (human edits have override authority for the edited field)
```

---

## API Design

### Graph API (structured queries) -- IMPLEMENTED

**Implementation:** `backend/api/app.py` (FastAPI + Pydantic v2 response models)
**Response models:** `backend/api/models.py`
**Run:** `uv run uvicorn backend.api.app:app --reload --port 8000`
**Docs:** http://localhost:8000/docs (Swagger UI) | http://localhost:8000/openapi.json

Every response joins Neo4j (graph + content) with SQLite (provenance + raw data).
Node and edge responses include the full provenance chain: which source file, which
field, which extraction method, what confidence, and the original raw value.

```
GET  /api/graph/node/{id}                                  # Node + attributes + provenance
GET  /api/graph/node/{id}/neighbors?relation_type=X&depth=N  # Traverse graph
GET  /api/graph/nodes?type=Person&limit=50&offset=0        # List nodes by type (paginated)
GET  /api/graph/edge/{id}                                  # Edge + provenance
GET  /api/graph/path?from={id}&to={id}&max_hops=6          # Shortest path
GET  /api/graph/stats                                      # Graph-level metrics
GET  /api/source/{source_file}/{record_id}                 # Raw source record (layer 4)
POST /api/graph/query                                      # Pattern query (typed DSL)
PUT  /api/graph/node/{id}                                  # Edit node (human-in-the-loop)
```

**Pattern query** (`POST /api/graph/query`): accepts a typed DSL pattern like
`(Person)-[SENT]->(Message)`. Node and relation types are validated against the
canonical registry. Returns paginated triples (source_node, edge, target_node)
with full provenance. Relation types are Cypher-safe (regex-validated, inlined
via `cast(LiteralString, ...)`); node types are passed as parameters.

**Edit API** (`PUT /api/graph/node/{id}`): human-in-the-loop corrections.
Updates node attributes and creates per-attribute provenance traces with
`extraction_method="human"`, `confidence=1.0`, `extraction_model="human:{editor}"`.
Satisfies the `source_records` FK constraint by inserting a synthetic source record.
Follows the same staged atomicity as `add_node`: SQLite first, Neo4j next, SQLite
commit last, Neo4j compensated on failure.

**Provenance trace flow** (how the UI maps graph data back to source files):

1. `GET /api/graph/node/person:emp_1002` returns the Person with provenance[]
2. Each provenance entry contains `source_file` and `source_record_id`
3. `GET /api/source/{source_file}/{source_record_id}` returns the original raw JSON record
4. The UI can show: fact -> extraction method -> source file -> original value

### VFS API (file-system operations for agents) -- NOT YET IMPLEMENTED

```
GET  /api/vfs/ls?path=/company/people/          # List directory
GET  /api/vfs/cat?path=/company/people/eng/...   # Read file
GET  /api/vfs/grep?pattern=VPN&path=/company/it/ # Search text
GET  /api/vfs/find?name=*.md&path=/company/      # Find files
GET  /api/vfs/stat?path=/company/people/eng/...   # File metadata (provenance, version)
GET  /api/vfs/tree?path=/company/&depth=2         # Directory tree
```

### Search API (hybrid retrieval) -- PARTIAL (R0 cascade spine landed, tiers pending)

The retrieval surface is a **single endpoint** backed by a cascade
orchestrator. Callers POST a query, the orchestrator walks the
registered tiers in order (fast → slow), and returns the result of the
first tier whose `relevance` clears its configured `escalate_below`
threshold. If every tier escalates past, the orchestrator returns the
last tier's result (best-effort, never an exception).

```
POST /api/query
  { "query": "...", "context": { "prefer_tier": "exact", "max_latency_ms": 500 } }

→ {
    "answer": "...",                # filled by LLM tiers (R3+); null otherwise
    "items":  [ { "kind": "node", "id": "...", "score": 0.91, "preview": "..." } ],
    "citations": [ { "source_file": "...", "source_record_id": "...", ... } ],
    "tier_used": "hybrid",
    "relevance": 0.91,              # algorithmic (cosine / BM25 / rerank), per-tier doc'd
    "latency_ms": 87
  }
```

`relevance` and `Hit.score` are algorithmic (cosine sim / BM25 /
cross-encoder rerank / exact-match indicator) — never magic numbers.
Each tier documents which algorithm it uses on its `Hit.score` and
`QueryResult.relevance`.

**Cascade composition** (`backend/retrieval/`):

| Tier | Status | Algorithm | Issue |
|---|---|---|---|
| `stub` | LANDED (R0) | always returns 0 hits, relevance 0.0 | #2 |
| `exact` | pending (R1) | Cypher pattern + fulltext index | #3 |
| `hybrid` | pending (R2) | vector + fulltext + cross-encoder rerank | #4 |
| `agentic` | pending (R3) | Gemini function-calling over store ops | #5 |

**Pre-routing** (R4, issue #6 — Pioneer.ai GLiNER2): a router will
classify the incoming query and pre-populate `QueryContext.prefer_tier`
so the orchestrator jumps to the right tier on the first try. The hook
already exists in R0 — `prefer_tier` is honored today.

**Eval harness** (`backend/eval/`):

* `golden.load_golden_set()` extracts `(query, expected_node_ids)`
  pairs from `dataset/EnterpriseBench/tasks.jsonl`. The first user
  message is the query; entity ids harvested from subsequent
  assistant tool-call arguments (`emp_id`, `product_id`, ...) are
  the expected node ids. Tasks with no extractable id are skipped.
* `harness.run_eval()` reports recall@5, recall@10, latency p50/p95,
  per-tier termination counts, and escalation rate. Output is a
  Markdown table written to `backend/eval/reports/<UTC-timestamp>.md`
  so successive runs can be diffed.

Run the harness end-to-end:

```
uv run python -m backend.eval.harness --limit 50
```

### Edit API (human-in-the-loop) -- IMPLEMENTED

**Implementation:** `PUT /api/graph/node/{id}` in `backend/api/app.py`,
backed by `GraphStore.edit_node()` in `backend/graph/store.py`.

```
PUT  /api/graph/node/{id}                                  # Edit node attributes
  { "attributes": {"skills": "Python, ML, Kubernetes"}, "editor": "user@company.com" }
```

The edit flow matches the design in Flow 5:
1. User submits changed attributes + editor identity
2. System creates a synthetic `source_record` (satisfies FK constraint)
3. One `Provenance` row per changed attribute: `extraction_method="human"`,
   `confidence=1.0`, `extraction_model="human:{editor}"`, `spec_version=None`
4. Node attributes updated in Neo4j, version bumped
5. Atomic: SQLite staged first, Neo4j next, SQLite committed last

**Not yet implemented from the original spec:**
- `PUT /api/vfs/edit` (VFS-path-based edit) -- requires VFS API
- `GET /api/conflicts` + `POST /api/conflicts/:id/resolve` -- requires conflict detection engine

### MCP Server (for Claude / AI agents) -- NOT YET IMPLEMENTED

```python
# Model Context Protocol server exposing VFS as tools
@mcp_tool("vfs_ls")
def ls(path: str) -> list[str]: ...

@mcp_tool("vfs_cat") 
def cat(path: str) -> str: ...

@mcp_tool("vfs_grep")
def grep(pattern: str, path: str) -> list[dict]: ...

@mcp_tool("graph_query")
def query(start: str, relation: str, depth: int) -> list[dict]: ...

@mcp_tool("search")
def search(query: str, scope: str, top_k: int) -> list[dict]: ...
```

---

## Web UI Design

### Layout

```
┌──────────────────────────────────────────────────────────────────┐
│  Better Context              🔍 Search...         [Conflicts: 3] │
├──────────────┬───────────────────────────────────┬───────────────┤
│              │                                   │               │
│  VFS Tree    │        Main Content               │  Graph View   │
│              │                                   │               │
│  ▼ company   │  # Raj Patel                      │   ┌───┐       │
│    ▼ people  │                                   │   │Raj├──┐    │
│      ▶ eng   │  **Department:** Engineering      │   └───┘  │    │
│      ▶ hr    │  **Level:** EN14 (Senior)         │      ┌───▼┐   │
│      ▶ sales │  **Email:** raj.patel@inazuma.com │      │Eng │   │
│    ▶ custom. │                                   │      └────┘   │
│    ▶ product │  ## Skills                        │               │
│    ▶ business│  Python, ML, System Design        │  ┌────┐       │
│    ▶ comms   │                                   │  │emp1├──┐    │
│    ▶ it      │  ## Recent Activity               │  └────┘  │    │
│    ▶ policies│  - 47 email threads               │      ┌───▼┐   │
│    ▶ process │  - 12 conversations               │      │Raj │   │
│    ▶ traject │  - 2 repositories                 │      └────┘   │
│    ▶ _meta   │                                   │               │
│              │  ## Sources                        │               │
│              │  📄 employees.json (emp_0431)      │               │
│              │  📄 resume_info.csv (emp_0431)     │               │
│              │                                   │               │
│              │  [Edit] [History] [Raw JSON]       │               │
│              │                                   │               │
├──────────────┴───────────────────────────────────┴───────────────┤
│  Provenance trail: employees.json:emp_0431 → extracted 2026-... │
└──────────────────────────────────────────────────────────────────┘
```

### Key UI Features

1. **Left panel: VFS tree navigator** — collapsible folder tree, file icons by type, badge counts
2. **Center panel: Content viewer/editor** — renders markdown with frontmatter, inline edit mode, diff view for version history
3. **Right panel: Graph neighborhood** — interactive force-directed graph showing the current entity's relationships (clickable nodes navigate the VFS)
4. **Top bar: Global search** — hybrid search across all VFS files, graph entities, and raw sources
5. **Conflict queue** — badge shows pending conflicts, click to open resolution UI (side-by-side comparison with "Accept A / Accept B / Merge" buttons)
6. **Provenance footer** — every page shows the source chain: which files, which fields, which extraction method, when
7. **History view** — version timeline for any file, showing what changed and why (human edit vs. source update)

---

## Detailed Ingestion Strategy Per Source

| Source | Parse Method | Entities Extracted | Estimated Time |
|---|---|---|---|
| employees.json (1,260) | Direct mapping + type coercion | Employee, Department, OrgUnit | <1s |
| resume_information.csv (1,013) | pandas + content parsing | Resume, additional Skills | ~2s |
| emails.json (11,928) | Direct mapping + error-tolerant JSON | Email, EmailThread | ~3s |
| conversations.json (2,897) | Direct mapping + speaker extraction | Conversation | ~1s |
| posts.json (971) | Direct mapping | SocialPost | <1s |
| customers.json (90) | Direct mapping | Customer | <1s |
| products.json (1,351) | Direct mapping + category parsing | Product, Category | <1s |
| sales.json (13,510) | Direct mapping + price parsing | Sale | ~2s |
| product_sentiment.json (13,510) | Direct mapping + dedup review text | Review | ~2s |
| customer_support_chats.json (1,000) | Direct mapping + transcript parsing | SupportChat | ~1s |
| clients.json (400) | Direct mapping + revenue parsing | Client | <1s |
| vendors.json (400) | Direct mapping + ID normalization | Vendor | <1s |
| it_tickets.json (163) | Direct mapping | ITTicket | <1s |
| GitHub.json (750) | Direct mapping + issue extraction | Repository, GitIssue | ~1s |
| Policy_Documents/ (24 PDFs) | PyMuPDF + LLM section extraction | Policy, PolicySection, Rule | ~2-5 min (LLM) |
| Customer_orders/ (270 PDFs) | PyMuPDF + regex extraction | Invoice, PO, ShippingOrder | ~30s |
| **Total** | | | **~5-8 min** |

---

## How This Wins the Hackathon

### Criteria alignment:

| Criterion | How We Address It |
|---|---|
| **Virtual file system** | Full Unix-style VFS with ls/cat/grep/find — navigable by both agents and humans |
| **Knowledge graph** | NetworkX graph with typed nodes, edges, confidence scores, temporal validity |
| **Static data** (employees, customers, products) | Directly modeled as graph nodes, materialized as VFS files |
| **Procedural knowledge** (processes, SOPs, rules) | Extracted from policies + inferred from data patterns → `/processes/` directory |
| **Trajectory information** (tasks, projects, progress) | Time-series analysis of sales, tickets, hiring → `/trajectories/` directory |
| **Explicit references inside graph** | Every VFS file has markdown cross-links to related entities |
| **References to source records** | YAML frontmatter + footer on every file traces back to exact source record |
| **AI retrieval interface** | MCP server + REST API with VFS ops, graph queries, and hybrid search |
| **Human inspect/validate/edit** | Web UI with tree nav, content viewer, graph viz, inline editing |
| **Generalize beyond dataset** | Parser registry pattern — add new sources by implementing a parser interface |
| **Resolve conflicts automatically** | Tiered: rule-based → LLM triage → human queue |
| **Human-in-the-loop where ambiguity matters** | Conflict queue in web UI with side-by-side comparison |
| **Fact-level provenance** | Every node/edge/fact carries `Provenance` objects back to source file + field + record |
| **Update when source facts change** | Incremental update pipeline: detect diff → re-parse → re-resolve → re-materialize |
| **Not markdown dumping** | Structured graph is the source of truth; VFS is a materialized view |
| **Not a chatbot** | System is the context base itself — a chatbot could be built on top, but isn't the product |
| **Explainable, editable, robust** | Confidence scores, version history, edit audit trail, conflict resolution |

### What makes this stand out:

1. **VFS as a compiled artifact, not storage** — the graph is the truth, the filesystem is a view. This means edits, updates, and conflict resolution happen at the graph level, and the VFS is always a consistent materialization.

2. **Provenance is not an afterthought** — it's baked into every data structure from SourceRecord through GraphNode to VFS frontmatter. You can click any fact and trace it back to the exact JSON field in the exact source file.

3. **Conflict resolution is a product feature, not a bug** — we surface conflicts explicitly, auto-resolve what we can, and give humans a proper UI for the rest. This is exactly what Better Context asked for: "involve humans where ambiguity actually matters."

4. **MCP server** — the AI retrieval interface isn't just a REST API; it's a Model Context Protocol server that any Claude-based agent can use natively with tool calling. This is the most natural way for AI to "operate on" the context base.

5. **Incremental updates** — we don't rebuild from scratch when data changes. The diff-based pipeline means the system stays current without the cost of full re-ingestion.

---

## 24-Hour Implementation Timeline

### Phase 1: Foundation (Hours 0–4)

- [ ] Project scaffolding: FastAPI backend, Next.js frontend, directory structure
- [ ] Source parsers for all 13 JSON/CSV sources (direct mapping, no LLM needed)
- [ ] Entity and Relation data models (Python dataclasses)
- [ ] NetworkX graph construction from parsed entities
- [ ] SQLite persistence layer for graph serialization

### Phase 2: Core (Hours 4–10)

- [ ] VFS path assignment pass (set `GraphNode.vfs_path` per type) — no disk writes
- [ ] VFS API endpoints (ls, cat, grep, find, stat, tree) — Cypher-backed
- [ ] Provenance tracking through the full pipeline
- [ ] Conflict detection engine (rule-based)
- [ ] Auto-resolution for known conflict types (signature mismatch, date ordering, etc.)

### Phase 3: Intelligence (Hours 10–16)

- [ ] PDF parsing for policy documents (PyMuPDF + LLM extraction)
- [ ] Embed `:Entity` nodes + create Neo4j native vector index
- [ ] Hybrid search API (semantic + keyword + graph)
- [ ] Graph query API (node lookup, traversal, path finding)
- [ ] LLM-based conflict triage for medium-confidence conflicts
- [ ] Process/trajectory extraction from data patterns

### Phase 4: UI (Hours 16–21)

- [ ] Web UI: VFS tree navigator (left panel)
- [ ] Web UI: Content viewer with frontmatter rendering (center)
- [ ] Web UI: Graph neighborhood visualization (right panel, using D3 or react-force-graph)
- [ ] Web UI: Search bar with results
- [ ] Web UI: Conflict resolution queue
- [ ] Web UI: Edit mode with provenance recording

### Phase 5: Polish & Demo (Hours 21–24)

- [ ] MCP server implementation (wrap VFS + graph APIs as MCP tools)
- [ ] Demo script: walk through all user flows
- [ ] Incremental update demonstration (change a source record → watch VFS update)
- [ ] Edge case handling, error states, loading states
- [ ] README and deployment instructions

---

## Repository Structure

```
better-context/
├── backend/
│   ├── main.py                    # FastAPI app entry point
│   ├── config.py                  # Settings, paths, model config
│   ├── parsers/
│   │   ├── base.py                # BaseParser interface
│   │   ├── employee_parser.py
│   │   ├── email_parser.py
│   │   ├── crm_parser.py
│   │   ├── business_parser.py
│   │   ├── it_parser.py
│   │   ├── github_parser.py
│   │   ├── conversation_parser.py
│   │   ├── post_parser.py
│   │   ├── policy_parser.py
│   │   └── order_parser.py
│   ├── models/
│   │   ├── entities.py            # Entity dataclasses
│   │   ├── relations.py           # Relation dataclasses
│   │   ├── provenance.py          # Provenance dataclass
│   │   └── graph.py               # GraphNode, GraphEdge
│   ├── graph/
│   │   ├── builder.py             # Graph construction from entities
│   │   ├── store.py               # NetworkX + SQLite persistence
│   │   └── query.py               # Graph query engine
│   ├── vfs/
│   │   ├── paths.py               # Assign GraphNode.vfs_path per type (no disk)
│   │   └── operations.py          # ls, cat, grep, find, stat — Cypher-backed
│   ├── search/
│   │   ├── embed.py               # Embed nodes, write to :Entity.vector
│   │   └── hybrid.py              # Neo4j native vector index + keyword
│   ├── conflicts/
│   │   ├── detector.py            # Rule-based conflict detection
│   │   ├── resolver.py            # Auto + LLM resolution
│   │   └── queue.py               # Human review queue
│   ├── api/
│   │   ├── vfs_routes.py          # VFS endpoints
│   │   ├── graph_routes.py        # Graph endpoints
│   │   ├── search_routes.py       # Search endpoints
│   │   └── edit_routes.py         # Edit + conflict endpoints
│   ├── mcp/
│   │   └── server.py              # MCP tool server
│   └── ingestion/
│       ├── pipeline.py            # Orchestrates parse→extract→link→resolve→build→materialize
│       └── incremental.py         # Diff-based incremental updates
├── frontend/
│   ├── app/
│   │   ├── page.tsx               # Main layout
│   │   ├── components/
│   │   │   ├── VFSTree.tsx        # File tree navigator
│   │   │   ├── ContentViewer.tsx  # Markdown renderer with frontmatter
│   │   │   ├── GraphView.tsx      # Force-directed graph viz
│   │   │   ├── SearchBar.tsx      # Global search
│   │   │   ├── ConflictQueue.tsx  # Conflict resolution UI
│   │   │   └── EditMode.tsx       # Inline editor
│   │   └── api/                   # API client hooks
│   └── package.json
├── vfs_output/                    # Materialized VFS files (generated)
├── data/                          # Symlink to dataset/EnterpriseBench
├── requirements.txt
├── Dockerfile
└── README.md
```

---

## Key Design Principles

1. **Graph is truth, VFS is view** — never edit the VFS directly; always go through the graph layer
2. **Provenance is mandatory** — no fact enters the graph without a source attribution
3. **Confidence is explicit** — every node, edge, and fact has a confidence score (0.0–1.0)
4. **Humans override machines** — human edits create high-authority provenance records that survive source re-ingestion
5. **Conflicts are features** — surfacing contradictions is more valuable than hiding them
6. **Incremental by default** — the system should handle source changes without full rebuild
7. **Agent-native** — the VFS and API are designed for LLM tool-calling patterns, not just human browsing
8. **Generalize through interfaces** — new data sources plug in via the BaseParser interface; the rest of the pipeline is source-agnostic
