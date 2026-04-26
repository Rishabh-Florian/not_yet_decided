"""Pydantic v2 result models for the VFS tool surface.

Every field crossing a process boundary (Gemini function-call result, MCP
tool response) is typed here. `ProvenanceResponse` and
`SourceRecordResponse` are imported from `backend.models.responses` —
the shared leaf module both `backend.api.models` and this file consume —
so the canonical response shapes are defined once.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from backend.models.responses import ProvenanceResponse, SourceRecordResponse


class DirEntry(BaseModel):
    """One entry returned by `ls` or `find`.

    `kind="dir"` rows describe a canonical type bucket (their `path` ends
    in `/`). `kind="node"` rows describe an actual graph node — `node_id`
    and `type` are populated, `child_count` is null.
    """

    name: str
    path: str
    kind: Literal["dir", "node"]
    type: str | None = Field(
        default=None,
        description="Canonical type. For dir entries this is the type the "
        "directory holds; for node entries it is the node's own type.",
    )
    node_id: str | None = None
    child_count: int | None = Field(
        default=None,
        description="Number of nodes inside a directory. Null for node entries.",
    )
    preview: str | None = Field(
        default=None,
        description="One-line preview of a node's attributes. Null for dir entries.",
    )
    version: int | None = None
    updated_at: datetime | None = None


class NeighborRef(BaseModel):
    """One neighbor of a node, grouped under a relation type by `FileBody`."""

    node_id: str
    type: str
    preview: str
    direction: Literal["out", "in"]
    edge_id: str
    relation_type: str


class FileBody(BaseModel):
    """Full payload returned by `cat`.

    Three sections, all derived from the graph at request time:

    * `frontmatter` — the bookkeeping a human or agent needs to trust the
      file: id, type, version, sources, last update.
    * `attributes` + `relations` + `provenance` — the canonical view of
      the node from the graph.
    * `raw_evidence` — the verbatim source records the canonical attrs
      were extracted from. One entry per unique (source_file,
      source_record_id) referenced by this node's provenance.
    """

    path: str
    frontmatter: dict[str, Any]
    attributes: dict[str, Any]
    relations: dict[str, list[NeighborRef]]
    provenance: list[ProvenanceResponse]
    raw_evidence: list[SourceRecordResponse]


class StatInfo(BaseModel):
    """Metadata-only view returned by `stat` — no neighbor or raw-record join."""

    path: str
    kind: Literal["dir", "node"]
    type: str | None = None
    node_id: str | None = None
    version: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    source_files: list[str] = Field(default_factory=list)
    provenance_count: int | None = None
    child_count: int | None = None


class GrepHit(BaseModel):
    """One fulltext match returned by `grep`."""

    path: str
    node_id: str
    type: str
    score: float = Field(
        ...,
        description="BM25-similar score normalized to [0, 1) via score / (1 + score) "
        "— mirrors ExactTier's fulltext arm.",
    )
    preview: str


class TreeNode(BaseModel):
    """One node in the recursive listing returned by `tree`.

    `tree` is mostly useful at the root: depth=1 returns the canonical
    types with their node counts; depth=2 expands one level into node
    ids. Beyond depth 2 there is nothing to expand — the path tree is
    flat below `/{Type}/`.
    """

    name: str
    path: str
    kind: Literal["dir", "node"]
    type: str | None = None
    node_id: str | None = None
    child_count: int | None = None
    children: list["TreeNode"] = Field(default_factory=list)


# Required for the self-reference on TreeNode.children under Pydantic v2.
TreeNode.model_rebuild()
