"""HuggingFace Text Embeddings Inference (TEI) Provider.

Calls the dedicated BGE-M3 embedding server via HTTP.
Fastest option - no model loading in the app process.

Usage:
    provider = TEIEmbeddingProvider(base_url="http://localhost:8080")
    output = provider.encode(["검색 쿼리"])
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from src.config.weights import weights as _w

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
    _DENSE_DIM: int = _w.embedding.dimension

    def __init__(self, base_url: str | None = None, timeout: float = 300.0) -> None:
        from src.config import get_settings
        self._base_url = (
            base_url or get_settings().tei.embedding_url
        ).rstrip("/")
        self._timeout = timeout
        self._client: httpx.Client | None = None

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=self._timeout)
        return self._client

    def _post_batch(self, client: httpx.Client, batch: list[str]) -> list[list[float]]:
        resp = client.post(
            f"{self._base_url}/embed",
            json={"inputs": batch, "truncate": True},
        )
        resp.raise_for_status()
        return resp.json()

    def is_ready(self) -> bool:
        try:
            resp = self._get_client().get(f"{self._base_url}/health")
            return resp.status_code == 200
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError):
            return False

    # TEI server constraint: max_batch_tokens=16384, max_client_batch_size=32.
    # Estimate ~3 chars/token for mixed KR/EN; keep batch budget well below the
    # server limit so long legal-doc chunks don't overflow.
    _MAX_CHARS_PER_BATCH = 30_000
    _MAX_ITEMS_PER_BATCH = 32

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

        # Only hit the TEI server when a dense vector is actually requested —
        # sparse is synthesized locally, so sparse-only calls must not pay for
        # an HTTP round trip (previously they silently duplicated the dense
        # encoding path and doubled ingestion wall time).
        dense_vecs: list[list[float]] = []
        if return_dense:
            client = self._get_client()
            embeddings: list[list[float]] = []
            batch: list[str] = []
            batch_chars = 0
            for text in texts:
                text_chars = len(text)
                if batch and (
                    batch_chars + text_chars > self._MAX_CHARS_PER_BATCH
                    or len(batch) >= self._MAX_ITEMS_PER_BATCH
                ):
                    embeddings.extend(self._post_batch(client, batch))
                    batch = []
                    batch_chars = 0
                batch.append(text)
                batch_chars += text_chars
            if batch:
                embeddings.extend(self._post_batch(client, batch))
            dense_vecs = [list(emb) for emb in embeddings]

        # TEI doesn't produce sparse vectors natively
        # Synthesize TF-based sparse (same as ONNX/Ollama providers)
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
        return self._DENSE_DIM

    def close(self) -> None:
        if self._client:
            self._client.close()
            self._client = None
