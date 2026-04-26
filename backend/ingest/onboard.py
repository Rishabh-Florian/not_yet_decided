"""Onboarder — auto-draft a MappingSpec for an unfamiliar source.

Flow:
    1. Sample N records from `source_path`.
    2. Build a system prompt embedding the canonical type/relation registry
       and the registered transformer names so the LLM can't invent any.
    3. Call Gemini Flash 2.5 with response_schema = MappingSpec JSON Schema.
    4. Validate the returned JSON via pydantic (+ canonical registry checks).
    5. On validation failure, send the error back ONCE for self-repair.
    6. Stamp `required_paths_hash` + `type_fingerprint` from the sample.
    7. Write to `mapping_specs` with status='draft'. Operator promotes
       to 'active' after review.

The LLM never sees the full source file — only N sample records. It also
never runs at ingest time on structured fields; that's a separate code path.
"""
from __future__ import annotations

import csv
import json
import logging
import re
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from . import runtime
from .llm import GeminiClient
from .spec import (
    CANONICAL_NODE_TYPES,
    CANONICAL_RELATION_TYPES,
    MappingSpec,
)
from .store import IngestStore

# A dot-notation JSONPath segment is "safe" only if it's an ASCII identifier.
# Anything else (spaces, punctuation, leading digit) MUST be bracket-quoted
# or jsonpath_ng's parser rejects it at runtime.
_SAFE_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# Match each `.<segment>` where <segment> is a run of chars not containing
# `.`, `[`, or `]`. This deliberately excludes bracket operators like `[*]`
# so they pass through untouched.
_DOT_SEGMENT = re.compile(r"\.([^.\[\]]+)")


def _bracketize_jsonpath(path: str) -> str:
    """Convert dot-notation segments containing non-identifier chars into
    bracket-quoted form. `$.Marital Status` -> `$['Marital Status']`."""
    if not isinstance(path, str) or not path.startswith("$"):
        return path

    def rewrite(m: re.Match[str]) -> str:
        key = m.group(1)
        if _SAFE_KEY.match(key):
            return f".{key}"
        return "['" + key.replace("'", "\\'") + "']"

    return _DOT_SEGMENT.sub(rewrite, path)


log = logging.getLogger("better_context.onboard")

DEFAULT_SAMPLE_SIZE = 20

_SYSTEM_INSTRUCTION = """You are a data-engineering assistant that turns unfamiliar enterprise data
into a deterministic MappingSpec for a knowledge-graph ingestion pipeline.

You MUST:
  - Pick canonical_type from this fixed set ONLY: {canonical_node_types}.
  - Pick edge canonical_type from this fixed set ONLY: {canonical_relation_types}.
  - Use canonical_aliases to map vendor-specific type names (e.g. "Email" -> "Message").
  - Use ONLY these registered transformers in `transform` lists: {transformers}.
  - Source paths are JSONPath strings beginning with "$" (e.g. "$.email_id").
  - id_template uses curly-brace variables matching FieldMap.attribute names
    (e.g. "person:{{emp_id}}"). The variable's transform chain is applied
    before substitution. Pick id_template fields that are stable identifiers,
    not free-text.
  - Mark a field `required: false` if it is plausibly missing in some records.
    Required fields cause records to be sent to dead_letter when missing.
  - Add a `when:` predicate (e.g. {{"not_null": "$.foo"}}) for nodes/edges
    that should be skipped when a key field is missing.
  - DO NOT invent canonical types. DO NOT invent transformers. DO NOT use
    Python or shell expressions inside `when:` — only the structured form.
  - Output JSON only, matching the response_schema exactly.
"""

_DRAFT_PROMPT = """Tenant: {tenant}
Source file: {source_file}
Format: {fmt}

Sample of {sample_count} record(s):
{sample}

The source has these distinct field paths across the sample:
{paths}

REFERENCE — a complete, validated, production-grade MappingSpec for a
DIFFERENT source (an emails feed). Imitate its structure, especially:
  - NodeRule.name (e.g. "sender") and how edges reference nodes via "@<name>"
  - the omission of `when:` on edges (it appears only on conditional nodes)
  - id_template like "person:{{emp_id}}" with curly-brace placeholders that
    match a FieldMap.attribute defined in the same node
  - canonical_aliases mapping vendor terms to canonical types

```yaml
{example_spec}
```

Now draft a complete MappingSpec for THIS source (described above). Use the
most specific canonical types that fit. If the source clearly contains
messages (emails, chats, posts) extract Person SENT/RECEIVED edges. If it
contains employees, extract Person nodes plus any visible REPORTS_TO/MEMBER_OF
edges. Otherwise pick the closest canonical types.

Set spec_version = 1. Leave required_paths_hash and type_fingerprint as null
(the runtime fills those in)."""


