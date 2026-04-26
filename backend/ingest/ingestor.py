"""Deterministic ingester.

`Ingestor.run(spec, source_path)` walks every record from `source_path`,
applies the `MappingSpec`, and writes nodes/edges (with provenance) into the
`GraphStore`. Per-record failures land in `dead_letter`; structural drift
aborts the whole run before any writes happen.

LLM blocks (`spec.llm_blocks`) are opt-in. A spec with no blocks does zero
LLM calls during ingestion. When blocks are declared, `_run_llm_blocks`
enforces the block's `require_grounding` and `max_extractions_per_record`
caps before any node/edge is created. The LLM's self-rated `confidence`
field is captured into `Provenance.model_self_score` (audit-only) and is
never used to filter or threshold facts; whether the surface_form is
grounded in the source span drives the categorical
`FactConfidence` (GROUNDED vs INFERRED).
"""
from __future__ import annotations

import csv
import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from backend.graph.store import GraphStore, _canonical_json
from backend.models.graph import FactConfidence, GraphEdge, GraphNode, Provenance

from . import runtime
from .llm import GeminiClient, LLMError
from .spec import FieldMap, LLMExtraction, MappingSpec, NodeRule
from .store import IngestStore


log = logging.getLogger("better_context.ingest")


class RecordError(Exception):
    """Per-record failure that should land in dead_letter, not abort the run."""


class DriftError(RuntimeError):
    """Structural drift between spec and source — abort the run."""


@dataclass
class IngestReport:
    run_id: str | None = None
    records_in: int = 0
    records_out: int = 0
    records_skipped: int = 0
    records_dead: int = 0
    nodes_created: int = 0
    edges_created: int = 0
    drift_diffs: list[str] = field(default_factory=list)


@dataclass
class ApplyRecordReport:
    """Outcome of `Ingestor.apply_record` — single-record analog of IngestReport.

    Used by the push-mode source-update endpoint
    (`POST /api/source/{source_file}/{record_id}`) to surface what changed.
    Conflicts that surfaced are NOT in this report; the caller queries
    `GraphStore.conflicts.list(node_id=...)` against `nodes_touched` to
    discover them — keeps responsibilities clean (the ingestor doesn't
    know about the conflict store).
    """

    source_record_id: str
    content_changed: bool
    nodes_touched: list[str]
    skipped: bool = False


_TEMPLATE_VAR = re.compile(r"\{([^{}]+)\}")
_DRIFT_SAMPLE_SIZE = 30


