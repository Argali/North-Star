"""
Provider-agnostic embedding generation.

Supports:
  - OpenAI (default)  — "text-embedding-3-small" / "text-embedding-3-large"
  - Local             — any sentence-transformers model (no API key required)
                        requires: pip install north-star[local]

Usage:

    from src.utils.embeddings import get_provider

    provider = get_provider()                    # reads EMBEDDING_PROVIDER from env
    vector = await provider.embed("some text")   # list[float]
    vectors = await provider.embed_batch(["a", "b", "c"])

    # Explicit provider:
    from src.utils.embeddings import OpenAIEmbeddings
    p = OpenAIEmbeddings(api_key="sk-...", model="text-embedding-3-small")
    vector = await p.embed("test")

The `embed` interface always returns `list[float]`.
Dimension depends on the model — must match EMBEDDING_DIM in settings / the
VECTOR(N) column size in the database.
"""
from __future__ import annotations

import asyncio
import os
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

import numpy as np

from src.config import settings

if TYPE_CHECKING:
    pass


# ── Abstract base ─────────────────────────────────────────────────────────────

class EmbeddingProvider(ABC):
    """Minimal interface all providers must implement."""

    @property
    @abstractmethod
    def dim(self) -> int:
        """Output vector dimension."""

    @abstractmethod
    async def embed(self, text: str) -> list[float]:
        """Embed a single string. Returns a list of `dim` floats."""

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """
        Embed a list of strings.

        Default implementation calls `embed` concurrently.
        Providers with native batch APIs should override this.
        """
        results = await asyncio.gather(*(self.embed(t) for t in texts))
        return list(results)

    @staticmethod
    def cosine_similarity(a: list[float], b: list[float]) -> float:
        """Convenience: cosine similarity between two vectors (0–1)."""
        va = np.array(a, dtype=np.float32)
        vb = np.array(b, dtype=np.float32)
        norm_a = np.linalg.norm(va)
        norm_b = np.linalg.norm(vb)
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return float(np.dot(va, vb) / (norm_a * norm_b))


# ── OpenAI provider ───────────────────────────────────────────────────────────

class OpenAIEmbeddings(EmbeddingProvider):
    """
    OpenAI embedding provider.

    Models:
      text-embedding-3-small  → 1536 dims  (default, cost-efficient)
      text-embedding-3-large  → 3072 dims  (higher quality)
      text-embedding-ada-002  → 1536 dims  (legacy)

    Requires: OPENAI_API_KEY env var (or explicit api_key argument).
    """

    _DIM_MAP: dict[str, int] = {
        "text-embedding-3-small": 1536,
        "text-embedding-3-large": 3072,
        "text-embedding-ada-002": 1536,
    }

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
    ) -> None:
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:
            raise ImportError(
                "openai package is required for OpenAIEmbeddings. "
                "Install it with: pip install openai"
            ) from exc

        self._model = model or settings.embedding_model
        self._client = AsyncOpenAI(api_key=api_key or settings.openai_api_key or None)
        self._dim = self._DIM_MAP.get(self._model, settings.embedding_dim)

    @property
    def dim(self) -> int:
        return self._dim

    async def embed(self, text: str) -> list[float]:
        """Embed a single string using the OpenAI embeddings API."""
        text = text.replace("\n", " ").strip()
        response = await self._client.embeddings.create(
            input=[text],
            model=self._model,
        )
        return response.data[0].embedding

    async def embed_batch(self, texts: list[str], batch_size: int = 512) -> list[list[float]]:
        """
        Embed a list of strings using OpenAI's native batch endpoint.

        Splits into chunks of `batch_size` to stay within API limits.
        """
        texts = [t.replace("\n", " ").strip() for t in texts]
        all_embeddings: list[list[float]] = []

        for i in range(0, len(texts), batch_size):
            chunk = texts[i : i + batch_size]
            response = await self._client.embeddings.create(
                input=chunk,
                model=self._model,
            )
            # OpenAI guarantees the response order matches the input order
            all_embeddings.extend(item.embedding for item in response.data)

        return all_embeddings


# ── Local provider ────────────────────────────────────────────────────────────

class LocalEmbeddings(EmbeddingProvider):
    """
    Local embedding provider using sentence-transformers.

    No API key required — runs entirely on your machine.
    Requires: pip install north-star[local]

    Recommended models:
      all-MiniLM-L6-v2   →  384 dims  (fast, lightweight)
      all-mpnet-base-v2  →  768 dims  (higher quality)

    Note: if you use a local model, set EMBEDDING_DIM to match the model's
    output dimension and re-run `alembic upgrade head` (or create a new
    migration) to adjust the VECTOR column size in the database.
    """

    def __init__(self, model: str | None = None) -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise ImportError(
                "sentence-transformers is required for LocalEmbeddings. "
                "Install it with: pip install north-star[local]"
            ) from exc

        self._model_name = model or settings.embedding_model
        self._st = SentenceTransformer(self._model_name)
        # Determine dim from a dummy encode
        sample = self._st.encode("test", convert_to_numpy=True)
        self._dim = int(sample.shape[-1])

    @property
    def dim(self) -> int:
        return self._dim

    async def embed(self, text: str) -> list[float]:
        """Embed a single string. Runs encode in a thread to avoid blocking."""
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None, lambda: self._st.encode(text, convert_to_numpy=True)
        )
        return result.tolist()

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Batch encode — sentence-transformers is optimised for batches."""
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None, lambda: self._st.encode(texts, convert_to_numpy=True)
        )
        return result.tolist()


# ── Factory ───────────────────────────────────────────────────────────────────

def get_provider(
    provider: str | None = None,
    **kwargs,
) -> EmbeddingProvider:
    """
    Return the configured embedding provider.

    Reads EMBEDDING_PROVIDER from settings unless overridden by the argument.

    Args:
        provider: "openai" | "local" (overrides settings)
        **kwargs: forwarded to the provider constructor

    Returns:
        EmbeddingProvider instance ready to call .embed() / .embed_batch()

    Example:
        p = get_provider()                  # from EMBEDDING_PROVIDER env var
        p = get_provider("local")           # force local, no API key needed
        p = get_provider("openai", api_key="sk-...")
    """
    name = (provider or settings.embedding_provider).lower()

    if name == "openai":
        return OpenAIEmbeddings(**kwargs)
    elif name == "local":
        return LocalEmbeddings(**kwargs)
    else:
        raise ValueError(
            f"Unknown embedding provider: {name!r}. "
            "Valid options: 'openai', 'local'."
        )
