# Better Context (workspace: `not_yet_decided`)

Turns fragmented enterprise data (email, CRM, HR, IT tickets, chat, code,
policies) into a structured, inspectable, editable company memory backed by
a knowledge graph with **fact-level provenance**. See
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full design.

## Layout

```
backend/        Python: graph store + adaptive ingestion + REST API + retrieval cascade + workflows + eval
frontend/       Next.js + React UI (not yet scaffolded)
dataset/        EnterpriseBench source data (sample tenant)
ingest_specs/   per-tenant per-source MappingSpec YAMLs
docs/           ARCHITECTURE.md + DATASET.md
data/           runtime SQLite db (gitignored)
```

## What's built

- **Knowledge graph store** -- Neo4j (graph + content) + SQLite (traces + raw
  records). `MERGE`-on-id dedup, deterministic edge ids, atomic-tx pattern.
- **Adaptive ingestion** -- `MappingSpec` per (tenant, source-file) drives a
  deterministic ingester. LLM (Gemini Flash 2.5) only at onboarding +
  opt-in unstructured extraction. Drift detection aborts on schema change.
- **REST API** -- FastAPI serving graph data with full provenance. Joins
  Neo4j (graph + content) with SQLite (traces + raw records) into unified
  JSON responses. Pydantic v2 models generate the OpenAPI spec.
  - **Graph read endpoints** (7 GET) -- node, edge, neighbors, path, stats, nodes-by-type, source record
  - **Pattern query** (`POST /api/graph/query`) -- typed DSL `(Person)-[SENT]->(Message)` returns paginated triples with provenance
  - **Edit API** (`PUT /api/graph/node/{id}`) -- human-in-the-loop corrections with per-attribute provenance tracking
- **Retrieval cascade** -- `POST /api/query` runs a 4-tier cascade
  (`exact` -> `router` -> `hybrid` -> `agentic`) with `stub` as
  terminal fallback. Tiers escalate on algorithmic relevance only
  (cosine / BM25 / RRF / exact-match indicator). Router pre-routes via
  a **fine-tuned 205M GLiNER2 SLM** (Pioneer.ai, multi-task: intent
  classification + NER in one forward pass); agentic uses bounded
  Gemini function-calling over a 6-tool surface. Each tier is hidden
  behind a `Tier` ABC; LLM / embedder / router each have a stub default
  so CI runs without weights or API keys.
- **Fine-tuned router (Pioneer SLM)** -- 4-way intent classification +
  6-type NER in 467 ms p95 on CPU. Round 1 numbers vs frontier:
  91.1 % intent acc / 0.394 macro NER F1 (base GLiNER2: 53.3 / 0.300;
  GPT-4o: 86.7 / 0.337). Beats GPT-4o on quality AND latency
  (3.6× faster) AND cost ($0/query local vs ~$5/1k API). See
  `pioneer/bench/results/comparison.md` for the full 3-column table
  + screenshots; Round 2 in progress targets the remaining gaps
  (`ambiguous` intent, `ticket_id`/`date` NER coverage).
- **Workflow framework** -- `POST /api/workflow/{name}` invokes a
  frozen-policy recipe over a locked tier subset. Two ship: 
  `answer-customer-email` (deterministic: T1 sender + neighbors -> T3
  product search -> single-shot LLM compose) and `thread-summary`
  (less-deterministic: T3 cluster recall -> bounded 3-tool agent loop
  -> structured markdown). Built-ins registered explicitly at
  FastAPI startup via `register_builtin_workflows()` -- no
  import-side-effect.
- **Identity resolution (light)** -- deterministic email-match -> `SAME_AS`
  edges. Fuzzy + LLM triage stubbed for later.
- **Eval harness** -- `backend/eval/` extracts `(query, expected_node_ids)`
  from `tasks.jsonl`, runs the cascade, reports recall@5/@10, p50/p95
  latency, per-tier termination, escalation rate. Output Markdown to
  `backend/eval/reports/<UTC-timestamp>.md`.
- **299 passing tests** -- ingest agnosticism, pattern-query/edit,
  per-tier retrieval (exact/hybrid/router/agentic/orchestrator), tools,
  workflow framework + 2 concrete workflows, API integration.

