"""REST API for the Better Context knowledge graph.

Joins Neo4j (graph + content) with SQLite (provenance + raw data) into
unified JSON responses. All response models are Pydantic v2 so the
generated OpenAPI spec is the single source of truth for frontend types.

Start with:  uv run uvicorn backend.api.app:app --reload --port 8000
Docs at:     http://localhost:8000/docs
"""
from __future__ import annotations

import os
import tempfile
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import backend.config as cfg
from backend.graph.store import GraphStore, parse_pattern
from backend.ingest import Ingestor, IngestStore, MappingSpec
from backend.ingest.ingestor import RecordError
from backend.models.graph import GraphEdge, GraphNode, Provenance
from backend.retrieval import (
    CascadeOrchestrator,
    ContextEngine,
    QueryResult,
    StubTier,
    TierConfig,
    build_orchestrator_with_store,
)
from backend.retrieval.workflows import (
    WorkflowInput,
    WorkflowResult,
    build_workflow,
    list_workflows,
    register_builtin_workflows,
)

from backend.conflict import (
    Conflict,
    ConflictListResponse,
    ResolveConflictRequest,
)

from .models import (
    EdgeResponse,
    EditNodeRequest,
    NeighborsResponse,
    NodeListResponse,
    NodeResponse,
    PathResponse,
    PatternMatch,
    PatternQueryRequest,
    PatternQueryResponse,
    ProvenanceResponse,
    QueryRequest,
    SourceRecordResponse,
    SourceUpdateResponse,
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
        valid_from=e.valid_from,
        valid_to=e.valid_to,
        version=e.version,
    )


def _build_default_engine() -> ContextEngine:
    """Stub-only engine for unit tests that bypass the live store.

    Production wiring uses `_build_engine_with_store` from the lifespan,
    which adds `ExactTier` (R1) as tier 0. This builder remains as a
    storeless fallback so `/api/query` can be exercised in tests that
    mock the GraphStore dependency.
    """
    tier = StubTier(name="stub")
    orch = CascadeOrchestrator(
        tiers=[tier],
        configs=[TierConfig(name="stub", escalate_below=0.0)],
    )
    return ContextEngine(orch)


def _build_engine_with_store(store: GraphStore) -> ContextEngine:
    """Cascade `[ExactTier, HybridTier, StubTier]` — the live `/api/query` engine."""
    return ContextEngine(build_orchestrator_with_store(store))


def _build_llm_from_env() -> object:
    """Mirror the QONTEXT_AGENTIC selection in build_orchestrator_with_store
    so workflows that need an LLM (thread-summary, customer-email) get the
    same backend the cascade uses.
    """
    from backend.retrieval.agentic import GeminiLLMClient, NoopLLMClient

    kind = os.environ.get("QONTEXT_AGENTIC", "noop").lower()
    if kind == "gemini":
        return GeminiLLMClient()
    if kind == "noop":
        return NoopLLMClient(text="workflow LLM not configured (set QONTEXT_AGENTIC=gemini)")
    raise ValueError(f"QONTEXT_AGENTIC must be 'gemini' or 'noop', got {kind!r}")


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
    # Built-in workflows must be registered before any /api/workflow
    # request lands; explicit call replaces the previous import-side-
    # effect registration in `workflows/__init__.py`.
    register_builtin_workflows()
    # The push-mode source-update endpoint reuses the existing Ingestor;
    # `llm_client=None` is safe because `apply_record` only invokes LLM
    # blocks when the spec declares them, and our active specs don't.
    ingest_store = IngestStore(store._conn)
    ingestor = Ingestor(store, ingest_store, llm_client=None)
    app.state.store = store
    app.state.ingest_store = ingest_store
    app.state.ingestor = ingestor
    app.state.llm = _build_llm_from_env()
    app.state.context_engine = _build_engine_with_store(store)
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


def get_context_engine(request: Request) -> ContextEngine:
    return request.app.state.context_engine  # type: ignore[no-any-return]


def get_llm(request: Request) -> object:
    return request.app.state.llm  # type: ignore[no-any-return]


def get_ingestor(request: Request) -> Ingestor:
    return request.app.state.ingestor  # type: ignore[no-any-return]


def get_ingest_store(request: Request) -> IngestStore:
    return request.app.state.ingest_store  # type: ignore[no-any-return]


# ---------- Retrieval API ----------


@app.post("/api/query", response_model=QueryResult)
async def query(
    body: QueryRequest,
    engine: ContextEngine = Depends(get_context_engine),
) -> QueryResult:
    if not body.query or not body.query.strip():
        raise HTTPException(400, "query must be a non-empty, non-whitespace string")
    try:
        return engine.query(body.query, body.context)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


# ---------- Workflow API ----------


@app.get("/api/workflow")
async def list_workflows_endpoint() -> dict[str, list[str]]:
    """Discovery endpoint — names of every registered workflow."""
    return {"workflows": list_workflows()}


