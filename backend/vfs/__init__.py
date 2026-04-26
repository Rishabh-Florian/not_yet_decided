"""Virtual File System — a path-style retrieval lens over the knowledge graph.

The VFS is **derived**, not stored. Every path resolves to a Cypher / SQLite
read against the existing graph; nothing is materialized to disk and no
`vfs_path` column needs to be populated at ingest. The path tree shape is
fixed at two levels and follows the canonical type registry directly:

    /                       -> list canonical node types
    /{Type}/                -> list nodes of that canonical type, by id
    /{Type}/{node_id}       -> one node (cat target)

Six operations are exposed (ls, cat, grep, find, stat, tree). They are
consumed exclusively by the AgenticTier's tool surface (see
`backend.retrieval.tools`); there is no REST router and no frontend
dependency. MCP wrapping is intentionally a separate concern.
"""

from .models import DirEntry, FileBody, GrepHit, NeighborRef, StatInfo, TreeNode
from .operations import VFS

__all__ = [
    "VFS",
    "DirEntry",
    "FileBody",
    "GrepHit",
    "NeighborRef",
    "StatInfo",
    "TreeNode",
]
