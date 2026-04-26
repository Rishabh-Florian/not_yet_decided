"""Shared helpers used by ExactTier, HybridTier, and the agent ToolBox.

Three thin pure functions deduplicated from the three retrieval surfaces:

* `_preview` — one-line summary of a node's attributes for `Hit.preview`.
* `_citations_from_provenance` — Pydantic-validated `Provenance` rows
  -> `Citation`s (the canonical four `extraction_method` literals are
  enforced at the boundary).
* `_escape_lucene` — escape Lucene's reserved-char set so a verbatim
  user query is safe to forward to a fulltext index without inadvertent
  boolean-operator interpretation.

All three live in this module rather than being re-derived per tier:
the Lucene escape rule is a property of the Lucene query syntax itself,
not of any particular tier's contract; the preview shape is what UI
consumers expect across every tier; the provenance->citation mapping
is the same dedup-key contract everywhere.
"""
from __future__ import annotations

import json
import re
from typing import Any

from backend.models.graph import Provenance

from .models import Citation


# Preview cap (chars). Chosen to keep tier hits readable inside an LLM
# prompt: long enough to surface a headline / summary line, short enough
# that a top-K result block does not eat the model's context budget.
_PREVIEW_CHARS: int = 200

# Reserved Lucene chars in the standard query syntax — escaped so the
# user's verbatim text is a literal phrase, never a boolean expression.
# Source: Lucene query-parser spec.
_LUCENE_SPECIAL = re.compile(r'([+\-!(){}\[\]^"~*?:\\/]|&&|\|\|)')


def _escape_lucene(query: str) -> str:
    return _LUCENE_SPECIAL.sub(r"\\\1", query)


def _preview(attributes: dict[str, Any]) -> str:
    """One-line summary of a node for `Hit.preview`.

    Picks the most useful human-readable field if present, else falls
    back to a truncated JSON dump of the attributes.
    """
    for key in ("name", "title", "subject", "summary", "description", "customer_name"):
        v = attributes.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()[:_PREVIEW_CHARS]
    return json.dumps(attributes, ensure_ascii=False)[:_PREVIEW_CHARS]


def _citations_from_provenance(rows: list[Provenance]) -> list[Citation]:
    cites: list[Citation] = []
    for p in rows:
        method = p.extraction_method
        # Pydantic Literal in Citation requires the canonical four values; the
        # store can only emit one of those four (validated at insert time).
        if method not in ("direct_mapping", "llm_extraction", "rule_based", "human", "synthetic"):
            raise ValueError(f"unexpected extraction_method {method!r} in provenance")
        cites.append(
            Citation(
                source_file=p.source_file,
                source_record_id=p.source_record_id,
                source_field=p.source_field,
                raw_value=p.raw_value,
                extraction_method=method,
            )
        )
    return cites


__all__ = [
    "_PREVIEW_CHARS",
    "_citations_from_provenance",
    "_escape_lucene",
    "_preview",
]
