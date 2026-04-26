"""Neo4j retrieval indexes — idempotent bootstrap.

Tier modules call `ensure_indexes()` at construction time. The
fulltext index (`node_text`) and the native vector index
(`entity_vec`) are both shared across tiers and named centrally so
no second instance can creep in.

* `node_text` — Lucene-tokenized fulltext over `:Entity.attributes_json`.
  Used by `ExactTier` (R1) for short-phrase BM25 recall and by
  `HybridTier` (R2) as its lexical arm. JSON-serialization is
  intentionally lossy w.r.t. structure but cheap and good enough.
* `entity_vec` — Neo4j 5.13+ native HNSW vector index over
  `:Entity.vector`, cosine similarity. Embedding dimension is fixed
  at index-creation time (see `ensure_vector_index`).
"""
from __future__ import annotations

from typing import LiteralString, cast

from neo4j import Driver

NODE_TEXT_INDEX: LiteralString = "node_text"
ENTITY_VECTOR_INDEX: LiteralString = "entity_vec"
ENTITY_VECTOR_PROPERTY: LiteralString = "vector"


def ensure_indexes(driver: Driver, database: str) -> None:
    """Create the fulltext index used by `ExactTier` if it does not exist.

    Uses Neo4j 5+ `CREATE FULLTEXT INDEX ... IF NOT EXISTS` syntax.
    Idempotent: safe to call on every process start. Raises on connection
    or syntax errors (fail-fast — a missing index is silently catastrophic
    for retrieval recall).
    """
    cypher = cast(
        LiteralString,
        f"CREATE FULLTEXT INDEX {NODE_TEXT_INDEX} IF NOT EXISTS "
        f"FOR (n:Entity) ON EACH [n.attributes_json]",
    )
    with driver.session(database=database) as s:
        s.run(cypher)


def ensure_vector_index(driver: Driver, database: str, *, dimensions: int) -> None:
    """Create the `:Entity.vector` HNSW vector index if it does not exist.

    Uses Neo4j 5.13+ `CREATE VECTOR INDEX ... IF NOT EXISTS` with cosine
    similarity. Idempotent on re-run (the IF NOT EXISTS clause is a
    name-only no-op — Neo4j does NOT verify that an existing index has
    the same dimension/metric, so callers must keep `dimensions` stable
    for the lifetime of the index).

    Raises on connection or syntax errors (fail-fast). Dimension must be
    a positive integer; the embedder owns this number.
    """
    if not isinstance(dimensions, int) or dimensions < 1:
        raise ValueError(
            f"dimensions must be a positive int, got {dimensions!r}"
        )
    cypher = cast(
        LiteralString,
        f"CREATE VECTOR INDEX {ENTITY_VECTOR_INDEX} IF NOT EXISTS "
        f"FOR (n:Entity) ON n.{ENTITY_VECTOR_PROPERTY} "
        f"OPTIONS {{indexConfig: {{"
        f"`vector.dimensions`: {dimensions}, "
        f"`vector.similarity_function`: 'cosine'"
        f"}}}}",
    )
    with driver.session(database=database) as s:
        s.run(cypher)
