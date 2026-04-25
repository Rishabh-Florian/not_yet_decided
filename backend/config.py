"""Single env-loading entry point.

Imports `.env` (via python-dotenv) at module-import time. After this module
is imported, every other backend module reads from `os.environ` directly —
no scattered `load_dotenv()` calls, no race conditions on import order.

Looks for `.env` walking up from the project root (so `python -m backend...`
from any cwd still finds it).
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


def _find_dotenv() -> Path | None:
    here = Path(__file__).resolve().parent  # backend/
    for parent in [here, *here.parents]:
        candidate = parent / ".env"
        if candidate.is_file():
            return candidate
    return None


_DOTENV_PATH = _find_dotenv()
if _DOTENV_PATH is not None:
    load_dotenv(_DOTENV_PATH, override=False)


def require(name: str) -> str:
    """Fail-fast accessor for required env vars."""
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(
            f"required env var {name} not set "
            f"(checked .env at {_DOTENV_PATH or '<none found>'})"
        )
    return val


GEMINI_API_KEY: str | None = os.environ.get("GEMINI_API_KEY")
NEO4J_URI: str = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER: str = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD: str = os.environ.get("NEO4J_PASSWORD", "neo4j")
NEO4J_DATABASE: str = os.environ.get("NEO4J_DATABASE", "neo4j")
