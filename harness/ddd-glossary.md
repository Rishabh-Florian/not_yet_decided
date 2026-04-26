# Domain Glossary

The **ubiquitous language** of Better Context. Every code symbol, doc, commit
message, and CLI command uses these names — no synonyms, no translations.

## Core graph

| Term | Definition | Code symbol | UI label |
|---|---|---|---|
| Graph node | Atomic entity in the knowledge graph (a person, a message, an asset…). Always typed by a *canonical type*. | `GraphNode` | "Node" |
| Graph edge | Typed relationship between two nodes. The type is a *canonical relation*. | `GraphEdge` | "Edge" |
| Provenance | A trace from one fact in the graph back to one field of one source record. Carries `extraction_method`, `extraction_model`, `confidence`, `spec_version`. | `Provenance` | "Trace" |
| Source record | The original ingested record, stored verbatim with a content hash. The graph never claims a fact whose source has been lost. | `SourceRecord` | "Source record" |
| Canonical type | A node type from the small fixed registry (`Person`, `Organization`, `Document`, `Message`, `Event`, `Asset`, `Topic`). Anchors the graph schema across vendors. | `canonical_type` | "Type" |
| Canonical relation | An edge type from the fixed registry (`MEMBER_OF`, `SENT`, `MENTIONS`, `SAME_AS`, …). | `relation_type` | "Relation" |

## Ingestion

| Term | Definition | Code symbol | UI label |
|---|---|---|---|
| Mapping spec | Per-(tenant, source-file) YAML contract. Drafted once at onboarding, reviewed by a human, runs forever after. The single artifact that absorbs vendor heterogeneity. | `MappingSpec` | "Mapping" |
| Tenant | A company / department / data owner — the operational unit that has its own specs and ingestion runs. One tenant per onboarded organisation. | `tenant` | "Tenant" |
| Node rule | A spec rule that produces one canonical node per record. | `NodeRule` | — |
| Edge rule | A spec rule that produces one canonical edge per record. | `EdgeRule` | — |
| Field map | One attribute of a node/edge: source JSONPath (or coalesce list) → optional transformer chain → attribute name. | `FieldMap` | — |
| LLM block | An opt-in `LLMExtraction` declared inside a spec, invoked at ingest time on a single unstructured field (e.g. email body → mentions). Cached, grounded, capped. | `LLMExtraction` | — |
| Predicate | The structured `when:` filter on a node or edge rule (`{not_null: …}`, `{equals: […]}`, `{and: […]}`, …). Never free-text. | (`runtime.evaluate_predicate`) | — |
| Transformer | A pure-fn name referenced by a field map (`lowercase`, `parse_iso_datetime`, `normalize_email`, …) registered in `runtime`. | `runtime.register_transformer` | — |
| Drift | A structural mismatch between a spec's `required_paths_hash` / `type_fingerprint` and the records currently in the source. Aborts the run. | `DriftError` | "Drift" |
| Dead letter | A per-record failure (missing required field, unparseable date, id-template gap). Logged with the raw record + reason; does not abort the run. | `dead_letter` table | "Dead letter" |
| Idempotency key | The `(spec_version, source_file, source_record_id, content_hash)` tuple that lets a re-ingest skip records already processed. | `ingest_runs_records` table | — |
| Run ledger | One `ingest_runs` row per `Ingestor.run` invocation (counts, status, timestamps). Audit trail for "what changed and when". | `ingest_runs` table | "Run" |

## Workflows

| Term | Definition | Code symbol | UI label |
|---|---|---|---|
| Onboarding | The one-time setup for a new source: sample records → LLM drafts a `MappingSpec` → human reviews → spec promoted to `active`. | `Onboarder.draft_spec` | "Onboard" |
| Ingestion | Running an active spec over its source file, producing nodes/edges/provenance. Deterministic and idempotent. | `Ingestor.run` | "Ingest" |
| Identity resolution | A post-ingest pass that finds nodes referring to the same real-world entity across sources and writes `SAME_AS` edges (without merging). | `IdentityResolver.resolve` | "Resolve identity" |
| Spec drafting | The LLM step inside onboarding that produces a candidate `MappingSpec` from sample records. | `Onboarder.draft_spec` | "Draft" |
| Spec promotion | Flipping a draft spec to `status='active'` so the Ingestor will pick it up. | `IngestStore.set_spec_status` | "Promote" |
| Self-repair | One-shot retry with the validator error sent back to Gemini when a drafted spec fails pydantic validation. Never used on real records. | `GeminiClient.repair` | — |

## Anti-examples (banned synonyms)

Search-and-rename if encountered in code, docs, or commits.

| Wrong | Right |
|---|---|
| ~~Source / Connector / Adapter~~ | **Mapping spec** (when referring to the YAML); **source file** (when referring to the data) |
| ~~Parser~~ | **Mapping spec** + `Ingestor` — there is no per-source parser; the whole point of v1 is that we don't write parsers |
| ~~Schema~~ (alone) | **Canonical schema** (the registry) or **mapping spec** (per-source). "Schema" alone is ambiguous |
| ~~Customer / Client / Tenant data~~ | **Tenant** is the data owner; **records** are the data |
| ~~Entity / Object / Item~~ | **Graph node** |
| ~~Relationship / Link / Connection~~ | **Graph edge** |
| ~~Lineage~~ | **Provenance** |
| ~~Pipeline~~ | **Ingestion** (the workflow) — "pipeline" is too generic |
| ~~Rule~~ (alone) | **Node rule** or **edge rule** — never bare "rule" |
| ~~Filter~~ | **Predicate** (when referring to `when:` clauses) |
| ~~Cleaner / Normaliser~~ | **Transformer** |
| ~~Failed record~~ | **Dead-lettered record** |
| ~~Match~~ (alone, in identity context) | **SAME_AS edge** or **identity cluster** |

## Update policy

- New concept appears in code or PRD → add a row in the same commit.
- Renamed concept → add the old name to anti-examples and run the rename across the codebase.
- This file is the source of truth: if a code symbol disagrees with what's here, the code symbol is wrong.
