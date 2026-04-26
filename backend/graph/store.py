"""Knowledge graph store: Neo4j for graph + content, SQLite for traces + raw data.

Four layers, kept distinct in code and on disk:

  1. GRAPH       -- Neo4j  : (:Entity) nodes connected by typed relationships
  2. CONTENT     -- Neo4j  : `attributes_json` on each node and relationship
  3. TRACES      -- SQLite : `provenance` rows (one per fact -> one source field)
  4. RAW DATA    -- SQLite : `source_records` (original ingested records, verbatim)

Provenance rows reference graph elements by id (node_id / edge_id) -- since the
graph lives in Neo4j there is no foreign key back, and cascading deletes are
implemented manually in `delete_node` / `delete_edge`.

Connection: NEO4J_URI / NEO4J_USER / NEO4J_PASSWORD / NEO4J_DATABASE env vars,
or the matching constructor kwargs. Defaults target a local Neo4j on
bolt://localhost:7687 with database "neo4j".
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, LiteralString, cast

from neo4j import Driver, GraphDatabase

from backend.conflict import Conflict, ConflictStore, reconcile
from backend.models.graph import (
    FactConfidence,
    GraphEdge,
    GraphNode,
    Provenance,
    SourceRecord,
)


SCHEMA_PATH = Path(__file__).parent / "schema.sql"

# Neo4j relationship types are part of query syntax, not parameters; we accept
# only safe identifiers and reject anything that would require escaping.
_REL_TYPE_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


def _parse_iso(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s) if s else None


def _canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _hash_record(record: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(record).encode("utf-8")).hexdigest()


def _validate_rel_type(rel: str) -> str:
    if not _REL_TYPE_PATTERN.match(rel):
        raise ValueError(
            f"invalid relation_type {rel!r}: must match [A-Za-z_][A-Za-z0-9_]*"
        )
    return rel


_PATTERN_RE = re.compile(r"^\((\w+)\)-\[(\w+)\]->\((\w+)\)$")


def parse_pattern(pattern: str) -> tuple[str, str, str]:
    """Parse '(Type)-[REL]->(Type)' into (source_type, rel_type, target_type).

    Validates node types against the canonical registry and relation types
    against both the Cypher-safe regex and the canonical registry.
    """
    from backend.ingest.spec import CANONICAL_NODE_TYPES, CANONICAL_RELATION_TYPES

    m = _PATTERN_RE.match(pattern.strip())
    if m is None:
        raise ValueError(
            f"invalid pattern {pattern!r}: expected (NodeType)-[REL_TYPE]->(NodeType)"
        )
    src_type, rel_type, tgt_type = m.group(1), m.group(2), m.group(3)
    if src_type not in CANONICAL_NODE_TYPES:
        raise ValueError(
            f"unknown source node type {src_type!r}; "
            f"valid: {sorted(CANONICAL_NODE_TYPES)}"
        )
    if tgt_type not in CANONICAL_NODE_TYPES:
        raise ValueError(
            f"unknown target node type {tgt_type!r}; "
            f"valid: {sorted(CANONICAL_NODE_TYPES)}"
        )
    _validate_rel_type(rel_type)
    if rel_type not in CANONICAL_RELATION_TYPES:
        raise ValueError(
            f"unknown relation type {rel_type!r}; "
            f"valid: {sorted(CANONICAL_RELATION_TYPES)}"
        )
    return src_type, rel_type, tgt_type


class GraphStore:
    def __init__(
        self,
        db_path: str | Path,
        *,
        neo4j_uri: str | None = None,
        neo4j_user: str | None = None,
        neo4j_password: str | None = None,
        neo4j_database: str | None = None,
    ):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        uri = neo4j_uri or os.environ.get("NEO4J_URI", "bolt://localhost:7687")
        user = neo4j_user or os.environ.get("NEO4J_USER", "neo4j")
        password = neo4j_password or os.environ.get("NEO4J_PASSWORD", "neo4j")
        self._database = neo4j_database or os.environ.get("NEO4J_DATABASE", "neo4j")
        self._driver: Driver = GraphDatabase.driver(uri, auth=(user, password))

        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._init_sqlite()
        self._init_neo4j()

        # Conflict detection at MERGE time. Resides in the same SQLite
        # connection so conflict rows + provenance writes share one tx.
        self._conflicts = ConflictStore(self._conn)

    @property
    def conflicts(self) -> ConflictStore:
        """Public accessor for the embedded ConflictStore.

        Used by the API layer to list/resolve conflicts without exposing
        the SQLite connection directly.
        """
        return self._conflicts

    # ---------- lifecycle ----------

    def _init_sqlite(self) -> None:
        # Run migrations against the legacy DB shape BEFORE applying schema.sql,
        # because schema.sql is `CREATE TABLE IF NOT EXISTS` and won't re-shape
        # an existing `provenance` table whose columns have drifted from the
        # canonical definition.
        self._run_migrations()

        with open(SCHEMA_PATH, "r") as f:
            self._conn.executescript(f.read())
        # Idempotent column additions for older databases. SQLite has no
        # IF NOT EXISTS for ADD COLUMN, so we catch the duplicate-column error.
        for ddl in [
            "ALTER TABLE provenance ADD COLUMN spec_version INTEGER",
            "ALTER TABLE provenance ADD COLUMN model_self_score REAL",
            "ALTER TABLE provenance ADD COLUMN attribute TEXT",
        ]:
            try:
                self._conn.execute(ddl)
            except sqlite3.OperationalError as e:
                if "duplicate column name" not in str(e).lower():
                    raise
        # Post-migration index — must come after ALTER TABLE adds `attribute`,
        # otherwise CREATE INDEX fails on legacy databases.
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_prov_node_attr "
            "ON provenance(node_id, attribute)"
        )
        self._conn.commit()

    def _run_migrations(self) -> None:
        import importlib

        # Module name has a leading digit, so importlib is required.
        mig = importlib.import_module(
            "backend.graph.migrations.001_confidence_enum"
        )
        mig.migrate(self._conn)

    def _init_neo4j(self) -> None:
        with self._session() as s:
            s.run(
                "CREATE CONSTRAINT entity_id_unique IF NOT EXISTS "
                "FOR (n:Entity) REQUIRE n.id IS UNIQUE"
            )
            s.run(
                "CREATE INDEX entity_type IF NOT EXISTS "
                "FOR (n:Entity) ON (n.type)"
            )

    def close(self) -> None:
        self._conn.close()
        self._driver.close()

    def __enter__(self) -> "GraphStore":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self._conn
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def _session(self):
        return self._driver.session(database=self._database)

    # ---------- raw data (layer 4) ----------

    def add_source_record(
        self,
        source_file: str,
        source_record_id: str,
        raw_record: dict[str, Any],
    ) -> SourceRecord:
        """Insert (or update on content change) the verbatim original record.

        Idempotent on (source_file, source_record_id): if the record's
        content_hash already matches, this is a no-op; otherwise it replaces
        the stored copy and bumps `ingested_at`.
        """
        rec = SourceRecord(
            source_file=source_file,
            source_record_id=source_record_id,
            raw_record=raw_record,
            content_hash=_hash_record(raw_record),
        )
        with self._tx() as c:
            c.execute(
                """INSERT INTO source_records
                       (source_file, source_record_id, raw_record, content_hash, ingested_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(source_file, source_record_id) DO UPDATE SET
                       raw_record   = excluded.raw_record,
                       content_hash = excluded.content_hash,
                       ingested_at  = excluded.ingested_at
                   WHERE source_records.content_hash != excluded.content_hash""",
                (
                    rec.source_file,
                    rec.source_record_id,
                    _canonical_json(rec.raw_record),
                    rec.content_hash,
                    _iso(rec.ingested_at),
                ),
            )
        return rec

    def get_source_record(self, source_file: str, source_record_id: str) -> SourceRecord | None:
        row = self._conn.execute(
            "SELECT * FROM source_records WHERE source_file = ? AND source_record_id = ?",
            (source_file, source_record_id),
        ).fetchone()
        return self._row_to_source_record(row) if row else None

    def resolve_provenance(self, p: Provenance) -> tuple[SourceRecord | None, Any]:
        """Resolve a trace back to its raw record and the value of its source_field.

        Returns (record, field_value). If the record is missing the value is None;
        if the field path doesn't exist on the record the value is None as well.
        Field paths support simple dotted access (e.g. "sender.email").
        """
        rec = self.get_source_record(p.source_file, p.source_record_id)
        if rec is None:
            return None, None
        value: Any = rec.raw_record
        for part in p.source_field.split("."):
            if isinstance(value, dict) and part in value:
                value = value[part]
            else:
                value = None
                break
        return rec, value

    # ---------- node ops ----------

    def add_node(self, node: GraphNode) -> GraphNode:
        """Insert or merge a node by id with conflict-aware attribute writes.

        Provenance is appended (never replaces) so re-ingestion keeps the full
        trace history. Asserts canonical-type equality on match — id collisions
        across types (e.g. Person vs Organization for the same id) raise rather
        than silently merge.

        Conflict resolution: when the node already exists, every incoming
        attribute that disagrees with the existing value is routed through
        :func:`backend.conflict.reconcile`. AUTO_PICK winners write through;
        LLM_TRIAGE / ESCALATE keep the existing value and queue a row in the
        `conflicts` table. Both losing and winning provenance always land in
        the trace table (append-only) so the audit trail is complete.

        Atomicity: SQLite provenance + conflict rows are staged first, the
        Neo4j MERGE runs next, then SQLite is committed. On Neo4j failure
        SQLite rolls back (dropping both the staged prov and any queued
        conflicts). On SQLite-commit failure we delete the just-merged Neo4j
        node by id so the two stores cannot diverge.
        """
        # Conflict reconcile (only meaningful when the node already exists).
        # `reconcile` mutates `node.attributes` to the resolved set so the
        # subsequent Neo4j MERGE writes the right values; conflict rows are
        # staged in the same SQLite tx as the provenance below.
        existing = self.get_node(node.id)
        if existing is not None:
            try:
                node.attributes = reconcile(
                    node_id=node.id,
                    existing_attrs=existing.attributes,
                    existing_provenance=existing.provenance,
                    incoming_attrs=node.attributes,
                    incoming_provenance=node.provenance,
                    conflict_store=self._conflicts,
                )
            except Exception:
                self._conn.rollback()
                raise

        # Stage provenance. Python's sqlite3 auto-begins a transaction on the
        # first write; rollback on any exception undoes those writes (and any
        # conflict rows staged just above).
        try:
            for p in node.provenance:
                self._insert_provenance(self._conn, p, node_id=node.id)
        except Exception:
            self._conn.rollback()
            raise

        try:
            with self._session() as s:
                rec = s.run(
                    """MERGE (n:Entity {id: $id})
                       ON CREATE SET
                           n.type = $type,
                           n.attributes_json = $attrs,
                           n.vfs_path = $vfs,
                           n.created_at = $ca,
                           n.updated_at = $ua,
                           n.version = $v,
                           n._was_created = 1
                       ON MATCH SET
                           n.attributes_json = $attrs,
                           n.updated_at = $ua,
                           n.version = coalesce(n.version, 0) + 1,
                           n._was_created = 0
                       WITH n, n._was_created AS was_created
                       REMOVE n._was_created
                       RETURN n.type AS existing_type, was_created,
                              n.created_at AS created_at, n.version AS version""",
                    id=node.id,
                    type=node.type,
                    attrs=json.dumps(node.attributes),
                    vfs=node.vfs_path,
                    ca=_iso(node.created_at),
                    ua=_iso(node.updated_at),
                    v=node.version,
                ).single()
                if rec is None:
                    raise RuntimeError(f"MERGE returned no row for node {node.id}")
                if not rec["was_created"] and rec["existing_type"] != node.type:
                    # Type collision — refuse to merge across canonical types.
                    raise ValueError(
                        f"node id collision across canonical types: "
                        f"{node.id!r} exists as {rec['existing_type']!r}, "
                        f"refusing to merge as {node.type!r}"
                    )
                # Reflect the post-merge state back into the in-memory node.
                if not rec["was_created"]:
                    node.created_at = _parse_iso(rec["created_at"]) or node.created_at
                    node.version = rec["version"] or node.version
        except Exception:
            self._conn.rollback()
            raise

        try:
            self._conn.commit()
        except Exception:
            # Compensate the Neo4j MERGE so the two stores don't diverge.
            with self._session() as s:
                s.run("MATCH (n:Entity {id: $id}) DETACH DELETE n", id=node.id)
            raise
        return node

    def add_node_provenance(
        self,
        node_id: str,
        provenance: list[Provenance],
    ) -> None:
        """Append additional provenance traces to an existing node.

        Used when re-ingesting a record under a new spec version, or when a
        second source contributes facts to a node that already exists.
        """
        if not provenance:
            return
        with self._tx() as c:
            for p in provenance:
                self._insert_provenance(c, p, node_id=node_id)

    def get_node(self, node_id: str) -> GraphNode | None:
        with self._session() as s:
            rec = s.run(
                "MATCH (n:Entity {id: $id}) RETURN n", id=node_id
            ).single()
        if rec is None:
            return None
        return self._neo_node_to_node(rec["n"], self._provenance_for_node(node_id))

    def update_node_attributes(self, node_id: str, attributes: dict[str, Any]) -> GraphNode:
        node = self.get_node(node_id)
        if node is None:
            raise KeyError(node_id)
        node.attributes.update(attributes)
        node.touch()
        with self._session() as s:
            res = s.run(
                """MATCH (n:Entity {id: $id})
                   SET n.attributes_json = $attrs,
                       n.updated_at = $ua,
                       n.version = $v
                   RETURN n""",
                id=node_id,
                attrs=json.dumps(node.attributes),
                ua=_iso(node.updated_at),
                v=node.version,
            ).single()
            if res is None:
                raise KeyError(node_id)
        return node

    def edit_node(
        self,
        node_id: str,
        attributes: dict[str, Any],
        editor: str,
    ) -> GraphNode:
        """Apply a human edit: update attributes with full provenance tracking.

        Creates a synthetic source_record (satisfying the FK constraint), one
        Provenance row per changed attribute, then updates the node in Neo4j.
        Follows the same staged atomicity as add_node: SQLite first, Neo4j
        next, SQLite commit last, Neo4j compensated on failure.
        """
        node = self.get_node(node_id)
        if node is None:
            raise KeyError(node_id)

        now = datetime.now(timezone.utc)
        iso_now = now.isoformat()
        source_record_id = f"edit:{node_id}:{iso_now}"

        self.add_source_record(
            source_file="human_edits",
            source_record_id=source_record_id,
            raw_record={
                "node_id": node_id,
                "editor": editor,
                "changes": attributes,
                "edited_at": iso_now,
            },
        )

        provenance = [
            Provenance(
                source_file="human_edits",
                source_record_id=source_record_id,
                source_field=attr_key,
                attribute=attr_key,
                extraction_method="human",
                extraction_model=f"human:{editor}",
                confidence=FactConfidence.HUMAN,
                raw_value=str(value),
                extracted_at=now,
                spec_version=None,
            )
            for attr_key, value in attributes.items()
        ]

        try:
            for p in provenance:
                self._insert_provenance(self._conn, p, node_id=node_id)
        except Exception:
            self._conn.rollback()
            raise

        old_attrs = json.dumps(node.attributes)
        old_ua = _iso(node.updated_at)
        old_version = node.version

        node.attributes.update(attributes)
        node.touch()

        try:
            with self._session() as s:
                res = s.run(
                    """MATCH (n:Entity {id: $id})
                       SET n.attributes_json = $attrs,
                           n.updated_at = $ua,
                           n.version = $v
                       RETURN n""",
                    id=node_id,
                    attrs=json.dumps(node.attributes),
                    ua=_iso(node.updated_at),
                    v=node.version,
                ).single()
                if res is None:
                    raise KeyError(node_id)
        except Exception:
            self._conn.rollback()
            raise

        try:
            self._conn.commit()
        except Exception:
            with self._session() as s:
                s.run(
                    """MATCH (n:Entity {id: $id})
                       SET n.attributes_json = $attrs,
                           n.updated_at = $ua,
                           n.version = $v""",
                    id=node_id,
                    attrs=old_attrs,
                    ua=old_ua,
                    v=old_version,
                )
            raise

        node.provenance = self._provenance_for_node(node_id)
        return node

    def resolve_conflict(
        self,
        conflict_id: int,
        *,
        value: Any,
        editor: str,
    ) -> Conflict:
        """Apply a human resolution to a queued conflict.

        Writes the chosen value to the graph through `edit_node` (so the
        node's attribute provenance gets a fresh `FactConfidence.HUMAN`
        row, making the resolution itself auditable + reversible like any
        other edit), then flips the conflict row to `resolved`.

        Raises:
            KeyError: `conflict_id` is unknown OR the referenced node
                was deleted between detection and resolution.
            ValueError: the conflict is already resolved (resolutions
                are append-only — surface a fresh edit through the edit
                API instead of re-resolving).
        """
        c = self._conflicts.get(conflict_id)
        if c is None:
            raise KeyError(f"conflict {conflict_id} not found")
        if c.status == "resolved":
            raise ValueError(
                f"conflict {conflict_id} is already resolved; "
                "use the edit API for further updates"
            )

        # `edit_node` owns its own staged-tx (provenance → Neo4j → commit);
        # if it fails, no conflict-status flip happens. The flip + commit
        # below is its own micro-tx.
        self.edit_node(c.node_id, {c.attribute: value}, editor)
        resolved = self._conflicts.resolve(
            conflict_id,
            chosen_value=value,
            resolution_method="human",
            resolved_by=editor,
        )
        self._conn.commit()
        return resolved

    def delete_node(self, node_id: str) -> None:
        # Cascade by hand: provenance lives in SQLite and references this node
        # plus any incident edges; we collect those edge ids before DETACH DELETE
        # removes the relationships from Neo4j.
        with self._session() as s:
            edge_ids = [
                r["id"]
                for r in s.run(
                    "MATCH (n:Entity {id: $id})-[r]-() WHERE r.id IS NOT NULL "
                    "RETURN DISTINCT r.id AS id",
                    id=node_id,
                )
                if r["id"]
            ]
            s.run("MATCH (n:Entity {id: $id}) DETACH DELETE n", id=node_id)
        with self._tx() as c:
            c.execute("DELETE FROM provenance WHERE node_id = ?", (node_id,))
            if edge_ids:
                qmarks = ",".join(["?"] * len(edge_ids))
                c.execute(
                    f"DELETE FROM provenance WHERE edge_id IN ({qmarks})",
                    edge_ids,
                )

    # ---------- edge ops ----------

    @staticmethod
    def deterministic_edge_id(
        source_node_id: str,
        target_node_id: str,
        relation_type: str,
        valid_from: datetime | None,
    ) -> str:
        """sha256 of (src|rel|tgt|valid_from) — same edge ingested twice
        produces the same id, so MERGE-on-id is safe.
        """
        key = "|".join([
            source_node_id,
            relation_type,
            target_node_id,
            _iso(valid_from) or "",
        ])
        return "edge_" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]

    def add_edge(self, edge: GraphEdge) -> GraphEdge:
        """Insert or merge an edge. Defaults the edge id to a deterministic
        hash so repeat ingestions of the same fact don't create duplicate
        relationships in Neo4j.

        Atomicity follows the same staged pattern as add_node: SQLite
        provenance staged first, Neo4j MERGE next, SQLite committed last;
        Neo4j compensated on commit failure.
        """
        rel_type = _validate_rel_type(edge.relation_type)
        # Replace UUID-default ids with a deterministic id for proper MERGE
        # behavior. Callers may still pass an explicit id (e.g. for tests).
        if edge.id.startswith("edge_") and len(edge.id) == 5 + 12:  # default uuid hex[:12]
            edge.id = self.deterministic_edge_id(
                edge.source_node_id, edge.target_node_id,
                rel_type, edge.valid_from,
            )

        try:
            for p in edge.provenance:
                self._insert_provenance(self._conn, p, edge_id=edge.id)
        except Exception:
            self._conn.rollback()
            raise

        try:
            with self._session() as s:
                res = s.run(
                    cast(LiteralString, f"""MATCH (a:Entity {{id: $src}}), (b:Entity {{id: $tgt}})
                        MERGE (a)-[r:{rel_type} {{id: $id}}]->(b)
                        ON CREATE SET
                            r.attributes_json = $attrs,
                            r.valid_from = $vf,
                            r.valid_to = $vt,
                            r.version = $v
                        ON MATCH SET
                            r.attributes_json = $attrs,
                            r.valid_to = $vt,
                            r.version = coalesce(r.version, 0) + 1
                        RETURN r"""),
                    src=edge.source_node_id,
                    tgt=edge.target_node_id,
                    id=edge.id,
                    attrs=json.dumps(edge.attributes),
                    vf=_iso(edge.valid_from),
                    vt=_iso(edge.valid_to),
                    v=edge.version,
                ).single()
                if res is None:
                    raise KeyError("edge endpoints must exist as nodes")
        except Exception:
            self._conn.rollback()
            raise

        try:
            self._conn.commit()
        except Exception:
            with self._session() as s:
                s.run("MATCH ()-[r {id: $id}]-() DELETE r", id=edge.id)
            raise
        return edge

    def add_edge_provenance(
        self,
        edge_id: str,
        provenance: list[Provenance],
    ) -> None:
        if not provenance:
            return
        with self._tx() as c:
            for p in provenance:
                self._insert_provenance(c, p, edge_id=edge_id)

    def get_edge(self, edge_id: str) -> GraphEdge | None:
        with self._session() as s:
            rec = s.run(
                """MATCH (a:Entity)-[r {id: $id}]->(b:Entity)
                   RETURN r, a.id AS src, b.id AS tgt, type(r) AS rt""",
                id=edge_id,
            ).single()
        if rec is None:
            return None
        return self._neo_rel_to_edge(
            edge_id, rec["src"], rec["tgt"], rec["rt"], rec["r"],
            self._provenance_for_edge(edge_id),
        )

    def delete_edge(self, edge_id: str) -> None:
        with self._session() as s:
            s.run("MATCH ()-[r {id: $id}]-() DELETE r", id=edge_id)
        with self._tx() as c:
            c.execute("DELETE FROM provenance WHERE edge_id = ?", (edge_id,))

    # ---------- queries ----------

    def neighbors(
        self,
        node_id: str,
        relation_type: str | None = None,
        depth: int = 1,
    ) -> set[str]:
        if depth < 1:
            return set()
        depth = int(depth)
        if relation_type is None:
            cypher = cast(LiteralString,
                f"MATCH (n:Entity {{id: $id}})-[*1..{depth}]-(m:Entity) "
                f"WHERE m.id <> $id "
                f"RETURN DISTINCT m.id AS id"
            )
        else:
            rel = _validate_rel_type(relation_type)
            cypher = cast(LiteralString,
                f"MATCH (n:Entity {{id: $id}})-[:{rel}*1..{depth}]-(m:Entity) "
                f"WHERE m.id <> $id "
                f"RETURN DISTINCT m.id AS id"
            )
        with self._session() as s:
            return {r["id"] for r in s.run(cypher, id=node_id)}

    def shortest_path(self, source: str, target: str, max_hops: int = 6) -> list[str] | None:
        max_hops = int(max_hops)
        cypher = cast(LiteralString,
            f"MATCH p = shortestPath((a:Entity {{id: $s}})-[*..{max_hops}]-(b:Entity {{id: $t}})) "
            f"RETURN [n IN nodes(p) | n.id] AS path"
        )
        with self._session() as s:
            rec = s.run(cypher, s=source, t=target).single()
        if rec is None or rec["path"] is None:
            return None
        return list(rec["path"])

    def nodes_by_type(self, node_type: str) -> Iterable[GraphNode]:
        with self._session() as s:
            records = list(s.run(
                "MATCH (n:Entity {type: $t}) RETURN n", t=node_type
            ))
        node_ids = [r["n"]["id"] for r in records]
        prov_map = self._provenance_map_for_nodes(node_ids)
        for r in records:
            n = r["n"]
            yield self._neo_node_to_node(n, prov_map.get(n["id"], []))

    def pattern_query(
        self,
        source_type: str,
        relation_type: str,
        target_type: str,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[tuple[GraphNode, GraphEdge, GraphNode]], int]:
        """Execute a typed pattern query: (SourceType)-[REL]->(TargetType).

        Returns (matches, total_count) where each match is a
        (source_node, edge, target_node) triple with provenance loaded.
        """
        rel = _validate_rel_type(relation_type)
        count_cypher = cast(LiteralString,
            f"MATCH (a:Entity {{type: $src}})-[r:{rel}]->(b:Entity {{type: $tgt}}) "
            f"RETURN count(*) AS c"
        )
        match_cypher = cast(LiteralString,
            f"MATCH (a:Entity {{type: $src}})-[r:{rel}]->(b:Entity {{type: $tgt}}) "
            f"RETURN a, b, r, r.id AS eid, type(r) AS rt "
            f"SKIP {int(offset)} LIMIT {int(limit)}"
        )
        with self._session() as s:
            count_rec = s.run(count_cypher, src=source_type, tgt=target_type).single()
            total = count_rec["c"] if count_rec else 0
            records = list(s.run(match_cypher, src=source_type, tgt=target_type))

        all_node_ids: list[str] = []
        edge_ids: list[str] = []
        for r in records:
            all_node_ids.append(r["a"]["id"])
            all_node_ids.append(r["b"]["id"])
            if r["eid"]:
                edge_ids.append(r["eid"])

        prov_map = self._provenance_map_for_nodes(list(set(all_node_ids)))

        results: list[tuple[GraphNode, GraphEdge, GraphNode]] = []
        for r in records:
            a_id = r["a"]["id"]
            b_id = r["b"]["id"]
            eid = r["eid"] or ""
            src_node = self._neo_node_to_node(r["a"], prov_map.get(a_id, []))
            tgt_node = self._neo_node_to_node(r["b"], prov_map.get(b_id, []))
            edge = self._neo_rel_to_edge(
                eid, a_id, b_id, r["rt"], r["r"],
                self._provenance_for_edge(eid) if eid else [],
            )
            results.append((src_node, edge, tgt_node))

        return results, total

    def stats(self) -> dict[str, Any]:
        with self._session() as s:
            type_counts = {
                r["t"]: r["c"]
                for r in s.run("MATCH (n:Entity) RETURN n.type AS t, count(*) AS c")
            }
            relation_counts = {
                r["t"]: r["c"]
                for r in s.run("MATCH ()-[r]->() RETURN type(r) AS t, count(*) AS c")
            }
            node_rec = s.run("MATCH (n:Entity) RETURN count(n) AS c").single()
            edge_rec = s.run("MATCH ()-[r]->() RETURN count(r) AS c").single()
            node_count = node_rec["c"] if node_rec else 0
            edge_count = edge_rec["c"] if edge_rec else 0
        prov_count = self._conn.execute("SELECT COUNT(*) FROM provenance").fetchone()[0]
        raw_count = self._conn.execute("SELECT COUNT(*) FROM source_records").fetchone()[0]
        return {
            "graph": {
                "node_count": node_count,
                "edge_count": edge_count,
                "node_types": type_counts,
                "relation_types": relation_counts,
            },
            "traces": {"provenance_count": prov_count},
            "raw": {"source_record_count": raw_count},
        }

    # ---------- internal: provenance helpers ----------

    def _insert_provenance(
        self,
        c: sqlite3.Connection,
        p: Provenance,
        *,
        node_id: str | None = None,
        edge_id: str | None = None,
    ) -> None:
        if not isinstance(p.confidence, FactConfidence):
            raise TypeError(
                f"Provenance.confidence must be FactConfidence, "
                f"got {type(p.confidence).__name__}: {p.confidence!r}"
            )
        c.execute(
            """INSERT INTO provenance
               (node_id, edge_id, source_file, source_record_id, source_field,
                attribute, extraction_method, extraction_model, extracted_at,
                confidence, model_self_score, raw_value, spec_version)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                node_id,
                edge_id,
                p.source_file,
                p.source_record_id,
                p.source_field,
                p.attribute,
                p.extraction_method,
                p.extraction_model,
                _iso(p.extracted_at),
                p.confidence.value,
                p.model_self_score,
                p.raw_value,
                p.spec_version,
            ),
        )

    def _provenance_for_node(self, node_id: str) -> list[Provenance]:
        rows = self._conn.execute(
            "SELECT * FROM provenance WHERE node_id = ?", (node_id,)
        ).fetchall()
        return [self._row_to_provenance(r) for r in rows]

    def _provenance_for_edge(self, edge_id: str) -> list[Provenance]:
        rows = self._conn.execute(
            "SELECT * FROM provenance WHERE edge_id = ?", (edge_id,)
        ).fetchall()
        return [self._row_to_provenance(r) for r in rows]

    def _provenance_map_for_nodes(self, node_ids: list[str]) -> dict[str, list[Provenance]]:
        if not node_ids:
            return {}
        qmarks = ",".join(["?"] * len(node_ids))
        rows = self._conn.execute(
            f"SELECT * FROM provenance WHERE node_id IN ({qmarks})",
            node_ids,
        ).fetchall()
        out: dict[str, list[Provenance]] = {}
        for r in rows:
            out.setdefault(r["node_id"], []).append(self._row_to_provenance(r))
        return out

    # ---------- internal: serialization ----------

    @staticmethod
    def _neo_node_to_node(n: Any, provenance: list[Provenance]) -> GraphNode:
        return GraphNode(
            id=n["id"],
            type=n["type"],
            attributes=json.loads(n["attributes_json"]),
            provenance=provenance,
            vfs_path=n.get("vfs_path", "") or "",
            created_at=_parse_iso(n.get("created_at")),
            updated_at=_parse_iso(n.get("updated_at")),
            version=n["version"],
        )

    @staticmethod
    def _neo_rel_to_edge(
        edge_id: str,
        src: str,
        tgt: str,
        rel_type: str,
        r: Any,
        provenance: list[Provenance],
    ) -> GraphEdge:
        return GraphEdge(
            id=edge_id,
            source_node_id=src,
            target_node_id=tgt,
            relation_type=rel_type,
            attributes=json.loads(r["attributes_json"]),
            provenance=provenance,
            valid_from=_parse_iso(r.get("valid_from")),
            valid_to=_parse_iso(r.get("valid_to")),
            version=r["version"],
        )

    @staticmethod
    def _row_to_source_record(row: sqlite3.Row) -> SourceRecord:
        return SourceRecord(
            source_file=row["source_file"],
            source_record_id=row["source_record_id"],
            raw_record=json.loads(row["raw_record"]),
            content_hash=row["content_hash"],
            ingested_at=_parse_iso(row["ingested_at"]),
        )

    @staticmethod
    def _row_to_provenance(row: sqlite3.Row) -> Provenance:
        keys = row.keys()
        return Provenance(
            source_file=row["source_file"],
            source_record_id=row["source_record_id"],
            source_field=row["source_field"],
            attribute=row["attribute"] if "attribute" in keys else None,
            extraction_method=row["extraction_method"],
            extraction_model=row["extraction_model"],
            confidence=FactConfidence(row["confidence"]),
            raw_value=row["raw_value"],
            model_self_score=(
                row["model_self_score"] if "model_self_score" in keys else None
            ),
            extracted_at=_parse_iso(row["extracted_at"]),
            spec_version=row["spec_version"] if "spec_version" in keys else None,
        )