@app.post("/api/workflow/{name}", response_model=WorkflowResult)
async def run_workflow(
    name: str,
    body: WorkflowInput,
    engine: ContextEngine = Depends(get_context_engine),
    store: GraphStore = Depends(get_store),
    llm: object = Depends(get_llm),
) -> WorkflowResult:
    """Invoke a registered workflow by name.

    * 404 — workflow `name` is not registered.
    * 422 — `body` is not a valid `WorkflowInput` (handled by FastAPI).
    * 400 — workflow's `run()` raised on its payload validation
      (`ValueError` / `TypeError`).
    * 200 — `WorkflowResult` JSON.
    """
    try:
        wf = build_workflow(name, engine.tiers_by_name, llm=llm, store=store)
    except KeyError as e:
        raise HTTPException(404, str(e)) from e
    except ValueError as e:
        # Engine missing a tier the workflow requires — a deployment bug,
        # not a client bug; surface it as 500 so it's noticed.
        raise HTTPException(500, str(e)) from e
    try:
        return wf.run(body)
    except (ValueError, TypeError) as e:
        raise HTTPException(400, str(e)) from e


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


# ---------- Pattern Query API ----------


@app.post("/api/graph/query", response_model=PatternQueryResponse)
async def pattern_query(
    body: PatternQueryRequest,
    store: GraphStore = Depends(get_store),
) -> PatternQueryResponse:
    try:
        src_type, rel_type, tgt_type = parse_pattern(body.pattern)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    matches, total = store.pattern_query(
        src_type, rel_type, tgt_type,
        limit=body.limit, offset=body.offset,
    )
    return PatternQueryResponse(
        pattern=body.pattern,
        matches=[
            PatternMatch(source=_node(s), edge=_edge(e), target=_node(t))
            for s, e, t in matches
        ],
        total=total,
    )


# ---------- Edit API ----------


@app.put("/api/graph/node/{node_id}", response_model=NodeResponse)
async def edit_node(
    node_id: str,
    body: EditNodeRequest,
    store: GraphStore = Depends(get_store),
) -> NodeResponse:
    if not body.attributes:
        raise HTTPException(400, "attributes must not be empty")
    try:
        node = store.edit_node(node_id, body.attributes, body.editor)
    except KeyError:
        raise HTTPException(404, f"node {node_id!r} not found") from None
    return _node(node)


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


@app.post("/api/source/{source_file:path}/{record_id}", response_model=SourceUpdateResponse)
async def push_source_update(
    source_file: str,
    record_id: str,
    raw_record: dict,
    store: GraphStore = Depends(get_store),
    ingestor: Ingestor = Depends(get_ingestor),
    ingest_store: IngestStore = Depends(get_ingest_store),
) -> SourceUpdateResponse:
    """Push-mode source update: apply a corrected/new raw record from a
    known source-of-truth and re-fire the active spec for just that record.

    The request body is the new raw JSON record (vendor-shape). The active
    `MappingSpec` for `source_file` is looked up; the spec's `id_template`
    must render to `record_id` (URL path) — mismatches are 400.

    Conflict detection at `add_node` runs as usual; the response surfaces
    every conflict CURRENTLY OPEN on the touched nodes (not just the ones
    that just opened) so callers can see at a glance whether their update
    landed on a node with unresolved disagreement.

    Idempotent on `(spec_version, source_file, record_id, content_hash)`:
    replaying the same body returns `{skipped: true, content_changed: false}`
    without touching the graph.
    """
    try:
        spec_row = ingest_store.find_active_spec_by_pattern(source_file)
    except ValueError as e:
        raise HTTPException(409, str(e)) from None
    if spec_row is None:
        raise HTTPException(
            404,
            f"no active spec for source_file={source_file!r}; "
            f"onboard + promote it first via `python -m backend.ingest`",
        )

    spec = MappingSpec.from_yaml(spec_row["yaml_text"])

    try:
        report = ingestor.apply_record(spec, raw_record, expected_record_id=record_id)
    except RecordError as e:
        raise HTTPException(400, str(e)) from None

    open_conflicts = []
    for node_id in report.nodes_touched:
        open_conflicts.extend(store.conflicts.list(node_id=node_id, status="open"))

    return SourceUpdateResponse(
        source_file=source_file,
        source_record_id=report.source_record_id,
        spec_version=spec.spec_version,
        content_changed=report.content_changed,
        skipped=report.skipped,
        nodes_touched=report.nodes_touched,
        conflicts_open=open_conflicts,
    )


# ---------- Conflict resolution API ----------


