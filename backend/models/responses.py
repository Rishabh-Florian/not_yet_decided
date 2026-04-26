"""Shared HTTP/tool response shapes.

Pure-leaf module with no retrieval-package or API-package imports — so it
can be consumed by `backend.api.models` (FastAPI responses) and by
`backend.vfs.models` (Gemini tool result shapes) without forming a
circular dependency through `backend.retrieval`.

Anything that needs to be Pydantic-validated at a process boundary AND
shared across the API + tool surfaces lives here.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel

from backend.models.graph import FactConfidence


class ProvenanceResponse(BaseModel):
    source_file: str
    source_record_id: str
    source_field: str
    extraction_method: str
    extraction_model: str
    confidence: FactConfidence
    raw_value: str
    model_self_score: float | None = None
    extracted_at: datetime | None = None
    spec_version: int | None = None


class SourceRecordResponse(BaseModel):
    source_file: str
    source_record_id: str
    raw_record: dict[str, Any]
    content_hash: str
    ingested_at: datetime | None = None