## What's not built yet

| Feature | Why it matters | Effort estimate |
|---------|---------------|-----------------|
| VFS API (`ls`, `cat`, `grep`, `find`, `stat`, `tree`) | Lets AI agents browse the knowledge graph as a file system | Medium -- Cypher queries over `vfs_path`, no disk writes |
| Cross-encoder rerank on retrieval cascade | Sharpens top-k ordering after RRF fusion | Small -- one model call after HybridTier emits, before relevance scoring |
| Conflict resolution engine + UI | Auto-resolve known conflict types, queue ambiguous ones for humans | Large -- rule engine + LLM triage + resolution API + UI |
| MCP server | Claude-native tool interface over the API | Small -- thin wrappers around existing endpoints |
| Web UI (React + Next.js) | Human browse, search, edit, resolve conflicts | Large -- VFS tree, content viewer, graph viz, conflict queue |

See the full flow-by-flow status in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Knowledge graph

The graph is the **source of truth**. Every entity is a node, every
relationship is an edge, and every fact carries provenance metadata linking
it back to the exact original record it came from.

Four layers, kept distinct on disk:

| Layer | Where it lives | What it holds |
|---|---|---|
| **1. Graph** | Neo4j `:Entity` nodes + typed relationships | Entities and relationships (the structure) |
| **2. Content** | Neo4j `attributes_json` per node and relationship | Typed, normalized fields |
| **3. Traces** | SQLite `provenance` | Fact-level extraction history (which source field, which extractor, which model, confidence, **spec_version**) |
| **4. Raw data** | SQLite `source_records` | Original ingested records, verbatim, with content hash |

Provenance has a foreign key to `source_records` — a trace cannot exist
without its raw record. Provenance refers to graph elements by `node_id` /
`edge_id`; the graph lives in Neo4j, so the store cascades those deletes
manually when a node or edge is removed.

### Node / Edge / Provenance shapes

See `backend/models/graph.py` (`GraphNode`, `GraphEdge`, `Provenance`,
`SourceRecord`). Highlights:

- `relation_type` is a free string but must match `[A-Za-z_][A-Za-z0-9_]*`
  (used directly as the Cypher relationship type).
- `attributes` are open dicts on both nodes and edges.
- `Provenance.extraction_method` ∈ {`direct_mapping`, `llm_extraction`,
  `rule_based`, `human`}.
- `Provenance.spec_version` links each fact back to the `MappingSpec`
  version that produced it.

## Backend setup

