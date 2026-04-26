"""VFS operations — path-style retrieval over the existing knowledge graph.

This module is **read-only**. Every method translates a path string into one
or more Cypher reads (plus the existing `node_text` Lucene index for
`grep`); nothing is materialized to disk and nothing is stored on the node.

Path tree shape — fixed, derived from `CANONICAL_NODE_TYPES`:

    /                       root           — `ls` returns the canonical types
    /{Type}/                directory      — `ls` returns its node ids
    /{Type}/{node_id}       file           — `cat` returns a `FileBody`

The type segment is validated against the canonical registry on every call.
A path with a node-id segment containing `/` is rejected (the registry
schema does not allow it; defending fail-fast keeps the path grammar
unambiguous).
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime
from typing import Any, Iterable, LiteralString, cast

from backend.graph.store import GraphStore
from backend.ingest.spec import CANONICAL_NODE_TYPES

from .models import (
    DirEntry,
    FileBody,
    GrepHit,
    NeighborRef,
    ProvenanceResponse,
    SourceRecordResponse,
    StatInfo,
    TreeNode,
)


# Pagination caps. Keep tool result blocks bounded so a single call cannot
# blow the LLM context window. Mirrors the `_MAX_K` constant in
# `backend.retrieval.tools`.
_DEFAULT_LIMIT: int = 25
_MAX_LIMIT: int = 100
_MAX_TREE_DEPTH: int = 3
_MAX_NEIGHBORS_PER_RELATION: int = 25

# Reserved Lucene chars (mirrors backend.retrieval._util._LUCENE_SPECIAL).
# Inlined rather than imported to keep VFS independent of the retrieval
# package's underscore-prefixed internals.
_LUCENE_SPECIAL = re.compile(r'([+\-!(){}\[\]^"~*?:\\/]|&&|\|\|)')


def _escape_lucene(query: str) -> str:
    return _LUCENE_SPECIAL.sub(r"\\\1", query)


def _preview_attrs(attrs: dict[str, Any]) -> str:
    """One-line summary of a node's attributes — kept local to avoid a
    cross-package import to `backend.retrieval._util`. Same field
    priority as `_preview` in the retrieval helpers.
    """
    for key in ("name", "title", "subject", "summary", "description", "customer_name"):
        v = attrs.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()[:200]
    return json.dumps(attrs, ensure_ascii=False)[:200]


def _normalize_bm25(score: float) -> float:
    # Same normalization ExactTier uses: maps Neo4j's unbounded BM25 score
    # into [0, 1) so cross-tier consumers can reason about it. Duplicated
    # here intentionally — VFS is independent of the retrieval cascade.
    return score / (1.0 + score)


def _parse_path(path: str) -> tuple[str, str | None, str | None]:
    """Split a VFS path into (kind, type_segment, node_id_segment).

    Returns:
      ("root", None, None)              for "/" (or "")
      ("dir",  "Person", None)          for "/Person" or "/Person/"
      ("node", "Person", "person:abc")  for "/Person/person:abc"

    Fails fast on malformed paths and on unknown canonical types.
    """
    if not isinstance(path, str):
        raise TypeError(f"path must be a string, got {type(path).__name__}")
    if not path or path == "/":
        return "root", None, None
    if not path.startswith("/"):
        raise ValueError(f"path must start with '/': {path!r}")

    stripped = path[1:].rstrip("/")
    if "/" not in stripped:
        type_segment = stripped
        node_id_segment = None
    else:
        type_segment, _, rest = stripped.partition("/")
        if "/" in rest:
            raise ValueError(
                f"path has more than two segments: {path!r}; "
                "VFS tree is two-level (/{Type}/{node_id})"
            )
        node_id_segment = rest if rest else None

    if type_segment not in CANONICAL_NODE_TYPES:
        raise ValueError(
            f"unknown canonical type {type_segment!r} in path {path!r}; "
            f"valid: {sorted(CANONICAL_NODE_TYPES)}"
        )
    return ("node" if node_id_segment else "dir"), type_segment, node_id_segment


def _node_path(node_type: str, node_id: str) -> str:
    return f"/{node_type}/{node_id}"


class VFS:
    """Six path-style operations over the existing knowledge graph.

    Construction is cheap; safe to instantiate per request. Holds only a
    `GraphStore` reference — no caching, no internal state. Every
    operation method validates its arguments and raises on bad input
    (no `.get(default)`, no `try/except` swallowing).
    """

    def __init__(self, store: GraphStore) -> None:
        if not isinstance(store, GraphStore):
            raise TypeError(f"store must be GraphStore, got {type(store).__name__}")
        self._store = store

    # ------------------------------------------------------------------
    # ls
    # ------------------------------------------------------------------

    def ls(
        self,
        path: str = "/",
        *,
        limit: int = _DEFAULT_LIMIT,
        offset: int = 0,
    ) -> list[DirEntry]:
        """List the contents of a directory.

        * `/`           → one DirEntry per canonical type, with `child_count`.
        * `/{Type}/`    → DirEntries for each node of that type, paginated.
        * `/{Type}/{id}` → ValueError (it's a file; use `cat` or `stat`).
        """
        self._check_pagination(limit, offset)
        kind, type_segment, node_id_segment = _parse_path(path)
        if kind == "node":
            raise ValueError(
                f"path {path!r} is a node (file), not a directory; use `cat` or `stat`"
            )
        if kind == "root":
            return self._ls_root()
        assert type_segment is not None  # for the type checker
        return self._ls_type(type_segment, limit=limit, offset=offset)

    def _ls_root(self) -> list[DirEntry]:
        counts = self._counts_per_type()
        out: list[DirEntry] = []
        for ntype in sorted(CANONICAL_NODE_TYPES):
            out.append(
                DirEntry(
                    name=ntype,
                    path=f"/{ntype}/",
                    kind="dir",
                    type=ntype,
                    child_count=counts.get(ntype, 0),
                )
            )
        return out

    def _ls_type(
        self,
        node_type: str,
        *,
        limit: int,
        offset: int,
    ) -> list[DirEntry]:
        cypher = cast(
            LiteralString,
            "MATCH (n:Entity {type: $t}) "
            "RETURN n.id AS id, n.attributes_json AS attrs, "
            "       n.version AS version, n.updated_at AS updated_at "
            "ORDER BY n.id "
            "SKIP $skip LIMIT $limit",
        )
        with self._store._session() as s:
            rows = list(s.run(cypher, t=node_type, skip=offset, limit=limit))

        out: list[DirEntry] = []
        for r in rows:
            attrs = json.loads(r["attrs"])
            out.append(
                DirEntry(
                    name=r["id"],
                    path=_node_path(node_type, r["id"]),
                    kind="node",
                    type=node_type,
                    node_id=r["id"],
                    preview=_preview_attrs(attrs),
                    version=r["version"],
                    updated_at=_parse_iso_or_none(r["updated_at"]),
                )
            )
        return out

    # ------------------------------------------------------------------
    # cat
    # ------------------------------------------------------------------

    def cat(self, path: str) -> FileBody:
        """Read a file. Returns the canonical node view + grouped relations
        + the verbatim source records the node's facts came from.

        Raises:
          ValueError if `path` is the root or a directory.
          KeyError   if no node lives at `path`.
        """
        kind, node_type, node_id = _parse_path(path)
        if kind != "node":
            raise ValueError(
                f"path {path!r} is a directory; use `ls` or `tree` instead of `cat`"
            )
        assert node_type is not None and node_id is not None

        node = self._store.get_node(node_id)
        if node is None or node.type != node_type:
            raise KeyError(f"no node at {path!r}")

        relations = self._neighbors_grouped(node_id)
        raw_evidence = self._raw_evidence_for_provenance(node.provenance)

        provenance_response = [
            self._provenance_to_response(p) for p in node.provenance
        ]

        source_files = sorted({p.source_file for p in node.provenance})
        frontmatter: dict[str, Any] = {
            "id": node.id,
            "type": node.type,
            "version": node.version,
            "created_at": node.created_at.isoformat() if node.created_at else None,
            "updated_at": node.updated_at.isoformat() if node.updated_at else None,
            "vfs_path": path,
            "source_files": source_files,
            "provenance_count": len(node.provenance),
        }

        return FileBody(
            path=path,
            frontmatter=frontmatter,
            attributes=node.attributes,
            relations=relations,
            provenance=provenance_response,
            raw_evidence=raw_evidence,
        )

    # ------------------------------------------------------------------
    # stat
    # ------------------------------------------------------------------

    def stat(self, path: str) -> StatInfo:
        """Metadata-only projection. No neighbor fetch, no raw-record join."""
        kind, node_type, node_id = _parse_path(path)
        if kind == "root":
            counts = self._counts_per_type()
            return StatInfo(
                path="/",
                kind="dir",
                type=None,
                child_count=sum(counts.values()),
            )
        if kind == "dir":
            assert node_type is not None
            count = self._counts_per_type().get(node_type, 0)
            return StatInfo(
                path=f"/{node_type}/",
                kind="dir",
                type=node_type,
                child_count=count,
            )
        assert node_type is not None and node_id is not None
        node = self._store.get_node(node_id)
        if node is None or node.type != node_type:
            raise KeyError(f"no node at {path!r}")
        source_files = sorted({p.source_file for p in node.provenance})
        return StatInfo(
            path=path,
            kind="node",
            type=node.type,
            node_id=node.id,
            version=node.version,
            created_at=node.created_at,
            updated_at=node.updated_at,
            source_files=source_files,
            provenance_count=len(node.provenance),
        )

    # ------------------------------------------------------------------
    # tree
    # ------------------------------------------------------------------

    def tree(self, path: str = "/", depth: int = 1) -> TreeNode:
        """Recursive directory listing capped at `depth`.

        Useful at the root: `depth=1` lists canonical types with counts;
        `depth=2` expands one level into node ids per type. The tree is
        flat below `/{Type}/`, so depths beyond 2 are no-ops.
        """
        if not isinstance(depth, int) or not (1 <= depth <= _MAX_TREE_DEPTH):
            raise ValueError(f"depth must be int in [1, {_MAX_TREE_DEPTH}], got {depth!r}")
        kind, node_type, node_id = _parse_path(path)

        if kind == "node":
            assert node_type is not None and node_id is not None
            node = self._store.get_node(node_id)
            if node is None or node.type != node_type:
                raise KeyError(f"no node at {path!r}")
            return TreeNode(
                name=node_id,
                path=path,
                kind="node",
                type=node_type,
                node_id=node_id,
            )

        if kind == "dir":
            assert node_type is not None
            count = self._counts_per_type().get(node_type, 0)
            children: list[TreeNode] = []
            if depth >= 1:
                for entry in self._ls_type(node_type, limit=_MAX_LIMIT, offset=0):
                    children.append(
                        TreeNode(
                            name=entry.name,
                            path=entry.path,
                            kind="node",
                            type=node_type,
                            node_id=entry.node_id,
                        )
                    )
            return TreeNode(
                name=node_type,
                path=f"/{node_type}/",
                kind="dir",
                type=node_type,
                child_count=count,
                children=children,
            )

        # root
        counts = self._counts_per_type()
        type_children: list[TreeNode] = []
        for ntype in sorted(CANONICAL_NODE_TYPES):
            sub_children: list[TreeNode] = []
            if depth >= 2:
                for entry in self._ls_type(ntype, limit=_MAX_LIMIT, offset=0):
                    sub_children.append(
                        TreeNode(
                            name=entry.name,
                            path=entry.path,
                            kind="node",
                            type=ntype,
                            node_id=entry.node_id,
                        )
                    )
            type_children.append(
                TreeNode(
                    name=ntype,
                    path=f"/{ntype}/",
                    kind="dir",
                    type=ntype,
                    child_count=counts.get(ntype, 0),
                    children=sub_children,
                )
            )
        return TreeNode(
            name="/",
            path="/",
            kind="dir",
            type=None,
            child_count=sum(counts.values()),
            children=type_children,
        )

    # ------------------------------------------------------------------
    # grep
    # ------------------------------------------------------------------

    def grep(
        self,
        query: str,
        path: str = "/",
        *,
        limit: int = _DEFAULT_LIMIT,
    ) -> list[GrepHit]:
        """Lucene fulltext over `:Entity.attributes_json`, scoped to a
        canonical-type bucket if `path` is `/{Type}/`. Wraps the same
        `node_text` index used by ExactTier and HybridTier.
        """
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be a non-empty string")
        self._check_pagination(limit, offset=0)
        kind, node_type, _ = _parse_path(path)
        if kind == "node":
            raise ValueError(
                f"grep path {path!r} must be a directory ('/' or '/{{Type}}/')"
            )

        escaped = _escape_lucene(query)
        if kind == "dir":
            assert node_type is not None
            cypher = cast(
                LiteralString,
                "CALL db.index.fulltext.queryNodes('node_text', $q) "
                "YIELD node, score "
                "WHERE node:Entity AND node.type = $t "
                "RETURN node.id AS id, node.type AS type, "
                "       node.attributes_json AS attrs, score "
                "LIMIT $limit",
            )
            with self._store._session() as s:
                rows = list(s.run(cypher, q=escaped, t=node_type, limit=limit))
        else:
            cypher = cast(
                LiteralString,
                "CALL db.index.fulltext.queryNodes('node_text', $q) "
                "YIELD node, score "
                "WHERE node:Entity "
                "RETURN node.id AS id, node.type AS type, "
                "       node.attributes_json AS attrs, score "
                "LIMIT $limit",
            )
            with self._store._session() as s:
                rows = list(s.run(cypher, q=escaped, limit=limit))

        out: list[GrepHit] = []
        for r in rows:
            attrs = json.loads(r["attrs"])
            out.append(
                GrepHit(
                    path=_node_path(r["type"], r["id"]),
                    node_id=r["id"],
                    type=r["type"],
                    score=_normalize_bm25(float(r["score"])),
                    preview=_preview_attrs(attrs),
                )
            )
        return out

    # ------------------------------------------------------------------
    # find
    # ------------------------------------------------------------------

    def find(
        self,
        path: str = "/",
        *,
        where: dict[str, Any] | None = None,
        modified_after: str | None = None,
        limit: int = _DEFAULT_LIMIT,
    ) -> list[DirEntry]:
        """Filter the directory at `path` by attribute equality and/or
        update time. The attribute filter is applied Python-side after a
        bounded Cypher slice — avoids an apoc dependency at the cost of
        not being usable as the *only* filter on a 1M-row type. Pair it
        with a type prefix to keep the slice sane.
        """
        self._check_pagination(limit, offset=0)
        kind, node_type, _ = _parse_path(path)
        if kind == "node":
            raise ValueError(
                f"find path {path!r} must be a directory ('/' or '/{{Type}}/')"
            )
        if where is not None and not isinstance(where, dict):
            raise TypeError(f"where must be dict[str, Any] | None, got {type(where).__name__}")

        # Pull a generous slice, then filter. The cap is `_MAX_LIMIT * 4`
        # so the after-filter result can still saturate `limit` for
        # selective predicates.
        scan_cypher: LiteralString
        params: dict[str, Any] = {"scan_cap": _MAX_LIMIT * 4}
        if kind == "dir":
            assert node_type is not None
            scan_cypher = cast(
                LiteralString,
                "MATCH (n:Entity {type: $t}) "
                "RETURN n.id AS id, n.type AS type, "
                "       n.attributes_json AS attrs, n.version AS version, "
                "       n.updated_at AS updated_at "
                "ORDER BY n.updated_at DESC LIMIT $scan_cap",
            )
            params["t"] = node_type
        else:
            scan_cypher = cast(
                LiteralString,
                "MATCH (n:Entity) "
                "RETURN n.id AS id, n.type AS type, "
                "       n.attributes_json AS attrs, n.version AS version, "
                "       n.updated_at AS updated_at "
                "ORDER BY n.updated_at DESC LIMIT $scan_cap",
            )

        with self._store._session() as s:
            rows = list(s.run(scan_cypher, **params))

        out: list[DirEntry] = []
        for r in rows:
            if len(out) >= limit:
                break
            attrs = json.loads(r["attrs"])
            if where is not None and not _matches_where(attrs, where):
                continue
            updated_at = _parse_iso_or_none(r["updated_at"])
            if modified_after is not None:
                cutoff = _parse_iso_or_none(modified_after)
                if cutoff is None:
                    raise ValueError(
                        f"modified_after must be ISO-8601, got {modified_after!r}"
                    )
                if updated_at is None or updated_at < cutoff:
                    continue
            out.append(
                DirEntry(
                    name=r["id"],
                    path=_node_path(r["type"], r["id"]),
                    kind="node",
                    type=r["type"],
                    node_id=r["id"],
                    preview=_preview_attrs(attrs),
                    version=r["version"],
                    updated_at=updated_at,
                )
            )
        return out

    # ------------------------------------------------------------------
    # internal helpers
    # ------------------------------------------------------------------

    def _counts_per_type(self) -> dict[str, int]:
        cypher: LiteralString = (
            "MATCH (n:Entity) RETURN n.type AS t, count(*) AS c"
        )
        with self._store._session() as s:
            rows = list(s.run(cypher))
        return {r["t"]: r["c"] for r in rows if r["t"] is not None}

    def _neighbors_grouped(self, node_id: str) -> dict[str, list[NeighborRef]]:
        cypher: LiteralString = (
            "MATCH (n:Entity {id: $id})-[r]-(m:Entity) "
            "RETURN m.id AS mid, m.type AS mtype, m.attributes_json AS mattrs, "
            "       type(r) AS rt, r.id AS eid, startNode(r).id AS src"
        )
        with self._store._session() as s:
            rows = list(s.run(cypher, id=node_id))

        grouped: dict[str, list[NeighborRef]] = defaultdict(list)
        for r in rows:
            rel_type = r["rt"]
            if len(grouped[rel_type]) >= _MAX_NEIGHBORS_PER_RELATION:
                # Cap fanout per relation to keep `cat` payloads bounded —
                # a hub node (e.g. a CEO with 200 reports) would otherwise
                # blow the LLM context window in one call.
                continue
            attrs = json.loads(r["mattrs"])
            direction = "out" if r["src"] == node_id else "in"
            grouped[rel_type].append(
                NeighborRef(
                    node_id=r["mid"],
                    type=r["mtype"],
                    preview=_preview_attrs(attrs),
                    direction=direction,
                    edge_id=r["eid"] or "",
                    relation_type=rel_type,
                )
            )
        return dict(grouped)

    def _raw_evidence_for_provenance(
        self, provenance: Iterable[Any]
    ) -> list[SourceRecordResponse]:
        """Pull each unique source record referenced by this node's
        provenance. One SQLite read per (source_file, source_record_id)
        — the dedup happens before the read.
        """
        seen: set[tuple[str, str]] = set()
        out: list[SourceRecordResponse] = []
        for p in provenance:
            key = (p.source_file, p.source_record_id)
            if key in seen:
                continue
            seen.add(key)
            rec = self._store.get_source_record(p.source_file, p.source_record_id)
            if rec is None:
                # Provenance row pointing at a missing source record indicates
                # control-plane corruption (FK constraint says it can't
                # happen for non-human sources). Surface loudly.
                raise RuntimeError(
                    f"provenance references missing source record: "
                    f"{p.source_file!r} / {p.source_record_id!r}"
                )
            out.append(
                SourceRecordResponse(
                    source_file=rec.source_file,
                    source_record_id=rec.source_record_id,
                    raw_record=rec.raw_record,
                    content_hash=rec.content_hash,
                    ingested_at=rec.ingested_at,
                )
            )
        return out

    @staticmethod
    def _provenance_to_response(p: Any) -> ProvenanceResponse:
        return ProvenanceResponse(**p.to_dict())

    @staticmethod
    def _check_pagination(limit: int, offset: int) -> None:
        if not isinstance(limit, int) or not (1 <= limit <= _MAX_LIMIT):
            raise ValueError(f"limit must be int in [1, {_MAX_LIMIT}], got {limit!r}")
        if not isinstance(offset, int) or offset < 0:
            raise ValueError(f"offset must be int >= 0, got {offset!r}")


def _matches_where(attrs: dict[str, Any], where: dict[str, Any]) -> bool:
    """Equality match on top-level attributes. Numeric / string compare;
    a value of `None` in `where` matches both missing keys and explicit
    None. Nested-attribute matching is intentionally out of scope (use
    `pattern_query` or a workflow if you need it)."""
    for key, expected in where.items():
        actual = attrs.get(key)
        if actual != expected:
            return False
    return True


def _parse_iso_or_none(s: Any) -> datetime | None:
    """Best-effort ISO-8601 parse. Empty string and `None` both yield `None`;
    a non-string sentinel from a legacy row also yields `None`. Anything that
    *looks* like a datetime (str) but is malformed raises — fail-fast on
    real corruption, tolerate the documented null-marker conventions.
    """
    if s is None or s == "":
        return None
    if not isinstance(s, str):
        raise TypeError(f"expected ISO-8601 string or null marker, got {type(s).__name__}")
    return datetime.fromisoformat(s)
