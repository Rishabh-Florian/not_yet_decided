"""MappingSpec — the contract between onboarding and ingestion.

A `MappingSpec` is a YAML document, drafted once by an LLM (or hand-written),
reviewed by a human, persisted in SQLite, and run forever after by the
deterministic Ingestor. After `MappingSpec.from_yaml(...)` returns,
callers may trust:

  - every node/edge type resolves to a canonical name (directly or via
    `canonical_aliases`);
  - every transformer name is registered in `runtime`;
  - every `when:` predicate parses against the predicate grammar;
  - every edge endpoint references a NodeRule that this spec actually defines.

The canonical type registry lives next door in `canonical.yaml`. It's data,
not code, so adding a new type is a one-line YAML edit.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Final, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from . import runtime


_REGISTRY_PATH: Final[Path] = Path(__file__).parent / "canonical.yaml"
_REL_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _load_canonical_registry() -> tuple[frozenset[str], frozenset[str]]:
    with open(_REGISTRY_PATH, "r") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{_REGISTRY_PATH}: top-level must be a mapping")
    nodes = data.get("node_types") or {}
    rels = data.get("relation_types") or {}
    if not nodes or not rels:
        raise ValueError(f"{_REGISTRY_PATH}: node_types and relation_types required")
    for r in rels:
        if not _REL_PATTERN.match(r):
            raise ValueError(
                f"relation_type {r!r} must match [A-Za-z_][A-Za-z0-9_]*"
            )
    return frozenset(nodes), frozenset(rels)


CANONICAL_NODE_TYPES, CANONICAL_RELATION_TYPES = _load_canonical_registry()


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FieldMap(_Strict):
    attribute: str
    source: str | list[str]                       # JSONPath or coalesce list
    transform: list[str] = Field(default_factory=list)
    required: bool = True
    pii: bool = False                              # reserved; ignored v1

    @field_validator("transform")
    @classmethod
    def _check_transformers(cls, v: list[str]) -> list[str]:
        unknown = set(v) - runtime.registered_transformers()
        if unknown:
            raise ValueError(
                f"unknown transformer(s) {sorted(unknown)}; "
                f"registered: {sorted(runtime.registered_transformers())}"
            )
        return v


class NodeRule(_Strict):
    name: str                                      # local id, referenced by edges as @name
    canonical_type: str
    id_template: str                               # "person:{sender_emp_id}"
    id_required_fields: list[str] = Field(default_factory=list)
    when: dict[str, Any] | None = None
    fields: list[FieldMap] = Field(default_factory=list)

    @field_validator("when")
    @classmethod
    def _check_predicate(cls, v: dict[str, Any] | None) -> dict[str, Any] | None:
        if v is not None:
            runtime.validate_predicate(v)
        return v


class EdgeRule(_Strict):
    canonical_type: str
    source_node: str                               # "@nodeRuleName"
    target_node: str
    when: dict[str, Any] | None = None
    fields: list[FieldMap] = Field(default_factory=list)

    @field_validator("source_node", "target_node")
    @classmethod
    def _check_node_ref(cls, v: str) -> str:
        if not v.startswith("@") or len(v) < 2:
            raise ValueError(
                f"node ref {v!r} must be '@<NodeRule.name>' "
                "(cross-record refs aren't supported in v1)"
            )
        return v

    @field_validator("when")
    @classmethod
    def _check_predicate(cls, v: dict[str, Any] | None) -> dict[str, Any] | None:
        if v is not None:
            runtime.validate_predicate(v)
        return v


class LLMExtraction(_Strict):
    name: str
    input_source: str                              # JSONPath
    prompt_template: str
    output_schema: dict[str, Any]                  # JSON Schema
    output_node_rule: str | None = None
    output_edge_rule: str | None = None
    cache_key: list[str]
    require_grounding: bool = True
    max_extractions_per_record: int = 50
    model: str = "gemini-2.5-flash"


class SourceSelector(_Strict):
    file_pattern: str
    format: Literal["json", "jsonl", "ndjson", "csv"]
    record_path: str = "$[*]"


class MappingSpec(_Strict):
    spec_version: int = 1
    tenant: str
    source: SourceSelector
    canonical_aliases: dict[str, str] = Field(default_factory=dict)
    nodes: list[NodeRule]
    edges: list[EdgeRule] = Field(default_factory=list)
    llm_blocks: list[LLMExtraction] = Field(default_factory=list)
    required_paths_hash: str | None = None
    type_fingerprint: dict[str, str] | None = None

    # ---------- cross-field validation ----------

    @model_validator(mode="after")
    def _resolve_and_check_types(self) -> "MappingSpec":
        node_names: set[str] = set()
        for n in self.nodes:
            resolved = self._resolve_alias(n.canonical_type)
            if resolved not in CANONICAL_NODE_TYPES:
                raise ValueError(
                    f"node rule {n.name!r}: canonical_type {n.canonical_type!r} "
                    f"(resolved {resolved!r}) not in canonical registry"
                )
            if n.name in node_names:
                raise ValueError(f"duplicate NodeRule.name {n.name!r}")
            node_names.add(n.name)

        edge_canonical_types: set[str] = set()
        for e in self.edges:
            if e.canonical_type not in CANONICAL_RELATION_TYPES:
                raise ValueError(
                    f"edge canonical_type {e.canonical_type!r} not in "
                    f"canonical relation registry"
                )
            edge_canonical_types.add(e.canonical_type)
            for ref in (e.source_node, e.target_node):
                bare = ref[1:]
                if bare not in node_names:
                    raise ValueError(
                        f"edge references unknown node rule {ref!r}; "
                        f"defined: {sorted(node_names)}"
                    )

        for b in self.llm_blocks:
            if b.output_node_rule and b.output_node_rule not in node_names:
                raise ValueError(
                    f"llm_block {b.name!r}: output_node_rule "
                    f"{b.output_node_rule!r} not declared"
                )
            if b.output_edge_rule and b.output_edge_rule not in edge_canonical_types:
                raise ValueError(
                    f"llm_block {b.name!r}: output_edge_rule "
                    f"{b.output_edge_rule!r} not declared"
                )
        return self

    def _resolve_alias(self, name: str) -> str:
        # canonical_aliases is part of the spec contract — using `.get` here
        # is the documented "fall through if no alias" path, not a silent
        # default for missing data.
        return self.canonical_aliases.get(name, name)

    def resolved_node_type(self, rule: NodeRule) -> str:
        return self._resolve_alias(rule.canonical_type)

    # ---------- IO ----------

    @classmethod
    def from_yaml(cls, text: str) -> "MappingSpec":
        return cls.model_validate(yaml.safe_load(text))

    def to_yaml(self) -> str:
        return yaml.safe_dump(
            self.model_dump(mode="json", exclude_none=True),
            sort_keys=False,
        )