Dependencies are managed with [uv](https://docs.astral.sh/uv/). From the repo root:

```bash
uv sync                # create venv, install runtime + dev deps, editable-install `backend`
uv run pytest          # tests
uv run ruff check .    # lint
uv run pyright         # types
```

Always go through `uv run` — never invoke `python` / `pip` / `pytest` directly.

Add `GEMINI_API_KEY=...` to a `.env` at the repo root. It powers the
LLM-driven onboarding (M2), the AgenticTier (set `QONTEXT_AGENTIC=gemini`
to enable), and any workflow that needs an LLM (`thread-summary`,
`answer-customer-email`). Neo4j connection comes from env vars (defaults
shown):

```bash
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=neo4j
NEO4J_DATABASE=neo4j
```

Start Neo4j:

```bash
docker run -d -p 7687:7687 -p 7474:7474 \
  -e NEO4J_AUTH=neo4j/better_context neo4j:5
```

## REST API

Start the API server:

```bash
uv run uvicorn backend.api.app:app --reload --port 8000
```

Interactive docs at http://localhost:8000/docs (Swagger UI).

### Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /api/graph/stats` | Node/edge counts, types, provenance/raw counts |
| `GET /api/graph/node/{id}` | Node + attributes + full provenance chain |
| `GET /api/graph/node/{id}/neighbors?relation_type=SENT&depth=1` | Traverse the graph |
| `GET /api/graph/nodes?type=Person&limit=50&offset=0` | List nodes by type (paginated) |
| `GET /api/graph/edge/{id}` | Edge + provenance |
| `GET /api/graph/path?from={id}&to={id}&max_hops=6` | Shortest path between nodes |
| `POST /api/graph/query` | Pattern query: `{"pattern": "(Person)-[SENT]->(Message)"}` |
| `PUT /api/graph/node/{id}` | Edit node: `{"attributes": {...}, "editor": "name"}` |
| `GET /api/source/{source_file}/{record_id}` | Original raw JSON record (layer 4) |
| `POST /api/query` | Retrieval cascade: `{"query": "...", "context": {...}}` -> `QueryResult` (items + citations + tier_used + relevance) |
| `GET /api/workflow` | List registered workflow names |
| `POST /api/workflow/{name}` | Invoke a workflow: `{"payload": {...}, "ctx": {...}}` -> `WorkflowResult` |

### Provenance trace (how to map graph data back to source files)

Every node/edge response includes a `provenance[]` array. Each entry contains:

- `source_file` -- which file the fact was ingested from (e.g. `Enterprise_mail_system/emails.json`)
- `source_record_id` -- which record within that file
- `source_field` -- which JSONPath field (e.g. `$.sender_email`)
- `extraction_method` -- `direct_mapping` | `llm_extraction` | `rule_based` | `human`
- `confidence` -- 0.0 to 1.0
- `raw_value` -- the original value before transformation

To get the full original record, call `GET /api/source/{source_file}/{source_record_id}`.

### Example: trace a person back to source

```bash
# 1. Get person node (from Neo4j + SQLite provenance)
curl http://localhost:8000/api/graph/node/person:emp_1002

# 2. Get emails this person sent (from Neo4j)
curl "http://localhost:8000/api/graph/node/person:emp_1002/neighbors?relation_type=SENT&depth=1"

# 3. Get the raw source record (from SQLite layer 4)
curl "http://localhost:8000/api/source/Enterprise_mail_system/emails.json/email:095a317c-8bd5-43d8-8796-490882a0f1bf"
```

### Example: pattern query (Flow 2)

```bash
# Find all Person->SENT->Message triples (paginated)
curl -X POST http://localhost:8000/api/graph/query \
  -H 'Content-Type: application/json' \
  -d '{"pattern": "(Person)-[SENT]->(Message)", "limit": 5}'

# Response: { "pattern": "...", "matches": [{source, edge, target}, ...], "total": 11928 }

# Other patterns:
# (Person)-[REPORTS_TO]->(Person)     -- reporting chains
# (Organization)-[OWNS]->(Asset)     -- org assets
# (Person)-[AUTHORED]->(Document)    -- authorship
```

Supported node types: Person, Organization, Document, Message, Event, Asset, Topic.
Supported relation types: MEMBER_OF, REPORTS_TO, WORKS_ON, OWNS, AUTHORED, SENT,
RECEIVED, MENTIONS, PART_OF, PURCHASED, ASSIGNED_TO, TAGGED, RELATED_TO, SAME_AS.

### Example: edit a node (Flow 5)

```bash
# Add a skill to a person (with human provenance tracking)
curl -X PUT http://localhost:8000/api/graph/node/person:emp_1002 \
  -H 'Content-Type: application/json' \
  -d '{"attributes": {"skills": "Python, ML, Kubernetes"}, "editor": "florian@company.com"}'

# Response includes the updated node with new human provenance:
# provenance[]: { extraction_method: "human", extraction_model: "human:florian@company.com",
#                 confidence: 1.0, source_file: "human_edits", ... }
```

## Retrieval pipeline

Two surfaces over the same tier set:

- **`POST /api/query`** -- ad-hoc cascade. Walks tiers fast -> slow,
  returns the first whose `relevance` clears its `escalate_below`
  threshold. Order: `[exact, router, hybrid, agentic, stub]`. Router
  may emit `route_to=<tier>` to skip ahead (honored once per query).
- **`POST /api/workflow/{name}`** -- frozen-policy recipe. Workflow
  declares `allowed_tiers: frozenset[str]` at class level; framework
  wraps the live tier set in a `TierRegistry` locked to that subset.
  Cuts latency / cost where the shape is known.

Tier algorithms (each documents its own scoring):

| Tier | Algorithm |
|---|---|
| `exact` | Cypher id lookup + Neo4j fulltext (BM25-similar, normalized) |
| `router` | **Fine-tuned 205M GLiNER2 SLM** (Pioneer.ai, multi-task: intent + NER, single forward pass). Round 1 numbers: 91.1 % intent acc, 0.394 macro NER F1, 467 ms p95 — beats GPT-4o (86.7 / 0.337 / 1699 ms). On `lookup` inline-delegates to ExactTier; emits `route_to` for `search` / `analytical` |
| `hybrid` | Neo4j HNSW vector + fulltext fused by Reciprocal Rank Fusion (k=60) |
| `agentic` | Bounded Gemini function-calling loop (max 6 calls, 10s wall-clock); 6-tool surface; relevance ∈ {0.7 grounded, 0.3 ungrounded, 0.0 failed} |
| `stub` | terminal fallback, always 0 hits |

Each backend is hidden behind a `Protocol` (`Embedder`, `EntityRouter`,
`LLMClient`) with a stub default so CI runs without weights or API
keys. Production wiring selected via env:

- `QONTEXT_EMBEDDER=bge` -- uses `BAAI/bge-small-en-v1.5`
  (`sentence-transformers` ships with the project). Without this the
  vector arm returns nothing -- run the embedding pass first:
  `uv run python -m backend.retrieval.embed [--limit N]` writes
  `:Entity.vector` for matched nodes (idempotent, skips
  already-embedded).
- `QONTEXT_ROUTER=gliner2` -- uses our fine-tuned 205M GLiNER2 SLM
  (Round 1 trained on Pioneer; ships local once weights are downloaded
  to `pioneer/weights/<model-name>/`). Needs `uv add gliner` and
  `GLINER2_MODEL_PATH` set. See `pioneer/README.md` for the Pioneer.ai
  workflow and `pioneer/bench/results/comparison.md` for the
  3-column eval (vs base GLiNER2 vs GPT-4o).
- `QONTEXT_AGENTIC=gemini` -- uses `gemini-2.5-flash`; needs
  `GEMINI_API_KEY`. Same setting also routes any workflow that needs
  an LLM (`thread-summary`, `answer-customer-email`).

Built-in workflows:

| Name | Tiers | Pipeline |
|---|---|---|
| `answer-customer-email` | `{exact, hybrid}` | T1 sender lookup -> T1 neighbors (cap 25) -> T3 product search top-5 -> single-shot LLM compose with `tools=[]` |
| `thread-summary` | `{hybrid}` | T3 cluster recall (per-participant + regex-NER id tokens) -> bounded 3-tool agent loop (`get_node` / `get_neighbors` / `get_source_record`, ≤6 calls) -> structured markdown |

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) ("Search API" +
"Workflow API") for the per-tier deep dive.

