"""Embedder protocol + two implementations.

Two implementations live behind a single `Embedder` Protocol so the
hybrid tier and the offline `embed.py` CLI stay agnostic of the model
backend:

* `BgeSmallEmbedder` — `BAAI/bge-small-en-v1.5` via
  ``sentence-transformers`` (per the issue spec). 384-dim, CPU, lazily
  loaded on first call so import is cheap. Raises on construction if
  the optional ``sentence-transformers`` dependency is not installed.
* `StubEmbedder` — deterministic SHA-256-derived 384-dim float vector,
  L2-normalized. Used by unit tests and as a fallback so the rest of
  the system is exercisable without the model download. Embeddings
  carry no semantic signal — DO NOT use for production retrieval.

The ``Hit.score`` produced by a hybrid query is documented at the call
site (RRF — see `hybrid.py`); the embedder itself does not score.
"""
from __future__ import annotations

import hashlib
import math
from typing import Protocol, runtime_checkable

EMBEDDING_DIM: int = 384


@runtime_checkable
class Embedder(Protocol):
    """Query- and document-side embedding contract.

    Implementations MUST return a list of `dim` floats. Vectors are
    expected (but not enforced here) to be L2-normalized so cosine
    similarity reduces to a dot product — Neo4j's
    `vector.similarity_function: 'cosine'` does the normalization
    server-side regardless, but normalized inputs make scores
    interpretable in [-1, 1].
    """

    @property
    def dim(self) -> int:
        ...

    def embed(self, text: str) -> list[float]:
        ...


def _l2_normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0.0:
        # Degenerate input. Caller passed an empty/zero text; the
        # downstream cosine query would yield NaN scores. Fail fast.
        raise ValueError("cannot L2-normalize a zero vector")
    return [x / norm for x in vec]


class StubEmbedder:
    """Deterministic hash-based embedder for tests / no-network fallback.

    Maps any text to a 384-dim L2-normalized vector by repeatedly
    hashing ``text`` with a counter and turning each byte into a
    centered float in ``[-0.5, 0.5)``. Two different strings produce
    two different vectors; the same string always produces the same
    vector. There is NO semantic structure — embeddings cluster
    randomly, not by meaning. Used to exercise the hybrid pipeline
    end-to-end in tests without pulling a real model.
    """

    def __init__(self, dim: int = EMBEDDING_DIM) -> None:
        if dim < 1:
            raise ValueError(f"dim must be >= 1, got {dim}")
        self._dim = dim

    @property
    def dim(self) -> int:
        return self._dim

    def embed(self, text: str) -> list[float]:
        if not isinstance(text, str):
            raise TypeError(f"text must be str, got {type(text).__name__}")
        if not text.strip():
            raise ValueError("text must be non-empty / non-whitespace")
        out: list[float] = []
        counter = 0
        while len(out) < self._dim:
            digest = hashlib.sha256(f"{counter}:{text}".encode("utf-8")).digest()
            for byte in digest:
                out.append((byte / 255.0) - 0.5)
                if len(out) == self._dim:
                    break
            counter += 1
        return _l2_normalize(out)


class BgeSmallEmbedder:
    """`BAAI/bge-small-en-v1.5` via ``sentence-transformers``.

    384-dim, CPU-only is fine. Lazy-loads the model on first
    `embed()` call (~3s warmup). Raises a clear ``ImportError`` at
    construction time if ``sentence-transformers`` is not installed —
    the package is intentionally optional so the rest of the system
    runs without the ~150MB model download in CI.
    """

    MODEL_NAME = "BAAI/bge-small-en-v1.5"

    def __init__(self) -> None:
        try:
            import sentence_transformers  # type: ignore[import-not-found]  # noqa: F401
        except ImportError as e:
            raise ImportError(
                "BgeSmallEmbedder requires the optional `sentence-transformers` "
                "dependency. Install with `uv add sentence-transformers` or "
                "use `StubEmbedder` for tests."
            ) from e
        self._model: object | None = None

    @property
    def dim(self) -> int:
        return EMBEDDING_DIM

    def _ensure_model(self) -> object:
        if self._model is None:
            from sentence_transformers import (  # type: ignore[import-not-found]
                SentenceTransformer,
            )

            self._model = SentenceTransformer(self.MODEL_NAME)
        return self._model

    def embed(self, text: str) -> list[float]:
        if not isinstance(text, str):
            raise TypeError(f"text must be str, got {type(text).__name__}")
        if not text.strip():
            raise ValueError("text must be non-empty / non-whitespace")
        model = self._ensure_model()
        # `normalize_embeddings=True` returns L2-normalized vectors so
        # the dot product equals cosine similarity.
        vec = model.encode(  # type: ignore[attr-defined]
            text, normalize_embeddings=True
        )
        return [float(x) for x in vec.tolist()]
