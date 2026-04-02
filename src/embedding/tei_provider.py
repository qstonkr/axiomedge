"""HuggingFace Text Embeddings Inference (TEI) Provider.

Calls the dedicated BGE-M3 embedding server via HTTP.
Fastest option - no model loading in the app process.

Usage:
    provider = TEIEmbeddingProvider(base_url="http://localhost:8080")
    output = provider.encode(["검색 쿼리"])
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from src.config_weights import weights as _w

logger = logging.getLogger(__name__)

__all__ = ["TEIEmbeddingProvider"]


class TEIEmbeddingProvider:
    """HuggingFace TEI-backed embedding provider.

    Advantages:
    - No model loading in app process (0 memory overhead)
    - Optimized C++ inference backend (faster than ONNX Python)
    - Shared across all services (API, CLI, ingestion)
    - Supports batch embedding natively
    """

    backend = "tei"
    DIMENSION: int = _w.embedding.dimension

    def __init__(self, base_url: str | None = None, timeout: float = 60.0):
        self._base_url = (
            base_url or os.getenv("BGE_TEI_URL", "http://localhost:8080")
        ).rstrip("/")
        self._timeout = timeout
        self._client: httpx.Client | None = None

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=self._timeout)
        return self._client

    def is_ready(self) -> bool:
        try:
            resp = self._get_client().get(f"{self._base_url}/health")
            return resp.status_code == 200
        except Exception:
            return False

    def encode(
        self,
        texts: list[str],
        return_dense: bool = True,
        return_sparse: bool = True,
        return_colbert_vecs: bool = False,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Encode via TEI /embed endpoint."""
        if not texts:
            return {"dense_vecs": [], "lexical_weights": [], "colbert_vecs": []}

        client = self._get_client()
        resp = client.post(
            f"{self._base_url}/embed",
            json={"inputs": texts},
        )
        resp.raise_for_status()
        embeddings = resp.json()

        dense_vecs = [list(emb) for emb in embeddings] if return_dense else []

        # TEI doesn't produce sparse vectors natively
        # Synthesize TF-based sparse (same as ONNX/Ollama providers)
        sparse_vecs = []
        if return_sparse:
            from src.embedding.embedding_guard import sparse_token_hash
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
        import asyncio
        result = await asyncio.to_thread(self.encode, [text], True, False, False)
        dense = result.get("dense_vecs", [])
        return dense[0] if dense and dense[0] else []

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        import asyncio
        result = await asyncio.to_thread(self.encode, texts, True, False, False)
        return result.get("dense_vecs", [])

    @property
    def dimension(self) -> int:
        return self.DIMENSION

    def close(self) -> None:
        if self._client:
            self._client.close()
            self._client = None