class Ingestor:
    def __init__(
        self,
        store: GraphStore,
        ingest_store: IngestStore,
        *,
        llm_client: GeminiClient | None = None,
        sample_size: int = _DRIFT_SAMPLE_SIZE,
    ):
        self._store = store
        self._ingest = ingest_store
        self._llm = llm_client
        self._sample_size = sample_size

    # ---------- public ----------

    def run(
        self,
        spec: MappingSpec,
        source_path: str | Path,
        *,
        limit: int | None = None,
        dry_run: bool = False,
    ) -> IngestReport:
        path = Path(source_path)
        report = IngestReport()

        diffs = self._check_drift(spec, path)
        report.drift_diffs = diffs
        if diffs:
            raise DriftError(
                f"structural drift between spec v{spec.spec_version} and "
                f"{path.name}: {diffs}"
            )

        run_id = None if dry_run else self._ingest.open_run(
            tenant=spec.tenant,
            source_pattern=spec.source.file_pattern,
            spec_version=spec.spec_version,
            source_path=str(path),
        )
        report.run_id = run_id

        status = "completed"
        error: str | None = None

        try:
            for i, record in enumerate(self._iter_records(spec, path)):
                if limit is not None and i >= limit:
                    break
                report.records_in += 1
                self._process_record(spec, record, run_id, report, dry_run)
        except Exception as e:
            status = "failed"
            error = str(e)
            raise
        finally:
            if run_id is not None:
                self._ingest.close_run(
                    run_id,
                    status=status,
                    records_in=report.records_in,
                    records_out=report.records_out,
                    records_skipped=report.records_skipped,
                    records_dead=report.records_dead,
                    error=error,
                )
        return report

    def apply_record(
        self,
        spec: MappingSpec,
        record: Any,
        *,
        expected_record_id: str | None = None,
    ) -> ApplyRecordReport:
        """Apply a single record through the spec — single-record analog of `run`.

        Used by the push-mode source-update endpoint. No `ingest_runs` row,
        no batch report. Idempotent on `(spec_version, source_file,
        source_record_id, content_hash)`: if the same content is replayed,
        returns `skipped=True` without touching the graph.

        Conflict detection in `add_node` fires as usual; conflicts that
        surface are queryable via `store.conflicts.list(node_id=...)` against
        `nodes_touched` — this method does not return them directly.

        Args:
            spec: the active `MappingSpec` for the source.
            record: the new raw JSON record (not a path; record-shape).
            expected_record_id: if set, asserts the rendered ID matches.
                The HTTP route gets `record_id` from the URL path; passing
                it here ensures the URL and the spec agree before any write.

        Raises:
            RecordError: missing required field, id_template fails to render,
                or `expected_record_id` mismatch. Fail-fast — no dead-letter.
        """
        source_file = spec.source.file_pattern
        source_record_id = self._render_primary_id(spec, record)
        if expected_record_id is not None and expected_record_id != source_record_id:
            raise RecordError(
                f"record id mismatch: expected {expected_record_id!r}, "
                f"spec id_template renders {source_record_id!r}"
            )

        content_hash = hashlib.sha256(_canonical_json(record).encode("utf-8")).hexdigest()
        if self._ingest.already_seen(
            spec_version=spec.spec_version,
            source_file=source_file,
            source_record_id=source_record_id,
            content_hash=content_hash,
        ):
            return ApplyRecordReport(
                source_record_id=source_record_id,
                content_changed=False,
                nodes_touched=[],
                skipped=True,
            )

        self._store.add_source_record(
            source_file=source_file,
            source_record_id=source_record_id,
            raw_record=record,
        )
        applied_nodes = self._apply_node_rules(
            spec, record, source_file, source_record_id, dry_run=False,
        )
        self._apply_edge_rules(
            spec, record, source_file, source_record_id, applied_nodes, dry_run=False,
        )
        self._run_llm_blocks(
            spec, record, source_file, source_record_id, applied_nodes, dry_run=False,
        )
        # Deliberately no `mark_seen` call: there's no run_id and the next
        # batch `run()` over the same file (unchanged on disk) is naturally
        # skipped by its own already_seen check against the file's content
        # hash. If the source file IS later updated, the batch run will see
        # a NEW hash and re-process — same_source_updated rule keeps that
        # path consistent with this push.
        return ApplyRecordReport(
            source_record_id=source_record_id,
            content_changed=True,
            nodes_touched=list(applied_nodes.values()),
            skipped=False,
        )

    # ---------- record iteration ----------

    def _iter_records(self, spec: MappingSpec, path: Path) -> Iterator[Any]:
        fmt = spec.source.format
        if fmt == "json":
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            yield from runtime.resolve_all(spec.source.record_path, data)
        elif fmt in {"jsonl", "ndjson"}:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    yield json.loads(line)
        elif fmt == "csv":
            with open(path, "r", encoding="utf-8", newline="") as f:
                yield from csv.DictReader(f)
        else:
            raise ValueError(f"unsupported source format {fmt!r}")

    # ---------- drift ----------

    def _check_drift(self, spec: MappingSpec, path: Path) -> list[str]:
        if spec.required_paths_hash is None and spec.type_fingerprint is None:
            return []  # spec was committed without fingerprints — skip check
        sample: list[Any] = []
        for i, record in enumerate(self._iter_records(spec, path)):
            if i >= self._sample_size:
                break
            sample.append(record)
        if not sample:
            return ["source produced zero records"]

        diffs: list[str] = []
        if spec.required_paths_hash is not None:
            req_paths = self._declared_required_paths(spec)
            current_hash = runtime.required_paths_hash(req_paths)
            sample_paths = set()
            for rec in sample:
                sample_paths.update(runtime.list_field_paths(rec))
            missing = [p for p in req_paths if p not in sample_paths]
            if missing or current_hash != spec.required_paths_hash:
                if missing:
                    diffs.append(f"required paths absent in sample: {missing}")
        if spec.type_fingerprint is not None:
            observed = runtime.type_fingerprint(sample)
            diffs.extend(runtime.fingerprint_diff(spec.type_fingerprint, observed))
        return diffs

    @staticmethod
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

    # ---------- per-record processing ----------

    def _process_record(
        self,
        spec: MappingSpec,
        record: Any,
        run_id: str | None,
        report: IngestReport,
        dry_run: bool,
    ) -> None:
        source_file = spec.source.file_pattern
        try:
            source_record_id = self._render_primary_id(spec, record)
            content_hash = hashlib.sha256(
                _canonical_json(record).encode("utf-8")
            ).hexdigest()

            if not dry_run and self._ingest.already_seen(
                spec_version=spec.spec_version,
                source_file=source_file,
                source_record_id=source_record_id,
                content_hash=content_hash,
            ):
                report.records_skipped += 1
                return

            if not dry_run:
                self._store.add_source_record(
                    source_file=source_file,
                    source_record_id=source_record_id,
                    raw_record=record,
                )

            applied_nodes = self._apply_node_rules(
                spec, record, source_file, source_record_id, dry_run
            )
            self._apply_edge_rules(
                spec, record, source_file, source_record_id, applied_nodes, dry_run
            )
            self._run_llm_blocks(
                spec, record, source_file, source_record_id, applied_nodes, dry_run
            )

            if not dry_run and run_id is not None:
                self._ingest.mark_seen(
                    spec_version=spec.spec_version,
                    source_file=source_file,
                    source_record_id=source_record_id,
                    content_hash=content_hash,
                    run_id=run_id,
                )
            report.records_out += 1
            report.nodes_created += len(applied_nodes)
        except RecordError as e:
            report.records_dead += 1
            if run_id is not None:
                self._ingest.write_dead_letter(
                    run_id=run_id,
                    source_file=source_file,
                    source_record_id=None,
                    reason=str(e),
                    raw_record=record,
                )

    @staticmethod
    def _render_primary_id(spec: MappingSpec, record: Any) -> str:
        """Render the FIRST node rule's id_template — that's the
        source_record_id under which the raw record is stored. Failure here
        means the record has no stable identity, so we can't ingest it; the
        record dead-letters cleanly without partial writes.
        """
        if not spec.nodes:
            raise RecordError("spec declares no node rules")
        return _render_id_template(spec.nodes[0], record)

    # ---------- node + edge rules ----------

    def _apply_node_rules(
        self,
        spec: MappingSpec,
        record: Any,
        source_file: str,
        source_record_id: str,
        dry_run: bool,
    ) -> dict[str, str]:
        out: dict[str, str] = {}
        for rule in spec.nodes:
            if rule.when is not None and not runtime.evaluate_predicate(rule.when, record):
                continue
            try:
                node_id = _render_id_template(rule, record)
            except RecordError as e:
                if rule is spec.nodes[0]:
                    raise
                log.warning("node rule %s skipped: %s", rule.name, e)
                continue

            attrs, prov = _build_attributes(
                rule.fields, record, source_file, source_record_id, spec.spec_version,
            )
            node = GraphNode(
                id=node_id,
                type=spec.resolved_node_type(rule),
                attributes=attrs,
                provenance=prov,
            )
            if not dry_run:
                self._store.add_node(node)
            out[rule.name] = node_id
        return out

    def _apply_edge_rules(
        self,
        spec: MappingSpec,
        record: Any,
        source_file: str,
        source_record_id: str,
        applied_nodes: dict[str, str],
        dry_run: bool,
    ) -> None:
        for rule in spec.edges:
            if rule.when is not None and not runtime.evaluate_predicate(rule.when, record):
                continue
            src_name = rule.source_node[1:]
            tgt_name = rule.target_node[1:]
            if src_name not in applied_nodes or tgt_name not in applied_nodes:
                # Endpoint node was filtered out by its own when clause.
                continue
            attrs, prov = _build_attributes(
                rule.fields, record, source_file, source_record_id, spec.spec_version,
            )
            edge = GraphEdge(
                source_node_id=applied_nodes[src_name],
                target_node_id=applied_nodes[tgt_name],
                relation_type=rule.canonical_type,
                attributes=attrs,
                provenance=prov,
                valid_from=None,  # idempotent edge id; semantic time goes in attrs
            )
            if not dry_run:
                self._store.add_edge(edge)

    # ---------- LLM extraction blocks ----------

    def _run_llm_blocks(
        self,
        spec: MappingSpec,
        record: Any,
        source_file: str,
        source_record_id: str,
        applied_nodes: dict[str, str],
        dry_run: bool,
    ) -> None:
        if not spec.llm_blocks:
            return
        if self._llm is None:
            raise RuntimeError(
                f"spec {spec.tenant!r} declares llm_blocks but no llm_client "
                "was passed to Ingestor"
            )
        for block in spec.llm_blocks:
            input_value = runtime.resolve(block.input_source, record)
            if input_value is runtime.MISSING or input_value is None or input_value == "":
                continue
            try:
                items = self._call_llm_block(block, input_value, record)
            except LLMError as e:
                # LLM-side failures (rate limits, parse errors) shouldn't
                # abort an entire ingest — log and skip this block for this
                # record. Real bugs (KeyError etc.) bubble up and dead_letter.
                log.warning(
                    "llm_block %s skipped for record %s: %s",
                    block.name, source_record_id, e,
                )
                continue
            self._materialize_llm_items(
                spec, block, items,
                source_file, source_record_id,
                applied_nodes, dry_run,
            )

    def _call_llm_block(
        self,
        block: LLMExtraction,
        input_value: Any,
        record: Any,
    ) -> list[dict[str, Any]]:
        assert self._llm is not None
        cache_inputs = {p: runtime.resolve(p, record) for p in block.cache_key}
        list_schema = {
            "type": "array",
            "items": block.output_schema,
            "maxItems": block.max_extractions_per_record,
        }
        parsed, _raw = self._llm.extract_structured(
            prompt_template=block.prompt_template + "\n\nINPUT:\n{_input}",
            prompt_inputs={"_input": str(input_value)[:8000]},
            output_schema=list_schema,
            cache_inputs=cache_inputs,
            model=block.model,
        )
        if not isinstance(parsed, list):
            raise LLMError(
                f"llm_block {block.name!r} expected a JSON array, got {type(parsed).__name__}"
            )
        haystack = str(input_value).lower()
        kept: list[dict[str, Any]] = []
        for item in parsed[: block.max_extractions_per_record]:
            if not isinstance(item, dict):
                raise LLMError(
                    f"llm_block {block.name!r} array contained non-object entry: {item!r}"
                )
            conf = item.get("confidence")
            if not isinstance(conf, (int, float)):
                raise LLMError(
                    f"llm_block {block.name!r}: every item must declare a numeric "
                    f"`confidence`; got {conf!r}. (This number is captured as "
                    "`model_self_score` for audit only; it never gates the fact.)"
                )
            surface = _grounding_surface(item)
            grounded = surface is not None and surface.lower() in haystack
            if block.require_grounding and not grounded:
                continue
            item["_grounded"] = grounded  # consumed by _materialize_llm_items
            kept.append(item)
        return kept

    def _materialize_llm_items(
        self,
        spec: MappingSpec,
        block: LLMExtraction,
        items: list[dict[str, Any]],
        source_file: str,
        source_record_id: str,
        applied_nodes: dict[str, str],
        dry_run: bool,
    ) -> None:
        if not items:
            return
        rule_index = {n.name: n for n in spec.nodes}
        edge_index = {e.canonical_type: e for e in spec.edges}
        node_rule = rule_index.get(block.output_node_rule) if block.output_node_rule else None
        edge_rule = edge_index.get(block.output_edge_rule) if block.output_edge_rule else None

        for item in items:
            self_score = float(item["confidence"])  # LLM self-rated, audit-only
            grounded = bool(item.pop("_grounded", False))
            fact_conf = FactConfidence.GROUNDED if grounded else FactConfidence.INFERRED
            mention_node_id: str | None = None
            if node_rule is not None:
                mention_node_id = _render_id_template(node_rule, item)
                attrs, prov = _build_attributes(
                    node_rule.fields, item, source_file, source_record_id, spec.spec_version,
                )
                for p in prov:
                    p.extraction_method = "llm_extraction"
                    p.extraction_model = block.model
                    p.confidence = fact_conf
                    p.model_self_score = self_score
                node = GraphNode(
                    id=mention_node_id,
                    type=spec.resolved_node_type(node_rule),
                    attributes=attrs,
                    provenance=prov,
                )
                if not dry_run:
                    self._store.add_node(node)
            if edge_rule is None or mention_node_id is None or node_rule is None:
                continue
            src_id = (
                mention_node_id if edge_rule.source_node[1:] == node_rule.name
                else applied_nodes.get(edge_rule.source_node[1:])
            )
            tgt_id = (
                mention_node_id if edge_rule.target_node[1:] == node_rule.name
                else applied_nodes.get(edge_rule.target_node[1:])
            )
            if src_id is None or tgt_id is None:
                continue
            edge = GraphEdge(
                source_node_id=src_id,
                target_node_id=tgt_id,
                relation_type=edge_rule.canonical_type,
                attributes={
                    k: v for k, v in item.items()
                    if k in {"surface_form", "context"}
                },
                provenance=[Provenance(
                    source_file=source_file,
                    source_record_id=source_record_id,
                    source_field=block.input_source,
                    extraction_method="llm_extraction",
                    extraction_model=block.model,
                    confidence=fact_conf,
                    raw_value=str(item.get("surface_form", ""))[:500],
                    model_self_score=self_score,
                    spec_version=spec.spec_version,
                )],
                valid_from=None,
            )
            if not dry_run:
                self._store.add_edge(edge)


