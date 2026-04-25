# Better Context (workspace: `not_yet_decided`)

Turns fragmented enterprise data (email, CRM, HR, IT tickets, chat, code,
policies) into a structured, inspectable, editable company memory backed by
a knowledge graph with **fact-level provenance**. See
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full design.

## Layout

```
backend/        Python: knowledge graph store + adaptive ingestion + LLM client
frontend/       Next.js + React UI (not yet scaffolded)
dataset/        EnterpriseBench source data (sample tenant)
ingest_specs/   per-tenant per-source MappingSpec YAMLs
docs/           ARCHITECTURE.md + DATASET.md
data/           runtime SQLite db (gitignored)
```

## What's built

- **Knowledge graph store** — Neo4j (graph + content) + SQLite (traces + raw
  records). `MERGE`-on-id dedup, deterministic edge ids, atomic-tx pattern.
- **Adaptive ingestion** — `MappingSpec` per (tenant, source-file) drives a
  deterministic ingester. LLM (Gemini Flash 2.5) only at onboarding +
  opt-in unstructured extraction. Drift detection aborts on schema change.
- **Identity resolution (light)** — deterministic email-match → `SAME_AS`
  edges. Fuzzy + LLM triage stubbed for later.

What's **not** built yet: virtual file system, REST API, MCP server, web UI,
vector index, conflict-resolution UI. See the implementation-status table in
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

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

Add `GEMINI_API_KEY=...` to a `.env` at the repo root for the
LLM-driven onboarding (M2). Neo4j connection comes from env vars (defaults
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
  -e NEO4J_AUTH=neo4j/neo4j neo4j:5
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
