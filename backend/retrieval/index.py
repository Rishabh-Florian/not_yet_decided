"""Neo4j retrieval indexes — idempotent bootstrap.

Tier modules call `ensure_indexes()` at construction time. The fulltext
index name (`node_text`) is shared across tiers — R1 reads it, later
tiers (R2 hybrid) may reuse it for the BM25 leg of a vector+BM25 fuse.
Keep the name stable.

The index targets `attributes_json` because that's where every node's
human-readable content (names, titles, emails, descriptions) lives in
the canonical store (see `backend/graph/store.py`). It's a single
Lucene-tokenized text field — Neo4j fulltext over JSON-serialized
attributes is intentionally lossy w.r.t. structure but cheap and good
enough for short-phrase recall in this tier.
"""
from __future__ import annotations

from typing import LiteralString, cast

from neo4j import Driver

NODE_TEXT_INDEX: LiteralString = "node_text"


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
