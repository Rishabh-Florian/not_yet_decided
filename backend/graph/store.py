"""Knowledge graph store: NetworkX in memory, SQLite for persistence.

The NetworkX MultiDiGraph is the working copy; SQLite is the durable mirror.
Mutations write through to both so the in-memory graph and the DB stay in sync.

Four layers, kept distinct on disk and in the API:

  1. GRAPH       -- nodes, edges
  2. CONTENT     -- node.attributes / edge.attributes (typed metadata)
  3. TRACES      -- provenance rows (one per fact -> one source field)
  4. RAW DATA    -- source_records (the original ingested records, verbatim)

Provenance rows reference (source_file, source_record_id) into source_records,
so any fact can be resolved back to the exact field of the exact original record.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Iterator

import networkx as nx

from backend.models.graph import GraphEdge, GraphNode, Provenance, SourceRecord


SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


def _parse_iso(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s) if s else None


def _canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _hash_record(record: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(record).encode("utf-8")).hexdigest()


class GraphStore:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.graph: nx.MultiDiGraph = nx.MultiDiGraph()
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._init_schema()
        self._load_into_memory()

    # ---------- lifecycle ----------

    def _init_schema(self) -> None:
        with open(SCHEMA_PATH, "r") as f:
            self._conn.executescript(f.read())
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

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
        with self._tx() as c:
            c.execute(
                """INSERT INTO nodes (id, type, attributes, confidence, vfs_path,
                                      created_at, updated_at, version)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    node.id,
                    node.type,
                    json.dumps(node.attributes),
                    node.confidence,
                    node.vfs_path,
                    _iso(node.created_at),
                    _iso(node.updated_at),
                    node.version,
                ),
            )
            for p in node.provenance:
                self._insert_provenance(c, p, node_id=node.id)

        self.graph.add_node(node.id, **self._node_to_attrs(node))
        return node

    def get_node(self, node_id: str) -> GraphNode | None:
        attrs = self.graph.nodes.get(node_id)
        if not attrs:
            return None
        return self._attrs_to_node(node_id, attrs)

    def update_node_attributes(self, node_id: str, attributes: dict[str, Any]) -> GraphNode:
        node = self.get_node(node_id)
        if node is None:
            raise KeyError(node_id)
        node.attributes.update(attributes)
        node.touch()
        with self._tx() as c:
            c.execute(
                """UPDATE nodes SET attributes = ?, updated_at = ?, version = ? WHERE id = ?""",
                (json.dumps(node.attributes), _iso(node.updated_at), node.version, node_id),
            )
        self.graph.nodes[node_id].update(self._node_to_attrs(node))
        return node

    def delete_node(self, node_id: str) -> None:
        with self._tx() as c:
            c.execute("DELETE FROM nodes WHERE id = ?", (node_id,))
        if node_id in self.graph:
            self.graph.remove_node(node_id)

    # ---------- edge ops ----------

    def add_edge(self, edge: GraphEdge) -> GraphEdge:
        if edge.source_node_id not in self.graph or edge.target_node_id not in self.graph:
            raise KeyError("edge endpoints must exist as nodes")
        with self._tx() as c:
            c.execute(
                """INSERT INTO edges (id, source_node_id, target_node_id, relation_type,
                                      attributes, confidence, valid_from, valid_to, version)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    edge.id,
                    edge.source_node_id,
                    edge.target_node_id,
                    edge.relation_type,
                    json.dumps(edge.attributes),
                    edge.confidence,
                    _iso(edge.valid_from),
                    _iso(edge.valid_to),
                    edge.version,
                ),
            )
            for p in edge.provenance:
                self._insert_provenance(c, p, edge_id=edge.id)

        self.graph.add_edge(
            edge.source_node_id,
            edge.target_node_id,
            key=edge.id,
            **self._edge_to_attrs(edge),
        )
        return edge

    def get_edge(self, edge_id: str) -> GraphEdge | None:
        for u, v, k, data in self.graph.edges(keys=True, data=True):
            if k == edge_id:
                return self._attrs_to_edge(edge_id, u, v, data)
        return None

    def delete_edge(self, edge_id: str) -> None:
        with self._tx() as c:
            c.execute("DELETE FROM edges WHERE id = ?", (edge_id,))
        for u, v, k in list(self.graph.edges(keys=True)):
            if k == edge_id:
                self.graph.remove_edge(u, v, key=k)
                break

    # ---------- queries ----------

    def neighbors(
        self,
        node_id: str,
        relation_type: str | None = None,
        depth: int = 1,
    ) -> set[str]:
        if node_id not in self.graph:
            return set()
        seen: set[str] = set()
        frontier: set[str] = {node_id}
        for _ in range(depth):
            next_frontier: set[str] = set()
            for src in frontier:
                for _, tgt, data in self.graph.out_edges(src, data=True):
                    if relation_type is None or data.get("relation_type") == relation_type:
                        if tgt not in seen and tgt != node_id:
                            seen.add(tgt)
                            next_frontier.add(tgt)
                for src_in, _, data in self.graph.in_edges(src, data=True):
                    if relation_type is None or data.get("relation_type") == relation_type:
                        if src_in not in seen and src_in != node_id:
                            seen.add(src_in)
                            next_frontier.add(src_in)
            frontier = next_frontier
        return seen

    def shortest_path(self, source: str, target: str, max_hops: int = 6) -> list[str] | None:
        try:
            path = nx.shortest_path(self.graph, source=source, target=target)
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return None
        return path if len(path) - 1 <= max_hops else None

    def nodes_by_type(self, node_type: str) -> Iterable[GraphNode]:
        for nid, attrs in self.graph.nodes(data=True):
            if attrs.get("type") == node_type:
                yield self._attrs_to_node(nid, attrs)

    def stats(self) -> dict[str, Any]:
        type_counts: dict[str, int] = {}
        for _, attrs in self.graph.nodes(data=True):
            type_counts[attrs["type"]] = type_counts.get(attrs["type"], 0) + 1
        relation_counts: dict[str, int] = {}
        for _, _, data in self.graph.edges(data=True):
            relation_counts[data["relation_type"]] = relation_counts.get(data["relation_type"], 0) + 1
        prov_count = self._conn.execute("SELECT COUNT(*) FROM provenance").fetchone()[0]
        raw_count = self._conn.execute("SELECT COUNT(*) FROM source_records").fetchone()[0]
        return {
            "graph": {
                "node_count": self.graph.number_of_nodes(),
                "edge_count": self.graph.number_of_edges(),
                "node_types": type_counts,
                "relation_types": relation_counts,
            },
            "traces": {"provenance_count": prov_count},
            "raw": {"source_record_count": raw_count},
        }

    # ---------- internal: load / serialize ----------

    def _load_into_memory(self) -> None:
        cur = self._conn.execute("SELECT * FROM nodes")
        for row in cur.fetchall():
            node = self._row_to_node(row)
            self.graph.add_node(node.id, **self._node_to_attrs(node))

        cur = self._conn.execute("SELECT * FROM edges")
        for row in cur.fetchall():
            edge = self._row_to_edge(row)
            self.graph.add_edge(
                edge.source_node_id,
                edge.target_node_id,
                key=edge.id,
                **self._edge_to_attrs(edge),
            )

        # Hydrate provenance onto in-memory nodes/edges. Build an edge_id -> (u, v)
        # index up-front so each provenance row is O(1) instead of O(E).
        edge_index: dict[str, tuple[str, str]] = {
            k: (u, v) for u, v, k in self.graph.edges(keys=True)
        }
        for row in self._conn.execute("SELECT * FROM provenance").fetchall():
            prov = self._row_to_provenance(row)
            if row["node_id"] and row["node_id"] in self.graph:
                self.graph.nodes[row["node_id"]].setdefault("provenance", []).append(prov)
            elif row["edge_id"] and row["edge_id"] in edge_index:
                u, v = edge_index[row["edge_id"]]
                self.graph[u][v][row["edge_id"]].setdefault("provenance", []).append(prov)

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

    @staticmethod
    def _node_to_attrs(node: GraphNode) -> dict[str, Any]:
        return {
            "type": node.type,
            "attributes": node.attributes,
            "provenance": list(node.provenance),
            "confidence": node.confidence,
            "vfs_path": node.vfs_path,
            "created_at": node.created_at,
            "updated_at": node.updated_at,
            "version": node.version,
        }

    @staticmethod
    def _edge_to_attrs(edge: GraphEdge) -> dict[str, Any]:
        return {
            "relation_type": edge.relation_type,
            "attributes": edge.attributes,
            "provenance": list(edge.provenance),
            "confidence": edge.confidence,
            "valid_from": edge.valid_from,
            "valid_to": edge.valid_to,
            "version": edge.version,
        }

    @staticmethod
    def _attrs_to_node(node_id: str, attrs: dict[str, Any]) -> GraphNode:
        return GraphNode(
            id=node_id,
            type=attrs["type"],
            attributes=dict(attrs.get("attributes", {})),
            provenance=list(attrs.get("provenance", [])),
            confidence=attrs["confidence"],
            vfs_path=attrs.get("vfs_path", ""),
            created_at=attrs["created_at"],
            updated_at=attrs["updated_at"],
            version=attrs["version"],
        )

    @staticmethod
    def _attrs_to_edge(edge_id: str, src: str, tgt: str, data: dict[str, Any]) -> GraphEdge:
        return GraphEdge(
            id=edge_id,
            source_node_id=src,
            target_node_id=tgt,
            relation_type=data["relation_type"],
            attributes=dict(data.get("attributes", {})),
            provenance=list(data.get("provenance", [])),
            confidence=data["confidence"],
            valid_from=data["valid_from"],
            valid_to=data.get("valid_to"),
            version=data["version"],
        )

    @staticmethod
    def _row_to_node(row: sqlite3.Row) -> GraphNode:
        return GraphNode(
            id=row["id"],
            type=row["type"],
            attributes=json.loads(row["attributes"]),
            provenance=[],
            confidence=row["confidence"],
            vfs_path=row["vfs_path"] or "",
            created_at=_parse_iso(row["created_at"]),
            updated_at=_parse_iso(row["updated_at"]),
            version=row["version"],
        )

    @staticmethod
    def _row_to_edge(row: sqlite3.Row) -> GraphEdge:
        return GraphEdge(
            id=row["id"],
            source_node_id=row["source_node_id"],
            target_node_id=row["target_node_id"],
            relation_type=row["relation_type"],
            attributes=json.loads(row["attributes"]),
            provenance=[],
            confidence=row["confidence"],
            valid_from=_parse_iso(row["valid_from"]),
            valid_to=_parse_iso(row["valid_to"]),
            version=row["version"],
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
