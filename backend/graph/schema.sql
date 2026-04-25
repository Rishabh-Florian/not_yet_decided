-- Knowledge graph persistence schema (SQLite).
-- The graph itself (layers 1+2: nodes, edges, attributes) lives in Neo4j.
-- This file owns layers 3+4 only:
--
--   3. TRACES        -> `provenance`        (fact-level extraction history)
--   4. RAW DATA      -> `source_records`    (original ingested records, verbatim)
--
-- Provenance rows reference graph elements by id (node_id / edge_id) — the
-- graph store in Neo4j is the source of truth for those, so there are no
-- foreign keys back to nodes/edges. (source_file, source_record_id) does have
-- a foreign key into source_records: a trace cannot exist without its raw
-- record.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

------------------------------------------------------------------------------
-- LAYER 4: RAW DATA  -- original ingested records, stored verbatim.
------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS source_records (
    source_file       TEXT NOT NULL,
    source_record_id  TEXT NOT NULL,
    raw_record        TEXT NOT NULL,
    content_hash      TEXT NOT NULL,
    ingested_at       TEXT NOT NULL,
    PRIMARY KEY (source_file, source_record_id)
);

CREATE INDEX IF NOT EXISTS idx_source_records_hash ON source_records(content_hash);

------------------------------------------------------------------------------
-- LAYER 3: TRACES  -- fact-level provenance.
-- Each row attributes a single node or edge (exclusive-or) to one source
-- record field, with the extraction method/model that produced it.
------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS provenance (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id           TEXT,
    edge_id           TEXT,
    source_file       TEXT NOT NULL,
    source_record_id  TEXT NOT NULL,
    source_field      TEXT NOT NULL,
    extraction_method TEXT NOT NULL,
    extraction_model  TEXT NOT NULL,
    extracted_at      TEXT NOT NULL,
    confidence        REAL NOT NULL,
    raw_value         TEXT NOT NULL,
    spec_version      INTEGER,                       -- mapping_specs.version, NULL for human/legacy
    CHECK ((node_id IS NOT NULL) <> (edge_id IS NOT NULL)),
    FOREIGN KEY (source_file, source_record_id)
        REFERENCES source_records(source_file, source_record_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_prov_node ON provenance(node_id);
CREATE INDEX IF NOT EXISTS idx_prov_edge ON provenance(edge_id);
CREATE INDEX IF NOT EXISTS idx_prov_source_record ON provenance(source_file, source_record_id);

------------------------------------------------------------------------------
-- INGESTION CONTROL PLANE
-- mapping_specs:        per (tenant, source_pattern, version) MappingSpec YAML
-- llm_cache:            cached structured outputs (Gemini etc.) keyed by hash
-- ingest_runs:          one row per Ingestor.run invocation
-- ingest_runs_records:  idempotency table (skip already-seen records)
-- dead_letter:          per-record failures with reason
------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS mapping_specs (
    tenant            TEXT NOT NULL,
    source_pattern    TEXT NOT NULL,
    version           INTEGER NOT NULL,
    yaml_text         TEXT NOT NULL,
    required_paths_hash TEXT,
    type_fingerprint  TEXT,                          -- JSON {path: type_tag}
    status            TEXT NOT NULL DEFAULT 'draft', -- draft | active | retired
    created_at        TEXT NOT NULL,
    PRIMARY KEY (tenant, source_pattern, version)
);

CREATE INDEX IF NOT EXISTS idx_specs_active
    ON mapping_specs(tenant, source_pattern, status);

CREATE TABLE IF NOT EXISTS llm_cache (
    cache_key_hash    TEXT PRIMARY KEY,
    prompt_hash       TEXT NOT NULL,
    model             TEXT NOT NULL,
    response_json     TEXT NOT NULL,                 -- parsed structured output
    raw_output        TEXT NOT NULL,                 -- the raw model response for audit
    created_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ingest_runs (
    run_id            TEXT PRIMARY KEY,
    tenant            TEXT NOT NULL,
    source_pattern    TEXT NOT NULL,
    spec_version      INTEGER NOT NULL,
    source_path       TEXT NOT NULL,
    status            TEXT NOT NULL,                 -- running | completed | aborted | failed
    records_in        INTEGER NOT NULL DEFAULT 0,
    records_out       INTEGER NOT NULL DEFAULT 0,
    records_skipped   INTEGER NOT NULL DEFAULT 0,
    records_dead      INTEGER NOT NULL DEFAULT 0,
    started_at        TEXT NOT NULL,
    finished_at       TEXT,
    error             TEXT
);

CREATE TABLE IF NOT EXISTS ingest_runs_records (
    spec_version      INTEGER NOT NULL,
    source_file       TEXT NOT NULL,
    source_record_id  TEXT NOT NULL,
    content_hash      TEXT NOT NULL,
    run_id            TEXT NOT NULL,
    processed_at      TEXT NOT NULL,
    PRIMARY KEY (spec_version, source_file, source_record_id, content_hash)
);

CREATE TABLE IF NOT EXISTS dead_letter (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id            TEXT NOT NULL,
    source_file       TEXT NOT NULL,
    source_record_id  TEXT,                          -- may be NULL if id_template fails
    reason            TEXT NOT NULL,
    raw_record        TEXT NOT NULL,
    failed_at         TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_dead_letter_run ON dead_letter(run_id);
