"""REST API for the Better Context knowledge graph.

Joins Neo4j (graph + content) with SQLite (provenance + raw data) into
unified JSON responses. All response models are Pydantic v2 so the
generated OpenAPI spec is the single source of truth for frontend types.

Start with:  uv run uvicorn backend.api.app:app --reload --port 8000
Docs at:     http://localhost:8000/docs
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware

import backend.config as cfg
from backend.graph.store import GraphStore
from backend.models.graph import GraphEdge, GraphNode, Provenance

from .models import (
    EdgeResponse,
    NeighborsResponse,
    NodeListResponse,
    NodeResponse,
    PathResponse,
    ProvenanceResponse,
    SourceRecordResponse,
    StatsResponse,
)


def _prov(p: Provenance) -> ProvenanceResponse:
    return ProvenanceResponse(**p.to_dict())


def _node(n: GraphNode) -> NodeResponse:
    return NodeResponse(
        id=n.id,
        type=n.type,
        attributes=n.attributes,
        provenance=[_prov(p) for p in n.provenance],
        confidence=n.confidence,
        vfs_path=n.vfs_path,
        created_at=n.created_at,
        updated_at=n.updated_at,
        version=n.version,
    )


def _edge(e: GraphEdge) -> EdgeResponse:
    return EdgeResponse(
        id=e.id,
        source_node_id=e.source_node_id,
        target_node_id=e.target_node_id,
        relation_type=e.relation_type,
        attributes=e.attributes,
        provenance=[_prov(p) for p in e.provenance],
        confidence=e.confidence,
        valid_from=e.valid_from,
        valid_to=e.valid_to,
        version=e.version,
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    db_path = os.environ.get("SQLITE_DB", "data/better_context.sqlite")
    store = GraphStore(
        db_path=db_path,
        neo4j_uri=cfg.NEO4J_URI,
        neo4j_user=cfg.NEO4J_USER,
        neo4j_password=cfg.NEO4J_PASSWORD,
        neo4j_database=cfg.NEO4J_DATABASE,
    )
    app.state.store = store
    yield
    store.close()


app = FastAPI(
    title="Better Context API",
    description="REST API for the enterprise knowledge graph with fact-level provenance",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_store(request: Request) -> GraphStore:
    return request.app.state.store  # type: ignore[no-any-return]


# ---------- Graph API ----------


@app.get("/api/graph/node/{node_id}/neighbors", response_model=NeighborsResponse)
async def get_neighbors(
    node_id: str,
    relation_type: str | None = Query(None),
    depth: int = Query(1, ge=1, le=10),
    store: GraphStore = Depends(get_store),
) -> NeighborsResponse:
    if store.get_node(node_id) is None:
        raise HTTPException(404, f"node {node_id!r} not found")
    neighbor_ids = store.neighbors(node_id, relation_type, depth)
    neighbors = []
    for nid in sorted(neighbor_ids):
        n = store.get_node(nid)
        if n is not None:
            neighbors.append(_node(n))
    return NeighborsResponse(node_id=node_id, neighbors=neighbors)


@app.get("/api/graph/node/{node_id}", response_model=NodeResponse)
async def get_node(
    node_id: str,
    store: GraphStore = Depends(get_store),
) -> NodeResponse:
    node = store.get_node(node_id)
    if node is None:
        raise HTTPException(404, f"node {node_id!r} not found")
    return _node(node)


@app.get("/api/graph/nodes", response_model=NodeListResponse)
async def list_nodes(
    type: str = Query(..., description="Canonical node type, e.g. Person, Message"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    store: GraphStore = Depends(get_store),
) -> NodeListResponse:
    all_nodes = list(store.nodes_by_type(type))
    total = len(all_nodes)
    page = all_nodes[offset : offset + limit]
    return NodeListResponse(
        nodes=[_node(n) for n in page],
        total=total,
        node_type=type,
    )


@app.get("/api/graph/edge/{edge_id}", response_model=EdgeResponse)
async def get_edge(
    edge_id: str,
    store: GraphStore = Depends(get_store),
) -> EdgeResponse:
    edge = store.get_edge(edge_id)
    if edge is None:
        raise HTTPException(404, f"edge {edge_id!r} not found")
    return _edge(edge)


@app.get("/api/graph/path", response_model=PathResponse)
async def shortest_path(
    from_id: str = Query(..., alias="from", description="Source node id"),
    to_id: str = Query(..., alias="to", description="Target node id"),
    max_hops: int = Query(6, ge=1, le=20),
    store: GraphStore = Depends(get_store),
) -> PathResponse:
    path = store.shortest_path(from_id, to_id, max_hops)
    if path is None:
        raise HTTPException(404, "no path found between the given nodes")
    return PathResponse(path=path, length=len(path) - 1)


@app.get("/api/graph/stats", response_model=StatsResponse)
async def graph_stats(
    store: GraphStore = Depends(get_store),
) -> StatsResponse:
    return StatsResponse(**store.stats())


# ---------- Source record API ----------


@app.get("/api/source/{source_file:path}/{record_id}", response_model=SourceRecordResponse)
async def get_source_record(
    source_file: str,
    record_id: str,
    store: GraphStore = Depends(get_store),
) -> SourceRecordResponse:
    rec = store.get_source_record(source_file, record_id)
    if rec is None:
        raise HTTPException(404, f"source record not found: {source_file}/{record_id}")
    return SourceRecordResponse(
        source_file=rec.source_file,
        source_record_id=rec.source_record_id,
        raw_record=rec.raw_record,
        content_hash=rec.content_hash,
        ingested_at=rec.ingested_at,
    )
