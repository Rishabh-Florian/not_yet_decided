"""SQLite-backed CRUD for the ingestion control plane.

Wraps the same connection used by `GraphStore` (constructor takes the
connection in directly so we don't double-open the database file). Tables
are created by `backend/graph/schema.sql` — we just read/write here.

Tables:
  mapping_specs          versioned MappingSpec YAML, status flag
  llm_cache              cached structured outputs keyed by hash
  ingest_runs            one row per Ingestor.run invocation
  ingest_runs_records    idempotency markers
  dead_letter            per-record failures
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class IngestStore:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self._conn
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    # ---------- mapping_specs ----------

    def save_spec(
        self,
        *,
        tenant: str,
        source_pattern: str,
        version: int,
        yaml_text: str,
        required_paths_hash: str | None,
        type_fingerprint: dict[str, str] | None,
        status: str = "draft",
    ) -> None:
        with self._tx() as c:
            c.execute(
                """INSERT OR REPLACE INTO mapping_specs
                   (tenant, source_pattern, version, yaml_text,
                    required_paths_hash, type_fingerprint, status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    tenant,
                    source_pattern,
                    version,
                    yaml_text,
                    required_paths_hash,
                    json.dumps(type_fingerprint) if type_fingerprint else None,
                    status,
                    _now_iso(),
                ),
            )

    def set_spec_status(
        self, tenant: str, source_pattern: str, version: int, status: str
    ) -> None:
        with self._tx() as c:
            c.execute(
                """UPDATE mapping_specs SET status = ?
                   WHERE tenant = ? AND source_pattern = ? AND version = ?""",
                (status, tenant, source_pattern, version),
            )

    def get_active_spec(
        self, tenant: str, source_pattern: str
    ) -> dict[str, Any] | None:
        row = self._conn.execute(
            """SELECT rowid, * FROM mapping_specs
               WHERE tenant = ? AND source_pattern = ? AND status = 'active'
               ORDER BY version DESC LIMIT 1""",
            (tenant, source_pattern),
        ).fetchone()
        return _spec_row(row) if row else None

    def get_spec(
        self, tenant: str, source_pattern: str, version: int
    ) -> dict[str, Any] | None:
        row = self._conn.execute(
            """SELECT rowid, * FROM mapping_specs
               WHERE tenant = ? AND source_pattern = ? AND version = ?""",
            (tenant, source_pattern, version),
        ).fetchone()
        return _spec_row(row) if row else None

    def get_spec_by_rowid(self, rowid: int) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT rowid, * FROM mapping_specs WHERE rowid = ?",
            (rowid,),
        ).fetchone()
        return _spec_row(row) if row else None

    def set_spec_status_by_rowid(self, rowid: int, status: str) -> None:
        with self._tx() as c:
            c.execute(
                "UPDATE mapping_specs SET status = ? WHERE rowid = ?",
                (status, rowid),
            )

    def find_active_spec_by_pattern(
        self, source_pattern: str
    ) -> dict[str, Any] | None:
        """Find the unique active spec for a source pattern across all tenants.

        Used by the push-mode source-update endpoint, which gets only the
        source_file from the URL (no tenant). Raises `ValueError` if more
        than one tenant has an active spec for the pattern — caller must
        disambiguate.
        """
        rows = self._conn.execute(
            """SELECT rowid, * FROM mapping_specs
               WHERE source_pattern = ? AND status = 'active'
               ORDER BY version DESC""",
            (source_pattern,),
        ).fetchall()
        if not rows:
            return None
        # Take the highest version per tenant, then check uniqueness across tenants.
        by_tenant: dict[str, sqlite3.Row] = {}
        for r in rows:
            by_tenant.setdefault(r["tenant"], r)  # first row per tenant (highest version due to ORDER BY)
        if len(by_tenant) > 1:
            raise ValueError(
                f"multiple tenants have an active spec for {source_pattern!r}: "
                f"{sorted(by_tenant.keys())}"
            )
        return _spec_row(next(iter(by_tenant.values())))

    # ---------- llm_cache ----------

    def llm_cache_get(self, cache_key_hash: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM llm_cache WHERE cache_key_hash = ?",
            (cache_key_hash,),
        ).fetchone()
        if row is None:
            return None
        return {
            "cache_key_hash": row["cache_key_hash"],
            "prompt_hash": row["prompt_hash"],
            "model": row["model"],
            "response": json.loads(row["response_json"]),
            "raw_output": row["raw_output"],
            "created_at": row["created_at"],
        }

    def llm_cache_put(
        self,
        *,
        cache_key_hash: str,
        prompt_hash: str,
        model: str,
        response: Any,
        raw_output: str,
    ) -> None:
        with self._tx() as c:
            c.execute(
                """INSERT OR REPLACE INTO llm_cache
                   (cache_key_hash, prompt_hash, model, response_json,
                    raw_output, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    cache_key_hash,
                    prompt_hash,
                    model,
                    json.dumps(response),
                    raw_output,
                    _now_iso(),
                ),
            )

    # ---------- ingest_runs ----------

    def open_run(
        self,
        *,
        tenant: str,
        source_pattern: str,
        spec_version: int,
        source_path: str,
    ) -> str:
        run_id = "run_" + uuid.uuid4().hex[:16]
        with self._tx() as c:
            c.execute(
                """INSERT INTO ingest_runs
                   (run_id, tenant, source_pattern, spec_version, source_path,
                    status, started_at)
                   VALUES (?, ?, ?, ?, ?, 'running', ?)""",
                (run_id, tenant, source_pattern, spec_version, source_path, _now_iso()),
            )
        return run_id

    def close_run(
        self,
        run_id: str,
        *,
        status: str,
        records_in: int,
        records_out: int,
        records_skipped: int,
        records_dead: int,
        error: str | None = None,
    ) -> None:
        with self._tx() as c:
            c.execute(
                """UPDATE ingest_runs SET
                       status = ?, records_in = ?, records_out = ?,
                       records_skipped = ?, records_dead = ?,
                       finished_at = ?, error = ?
                   WHERE run_id = ?""",
                (
                    status,
                    records_in,
                    records_out,
                    records_skipped,
                    records_dead,
                    _now_iso(),
                    error,
                    run_id,
                ),
            )

    def already_seen(
        self,
        *,
        spec_version: int,
        source_file: str,
        source_record_id: str,
        content_hash: str,
    ) -> bool:
        row = self._conn.execute(
            """SELECT 1 FROM ingest_runs_records
               WHERE spec_version = ? AND source_file = ?
                 AND source_record_id = ? AND content_hash = ?""",
            (spec_version, source_file, source_record_id, content_hash),
        ).fetchone()
        return row is not None

    def mark_seen(
        self,
        *,
        spec_version: int,
        source_file: str,
        source_record_id: str,
        content_hash: str,
        run_id: str,
    ) -> None:
        with self._tx() as c:
            c.execute(
                """INSERT OR IGNORE INTO ingest_runs_records
                   (spec_version, source_file, source_record_id, content_hash,
                    run_id, processed_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    spec_version,
                    source_file,
                    source_record_id,
                    content_hash,
                    run_id,
                    _now_iso(),
                ),
            )

    # ---------- dead_letter ----------

    def write_dead_letter(
        self,
        *,
        run_id: str,
        source_file: str,
        source_record_id: str | None,
        reason: str,
        raw_record: Any,
    ) -> None:
        with self._tx() as c:
            c.execute(
                """INSERT INTO dead_letter
                   (run_id, source_file, source_record_id, reason, raw_record,
                    failed_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    run_id,
                    source_file,
                    source_record_id,
                    reason,
                    json.dumps(raw_record),
                    _now_iso(),
                ),
            )

    def dead_letter_count(self, run_id: str) -> int:
        return self._conn.execute(
            "SELECT COUNT(*) FROM dead_letter WHERE run_id = ?",
            (run_id,),
        ).fetchone()[0]


def _spec_row(row: sqlite3.Row) -> dict[str, Any]:
    d: dict[str, Any] = {
        "tenant": row["tenant"],
        "source_pattern": row["source_pattern"],
        "version": row["version"],
        "yaml_text": row["yaml_text"],
        "required_paths_hash": row["required_paths_hash"],
        "type_fingerprint": (
            json.loads(row["type_fingerprint"]) if row["type_fingerprint"] else None
        ),
        "status": row["status"],
        "created_at": row["created_at"],
    }
    try:
        d["rowid"] = row["rowid"]
    except IndexError:
        pass
    return d
