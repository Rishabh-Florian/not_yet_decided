"""Migrate `provenance.confidence` from REAL float to TEXT enum.

Schema before this migration::

    confidence  REAL NOT NULL

Schema after::

    confidence       TEXT NOT NULL CHECK (confidence IN ('exact','grounded','inferred','human'))
    model_self_score REAL

Legacy float values are mapped by `extraction_method` (best-effort):

    - 'direct_mapping' / 'rule_based' -> 'exact'
    - 'human'                         -> 'human'
    - 'llm_extraction'                -> 'grounded' (we cannot reliably detect
      grounding from a stored float alone; existing rows in the codebase only
      reach SQLite when grounding already passed in `_call_llm_block`, so this
      is the conservative classification. The original float is preserved in
      `model_self_score` so any future calibration study has the raw signal.)

Idempotent: detects whether the conversion already ran via the column's
declared type (`REAL` vs `TEXT`) in `PRAGMA table_info`. Crashes hard on
unexpected values.
"""
from __future__ import annotations

import sqlite3
from typing import Final


_VALID: Final[frozenset[str]] = frozenset({"exact", "grounded", "inferred", "human"})


def _confidence_column_type(conn: sqlite3.Connection) -> str | None:
    for row in conn.execute("PRAGMA table_info(provenance)"):
        if row[1] == "confidence":
            return str(row[2]).upper()
    return None


def _classify_legacy(extraction_method: str) -> str:
    if extraction_method in ("direct_mapping", "rule_based"):
        return "exact"
    if extraction_method == "human":
        return "human"
    if extraction_method == "llm_extraction":
        return "grounded"
    raise ValueError(
        f"unknown extraction_method {extraction_method!r} in legacy provenance row; "
        "refusing to silently classify"
    )


def migrate(conn: sqlite3.Connection) -> None:
    col_type = _confidence_column_type(conn)
    if col_type is None:
        # provenance table doesn't exist yet — schema.sql will create it with
        # the new shape directly; nothing to do.
        return
    if col_type == "TEXT":
        return  # already migrated

    if col_type != "REAL":
        raise RuntimeError(
            f"migration 001_confidence_enum: unexpected confidence column type {col_type!r}; "
            "expected REAL (legacy) or TEXT (already migrated)"
        )

    # SQLite < 3.35 has no DROP COLUMN; even on newer versions, changing type
    # requires a table rebuild. We do it inside a single transaction so the
    # database is never observably half-migrated.
    cur = conn.cursor()
    cur.execute("BEGIN")
    try:
        cur.execute(
            """CREATE TABLE provenance__new (
                   id                INTEGER PRIMARY KEY AUTOINCREMENT,
                   node_id           TEXT,
                   edge_id           TEXT,
                   source_file       TEXT NOT NULL,
                   source_record_id  TEXT NOT NULL,
                   source_field      TEXT NOT NULL,
                   extraction_method TEXT NOT NULL,
                   extraction_model  TEXT NOT NULL,
                   extracted_at      TEXT NOT NULL,
                   confidence        TEXT NOT NULL
                       CHECK (confidence IN ('exact','grounded','inferred','human')),
                   model_self_score  REAL,
                   raw_value         TEXT NOT NULL,
                   spec_version      INTEGER,
                   CHECK ((node_id IS NOT NULL) <> (edge_id IS NOT NULL)),
                   FOREIGN KEY (source_file, source_record_id)
                       REFERENCES source_records(source_file, source_record_id) ON DELETE CASCADE
               )"""
        )

        rows = cur.execute(
            """SELECT id, node_id, edge_id, source_file, source_record_id, source_field,
                      extraction_method, extraction_model, extracted_at, confidence,
                      raw_value, spec_version
               FROM provenance"""
        ).fetchall()

        for row in rows:
            (
                pid, node_id, edge_id, source_file, source_record_id, source_field,
                extraction_method, extraction_model, extracted_at, legacy_conf,
                raw_value, spec_version,
            ) = row
            new_conf = _classify_legacy(extraction_method)
            if new_conf not in _VALID:  # belt + suspenders
                raise RuntimeError(f"classifier produced invalid label {new_conf!r}")
            self_score = (
                float(legacy_conf) if extraction_method == "llm_extraction" else None
            )
            cur.execute(
                """INSERT INTO provenance__new
                       (id, node_id, edge_id, source_file, source_record_id, source_field,
                        extraction_method, extraction_model, extracted_at, confidence,
                        model_self_score, raw_value, spec_version)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    pid, node_id, edge_id, source_file, source_record_id, source_field,
                    extraction_method, extraction_model, extracted_at, new_conf,
                    self_score, raw_value, spec_version,
                ),
            )

        cur.execute("DROP TABLE provenance")
        cur.execute("ALTER TABLE provenance__new RENAME TO provenance")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_prov_node ON provenance(node_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_prov_edge ON provenance(edge_id)")
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_prov_source_record "
            "ON provenance(source_file, source_record_id)"
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
