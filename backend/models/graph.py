from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
import uuid


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class FactConfidence(StrEnum):
    """Categorical fact-trust label. Never a magic float.

    A confidence value is always grounded in a real computation, deterministic
    rule, or human action. If we don't have an algorithm, we use a category —
    not a fabricated number. See docs/ARCHITECTURE.md for the principle.
    """
    EXACT = "exact"          # direct mapping or deterministic rule
    GROUNDED = "grounded"    # LLM extraction whose surface_form was found in source
    INFERRED = "inferred"    # LLM extraction without grounding match
    HUMAN = "human"          # human edit / override


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
    ingested_at: datetime | None = field(default_factory=_now)


@dataclass
class Provenance:
    source_file: str
    source_record_id: str
    source_field: str
    extraction_method: str          # "direct_mapping" | "llm_extraction" | "rule_based" | "human"
    extraction_model: str           # e.g. "claude-sonnet-4-6" or "rule:email_parser_v1"
    confidence: FactConfidence      # categorical fact-trust label, never a float
    raw_value: str
    attribute: str | None = None    # which attribute on the node/edge this trace describes;
                                    # populated by ingestor / edit_node / llm_blocks. Conflict
                                    # detection at MERGE time uses it to find the per-attribute
                                    # confidence. None for legacy rows written before the column
                                    # existed — those default to EXACT during reconcile().
    model_self_score: float | None = None   # LLM self-rated number, audit-only; never used for filtering
    extracted_at: datetime | None = field(default_factory=_now)
    spec_version: int | None = None # MappingSpec version that produced this fact

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["extracted_at"] = self.extracted_at.isoformat() if self.extracted_at else None
        # StrEnum.value for clean JSON output (FactConfidence already serializes
        # as its string value, but be explicit so downstream consumers don't
        # depend on str(enum) semantics).
        d["confidence"] = self.confidence.value
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Provenance":
        d = dict(d)
        if isinstance(d.get("extracted_at"), str):
            d["extracted_at"] = datetime.fromisoformat(d["extracted_at"])
        if "confidence" in d and not isinstance(d["confidence"], FactConfidence):
            d["confidence"] = FactConfidence(d["confidence"])
        return cls(**d)


@dataclass
class GraphNode:
    type: str                                   # "Employee", "Customer", "Product", ...
    attributes: dict[str, Any] = field(default_factory=dict)
    provenance: list[Provenance] = field(default_factory=list)
    vfs_path: str = ""
    id: str = field(default_factory=lambda: _new_id("node"))
    created_at: datetime | None = field(default_factory=_now)
    updated_at: datetime | None = field(default_factory=_now)
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
    valid_from: datetime | None = field(default_factory=_now)
    valid_to: datetime | None = None
    id: str = field(default_factory=lambda: _new_id("edge"))
    version: int = 1