### Example: cascade query

```bash
curl -X POST http://localhost:8000/api/query \
  -H 'Content-Type: application/json' \
  -d '{"query": "emp_1002", "context": {"max_latency_ms": 500}}'

# Response:
# { "answer": null,
#   "items": [{"kind": "node", "id": "person:emp_1002", "score": 1.0, "preview": "..."}],
#   "citations": [{"source_file": "...", "source_record_id": "...", ...}],
#   "tier_used": "exact",
#   "relevance": 1.0,
#   "latency_ms": 12 }
```

### Example: invoke a workflow

```bash
# Discovery
curl http://localhost:8000/api/workflow
# { "workflows": ["answer-customer-email", "thread-summary"] }

# Draft a customer-email reply
curl -X POST http://localhost:8000/api/workflow/answer-customer-email \
  -H 'Content-Type: application/json' \
  -d '{"payload": {"from_address": "alice@acme.com",
                    "subject": "Order #1234 status",
                    "body": "Hi, where is my order?"}}'

# Response: WorkflowResult (QueryResult shape +
#   workflow="answer-customer-email" + extras={from_address,
#   sender_node_id, related_count, product_candidate_count})
```

### Eval harness

```bash
uv run python -m backend.eval.harness --limit 50
# Writes backend/eval/reports/<UTC-timestamp>.md with recall@5/@10,
# p50/p95 latency, per-tier termination, escalation rate.
```

## Adaptive ingestion — company-data agnostic

