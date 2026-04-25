"""Identity resolution — finds Person nodes that refer to the same real-world
human across sources, and writes SAME_AS edges between them.

Resolution does NOT merge nodes destructively. Each source's contribution
keeps its own node + provenance; downstream queries traverse SAME_AS to
treat the cluster as one. This preserves the full audit trail.

Implemented today: deterministic match on normalized email. Fuzzy and
LLM-triaged passes are out of scope for v1 — see the human backlog.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from backend.graph.store import GraphStore
from backend.models.graph import GraphEdge, Provenance

from .runtime import get_transformer

_normalize_email = get_transformer("normalize_email")


log = logging.getLogger("better_context.identity")


@dataclass
class IdentityReport:
    persons_examined: int = 0
    clusters_found: int = 0
    same_as_edges_created: int = 0


class IdentityResolver:
    def __init__(self, store: GraphStore):
        self._store = store

    def resolve(self) -> IdentityReport:
        report = IdentityReport()
        clusters = self._cluster_by_email()
        report.persons_examined = sum(len(ids) for ids in clusters.values())
        report.clusters_found = sum(1 for ids in clusters.values() if len(ids) > 1)

        for email, node_ids in clusters.items():
            if len(node_ids) < 2:
                continue
            for src, tgt in _ordered_pairs(node_ids):
                edge = GraphEdge(
                    source_node_id=src,
                    target_node_id=tgt,
                    relation_type="SAME_AS",
                    attributes={
                        "match_method": "deterministic_email",
                        "matched_on": email,
                    },
                    provenance=[Provenance(
                        source_file="<identity_resolver>",
                        source_record_id=email,
                        source_field="email",
                        extraction_method="rule_based",
                        extraction_model="rule:identity_email_v1",
                        confidence=1.0,
                        raw_value=email,
                        extracted_at=datetime.now(timezone.utc),
                    )],
                    confidence=1.0,
                    valid_from=None,
                )
                # Provenance has a FK to source_records, so we need a stub
                # raw record for "<identity_resolver>" before the edge can
                # land. add_source_record is idempotent.
                self._store.add_source_record(
                    source_file="<identity_resolver>",
                    source_record_id=email,
                    raw_record={"matched_email": email},
                )
                self._store.add_edge(edge)
                report.same_as_edges_created += 1
        return report

    def _cluster_by_email(self) -> dict[str, list[str]]:
        """Group Person nodes by normalized email."""
        clusters: dict[str, list[str]] = {}
        for node in self._store.nodes_by_type("Person"):
            raw_email = node.attributes.get("email")
            if not isinstance(raw_email, str) or "@" not in raw_email:
                continue
            email = _normalize_email(raw_email)
            clusters.setdefault(email, []).append(node.id)
        return clusters

def _ordered_pairs(items: Iterable[str]) -> Iterable[tuple[str, str]]:
    items = sorted(items)
    for i, a in enumerate(items):
        for b in items[i + 1 :]:
            yield a, b