def _load_example_spec() -> str:
    """The hand-written emails.yaml is the canonical few-shot reference for
    structurally-correct MappingSpecs. Loaded once at import; cached."""
    p = Path(__file__).parent.parent.parent / "ingest_specs" / "enterprisebench" / "emails.yaml"
    return p.read_text(encoding="utf-8")


_EXAMPLE_SPEC = _load_example_spec()


class OnboardError(RuntimeError):
    pass


class Onboarder:
    def __init__(
        self,
        gemini: GeminiClient,
        ingest_store: IngestStore,
        *,
        sample_size: int = DEFAULT_SAMPLE_SIZE,
    ):
        self._gemini = gemini
        self._store = ingest_store
        self._sample_size = sample_size

    def draft_spec(
        self,
        source_path: str | Path,
        *,
        tenant: str,
        source_format: str | None = None,
        record_path: str = "$[*]",
    ) -> MappingSpec:
        path = Path(source_path)
        fmt = source_format or _guess_format(path)
        sample = _read_sample(path, fmt, record_path, self._sample_size)
        if not sample:
            raise OnboardError(f"no records found in {path} (format={fmt})")

        paths_seen: set[str] = set()
        for rec in sample:
            paths_seen.update(runtime.list_field_paths(rec))

        prompt_inputs = {
            "tenant": tenant,
            "source_file": path.name,
            "fmt": fmt,
            "sample_count": len(sample),
            "sample": json.dumps(sample[:5], indent=2)[:4000],
            "paths": "\n".join(f"  {p}" for p in sorted(paths_seen)),
            "example_spec": _EXAMPLE_SPEC,
        }
        system = _SYSTEM_INSTRUCTION.format(
            canonical_node_types=sorted(CANONICAL_NODE_TYPES),
            canonical_relation_types=sorted(CANONICAL_RELATION_TYPES),
            transformers=sorted(runtime.registered_transformers()),
        )

        schema = _mapping_spec_json_schema()

        parsed, raw = self._gemini.extract_structured(
            prompt_template=_DRAFT_PROMPT,
            prompt_inputs=prompt_inputs,
            output_schema=schema,
            cache_inputs={
                "tenant": tenant,
                "source_file": path.name,
                "fmt": fmt,
                "paths": sorted(paths_seen),
            },
            system_instruction=system,
        )
        spec = self._validate_with_repair(parsed, raw, prompt_inputs, schema, system)

        # Stamp drift signals from the actual sample.
        req = _declared_required_paths(spec)
        spec = spec.model_copy(update={
            "required_paths_hash": runtime.required_paths_hash(req),
            "type_fingerprint": runtime.type_fingerprint(sample),
        })

        # Persist as draft.
        self._store.save_spec(
            tenant=tenant,
            source_pattern=spec.source.file_pattern,
            version=spec.spec_version,
            yaml_text=spec.to_yaml(),
            required_paths_hash=spec.required_paths_hash,
            type_fingerprint=spec.type_fingerprint,
            status="draft",
        )
        return spec

    @staticmethod
    def _normalize_llm_output(obj: Any) -> Any:
        """LLM-output normalization at the boundary, before pydantic validates.

        Four recurring Gemini quirks for the MappingSpec schema:

        1. `when: {{}}`. The response_schema can't express nullable object
           fields reliably, so the LLM emits an empty dict to mean "no
           predicate". The validator rejects `{{}}`, so drop it.

        2. `id_required_fields` as bare attribute names ('emp_id') instead of
           JSONPaths ('$.emp_id'). The runtime appends these verbatim to the
           required-paths list — bare names never match observed paths so the
           drift check fires "required paths absent in sample". When a bare
           name matches a field's `attribute`, replace it with that field's
           `source` (the canonical JSONPath); otherwise prepend "$.".

        3. `required: true` on every FieldMap. The LLM ignores the prompt's
           guidance to mark sparse fields optional. Force `required: false`
           on all fields — record-level "must have" is enforced separately
           via `id_required_fields` (the ID is the only true must). Without
           this, the runtime drift check inevitably reports "required paths
           absent" for any field rare enough to miss its 30-record sample.

        4. JSONPath dot-notation for keys with spaces/punctuation. The data
           has keys like "Marital Status" and "Performance Rating"; the LLM
           emits `$.Marital Status` which jsonpath_ng can't parse. Convert
           non-identifier dot segments to bracket form: `$['Marital Status']`.

        Mutates and returns `obj`.
        """
        if not isinstance(obj, dict):
            return obj
        for key in ("nodes", "edges"):
            for rule in obj.get(key, []) or []:
                if isinstance(rule, dict) and rule.get("when") == {}:
                    rule.pop("when", None)
        for rule_key in ("nodes", "edges"):
            for rule in obj.get(rule_key, []) or []:
                if not isinstance(rule, dict):
                    continue
                for fm in rule.get("fields", []) or []:
                    if not isinstance(fm, dict):
                        continue
                    src = fm.get("source")
                    if isinstance(src, str):
                        fm["source"] = _bracketize_jsonpath(src)
                    elif isinstance(src, list):
                        fm["source"] = [
                            _bracketize_jsonpath(s) if isinstance(s, str) else s
                            for s in src
                        ]
                    fm["required"] = False
        for rule in obj.get("nodes", []) or []:
            if not isinstance(rule, dict):
                continue
            attr_to_source: dict[str, str] = {}
            for fm in rule.get("fields", []) or []:
                if not isinstance(fm, dict):
                    continue
                src = fm.get("source")
                attr = fm.get("attribute")
                if isinstance(attr, str) and isinstance(src, str) and src.startswith("$"):
                    attr_to_source[attr] = src
            irf = rule.get("id_required_fields")
            if isinstance(irf, list) and irf:
                rule["id_required_fields"] = [
                    _bracketize_jsonpath(
                        attr_to_source.get(
                            v, v if isinstance(v, str) and v.startswith("$") else f"$.{v}"
                        )
                    )
                    for v in irf
                ]
        return obj

    def _validate_with_repair(
        self,
        parsed: Any,
        raw: str,
        prompt_inputs: dict[str, Any],
        schema: dict[str, Any],
        system: str,
        *,
        max_repair_rounds: int = 3,
    ) -> MappingSpec:
        """Validate `parsed` against MappingSpec; on failure feed the error
        back to the LLM and retry up to `max_repair_rounds` times. Each retry
        sees the latest error message — Gemini Flash typically converges
        within 2-3 rounds when the problem is a constraint it didn't initially
        respect.
        """
        last_error: ValidationError | None = None
        current = parsed
        for attempt in range(max_repair_rounds + 1):
            current = self._normalize_llm_output(current)
            try:
                return MappingSpec.model_validate(current)
            except ValidationError as e:
                last_error = e
                if attempt == max_repair_rounds:
                    break
                log.warning(
                    "validation attempt %d/%d failed; asking LLM to repair: %s",
                    attempt + 1, max_repair_rounds + 1, e,
                )
                current, _ = self._gemini.repair(
                    prompt_template=_DRAFT_PROMPT,
                    prompt_inputs=prompt_inputs,
                    output_schema=schema,
                    previous_output=current,
                    validator_error=str(e),
                    system_instruction=system,
                )
        raise OnboardError(
            f"spec validation failed after {max_repair_rounds + 1} attempts. "
            f"last error: {last_error}"
        ) from last_error


