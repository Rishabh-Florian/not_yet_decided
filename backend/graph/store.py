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
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Iterator

from neo4j import Driver, GraphDatabase

from backend.models.graph import GraphEdge, GraphNode, Provenance, SourceRecord


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

    # ---------- lifecycle ----------

    def _init_sqlite(self) -> None:
        with open(SCHEMA_PATH, "r") as f:
            self._conn.executescript(f.read())
        self._conn.commit()

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
        with self._session() as s:
            s.run(
                """CREATE (n:Entity {
                       id: $id, type: $type, attributes_json: $attrs,
                       confidence: $conf, vfs_path: $vfs,
                       created_at: $ca, updated_at: $ua, version: $v
                   })""",
                id=node.id,
                type=node.type,
                attrs=json.dumps(node.attributes),
                conf=node.confidence,
                vfs=node.vfs_path,
                ca=_iso(node.created_at),
                ua=_iso(node.updated_at),
                v=node.version,
            )
        with self._tx() as c:
            for p in node.provenance:
                self._insert_provenance(c, p, node_id=node.id)
        return node

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

    def add_edge(self, edge: GraphEdge) -> GraphEdge:
        rel_type = _validate_rel_type(edge.relation_type)
        with self._session() as s:
            res = s.run(
                f"""MATCH (a:Entity {{id: $src}}), (b:Entity {{id: $tgt}})
                    CREATE (a)-[r:{rel_type} {{
                        id: $id, attributes_json: $attrs,
                        confidence: $conf,
                        valid_from: $vf, valid_to: $vt,
                        version: $v
                    }}]->(b)
                    RETURN r""",
                src=edge.source_node_id,
                tgt=edge.target_node_id,
                id=edge.id,
                attrs=json.dumps(edge.attributes),
                conf=edge.confidence,
                vf=_iso(edge.valid_from),
                vt=_iso(edge.valid_to),
                v=edge.version,
            ).single()
            if res is None:
                raise KeyError("edge endpoints must exist as nodes")
        with self._tx() as c:
            for p in edge.provenance:
                self._insert_provenance(c, p, edge_id=edge.id)
        return edge

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
            cypher = (
                f"MATCH (n:Entity {{id: $id}})-[*1..{depth}]-(m:Entity) "
                f"WHERE m.id <> $id "
                f"RETURN DISTINCT m.id AS id"
            )
        else:
            rel = _validate_rel_type(relation_type)
            cypher = (
                f"MATCH (n:Entity {{id: $id}})-[:{rel}*1..{depth}]-(m:Entity) "
                f"WHERE m.id <> $id "
                f"RETURN DISTINCT m.id AS id"
            )
        with self._session() as s:
            return {r["id"] for r in s.run(cypher, id=node_id)}

    def shortest_path(self, source: str, target: str, max_hops: int = 6) -> list[str] | None:
        max_hops = int(max_hops)
        cypher = (
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
            node_count = s.run("MATCH (n:Entity) RETURN count(n) AS c").single()["c"]
            edge_count = s.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"]
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
        c.execute(
            """INSERT INTO provenance
               (node_id, edge_id, source_file, source_record_id, source_field,
                extraction_method, extraction_model, extracted_at, confidence, raw_value)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                node_id,
                edge_id,
                p.source_file,
                p.source_record_id,
                p.source_field,
                p.extraction_method,
                p.extraction_model,
                _iso(p.extracted_at),
                p.confidence,
                p.raw_value,
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
            confidence=n["confidence"],
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
            confidence=r["confidence"],
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
        return Provenance(
            source_file=row["source_file"],
            source_record_id=row["source_record_id"],
            source_field=row["source_field"],
            extraction_method=row["extraction_method"],
            extraction_model=row["extraction_model"],
            confidence=row["confidence"],
            raw_value=row["raw_value"],
            extracted_at=_parse_iso(row["extracted_at"]),
        )
