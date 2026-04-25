-- Knowledge graph persistence schema (SQLite).
-- The authoritative graph lives in NetworkX in memory; this file is the
-- on-disk mirror used for restart-safety and incremental updates.
--
-- Four distinct layers are modeled separately, all linked by id:
--   1. GRAPH         -> `nodes`, `edges`              (entities + relationships)
--   2. CONTENT       -> `nodes.attributes`,           (typed metadata, JSON)
--                       `edges.attributes`
--   3. TRACES        -> `provenance`                  (fact-level extraction history)
--   4. RAW DATA      -> `source_records`              (original ingested records, verbatim)
--
-- Provenance rows carry pointers (source_file, source_record_id, source_field) into
-- `source_records`, so any fact in the graph can be resolved back to the exact
-- field of the exact original record it was derived from.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

------------------------------------------------------------------------------
-- LAYER 4: RAW DATA  -- original ingested records, stored verbatim.
------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS source_records (
    source_file       TEXT NOT NULL,    -- e.g. "Enterprise_mail_system/emails.json"
    source_record_id  TEXT NOT NULL,    -- e.g. "email_id:4226322d-0ea5-..."
    raw_record        TEXT NOT NULL,    -- full original JSON record, unmodified
    content_hash      TEXT NOT NULL,    -- sha256(raw_record); enables change detection
    ingested_at       TEXT NOT NULL,
    PRIMARY KEY (source_file, source_record_id)
);

CREATE INDEX IF NOT EXISTS idx_source_records_hash ON source_records(content_hash);

------------------------------------------------------------------------------
-- LAYER 1+2: GRAPH + CONTENT (METADATA)  -- nodes/edges with typed attributes.
------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS nodes (
    id          TEXT PRIMARY KEY,
    type        TEXT NOT NULL,
    attributes  TEXT NOT NULL,        -- JSON
    confidence  REAL NOT NULL,
    vfs_path    TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    version     INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_nodes_type ON nodes(type);
CREATE INDEX IF NOT EXISTS idx_nodes_vfs_path ON nodes(vfs_path);

CREATE TABLE IF NOT EXISTS edges (
    id              TEXT PRIMARY KEY,
    source_node_id  TEXT NOT NULL,
    target_node_id  TEXT NOT NULL,
    relation_type   TEXT NOT NULL,
    attributes      TEXT NOT NULL,    -- JSON
    confidence      REAL NOT NULL,
    valid_from      TEXT NOT NULL,
    valid_to        TEXT,
    version         INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (source_node_id) REFERENCES nodes(id) ON DELETE CASCADE,
    FOREIGN KEY (target_node_id) REFERENCES nodes(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_edges_source   ON edges(source_node_id);
CREATE INDEX IF NOT EXISTS idx_edges_target   ON edges(target_node_id);
CREATE INDEX IF NOT EXISTS idx_edges_relation ON edges(relation_type);
CREATE INDEX IF NOT EXISTS idx_edges_pair     ON edges(source_node_id, target_node_id, relation_type);

------------------------------------------------------------------------------
-- LAYER 3: TRACES  -- fact-level provenance.
-- Each row attributes a single node or edge (exclusive-or) to one source record
-- field, with the extraction method/model that produced it.
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
    FOREIGN KEY (node_id) REFERENCES nodes(id) ON DELETE CASCADE,
    FOREIGN KEY (edge_id) REFERENCES edges(id) ON DELETE CASCADE,
    FOREIGN KEY (source_file, source_record_id)
        REFERENCES source_records(source_file, source_record_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_prov_node ON provenance(node_id);
CREATE INDEX IF NOT EXISTS idx_prov_edge ON provenance(edge_id);
CREATE INDEX IF NOT EXISTS idx_prov_source_record ON provenance(source_file, source_record_id);