> **Yes, this is the central claim of the project.** The graph-building
> logic does not know or care what vendor a record came from. Drop in any
> CRM/HR/ITSM/comms source you can read as JSON / JSONL / NDJSON / CSV;
> the LLM drafts a `MappingSpec` once at onboarding; a deterministic
> ingester runs it forever after. Same code, same canonical graph types
> out, regardless of the input schema.

### Proof: four real-world CRM shapes through one Ingestor

`backend/test_ingest_agnostic.py` runs four deliberately-different vendor
payload shapes through the **same** `Ingestor` instance and asserts they
collapse to identical canonical `Person` nodes:

| Vendor flavor | Email lives at | Sample payload quirk |
|---|---|---|
| HubSpot-like | `$.properties.email` | nested `properties` envelope, `associations` arrays, ISO `Z` dates |
| Salesforce-like | `$.Email` | per-record `attributes` envelope, `IsDeleted` soft-delete flag |
| Dynamics 365 OData-like | `$.emailaddress1` | `@odata.etag` keys, `_lookup_field_value` references, `statecode` filter |
| Pipedrive-like | `$.primary_email[*].value` | array of email objects, `org_id.name` nested |

```bash
$ uv run pytest backend/test_ingest_agnostic.py -v
test_each_vendor_produces_canonical_person_nodes[hubspot]    PASSED
test_each_vendor_produces_canonical_person_nodes[salesforce] PASSED
test_each_vendor_produces_canonical_person_nodes[dynamics]   PASSED
test_each_vendor_produces_canonical_person_nodes[pipedrive]  PASSED
test_one_ingestor_handles_all_four_vendors                   PASSED
test_filter_predicate_excludes_deleted_records               PASSED
6 passed
```

After ingest, every node carries a `Provenance` row pointing back to the
**original vendor-specific** field path (`$.properties.email`, `$.Email`,
`$.emailaddress1`, `$.primary_email[*].value`), so downstream queries can
answer "where did this fact come from?" without knowing which CRM was
involved.

### Where vendor heterogeneity is absorbed

All in the spec — **never in code**:

| Vendor difference | Where it's absorbed |
|---|---|
| Different field names (`sender_emp_id` vs `from_id` vs `from.user.id`) | `FieldMap.source` JSONPath |
| Same field, different format (ISO date vs `15/01/2025` vs epoch int) | `FieldMap.transform` chain (`parse_iso_datetime`, `normalize_email`, …) |
| Field optional in some sources | coalesce list: `source: [$.dob, $.date_of_birth, $.birthDate]` |
| "Staff" / "Employee" / "TeamMember" all = same concept | `canonical_aliases: { Staff: Person, Employee: Person }` |
| Soft-deleted / archived records | `when: { equals: ['$.IsDeleted', false] }` |
| Free-text fields needing LLM extraction (email body → mentions) | `llm_blocks` (opt-in, cached, grounded) |
| Vendor changes export format | `required_paths_hash` + `type_fingerprint` → hard abort, no silent re-inference |
| Same person across sources | `IdentityResolver` post-pass → `SAME_AS` edges, provenance preserved |

### Honest scope: what works and what would need a thin shim

**Works out of the box** (`Ingestor` reads natively):
- JSON arrays, JSONL, NDJSON, CSV (covers ~all CRM exports and ~all REST
  APIs once you save the response to disk).
- Arbitrarily nested objects via JSONPath.
- Array-of-objects fields via `[*]` wildcards.

**Needs a small shim** (~10 lines each):
- Live API ingestion (Salesforce REST, HubSpot API, Pipedrive API, etc.)
  — call the API, dump the response to JSON, run `Ingestor`. The spec
  doesn't care whether records came from a file or an HTTP body.
- Excel `.xlsx` — convert to CSV with `pandas.read_excel(...).to_csv(...)`,
  or extend `_iter_records` in `ingestor.py` (~5 lines).
- XML / SOAP — convert to JSON with `xmltodict`, then ingest as JSON.
- SQL dumps — export to CSV per table.

**Out of scope today**: live streaming, binary attachments (PDFs, images),
schema discovery from a database catalog. Adding them is a localized
change to `_iter_records` — the rest of the pipeline is format-blind.

### CLI

