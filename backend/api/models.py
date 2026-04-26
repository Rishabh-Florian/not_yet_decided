from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel

from backend.models.responses import ProvenanceResponse, SourceRecordResponse
from backend.retrieval.models import QueryContext

# Re-exported for callers that import these shapes from the API surface.
__all__ = [
    "ProvenanceResponse",
    "SourceRecordResponse",
    # plus everything defined below
]


class NodeResponse(BaseModel):
    id: str
    type: str
    attributes: dict[str, Any]
    provenance: list[ProvenanceResponse]
    vfs_path: str
    created_at: datetime | None = None
    updated_at: datetime | None = None
    version: int


class EdgeResponse(BaseModel):
    id: str
    source_node_id: str
    target_node_id: str
    relation_type: str
    attributes: dict[str, Any]
    provenance: list[ProvenanceResponse]
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    version: int


class NeighborsResponse(BaseModel):
    node_id: str
    neighbors: list[NodeResponse]


class PathResponse(BaseModel):
    path: list[str]
    length: int


class NodeListResponse(BaseModel):
    nodes: list[NodeResponse]
    total: int
    node_type: str


class StatsResponse(BaseModel):
    graph: dict[str, Any]
    traces: dict[str, Any]
    raw: dict[str, Any]


class PatternQueryRequest(BaseModel):
    pattern: str
    limit: int = 50
    offset: int = 0


class PatternMatch(BaseModel):
    source: NodeResponse
    edge: EdgeResponse
    target: NodeResponse


class PatternQueryResponse(BaseModel):
    pattern: str
    matches: list[PatternMatch]
    total: int


class EditNodeRequest(BaseModel):
    attributes: dict[str, Any]
    editor: str


class QueryRequest(BaseModel):
    """Request body for `POST /api/query` — the retrieval cascade entrypoint."""

    query: str
    context: QueryContext | None = None
