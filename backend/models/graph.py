from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any
import uuid


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


@dataclass
class SourceRecord:
    """Layer 4: an original ingested record, stored verbatim.

    Provenance rows point at a (source_file, source_record_id) pair plus a
    `source_field`, so any fact in the graph can be resolved back to the exact
    field of the exact original record it was derived from.
    """
    source_file: str                        # e.g. "Enterprise_mail_system/emails.json"
    source_record_id: str                   # e.g. "email_id:4226322d-0ea5-..."
    raw_record: dict[str, Any]              # full original record, unmodified
    content_hash: str                       # sha256 hex of canonical JSON
    ingested_at: datetime = field(default_factory=_now)


@dataclass
class Provenance:
    source_file: str
    source_record_id: str
    source_field: str
    extraction_method: str          # "direct_mapping" | "llm_extraction" | "rule_based" | "human"
    extraction_model: str           # e.g. "claude-sonnet-4-6" or "rule:email_parser_v1"
    confidence: float
    raw_value: str
    extracted_at: datetime = field(default_factory=_now)
    spec_version: int | None = None # MappingSpec version that produced this fact

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["extracted_at"] = self.extracted_at.isoformat()
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Provenance":
        d = dict(d)
        if isinstance(d.get("extracted_at"), str):
            d["extracted_at"] = datetime.fromisoformat(d["extracted_at"])
        return cls(**d)


@dataclass
class GraphNode:
    type: str                                   # "Employee", "Customer", "Product", ...
    attributes: dict[str, Any] = field(default_factory=dict)
    provenance: list[Provenance] = field(default_factory=list)
    confidence: float = 1.0
    vfs_path: str = ""
    id: str = field(default_factory=lambda: _new_id("node"))
    created_at: datetime = field(default_factory=_now)
    updated_at: datetime = field(default_factory=_now)
    version: int = 1

    def touch(self) -> None:
        self.updated_at = _now()
        self.version += 1


@dataclass
class GraphEdge:
    source_node_id: str
    target_node_id: str
    relation_type: str                          # "REPORTS_TO", "PURCHASED", ...
    attributes: dict[str, Any] = field(default_factory=dict)
    provenance: list[Provenance] = field(default_factory=list)
    confidence: float = 1.0
    valid_from: datetime | None = field(default_factory=_now)
    valid_to: datetime | None = None
    id: str = field(default_factory=lambda: _new_id("edge"))
    version: int = 1
