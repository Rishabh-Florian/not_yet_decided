# Better Context

> **Qontext Track — Big Berlin Hack 2026**
> Turn fragmented company data into a context base AI can operate on.

Better Context ingests the full EnterpriseBench dataset (email, HR, CRM, IT tickets, policies, code) into a structured, inspectable, editable **company memory** — a knowledge graph with fact-level provenance, a virtual file system, and a live web UI for both humans and AI agents.

![Better Context — system design](docs/system_design_architecture_diagram.png)

---

## What it does

1. **Adaptive ingestion** — point it at any JSON/CSV data source. An LLM (Gemini Flash 2.5) drafts a `MappingSpec` YAML once; a deterministic ingestor runs it forever. No per-vendor hardcoded parsers.

2. **Knowledge graph** — Neo4j (nodes + relationships + embeddings) + SQLite (fact-level provenance traces + verbatim raw records). Every attribute traces back to the exact file, field, and original value it came from.

3. **4-tier retrieval cascade** — `exact → router → hybrid → agentic`. The router is **two fine-tuned 205M GLiNER2 SLMs** (Pioneer.ai) called in parallel via `TwoModelEntityRouter`: v2 schema for intent (**0.978 acc**, beats GPT-4o by +11 pp), v3 NER-only for entities (**0.851 macro F1**, beats v2's joint NER head by +42 pp by training NER in isolation). Both at $0/query.

4. **Virtual file system (VFS)** — Unix-style `ls / cat / grep / find / stat / tree` over the graph. Surfaced as Gemini function-calling tools for AI agents. No disk materialization — derived at query time from `(canonical_type, node_id)`.

5. **Web UI** — Next.js 14 app with 4 live views: force-directed knowledge graph with subgraph filters, paginated node browser with provenance timeline, pattern query DSL, inline node editor with human-provenance tracking. Home page chatbar wired directly to the 4-tier cascade.

6. **Subgraph filters** — Department and Location chips in the graph panel. Three view modes: **Dim** (all nodes visible, matched at full opacity), **Isolate** (matched nodes + internal edges only), **Expand** (matched + all direct neighbors). AND across dimensions, OR within each. Named views saved to localStorage.

---

## Partner technologies used

| Technology | How it's used |
|------------|--------------|
| **Google Gemini (DeepMind)** | Onboarding: LLM drafts MappingSpec YAMLs from data samples. AgenticTier: bounded Gemini function-calling loop (6 tools, max 6 calls). Workflows: thread-summary + answer-customer-email compose. |
| **Pioneer.ai (Fastino)** | **Two** fine-tuned 205M GLiNER2 SLMs powering the production router. **v2 schema** (Round 2) for intent: 0.978 acc — beats GPT-4o (0.867) by +11 pp. **v3 NER-only** (Round 3) for entities: 0.851 macro F1 — beats v2's joint NER head (0.430) by +42 pp by training NER alone on a templated dataset built from real graph entities (per-entity: emp_id 1.00, customer_id 0.94, ticket_id 0.91, date 0.92, dept 0.86, product 0.47). Both LoRA adapters (~11 MB each) shipped at `pioneer/weights/`. |
| **Neo4j** | Primary graph store. Native HNSW vector indexes for hybrid search. Fulltext indexes for BM25-like ranking. MERGE-on-id dedup with deterministic edge IDs. |

---

## Quick start

Requirements: Docker, Python 3.12+, [uv](https://docs.astral.sh/uv/), Node.js 18+.

### Fine-tuned Pioneer models (~22 MB each)

The two production routers (v2 schema for intent, v3 NER-only for
entities) ship in-repo at `pioneer/weights/inazuma-gliner2-v2/` and
`pioneer/weights/inazuma-gliner2-ner-v3/`. They're also mirrored on
Google Drive in case the repo trims them later:

→ <https://drive.google.com/drive/folders/1gH6r4uec2ElQlyXiIszvw8UxmpuQBSnD?usp=drive_link>

If cloning gave you the weights you don't need to do anything. If
they're missing, download both folders from the Drive link into
`pioneer/weights/`.

### Bring up the stack

```sh
# 1. Install Python deps + start Neo4j
uv sync
docker run -d -p 7687:7687 -p 7474:7474 \
  -e NEO4J_AUTH=neo4j/better_context neo4j:5

# 2. Configure .env at the repo root
cat > .env <<'EOF'
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=better_context
NEO4J_DATABASE=neo4j
GEMINI_API_KEY=<get from https://aistudio.google.com/apikey>
BETTER_CONTEXT_AGENTIC=gemini
BETTER_CONTEXT_EMBEDDER=bge
BETTER_CONTEXT_ROUTER=two-model
PIONEER_INTENT_MODEL_ID=683f9b1f-db87-4eba-9cf8-719b1350251d  # v2 schema (intent)
PIONEER_NER_MODEL_ID=ee1a87ae-2611-4eed-9f66-64437d40e0bb    # v3 NER-only (entities)
PIONEER_API_KEY=<from https://agent.pioneer.ai → API Keys>
# Fallback: BETTER_CONTEXT_ROUTER=gliner2 + GLINER2_MODEL_PATH=pioneer/weights/inazuma-gliner2-v2 (single-model)
EOF

# 3. Ingest all 14 data sources
uv run bash scripts/ingest_all.sh           # ~15 min

# 4. Bootstrap org subgraph (departments + locations as explicit nodes)
uv run python scripts/bootstrap_subgraph_nodes.py

# 5. Populate vector embeddings for hybrid search
uv run python -m backend.retrieval.embed    # ~10 min

# 6. Start the API
uv run uvicorn backend.api.app:app --reload --port 8000
# → http://localhost:8000/docs (Swagger UI)

# 7. Start the frontend (separate terminal)
cd frontend && npm install && npm run dev
# → http://localhost:3000
```

Without API keys, the system still runs: retrieval uses stub fallbacks at each tier, ingestion skips LLM-drafted specs (use existing YAML specs in `ingest_specs/`).

---

## Repository layout

```
backend/            Python: graph store, adaptive ingestion, REST API, retrieval cascade, VFS, workflows, eval
frontend/           Next.js 14 + React: graph viz, node browser, query UI, provenance timeline, edit form
dataset/            EnterpriseBench source data (14 JSON/CSV sources + 24 policy PDFs + 270 invoice PDFs)
ingest_specs/       Per-tenant per-source MappingSpec YAMLs (reviewed + promoted to active)
scripts/            ingest_all.sh (bulk pipeline) + bootstrap_subgraph_nodes.py
pioneer/            Fine-tuned GLiNER2 SLM weights + training pipeline + benchmark results
docs/               ARCHITECTURE.md (full design) + DATASET.md
harness/            Agent harness: PRINCIPLES, ddd-glossary, failure-modes, ralph/ autonomous loop, skills installer
data/               Runtime SQLite db (gitignored)
```

---

## What's built and working

### Backend

- **Knowledge graph store** — Neo4j (graph + content + embeddings) + SQLite (provenance traces + raw records). `MERGE`-on-id dedup, deterministic edge ids via `sha256(src|rel|tgt)`, atomic-tx pattern. 13,201 nodes, 26,937 edges, 200,907 provenance records ingested.
- **Conflict resolution** — detection at the `add_node` MERGE seam (`backend/conflict.py`). Per attribute that disagrees, a deterministic decision table routes by `FactConfidence` rung (HUMAN > EXACT > GROUNDED > INFERRED): equal values auto-merge, ladder-broken ties auto-pick the higher rung, both INFERRED route to LLM_TRIAGE (live Gemini call, gated on `BETTER_CONTEXT_AGENTIC=gemini`), tied confident-rung ESCALATE to a queue. REST surface: `GET /api/conflicts`, `GET /api/conflicts/{id}`, `POST /api/conflicts/{id}/resolve`. Human resolutions go through `edit_node` so they carry `FactConfidence.HUMAN` provenance and are reversible like any edit.
- **Adaptive ingestion** — `MappingSpec` YAML per (tenant, source) drives a deterministic ingestor. Gemini Flash 2.5 drafts the spec once; idempotent on `(spec_version, source_file, record_id, content_hash)`. Drift detection aborts on schema change. Proven vendor-agnostic: 4 different CRM shapes collapse to identical canonical nodes through one `Ingestor` (`test_ingest_agnostic.py`).
- **Organizational subgraph** — bootstrap script injects synthetic `office_location` onto all 1,260 Person nodes (deterministic from `sha256(emp_id) % 5`) and creates explicit `Organization` nodes for 8 departments + 5 locations with `MEMBER_OF` edges.
- **Identity resolution** — deterministic email-match → `SAME_AS` edges (preserves per-source provenance, no merge).
- **REST API** — 12 endpoints (see table below). FastAPI + Pydantic v2 models generate the OpenAPI spec.
- **4-tier retrieval cascade** — `exact → router → hybrid → agentic → stub`. Tiers escalate on algorithmic relevance only. Router pre-routes via fine-tuned GLiNER2 SLM.
- **Fine-tuned Pioneer router** — `TwoModelEntityRouter` calls two GLiNER2 LoRA adapters in parallel via threadpool. **v2 schema** for intent: 0.978 acc (beats GPT-4o 0.867 by +11 pp). **v3 NER-only** for entities: 0.851 macro F1 (beats joint v2 NER 0.430 by +42 pp by training NER in isolation; per-entity emp_id 1.00, customer_id 0.94, ticket_id 0.91, date 0.92, dept 0.86, product 0.47). Total p95 ≈ max of the two calls. Full eval tables in [`pioneer/bench/results/comparison.md`](pioneer/bench/results/comparison.md); architecture rationale in [`pioneer/MODELS.md`](pioneer/MODELS.md).
- **Hybrid search** — Neo4j HNSW vector + fulltext, Reciprocal Rank Fusion (k=60).
- **Agentic tier** — bounded Gemini function-calling loop (6 tools, max 6 calls, 10s wall-clock).
- **VFS** — 6 pure-Cypher operations (`ls/cat/stat/grep/find/tree`), surfaced as Gemini function-calling tools for AI agents.
- **Workflow framework** — frozen-policy recipes over a locked tier subset. Two built-ins: `answer-customer-email` (T1 sender + neighbors → T3 product search → LLM compose) and `thread-summary` (T3 cluster recall → bounded 3-tool agent → structured markdown).
- **Eval harness** — recall@5/@10, p50/p95 latency, per-tier termination, escalation rate. Reports to `backend/eval/reports/<timestamp>.md`.
- **410 passing tests** — ingest agnosticism, pattern query, edit API, per-tier retrieval, tools, workflows, conflict resolution (unit + integration).

### Frontend

- `/` — Landing page: animated rotating sphere, prompt chips, chatbar wired to `POST /api/query`, live result cards (tier used, relevance, latency).
- `/app/graph` — Force-directed knowledge graph (react-force-graph-2d). FilterPanel with: node-type checkboxes, **subgraph Department + Location chips** (3 view modes: Dim/Isolate/Expand), time window, min-connections slider, source toggle, name search. Saved views persisted to localStorage.
- `/app/nodes` — Paginated node browser. Node detail with provenance timeline (per-attribute extraction history) and raw-record drawer (verbatim original JSON from Layer 4).
- `/app/query` — Pattern DSL query UI: `(Person)-[SENT]->(Message)` → paginated triples with full provenance.
- `/app/edit/:id` — Inline node editor. Every edit tracked with `extraction_method="human"`, editor identity, and timestamp.
- VFS tree sidebar on every app page, live stats in topbar.

---

## Knowledge graph data model

Four layers, kept distinct:

| Layer | Where | What |
|-------|-------|------|
| **1. Graph** | Neo4j `:Entity` nodes + typed relationships | Entities and relationships (the structure) |
| **2. Content** | Neo4j `attributes_json` per node + relationship | Typed, normalized fields |
| **3. Traces** | SQLite `provenance` | Fact-level extraction history (source file, field, extractor, model, confidence, spec_version) |
| **4. Raw data** | SQLite `source_records` | Original records verbatim, with content hash |

Provenance has a foreign key to `source_records` — a trace cannot exist without its raw record.

**Canonical node types:** Person, Organization, Document, Message, Event, Asset, Topic

**Canonical relation types:** MEMBER_OF, REPORTS_TO, WORKS_ON, OWNS, AUTHORED, SENT, RECEIVED, MENTIONS, PART_OF, PURCHASED, ASSIGNED_TO, TAGGED, RELATED_TO, SAME_AS

---

## REST API

Start: `uv run uvicorn backend.api.app:app --reload --port 8000`
Interactive docs: http://localhost:8000/docs

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/graph/stats` | Node/edge counts, types, provenance counts |
| `GET` | `/api/graph/node/{id}` | Node + attributes + full provenance chain |
| `GET` | `/api/graph/node/{id}/neighbors` | Graph traversal (`?relation_type=SENT&depth=1`) |
| `GET` | `/api/graph/nodes` | Paginated node list (`?type=Person&limit=50&offset=0`) |
| `GET` | `/api/graph/edge/{id}` | Edge + provenance |
| `GET` | `/api/graph/path` | Shortest path (`?from={id}&to={id}&max_hops=6`) |
| `POST` | `/api/graph/query` | Pattern DSL: `{"pattern": "(Person)-[SENT]->(Message)", "limit": 50}` |
| `PUT` | `/api/graph/node/{id}` | Human edit with provenance: `{"attributes": {...}, "editor": "..."}` |
| `GET` | `/api/source/{source_file}/{record_id}` | Verbatim original record (Layer 4) |
| `POST` | `/api/query` | 4-tier cascade: `{"query": "...", "context": {...}}` |
| `GET` | `/api/workflow` | List registered workflows |
| `POST` | `/api/workflow/{name}` | Invoke workflow: `{"payload": {...}}` |
| `GET` | `/api/conflicts` | List queued conflicts (`?status=open&limit=50`) |
| `GET` | `/api/conflicts/{id}` | Get a single conflict with both candidate values |
| `POST` | `/api/conflicts/{id}/resolve` | Resolve: `{"value": "...", "editor": "..."}` → writes HUMAN provenance |

### Example: trace a fact to its source

```bash
# Get a person node with full provenance
curl http://localhost:8000/api/graph/node/person:emp_1002

# Walk their reporting chain
curl "http://localhost:8000/api/graph/node/person:emp_1002/neighbors?relation_type=REPORTS_TO&depth=2"

# Get the raw original record
curl "http://localhost:8000/api/source/Human_Resource_Management%2FEmployees%2Femployees.json/person:emp_1002"
```

### Example: subgraph query

```bash
# All communication in the Engineering department
curl -X POST http://localhost:8000/api/graph/query \
  -H 'Content-Type: application/json' \
  -d '{"pattern": "(Person)-[SENT]->(Message)", "limit": 100}'

# Engineering org structure
curl -X POST http://localhost:8000/api/graph/query \
  -H 'Content-Type: application/json' \
  -d '{"pattern": "(Person)-[MEMBER_OF]->(Organization)", "limit": 500}'
```

### Example: cascade retrieval

```bash
curl -X POST http://localhost:8000/api/query \
  -H 'Content-Type: application/json' \
  -d '{"query": "who leads the engineering team?"}'

# Response includes: answer, items[], citations[], tier_used, relevance, latency_ms
```

---

## Retrieval pipeline

```
Query
  │
  ├─ ExactTier      Cypher id lookup + Neo4j BM25 fulltext
  │                 → relevance 1.0 if id hit; normalized BM25 score otherwise
  │
  ├─ RouterTier     Fine-tuned 205M GLiNER2 SLM (Pioneer.ai)
  │                 4-way intent: lookup / search / analytical / unknown
  │                 6-type NER: person / org / date / ticket_id / product / location
  │                 → inline-delegates lookup to ExactTier; emits route_to for others
  │
  ├─ HybridTier     Neo4j HNSW vector + fulltext fused by RRF (k=60)
  │                 → relevance = cosine similarity after RRF
  │
  ├─ AgenticTier    Bounded Gemini function-calling loop
  │                 6 tools: pattern_query / fulltext_search / vector_search /
  │                          get_node / get_neighbors / get_source_record
  │                 Max 6 calls, 10s wall-clock
  │
  └─ StubTier       Terminal fallback (always 0 hits)
```

Each tier is a `Protocol`-backed class with a stub default so CI runs without weights or API keys.

---

## Ingest pipeline

```
1. ONBOARD (one-time per source)
   Sample N records → Gemini Flash 2.5 drafts MappingSpec YAML
   → 3-round pydantic validation + self-repair → persist to SQLite (status='draft')
   → Human reviews + promotes: uv run python -m backend.ingest.manage promote <spec_id>

2. RUN (deterministic, idempotent)
   For each record:
     Idempotency check: skip if (spec_version, file, record_id, content_hash) seen
     add_source_record() → verbatim raw in SQLite
     apply NodeRules → MERGE :Entity on id_template
     apply EdgeRules → MERGE relationship on sha256(src|rel|tgt)
     run LLMExtraction blocks (opt-in, cached, capped)
   → ledger: records_in / records_out / records_dead

3. IDENTITY RESOLVE (post-pass)
   Cluster Person nodes by normalized email
   → emit SAME_AS edges (no merge, preserves per-source provenance)

4. EMBED (manual one-shot)
   uv run python -m backend.retrieval.embed
   → writes :Entity.vector for HNSW vector search
```

To run specific sources: `uv run bash scripts/ingest_all.sh emails employees`

---

## Running tests

```bash
uv run pytest                          # all 331 tests
uv run pytest backend/tests/test_ingest_agnostic.py  # vendor-agnostic ingest proof
uv run pytest backend/tests/test_retrieval.py         # per-tier retrieval
uv run pytest backend/tests/test_api_integration.py   # full API roundtrip
```

TypeScript:
```bash
cd frontend && npx tsc --noEmit        # zero errors
```

## Key design decisions

- **No per-vendor parsers.** MappingSpec + one Ingestor handles all 14 sources. Adding a new data source = adding one YAML file.
- **Provenance is non-optional.** Every attribute in the graph has a `Provenance` record linking it back to exact source file + field + original value. Human edits get the same treatment.
- **Small model beats large model.** The fine-tuned 205M GLiNER2 SLM outperforms GPT-4o on the routing task at 1/3600th the per-query cost and 3.6× faster.
- **VFS is a lens, not storage.** The virtual file system is derived at query time from `(canonical_type, node_id)`. No materialization, no sync. Adding a new node type to `canonical.yaml` auto-surfaces it.
- **Subgraph as first-class concept.** Department/Location are explicit `Organization` nodes connected by `MEMBER_OF` edges — not just flat properties. The filter panel builds subgraph views by traversing these edges.
