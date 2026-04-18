"""Ollama Embedding Provider.

Uses Ollama's /api/embed endpoint for BGE-M3 embedding.
On Apple Silicon, Ollama leverages Metal GPU for 2-5x faster inference
compared to ONNX CPU.

Usage:
    provider = OllamaEmbeddingProvider(base_url="http://localhost:11434")
    output = provider.encode(["검색 쿼리"])
    dense = output["dense_vecs"][0]  # 1024d vector
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from src.config.weights import weights as _w

logger = logging.getLogger(__name__)

__all__ = ["OllamaEmbeddingProvider"]


class OllamaEmbeddingProvider:
    """Ollama-backed embedding provider using /api/embed.

    Advantages over ONNX:
    - Metal GPU acceleration on Apple Silicon (2-5x faster)
    - No ONNX model download required (Ollama manages models)
    - Supports batch embedding natively

    Limitations:
    - Only produces dense vectors (no native sparse/ColBERT)
    - Requires Ollama server running with bge-m3 model pulled
    """

    backend = "ollama"
    _DENSE_DIM: int = _w.embedding.dimension

    def __init__(
        self,
        base_url: str | None = None,
        model: str = "",
        timeout: float = 300.0,
    ) -> None:
        from src.config import DEFAULT_EMBEDDING_MODEL, get_settings

        self._base_url = (base_url or get_settings().ollama.base_url).rstrip("/")
        self._model = model or DEFAULT_EMBEDDING_MODEL
        self._timeout = timeout
        self._client: httpx.Client | None = None

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=self._timeout)
        return self._client

    def is_ready(self) -> bool:
        """Check if Ollama embedding model is available."""
        try:
            client = self._get_client()
            resp = client.get(f"{self._base_url}/api/tags")
            if resp.status_code != 200:
                return False
            models = [m.get("name", "") for m in resp.json().get("models", [])]
            return any(self._model.split(":")[0] in m for m in models)
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError):
            return False

    def encode(
        self,
        texts: list[str],
        return_dense: bool = True,
        return_sparse: bool = True,
        return_colbert_vecs: bool = False,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Encode texts via Ollama /api/embed endpoint.

        Returns FlagEmbedding-compatible dict with dense_vecs.
        Sparse vectors are synthesized from token frequency (same as ONNX provider).
        ColBERT vectors are not supported.
        """
        empty = {"dense_vecs": [], "lexical_weights": [], "colbert_vecs": []}
        if not texts:
            return empty

        # Batch embedding to avoid Ollama timeout on large inputs
        BATCH_SIZE = 5
        client = self._get_client()
        embeddings = []
        for i in range(0, len(texts), BATCH_SIZE):
            batch = texts[i:i + BATCH_SIZE]
            resp = client.post(
                f"{self._base_url}/api/embed",
                json={"model": self._model, "input": batch},
            )
            resp.raise_for_status()
            data = resp.json()
            embeddings.extend(data.get("embeddings", []))

        if not embeddings:
            return empty

        dense_vecs = [list(emb) for emb in embeddings]

        # Synthesize sparse vectors (TF-based, same approach as ONNX provider)
        sparse_vecs = []
        if return_sparse:
            from src.nlp.embedding.embedding_guard import sparse_token_hash
            for text in texts:
                tokens = text.split()
                sparse: dict[int, float] = {}
                for token in tokens:
                    h = sparse_token_hash(token)
                    sparse[h] = sparse.get(h, 0.0) + 1.0
                if sparse:
                    max_w = max(sparse.values())
                    sparse = {k: v / max_w for k, v in sparse.items()}
                sparse_vecs.append(sparse)

        return {
            "dense_vecs": dense_vecs,
            "lexical_weights": sparse_vecs,
            "colbert_vecs": [],
        }

    async def embed(self, text: str) -> list[float]:
        """Single text embedding (async)."""
        result = await asyncio.to_thread(self.encode, [text], True, False, False)
        dense = result.get("dense_vecs", [])
        return dense[0] if dense and dense[0] else []

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Batch embedding (async)."""
        result = await asyncio.to_thread(self.encode, texts, True, False, False)
        return result.get("dense_vecs", [[] for _ in texts])

    @property
    def dimension(self) -> int:
        return self._DENSE_DIM

    def close(self) -> None:
        if self._client:
            self._client.close()
            self._client = None