```bash
# 1. Onboard a brand-new source. Gemini drafts a draft MappingSpec.
uv run python -m backend.ingest onboard dataset/EnterpriseBench/Enterprise_mail_system/emails.json \
  --tenant enterprisebench

# Review the drafted YAML at ingest_specs/enterprisebench/emails.yaml,
# edit as needed.

# 2. Promote the spec to active.
uv run python -m backend.ingest promote \
  --tenant enterprisebench \
  --source-pattern Enterprise_mail_system/emails.json \
  --version 1

# 3. Dry-run to validate before touching Neo4j.
uv run python -m backend.ingest dryrun \
  ingest_specs/enterprisebench/emails.yaml \
  dataset/EnterpriseBench/Enterprise_mail_system/emails.json --limit 100

# 4. Real ingest (requires running Neo4j).
uv run python -m backend.ingest run \
  ingest_specs/enterprisebench/emails.yaml \
  dataset/EnterpriseBench/Enterprise_mail_system/emails.json --limit 100

# 5. Resolve identity across sources.
uv run python -m backend.ingest resolve-identity
```

Re-running `run` on the same source is idempotent: matching
`(spec_version, source_file, source_record_id, content_hash)` are skipped.
Renaming a source field aborts the run via drift detection — no silent
re-inference.

### Bulk ingest — `scripts/ingest_all.sh`

For onboarding the full EnterpriseBench dataset (14 sources) end-to-end in
one shot, use the bulk script. Idempotent at every layer: onboard skips
when a YAML already exists, promote is a no-op when already active, and
record-level dedup means re-runs only ingest new rows.

```bash
# All 14 sources, then identity resolution.
bash scripts/ingest_all.sh

# Just one or two by spec-stem name.
bash scripts/ingest_all.sh emails posts

# Force re-onboard (re-draft the YAML even if it exists).
FORCE_ONBOARD=1 bash scripts/ingest_all.sh employees
```

The script post-processes each Gemini-drafted YAML (`normalize_spec_yaml`):
overwrites `source.file_pattern` with the canonical relative path so the
subsequent `promote --source-pattern` always matches, and clears
`required_paths_hash` + `type_fingerprint` so the runtime drift check
skips on the first ever onboarding (nothing to drift FROM yet).

The onboarder itself (`backend/ingest/onboard.py`) ships with three
hardenings against Gemini's response_schema quirks:

- **Few-shot reference**: the hand-written `ingest_specs/enterprisebench/emails.yaml`
  is injected into the draft prompt as a working example. Gemini imitates
  its structure (especially `@<NodeRule.name>` edge refs and bracket-quoted
  JSONPath for keys with spaces).
- **3-round repair loop**: validation failures feed the pydantic error
  back to Gemini up to 3 times before giving up.
- **Boundary normalization** (`Onboarder._normalize_llm_output`): strips
  `when: {}` (Gemini emits empty dicts to mean "no predicate"), forces
  `required: false` on every FieldMap (the LLM over-marks everything
  required; the ID is enforced separately via `id_required_fields`),
  rewrites bare attribute names in `id_required_fields` to JSONPaths via
  the per-node attribute-to-source map, and bracket-quotes JSONPath
  segments containing non-identifier characters (`$.Marital Status` →
  `$['Marital Status']`).

### Programmatic use

```python
from backend.graph.store import GraphStore
from backend.ingest.ingest_store import IngestStore
from backend.ingest.ingestor import Ingestor
from backend.ingest.spec import MappingSpec

spec = MappingSpec.from_yaml(open("ingest_specs/enterprisebench/emails.yaml").read())

with GraphStore("data/better_context.sqlite") as store:
    ingest_store = IngestStore(store._conn)
    ing = Ingestor(store, ingest_store)
    report = ing.run(spec, "dataset/EnterpriseBench/Enterprise_mail_system/emails.json",
                     limit=100)
    print(report)
    # records_in=100 records_out=100 records_skipped=0 records_dead=0 ...
```

## Canonical type registry

`backend/ingest/canonical.yaml` is the soft schema that anchors every
vendor's data:

| Node types | Relation types |
|---|---|
| Person, Organization, Document, Message, Event, Asset, Topic | MEMBER_OF, REPORTS_TO, WORKS_ON, OWNS, AUTHORED, SENT, RECEIVED, MENTIONS, PART_OF, PURCHASED, ASSIGNED_TO, TAGGED, RELATED_TO, SAME_AS |

