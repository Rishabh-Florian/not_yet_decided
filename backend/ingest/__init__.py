"""Adaptive ingestion — vendor-agnostic record → knowledge graph.

A `MappingSpec` (drafted once at onboarding, reviewed by a human, persisted
in SQLite) drives a deterministic `Ingestor` at run time. The LLM is used
only at onboarding plus for opt-in unstructured-field extraction declared
inside the spec — never as a per-record fallback.

Public surface:

    MappingSpec                     # the per-source contract
    Ingestor, IngestReport          # runs records through a spec
    Onboarder                       # LLM drafts a MappingSpec from samples
    IdentityResolver                # post-pass: SAME_AS edges across sources
    GeminiClient                    # Gemini Flash 2.5 + cache
    IngestStore                     # control-plane SQLite (specs, runs, dead-letter)
    DriftError, RecordError, LLMError, OnboardError
    runtime                         # JSONPath / transformers / predicates / drift

Everything else is package-internal.
"""
from .identity import IdentityReport, IdentityResolver
from .ingestor import DriftError, IngestReport, Ingestor, RecordError
from .llm import GeminiClient, LLMError
from .onboard import Onboarder, OnboardError
from .spec import (
    CANONICAL_NODE_TYPES,
    CANONICAL_RELATION_TYPES,
    EdgeRule,
    FieldMap,
    LLMExtraction,
    MappingSpec,
    NodeRule,
    SourceSelector,
)
from .store import IngestStore
from . import runtime

__all__ = [
    "CANONICAL_NODE_TYPES",
    "CANONICAL_RELATION_TYPES",
    "DriftError",
    "EdgeRule",
    "FieldMap",
    "GeminiClient",
    "IdentityReport",
    "IdentityResolver",
    "IngestReport",
    "IngestStore",
    "Ingestor",
    "LLMError",
    "LLMExtraction",
    "MappingSpec",
    "NodeRule",
    "OnboardError",
    "Onboarder",
    "RecordError",
    "SourceSelector",
    "runtime",
]
