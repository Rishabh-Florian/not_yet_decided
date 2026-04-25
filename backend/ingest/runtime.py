"""Deterministic evaluation engine — JSONPath, transformers, predicates, drift.

A single deep module owning the question: "given a record and a spec rule,
what value (if any) does it produce, and does the source still match the
schema we onboarded against?" Splitting this into multiple files would force
every caller to re-learn the same boundary four times.

Public surface (what `spec.py` validates against and `ingestor.py` calls at
runtime):

    # JSONPath, with a sentinel that distinguishes missing from null.
    MISSING
    resolve(expr, record)         -> value | MISSING
    resolve_all(expr, record)     -> list[value]
    coalesce(exprs, record)       -> first non-null/non-missing or MISSING

    # `when:` predicates — structured, never free-text.
    validate_predicate(pred)            -> raises ValueError on bad shape
    evaluate_predicate(pred, record)    -> bool

    # Transformer registry — pure functions referenced by name in specs.
    register_transformer(name)          -> decorator
    apply_transformers(value, chain)    -> value
    registered_transformers()           -> frozenset[str]

    # Drift detection — refuses to ingest after a vendor's schema shifts.
    list_field_paths(record)            -> sorted list of leaf JSONPaths
    required_paths_hash(paths)          -> stable sha256
    type_fingerprint(records)           -> {path: type_tag}
    fingerprint_diff(expected, got)     -> list[str] (empty = no drift)
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any, Callable, Final, Iterable

from jsonpath_ng.ext import parse as _parse_jsonpath


# --------------------------------------------------------------------- JSONPath

class _Missing:
    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover
        return "<MISSING>"


MISSING: Final[Any] = _Missing()
_PATH_CACHE: dict[str, Any] = {}


def _compile(expr: str) -> Any:
    cached = _PATH_CACHE.get(expr)
    if cached is not None:
        return cached
    parsed = _parse_jsonpath(expr)
    _PATH_CACHE[expr] = parsed
    return parsed


def resolve(expr: str, record: Any) -> Any:
    matches = _compile(expr).find(record)
    if not matches:
        return MISSING
    return matches[0].value


def resolve_all(expr: str, record: Any) -> list[Any]:
    return [m.value for m in _compile(expr).find(record)]


def coalesce(exprs: list[str], record: Any) -> Any:
    for expr in exprs:
        v = resolve(expr, record)
        if v is not MISSING and v is not None:
            return v
    return MISSING


# ---------------------------------------------------------------- transformers

Transformer = Callable[[Any], Any]
_REGISTRY: dict[str, Transformer] = {}


def register_transformer(name: str) -> Callable[[Transformer], Transformer]:
    def deco(fn: Transformer) -> Transformer:
        if name in _REGISTRY:
            raise ValueError(f"transformer {name!r} already registered")
        _REGISTRY[name] = fn
        return fn
    return deco


def get_transformer(name: str) -> Transformer:
    if name not in _REGISTRY:
        raise KeyError(
            f"unknown transformer {name!r}; "
            f"available: {sorted(_REGISTRY)}"
        )
    return _REGISTRY[name]


def registered_transformers() -> frozenset[str]:
    return frozenset(_REGISTRY)


def apply_transformers(value: Any, chain: list[str]) -> Any:
    for name in chain:
        value = get_transformer(name)(value)
    return value


@register_transformer("lowercase")
def _lowercase(v: Any) -> Any:
    return v.lower() if isinstance(v, str) else v


@register_transformer("strip")
def _strip(v: Any) -> Any:
    return v.strip() if isinstance(v, str) else v


@register_transformer("normalize_email")
def _normalize_email(v: Any) -> Any:
    if not isinstance(v, str):
        return v
    return unicodedata.normalize("NFC", v.strip().lower())


@register_transformer("coalesce_empty_to_null")
def _coalesce_empty_to_null(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, str) and not v.strip():
        return None
    return v


# Datetimes from real CRM exports come in many shapes. We accept ISO-8601 and
# the SQL-ish "YYYY-MM-DD HH:MM:SS [tz_abbrev]" form (e.g. "2012-03-18 06:58:29 IST").
# Anything else is a real bug — raise so the record lands in dead_letter and
# the operator notices, instead of silently storing a malformed string.
_SQL_DATE = re.compile(r"^\s*(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})(.*)$")


@register_transformer("parse_iso_datetime")
def _parse_iso_datetime(v: Any) -> Any:
    if v is None or isinstance(v, datetime):
        return v.isoformat() if isinstance(v, datetime) else v
    if not isinstance(v, str):
        raise ValueError(f"parse_iso_datetime expected str, got {type(v).__name__}: {v!r}")
    s = v.strip()
    try:
        return datetime.fromisoformat(s).isoformat()
    except ValueError:
        pass
    m = _SQL_DATE.match(s)
    if m:
        date, time, _tz = m.groups()
        return datetime.fromisoformat(f"{date}T{time}").replace(tzinfo=timezone.utc).isoformat()
    raise ValueError(f"parse_iso_datetime cannot parse {s!r}")


# ------------------------------------------------------------------ predicates
#
# `when:` is a tagged-union dict, never free-text:
#   {"not_null": "$.x"}                  value present AND non-null
#   {"exists":   "$.x"}                  path resolves (value may be null)
#   {"equals":   ["$.x", literal]}
#   {"in":       ["$.x", [a, b, ...]]}
#   {"matches":  ["$.x", "regex"]}
#   {"and":      [pred, ...]}
#   {"or":       [pred, ...]}

MAX_PREDICATE_DEPTH = 4
_PREDICATE_OPS = frozenset({"not_null", "exists", "equals", "in", "matches", "and", "or"})


def validate_predicate(pred: Any, *, depth: int = 0) -> None:
    if depth > MAX_PREDICATE_DEPTH:
        raise ValueError(f"predicate nesting exceeds depth {MAX_PREDICATE_DEPTH}")
    if not isinstance(pred, dict) or len(pred) != 1:
        raise ValueError(f"predicate must be a single-key dict, got {pred!r}")
    op, args = next(iter(pred.items()))
    if op not in _PREDICATE_OPS:
        raise ValueError(f"unknown predicate op {op!r}; valid: {sorted(_PREDICATE_OPS)}")
    if op in {"not_null", "exists"}:
        if not isinstance(args, str):
            raise ValueError(f"{op} expects a JSONPath string, got {args!r}")
    elif op in {"equals", "in", "matches"}:
        if not (isinstance(args, list) and len(args) == 2 and isinstance(args[0], str)):
            raise ValueError(f"{op} expects [JSONPath, value], got {args!r}")
        if op == "matches" and not isinstance(args[1], str):
            raise ValueError(f"matches expects regex string, got {args[1]!r}")
        if op == "in" and not isinstance(args[1], list):
            raise ValueError(f"in expects list literal, got {args[1]!r}")
    else:  # and / or
        if not (isinstance(args, list) and args):
            raise ValueError(f"{op} expects a non-empty list of sub-predicates")
        for sub in args:
            validate_predicate(sub, depth=depth + 1)


def evaluate_predicate(pred: dict[str, Any], record: Any) -> bool:
    op, args = next(iter(pred.items()))
    if op == "exists":
        return resolve(args, record) is not MISSING
    if op == "not_null":
        v = resolve(args, record)
        return v is not MISSING and v is not None
    if op == "equals":
        return resolve(args[0], record) == args[1]
    if op == "in":
        return resolve(args[0], record) in args[1]
    if op == "matches":
        v = resolve(args[0], record)
        return isinstance(v, str) and re.search(args[1], v) is not None
    if op == "and":
        return all(evaluate_predicate(sub, record) for sub in args)
    if op == "or":
        return any(evaluate_predicate(sub, record) for sub in args)
    raise ValueError(f"unhandled predicate op {op!r}")


# ----------------------------------------------------------------------- drift
#
# Two signals over a sample of records:
#   1. required_paths_hash — SHA of declared-required JSONPaths. Catches a
#      vendor renaming a load-bearing field ("sender_emp_id" -> "from_id").
#   2. type_fingerprint    — {path: type_tag}. Catches the silent killer
#      where the path is unchanged but values flip from ISO date to epoch int.

_ISO_LOOKING = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}")
_EMAIL_LOOKING = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _type_tag(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        if _ISO_LOOKING.match(value):
            return "iso_datetime"
        if _EMAIL_LOOKING.match(value):
            return "email_like"
        return "str"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "dict"
    return "other"


def list_field_paths(record: Any, *, prefix: str = "$") -> list[str]:
    out: list[str] = []
    _walk(record, prefix, out)
    return sorted(out)


def _walk(value: Any, prefix: str, out: list[str]) -> None:
    if isinstance(value, dict):
        if not value:
            out.append(prefix)
            return
        for k, v in value.items():
            _walk(v, f"{prefix}.{k}", out)
    elif isinstance(value, list):
        if not value:
            out.append(f"{prefix}[*]")
            return
        seen: set[str] = set()
        for v in value:
            shape = type(v).__name__
            if shape in seen:
                continue
            seen.add(shape)
            _walk(v, f"{prefix}[*]", out)
    else:
        out.append(prefix)


def required_paths_hash(paths: Iterable[str]) -> str:
    canon = json.dumps(sorted(set(paths)), separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def type_fingerprint(records: Iterable[Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for rec in records:
        for path in list_field_paths(rec):
            v = _resolve_path_for_fingerprint(path, rec)
            tag = _type_tag(v)
            prev = out.get(path)
            if prev is None:
                out[path] = tag
            elif prev != tag and prev != "mixed":
                out[path] = "mixed"
    return out


def fingerprint_diff(
    expected: dict[str, str],
    observed: dict[str, str],
    *,
    ignore_extra_paths: bool = True,
) -> list[str]:
    """Empty list = no drift on declared paths. New paths in `observed` are a
    soft signal (schema expansion is allowed) unless `ignore_extra_paths=False`.
    """
    diffs: list[str] = []
    for path, exp_tag in expected.items():
        got = observed.get(path)
        if got is None:
            diffs.append(f"{path}: declared but missing from observed sample")
        elif got != exp_tag:
            diffs.append(f"{path}: type changed {exp_tag!r} -> {got!r}")
    if not ignore_extra_paths:
        for path in observed:
            if path not in expected:
                diffs.append(f"{path}: new path not seen at onboarding")
    return diffs


def _resolve_path_for_fingerprint(path: str, rec: Any) -> Any:
    """Walks paths emitted by list_field_paths (dotted with [*] markers).
    Returns the first matched value or None — fingerprint cares about types
    not values, so we only need a representative.
    """
    cur: Any = rec
    body = path.lstrip("$").lstrip(".")
    if not body:
        return cur
    for token in _split_path_tokens(body):
        if token == "[*]":
            if isinstance(cur, list) and cur:
                cur = cur[0]
            else:
                return None
        elif isinstance(cur, dict) and token in cur:
            cur = cur[token]
        else:
            return None
    return cur


def _split_path_tokens(body: str) -> list[str]:
    out: list[str] = []
    buf = ""
    i = 0
    while i < len(body):
        ch = body[i]
        if ch == ".":
            if buf:
                out.append(buf)
                buf = ""
        elif ch == "[" and body[i:i + 3] == "[*]":
            if buf:
                out.append(buf)
                buf = ""
            out.append("[*]")
            i += 2
        else:
            buf += ch
        i += 1
    if buf:
        out.append(buf)
    return out