_FORMAT_BY_SUFFIX = {".json": "json", ".jsonl": "jsonl", ".ndjson": "ndjson", ".csv": "csv"}


def _guess_format(path: Path) -> str:
    suf = path.suffix.lower()
    if suf not in _FORMAT_BY_SUFFIX:
        raise OnboardError(
            f"can't guess format for {path.name}; pass source_format explicitly"
        )
    return _FORMAT_BY_SUFFIX[suf]


def _read_sample(path: Path, fmt: str, record_path: str, n: int) -> list[Any]:
    out: list[Any] = []
    if fmt == "json":
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        out = runtime.resolve_all(record_path, data)[:n]
    elif fmt in {"jsonl", "ndjson"}:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                out.append(json.loads(line))
                if len(out) >= n:
                    break
    elif fmt == "csv":
        with open(path, "r", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                out.append(dict(row))
                if len(out) >= n:
                    break
    else:
        raise OnboardError(f"unsupported format {fmt!r}")
    return out


def _declared_required_paths(spec: MappingSpec) -> list[str]:
    out: list[str] = []
    for rule in spec.nodes:
        for fm in rule.fields:
            if not fm.required:
                continue
            src = fm.source if isinstance(fm.source, str) else fm.source[0]
            out.append(src)
        out.extend(rule.id_required_fields)
    return out


_SCHEMA_CACHE: dict[str, Any] | None = None


def _mapping_spec_json_schema() -> dict[str, Any]:
    """Pydantic-generated JSON Schema for MappingSpec, used as Gemini's
    response_schema. Sanitization for Gemini's OpenAPI subset happens inside
    the LLM client. Cached after first call.
    """
    global _SCHEMA_CACHE
    if _SCHEMA_CACHE is None:
        _SCHEMA_CACHE = MappingSpec.model_json_schema()
    return _SCHEMA_CACHE
