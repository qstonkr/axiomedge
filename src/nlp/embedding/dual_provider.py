"""Dual Embedding Provider.

Orchestrates dense + sparse embedding from a single BGE-M3 model.
Wraps any of the existing providers (Ollama, ONNX, TEI) and exposes
a unified encode() interface returning both dense and sparse vectors.

Adapted from oreo-ecosystem infrastructure/embedding/dual_embedding_provider.py.
Simplified: no governor, no circuit breaker, no CUDA OOM recovery, no S3 download.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from .embedding_guard import safe_embedding_or_zero, EXPECTED_DIMENSION

logger = logging.getLogger(__name__)


@dataclass
class DualEmbedding:
    """Dual embedding result from BGE-M3.

    Attributes:
        dense: Dense vector (1024d).
        sparse: Sparse lexical weights {token_index: weight}.
        colbert: ColBERT multi-vectors (optional).
        text: Original text.
    """

    dense: list[float]
    sparse: dict[int, float] = field(default_factory=dict)
    colbert: list[list[float]] = field(default_factory=list)
    text: str = ""


class DualEmbeddingProvider:
    """Orchestrates dense + sparse from a single BGE-M3 model provider.

    Wraps an underlying provider (OllamaEmbeddingProvider, OnnxBgeEmbeddingProvider,
    or TEIEmbeddingProvider) that exposes a synchronous encode() method returning
    a FlagEmbedding-compatible dict with dense_vecs, lexical_weights, colbert_vecs.

    Usage::

        from src.nlp.embedding.dual_provider import DualEmbeddingProvider
        from src.nlp.embedding.ollama_provider import OllamaEmbeddingProvider

        ollama = OllamaEmbeddingProvider()
        dual = DualEmbeddingProvider(provider=ollama)

        result = await dual.embed_dual("검색 쿼리")
        print(result.dense)   # [0.012, -0.034, ...]
        print(result.sparse)  # {1234: 0.8, 5678: 0.5, ...}
    """

    def __init__(
        self,
        provider: Any,
        use_sparse: bool = True,
        use_colbert: bool = False,
        batch_size: int = 32,
        max_text_chars: int = 4000,
    ):
        """Initialize DualEmbeddingProvider.

        Args:
            provider: Underlying embedding provider with encode() method.
            use_sparse: Whether to request sparse embeddings.
            use_colbert: Whether to request ColBERT multi-vectors.
            batch_size: Batch size for encoding.
            max_text_chars: Maximum text length (truncated).
        """
        self._provider = provider
        self._use_sparse = use_sparse
        self._use_colbert = use_colbert
        self._batch_size = max(1, batch_size)
        self._max_text_chars = max(1, max_text_chars)

    def _sanitize_texts(self, texts: list[str]) -> list[str]:
        """Truncate texts to max_text_chars."""
        return [t[: self._max_text_chars] for t in texts] if texts else []

    async def embed_dual(self, text: str) -> DualEmbedding:
        """Single text dual embedding."""
        batch = await self.embed_dual_batch([text])
        if batch:
            return batch[0]
        return DualEmbedding(dense=[], sparse={}, colbert=[], text=text)

    async def embed_dual_batch(self, texts: list[str]) -> list[DualEmbedding]:
        """Batch dual embedding.

        Returns list of DualEmbedding, one per input text.
        """
        if not texts:
            return []

        texts = self._sanitize_texts(texts)

        output = await self._run_encode(
            texts,
            return_dense=True,
            return_sparse=self._use_sparse,
            return_colbert=self._use_colbert,
        )

        if output is None:
            return [
                DualEmbedding(dense=[], sparse={}, colbert=[], text=t) for t in texts
            ]

        dense_vecs = output.get("dense_vecs", [])
        sparse_vecs = output.get("lexical_weights", [])
        colbert_vecs = output.get("colbert_vecs", [])

        results = []
        for i, text in enumerate(texts):
            dense = dense_vecs[i] if i < len(dense_vecs) else []
            # Guard: validate dense vector
            dense = safe_embedding_or_zero(
                dense if dense else None,
                expected_dim=self.dimension,
            )
            sparse = sparse_vecs[i] if i < len(sparse_vecs) else {}
            colbert = colbert_vecs[i] if i < len(colbert_vecs) else []
            results.append(DualEmbedding(dense=dense, sparse=sparse, colbert=colbert, text=text))

        return results

    async def embed(self, text: str) -> list[float]:
        """IEmbeddingProvider compatible: dense vector only."""
        output = await self._run_encode([text], return_dense=True, return_sparse=False, return_colbert=False)
        if output:
            vecs = output.get("dense_vecs", [])
            if vecs and vecs[0]:
                return safe_embedding_or_zero(vecs[0], expected_dim=self.dimension)
        return [0.0] * self.dimension

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """IEmbeddingProvider compatible: batch dense vectors."""
        if not texts:
            return []
        texts = self._sanitize_texts(texts)
        output = await self._run_encode(texts, return_dense=True, return_sparse=False, return_colbert=False)
        if output:
            vecs = output.get("dense_vecs", [])
            return [
                safe_embedding_or_zero(
                    vecs[i] if i < len(vecs) and vecs[i] else None,
                    expected_dim=self.dimension,
                )
                for i in range(len(texts))
            ]
        return [[0.0] * self.dimension for _ in texts]

    @property
    def dimension(self) -> int:
        """Dense vector dimension."""
        if hasattr(self._provider, "DIMENSION"):
            return self._provider.DIMENSION
        if hasattr(self._provider, "dimension"):
            return self._provider.dimension
        return EXPECTED_DIMENSION

    async def _run_encode(
        self,
        texts: list[str],
        return_dense: bool = True,
        return_sparse: bool = True,
        return_colbert: bool = False,
    ) -> dict[str, Any] | None:
        """Run the underlying provider's encode() in a thread."""
        encode = getattr(self._provider, "encode", None)
        if encode is None or not callable(encode):
            logger.warning("Underlying provider has no encode() method")
            return None

        try:
            result = await asyncio.to_thread(
                encode,
                texts,
                return_dense,
                return_sparse,
                return_colbert,
            )
            return result
        except Exception as e:  # noqa: BLE001
            logger.error("Dual embedding encode failed: %s", e)
            return None