# ---------- module-private helpers ----------


def _read_field(fm: FieldMap, record: Any) -> tuple[Any, str]:
    """(value, path_actually_used). For a coalesce list, returns the path
    that produced the value; otherwise returns the single declared path.
    """
    if isinstance(fm.source, str):
        return runtime.resolve(fm.source, record), fm.source
    for expr in fm.source:
        v = runtime.resolve(expr, record)
        if v is not runtime.MISSING and v is not None:
            return v, expr
    return runtime.MISSING, fm.source[0]


def _build_attributes(
    fields: list[FieldMap],
    record: Any,
    source_file: str,
    source_record_id: str,
    spec_version: int,
) -> tuple[dict[str, Any], list[Provenance]]:
    attrs: dict[str, Any] = {}
    prov: list[Provenance] = []
    for fm in fields:
        value, used_path = _read_field(fm, record)
        if value is runtime.MISSING or value is None:
            if fm.required:
                raise RecordError(
                    f"required field {fm.attribute!r} missing "
                    f"(source: {fm.source!r})"
                )
            continue
        try:
            transformed = runtime.apply_transformers(value, fm.transform)
        except ValueError as e:
            # Transformer-side failure (e.g. unparseable date) — convert to a
            # per-record error so the record dead-letters with a clear reason
            # instead of crashing the entire run.
            raise RecordError(
                f"transformer failure on field {fm.attribute!r}: {e}"
            ) from e
        attrs[fm.attribute] = transformed
        prov.append(Provenance(
            source_file=source_file,
            source_record_id=source_record_id,
            source_field=used_path,
            attribute=fm.attribute,
            extraction_method="direct_mapping",
            extraction_model=f"spec:v{spec_version}",
            confidence=FactConfidence.EXACT,
            raw_value=str(value),
            spec_version=spec_version,
        ))
    return attrs, prov