Adding a new type is a one-line YAML edit, not a code change. Specs that
reference types outside this set fail validation at load.

## LLM usage policy

The LLM is **not** in the per-record hot path for structured data. Three
bounded uses only:

1. **Onboarding** — Gemini drafts a `MappingSpec` once per source.
2. **Opt-in extraction** — `llm_blocks` declared inside a spec. Cached,
   confidence-floored, grounded against the source span (rejects
   hallucinations), capped per-record.
3. **One-shot self-repair** — if the drafted spec fails pydantic
   validation, the validator error is sent back ONCE for repair.

Missing required field → `dead_letter`. Schema drift → `DriftError`. Type
coercion → registered transformer. Never the LLM.

## Layout (backend)

```
pyproject.toml          uv project: deps, dev tools, build config
.python-version         pinned Python (3.12)
uv.lock                 deterministic dep lock
backend/
├── config.py           load_dotenv + env constants
├── models/graph.py     GraphNode, GraphEdge, Provenance, SourceRecord
├── graph/
│   ├── schema.sql      raw + provenance + ingestion-control-plane tables
│   └── store.py        GraphStore (Neo4j + SQLite, MERGE-based)
├── api/
│   ├── models.py       Pydantic v2 response models (OpenAPI source of truth)
│   └── app.py          FastAPI app: graph + pattern query + edit + source-record + /api/query + /api/workflow endpoints
├── test_ingest_agnostic.py  cross-vendor agnosticism proof (4 shapes, 1 Ingestor)
├── test_graph_query_edit.py pattern query DSL + edit API tests
├── retrieval/
│   ├── models.py       Pydantic v2 Citation / Hit / QueryContext / QueryResult
│   ├── tiers.py        Tier ABC + StubTier (terminal fallback)
│   ├── exact.py        ExactTier (R1) — Cypher id lookup + Lucene fulltext
│   ├── hybrid.py       HybridTier (R2) — vector + fulltext fused by RRF
│   ├── router.py       RouterTier (R4) — GLiNER2 intent + NER (StubEntityRouter default)
│   ├── agentic.py      AgenticTier (R3) + LLMClient Protocol (Noop / Stub / Gemini)
│   ├── tools.py        6-tool surface for AgenticTier (pattern_query, fulltext_search, vector_search, get_node, get_neighbors, get_source_record)
│   ├── embedder.py     Embedder Protocol + StubEmbedder + BgeSmallEmbedder
│   ├── embed.py        one-shot manual pass to populate :Entity.vector
│   ├── index.py        Neo4j HNSW + fulltext index DDL
│   ├── orchestrator.py CascadeOrchestrator + TierConfig + factory builders
│   ├── engine.py       ContextEngine — public façade over the cascade
│   ├── _util.py        shared preview / lucene-escape / citation helpers
│   └── workflows/
│       ├── base.py        Workflow ABC + WorkflowInput/Result + TierRegistry
│       ├── registry.py    register_workflow / build_workflow / register_builtin_workflows
│       ├── customer_email.py  answer-customer-email (R5b)
│       └── thread_summary.py  thread-summary (R5c)
├── eval/
│   ├── golden.py       loads (query, expected_node_ids) from tasks.jsonl
│   ├── harness.py      runs cascade, writes Markdown report
│   └── reports/        timestamped runs (gitignored)
└── ingest/
    ├── canonical.yaml  canonical type registry (data, not code)
    ├── spec.py         pydantic MappingSpec + canonical-registry loader
    ├── runtime.py      JSONPath + transformers + predicates + drift detection
    ├── store.py        SQLite CRUD: mapping_specs, llm_cache, runs, dead_letter
    ├── llm.py          GeminiClient + JSON-Schema sanitizer + SQLite cache
    ├── onboard.py      Onboarder.draft_spec() — LLM-drafted MappingSpec
    ├── ingestor.py     Ingestor.run() — deterministic record→graph
    ├── identity.py     IdentityResolver (deterministic email match)
    └── __main__.py     CLI: dryrun / run / onboard / promote / resolve-identity
```