@app.get("/api/conflicts", response_model=ConflictListResponse)
async def list_conflicts(
    status: str = Query("open", pattern="^(open|resolved)$"),
    node_id: str | None = Query(None),
    attribute: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    store: GraphStore = Depends(get_store),
) -> ConflictListResponse:
    rows = list(store.conflicts.list(
        status=status,                          # type: ignore[arg-type]
        node_id=node_id, attribute=attribute,
        limit=limit, offset=offset,
    ))
    return ConflictListResponse(conflicts=rows, status=status, total=len(rows))  # type: ignore[arg-type]


@app.get("/api/conflicts/{conflict_id}", response_model=Conflict)
async def get_conflict(
    conflict_id: int,
    store: GraphStore = Depends(get_store),
) -> Conflict:
    c = store.conflicts.get(conflict_id)
    if c is None:
        raise HTTPException(404, f"conflict {conflict_id} not found")
    return c


@app.post("/api/conflicts/{conflict_id}/resolve", response_model=Conflict)
async def resolve_conflict(
    conflict_id: int,
    body: ResolveConflictRequest,
    store: GraphStore = Depends(get_store),
) -> Conflict:
    try:
        return store.resolve_conflict(conflict_id, value=body.value, editor=body.editor)
    except KeyError as e:
        raise HTTPException(404, str(e)) from None
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


# ---------- Onboarding API ----------


class OnboardRequest(BaseModel):
    tenant: str
    source_format: str | None = None  # json | csv | jsonl — auto-detected if omitted
    record_path: str = "$[*]"
    sample_size: int = 20


class OnboardResponse(BaseModel):
    spec_id: int | None = None
    tenant: str
    source_pattern: str
    spec_version: int
    status: str  # "draft"
    yaml_text: str
    node_types: list[str]
    edge_types: list[str]


class PromoteRequest(BaseModel):
    editor: str | None = None  # for audit trail only


@app.post("/api/onboard", response_model=OnboardResponse)
async def onboard_source(
    source_file: UploadFile = File(...),
    tenant: str = Form("default"),
    source_format: str | None = Form(None),
    record_path: str = Form("$[*]"),
    sample_size: int = Form(20),
    store: GraphStore = Depends(get_store),
) -> OnboardResponse:
    """Accept an uploaded file + metadata, run the LLM onboarder, return the draft spec.

    The file is written to a temp path so Onboarder can stat + sample it normally.
    The draft is saved to SQLite (status='draft') — call POST /api/onboard/{spec_id}/promote
    to activate it.
    """
    from backend.ingest.llm import GeminiClient
    from backend.ingest.onboard import Onboarder, OnboardError
    from backend.ingest.spec import MappingSpec as _MappingSpec

    ingest_store = IngestStore(store._conn)
    tmp_dir = tempfile.mkdtemp()
    try:
        suffix = Path(source_file.filename or "upload.json").suffix
        tmp_path = Path(tmp_dir) / (source_file.filename or f"upload{suffix}")
        content = await source_file.read()
        tmp_path.write_bytes(content)

        gemini = GeminiClient(ingest_store)
        onboarder = Onboarder(gemini, ingest_store, sample_size=sample_size)

        try:
            spec = onboarder.draft_spec(
                tmp_path,
                tenant=tenant,
                source_format=source_format,
                record_path=record_path,
            )
        except OnboardError as e:
            raise HTTPException(422, str(e)) from e

        saved = ingest_store.get_spec(
            tenant,
            spec.source.file_pattern,
            spec.spec_version,
        )
        return OnboardResponse(
            spec_id=saved["rowid"] if saved and "rowid" in saved else None,
            tenant=spec.tenant,
            source_pattern=spec.source.file_pattern,
            spec_version=spec.spec_version,
            status="draft",
            yaml_text=spec.to_yaml(),
            node_types=[spec.resolved_node_type(n) for n in spec.nodes],
            edge_types=[e.canonical_type for e in spec.edges],
        )
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@app.post("/api/onboard/{spec_id}/promote", response_model=OnboardResponse)
async def promote_spec(
    spec_id: int,
    body: PromoteRequest,
    store: GraphStore = Depends(get_store),
) -> OnboardResponse:
    """Promote a draft spec to active so the ingestor will use it."""
    from backend.ingest.spec import MappingSpec as _MappingSpec

    ingest_store = IngestStore(store._conn)
    saved = ingest_store.get_spec_by_rowid(spec_id)
    if saved is None:
        raise HTTPException(404, f"spec {spec_id} not found")
    ingest_store.set_spec_status_by_rowid(spec_id, "active")
    saved = ingest_store.get_spec_by_rowid(spec_id)
    spec = _MappingSpec.from_yaml(saved["yaml_text"])
    return OnboardResponse(
        spec_id=spec_id,
        tenant=spec.tenant,
        source_pattern=spec.source.file_pattern,
        spec_version=spec.spec_version,
        status="active",
        yaml_text=saved["yaml_text"],
        node_types=[spec.resolved_node_type(n) for n in spec.nodes],
        edge_types=[e.canonical_type for e in spec.edges],
    )

