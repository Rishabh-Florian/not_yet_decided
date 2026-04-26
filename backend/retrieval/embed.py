"""One-shot offline embedding pass over `:Entity` nodes.

Reads each `:Entity` node, builds a textual representation from its
`type`, `attributes_json`, and (if available) the linked
`source_records.raw_record` excerpts, embeds it with the configured
`Embedder`, and writes the result to ``n.vector``.

Idempotent: by default skips nodes that already have a `vector`
property. Pass ``--force`` to re-embed everything.

CLI:

    uv run python -m backend.retrieval.embed [--limit N] [--node-type T] [--force]

The default embedder is `BgeSmallEmbedder` (`BAAI/bge-small-en-v1.5`).
If ``sentence-transformers`` is not installed the constructor raises
with an actionable message — the caller is expected to either install
the dep or pass ``--stub`` to use the deterministic test embedder
(which has NO semantic signal and is intended for smoke-testing the
write path only).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

import backend.config  # loads .env so NEO4J_* + GEMINI_API_KEY land in os.environ  # noqa: F401
from backend.graph.store import GraphStore

from .embedder import BgeSmallEmbedder, Embedder, StubEmbedder
from .index import ENTITY_VECTOR_PROPERTY, ensure_vector_index


def _build_text_for_node(node_type: str, attributes: dict[str, Any]) -> str:
    """Compose the embedding input string for one node.

    The input is intentionally compact and human-readable: the node
    type followed by ``key: value`` pairs sorted by key. We avoid
    dumping the full JSON because the BGE tokenizer wastes tokens on
    braces and quotes; the sorted-pair form keeps deterministic order
    so embeddings are reproducible across runs.
    """
    parts = [node_type]
    for k in sorted(attributes.keys()):
        v = attributes[k]
        if v is None or v == "":
            continue
        if isinstance(v, (str, int, float, bool)):
            parts.append(f"{k}: {v}")
        else:
            # Lists / nested dicts get a compact JSON serialization.
            parts.append(f"{k}: {json.dumps(v, ensure_ascii=False, sort_keys=True)}")
    return " | ".join(parts)


def _iter_target_nodes(
    store: GraphStore,
    node_type: str | None,
    limit: int | None,
    *,
    force: bool,
) -> list[dict[str, Any]]:
    """Fetch the ids+attrs of nodes that need embedding."""
    where_clauses = []
    params: dict[str, Any] = {}
    if node_type is not None:
        where_clauses.append("n.type = $node_type")
        params["node_type"] = node_type
    if not force:
        where_clauses.append(f"n.{ENTITY_VECTOR_PROPERTY} IS NULL")
    where = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    limit_clause = f"LIMIT {int(limit)}" if limit is not None else ""
    cypher = (
        f"MATCH (n:Entity) {where} "
        f"RETURN n.id AS id, n.type AS type, n.attributes_json AS attrs "
        f"{limit_clause}"
    )
    with store._session() as s:
        rows = list(s.run(cypher, **params))  # type: ignore[arg-type]
    return [
        {"id": r["id"], "type": r["type"], "attrs": json.loads(r["attrs"])}
        for r in rows
    ]


def _write_embedding(store: GraphStore, node_id: str, vector: list[float]) -> None:
    cypher = (
        f"MATCH (n:Entity {{id: $id}}) "
        f"SET n.{ENTITY_VECTOR_PROPERTY} = $vec"
    )
    with store._session() as s:
        s.run(cypher, id=node_id, vec=vector)  # type: ignore[arg-type]


def run_embedding_pass(
    store: GraphStore,
    embedder: Embedder,
    *,
    node_type: str | None = None,
    limit: int | None = None,
    force: bool = False,
) -> int:
    """Embed every (or every-missing) `:Entity` node. Returns the count written.

    Ensures the vector index exists at the embedder's dim before
    writing — the index is what makes subsequent `HybridTier` queries
    work, so creating it here keeps the contract local to the
    embedding pass.
    """
    ensure_vector_index(store._driver, store._database, dimensions=embedder.dim)
    targets = _iter_target_nodes(store, node_type, limit, force=force)
    written = 0
    for row in targets:
        text = _build_text_for_node(row["type"], row["attrs"])
        if not text.strip():
            raise ValueError(
                f"node {row['id']!r} produced empty embedding text — "
                f"refusing to embed an empty string"
            )
        vec = embedder.embed(text)
        _write_embedding(store, row["id"], vec)
        written += 1
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Embed at most N nodes (default: all)",
    )
    parser.add_argument(
        "--node-type", type=str, default=None,
        help="Restrict to a single canonical node type (e.g. Person)",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Re-embed nodes that already have a `vector` property",
    )
    parser.add_argument(
        "--stub", action="store_true",
        help="Use the deterministic StubEmbedder (no semantic signal — "
             "smoke-test only). Default is BgeSmallEmbedder.",
    )
    parser.add_argument(
        "--db", type=str,
        default=os.environ.get("STORE_SQLITE_PATH", "data/store.sqlite"),
        help="Path to the SQLite store (default: $STORE_SQLITE_PATH or data/store.sqlite)",
    )
    args = parser.parse_args(argv)

    embedder: Embedder = StubEmbedder() if args.stub else BgeSmallEmbedder()
    store = GraphStore(db_path=args.db)
    try:
        written = run_embedding_pass(
            store,
            embedder,
            node_type=args.node_type,
            limit=args.limit,
            force=args.force,
        )
    finally:
        store.close()
    print(f"embedded {written} node(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