def _render_id_template(rule: NodeRule, record: Any) -> str:
    field_index = {fm.attribute: fm for fm in rule.fields}
    parts: list[str] = []
    last = 0
    for m in _TEMPLATE_VAR.finditer(rule.id_template):
        parts.append(rule.id_template[last:m.start()])
        var = m.group(1)
        value = _read_template_var(var, field_index, record)
        if value is runtime.MISSING or value is None or value == "":
            raise RecordError(
                f"id_template {rule.id_template!r}: variable {var!r} "
                "resolved to empty/missing"
            )
        parts.append(str(value))
        last = m.end()
    parts.append(rule.id_template[last:])
    return "".join(parts)


def _read_template_var(
    var: str,
    field_index: dict[str, FieldMap],
    record: Any,
) -> Any:
    """If `var` matches a FieldMap.attribute, route through that FieldMap so
    its transform chain applies to the id component too. Otherwise read
    `$.{var}` from the record directly.
    """
    if var in field_index:
        fm = field_index[var]
        v, _ = _read_field(fm, record)
        if v is runtime.MISSING:
            return runtime.MISSING
        return runtime.apply_transformers(v, fm.transform)
    return runtime.resolve(f"$.{var}", record)


def _grounding_surface(item: dict[str, Any]) -> str | None:
    """The text the LLM claimed to extract — must appear in the source span
    when `require_grounding: true`. Looks at the conventional fields in
    priority order.
    """
    for key in ("surface_form", "text", "name"):
        v = item.get(key)
        if isinstance(v, str) and v.strip():
            return v
    return None
