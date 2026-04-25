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
    CHECK ((node_id IS NOT NULL) <> (edge_id IS NOT NULL)),
    FOREIGN KEY (source_file, source_record_id)
        REFERENCES source_records(source_file, source_record_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_prov_node ON provenance(node_id);
CREATE INDEX IF NOT EXISTS idx_prov_edge ON provenance(edge_id);
CREATE INDEX IF NOT EXISTS idx_prov_source_record ON provenance(source_file, source_record_id);
