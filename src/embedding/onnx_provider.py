"""ONNX embedding provider for BGE-M3.

Purpose:
    Provide a memory-efficient local inference backend for BGE-M3 using
    ONNX Runtime directly (bypassing Optimum wrapper).

Features:
    - Lazy-load from pre-exported ONNX model artifacts
    - CPU-only inference via onnxruntime
    - Dense vector encoding compatible with `DualEmbeddingProvider.encode`
    - Sparse TF lexical weights + token-level vectors for hybrid/ColBERT paths
    - Uses sentence_embedding output directly (already pooled, 1024-dim)

Usage:
    from src.embedding.onnx_provider import OnnxBgeEmbeddingProvider

    provider = OnnxBgeEmbeddingProvider(model_name="BAAI/bge-m3")
    output = provider.encode(["sample query"])
    vectors = output["dense_vecs"]

Examples:
    >>> provider = OnnxBgeEmbeddingProvider(model_name="BAAI/bge-m3")
    >>> output = provider.encode(["안녕하세요"])
    >>> isinstance(output["dense_vecs"], list)
    True
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any

import numpy as np

from src.config_weights import weights

logger = logging.getLogger(__name__)

__all__ = ["OnnxBgeEmbeddingProvider"]

_COLBERT_MAX_TOKENS = int(os.getenv("KNOWLEDGE_BGE_COLBERT_MAX_TOKENS", "128"))


class OnnxBgeEmbeddingProvider:
    """ONNX Runtime-backed BGE-M3 embedding provider.

    Uses onnxruntime.InferenceSession directly instead of Optimum's
    ORTModelForFeatureExtraction, because the sentence-transformers ONNX
    export uses output names (token_embeddings, sentence_embedding) that
    differ from what Optimum expects (last_hidden_state).
    """

    backend = "onnx"
    _DENSE_DIM: int = weights.embedding.dimension

    def __init__(
        self,
        model_name: str = "",
        model_path: str | None = None,
        use_fp16: bool = True,
        use_sparse: bool = True,
        max_length: int = weights.embedding.onnx_max_length,
        onnx_file_name: str = os.getenv("KNOWLEDGE_BGE_ONNX_FILE_NAME", "model.onnx"),
    ):
        from src.config import DEFAULT_EMBEDDING_MODEL_HF

        self._model_name = model_name or DEFAULT_EMBEDDING_MODEL_HF
        self._model_path = (model_path or "").strip()
        self._use_fp16 = use_fp16
        self._use_sparse = use_sparse
        self._max_length = max_length
        self._onnx_file_name = onnx_file_name
        self._session: Any | None = None
        self._tokenizer: Any | None = None
        self._output_names: list[str] = []
        self._ready = False
        # P1: Thread-safe LRU cache for query embeddings (512 entries, ~4MB)
        self._cache: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._cache_lock = threading.Lock()
        self._cache_max = int(os.getenv("KNOWLEDGE_EMBEDDING_CACHE_SIZE", str(weights.embedding.cache_size)))
        self._cache_hits = 0
        self._cache_misses = 0

    def is_ready(self) -> bool:
        """Return whether ONNX session and tokenizer are initialized."""
        if self._ready:
            return True
        self._ensure_model()
        return self._ready

    def _resolve_source(self) -> str:
        """Determine model source path.

        Priority:
        1. Explicit model_path constructor arg
        2. KNOWLEDGE_BGE_ONNX_MODEL_PATH env var
        3. S3-downloaded cache at /tmp/bge-m3-cache/onnx (created by
           DualEmbeddingProvider._download_onnx_from_s3 at startup)
        4. model_name (PVC mount path)
        """
        if self._model_path:
            return self._model_path
        env_path = os.getenv("KNOWLEDGE_BGE_ONNX_MODEL_PATH", "").strip()
        if env_path:
            return env_path
        # Fallback: S3-downloaded ONNX cache
        # Check for BOTH model.onnx AND model.onnx_data (2.2GB weights file)
        s3_cache = os.getenv("KNOWLEDGE_BGE_S3_CACHE_DIR", "/tmp/bge-m3-cache/onnx")
        onnx_model = os.path.join(s3_cache, self._onnx_file_name)
        onnx_data = os.path.join(s3_cache, "model.onnx_data")
        if os.path.isfile(onnx_model) and os.path.isfile(onnx_data):
            return s3_cache
        if os.path.isfile(onnx_model) and not os.path.isfile(onnx_data):
            logger.warning(
                "ONNX model.onnx found but model.onnx_data missing at %s "
                "(incomplete S3 download)",
                s3_cache,
            )
        return self._model_name

    def _ensure_model(self) -> None:
        """Initialize ONNX InferenceSession and tokenizer lazily."""
        if self._ready:
            return

        import onnxruntime as ort
        from transformers import AutoTokenizer

        source = self._resolve_source()
        onnx_path = Path(source) / self._onnx_file_name
        if not onnx_path.exists():
            raise FileNotFoundError(f"ONNX model file not found: {onnx_path}")

        sess_options = ort.SessionOptions()
        sess_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        # P0: Thread tuning for 1.3-2x speedup
        cpu_count = os.cpu_count() or 4
        sess_options.intra_op_num_threads = cpu_count
        sess_options.inter_op_num_threads = min(2, cpu_count)
        sess_options.execution_mode = ort.ExecutionMode.ORT_PARALLEL

        # Prefer CoreML/Metal on Apple Silicon, fallback to CPU
        providers = ["CPUExecutionProvider"]
        available = ort.get_available_providers()
        if "CoreMLExecutionProvider" in available:
            providers.insert(0, "CoreMLExecutionProvider")

        self._session = ort.InferenceSession(
            str(onnx_path),
            sess_options=sess_options,
            providers=providers,
        )
        self._output_names = [o.name for o in self._session.get_outputs()]
        logger.debug(
            "ONNX session created: outputs=%s", self._output_names,
        )

        self._tokenizer = AutoTokenizer.from_pretrained(source)
        self._ready = True

    def _check_cache(self, cache_key: str) -> dict[str, Any] | None:
        """Check LRU cache, return cached result or None."""
        with self._cache_lock:
            if cache_key in self._cache:
                self._cache_hits += 1
                self._cache.move_to_end(cache_key)
                return self._cache[cache_key]
            self._cache_misses += 1
        return None

    def encode(
        self,
        texts: list[str],
        return_dense: bool = True,
        return_sparse: bool = True,
        return_colbert_vecs: bool = False,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Encode text batch and return FlagEmbedding-compatible key set.

        Args:
            texts: Input text batch.
            return_dense: Whether to generate dense embeddings.
            return_sparse: Sparse not supported by ONNX; returns empty dicts.
            return_colbert_vecs: ColBERT not supported by ONNX; returns empty lists.
            kwargs: Compatibility parameters (batch_size, use_fp16, etc.).
        """
        empty = {"dense_vecs": [], "lexical_weights": [], "colbert_vecs": []}
        if not texts:
            return empty

        if not self.is_ready():
            raise RuntimeError("ONNX BGE-M3 provider is not available")

        if self._session is None or self._tokenizer is None:
            return empty

        # P1: Thread-safe LRU cache for single-text queries (search hot path)
        if len(texts) == 1 and not return_colbert_vecs:
            cache_key = f"{texts[0]}::d={return_dense}::s={return_sparse}"
            cached = self._check_cache(cache_key)
            if cached is not None:
                return cached

        # Tokenize
        encoded = self._tokenizer(
            texts,
            padding=True,
            truncation=True,
            max_length=self._max_length,
            return_tensors="np",  # numpy for direct ONNX Runtime input
        )

        # Run ONNX inference
        feed = {
            "input_ids": encoded["input_ids"].astype(np.int64),
            "attention_mask": encoded["attention_mask"].astype(np.int64),
        }
        raw_outputs = self._session.run(None, feed)

        # Build output name -> numpy array mapping
        output_map = dict(zip(self._output_names, raw_outputs, strict=False))

        # Extract dense vectors
        if return_dense:
            dense_vecs = self._extract_dense(output_map, encoded["attention_mask"], len(texts))
        else:
            dense_vecs = [[] for _ in texts]

        # Extract sparse lexical weights from input token ids.
        # ONNX export does not provide BGE lexical weights directly, so we
        # synthesize normalized TF-style sparse vectors for retrieval fusion.
        if return_sparse:
            sparse_vecs = self._extract_sparse(
                encoded["input_ids"],
                encoded["attention_mask"],
            )
        else:
            sparse_vecs = []

        if return_colbert_vecs:
            colbert_vecs = self._extract_colbert(output_map, encoded["attention_mask"])
        else:
            colbert_vecs = []

        result = {
            "dense_vecs": dense_vecs,
            "lexical_weights": sparse_vecs,
            "colbert_vecs": colbert_vecs,
        }

        # P1: Store in thread-safe LRU cache for single-text queries
        if len(texts) == 1 and not return_colbert_vecs:
            cache_key = f"{texts[0]}::d={return_dense}::s={return_sparse}"
            with self._cache_lock:
                if len(self._cache) >= self._cache_max:
                    # Evict LRU entry (oldest in OrderedDict)
                    self._cache.popitem(last=False)
                self._cache[cache_key] = result

        return result

    @property
    def cache_info(self) -> dict[str, int]:
        """Return cache statistics."""
        return {
            "hits": self._cache_hits,
            "misses": self._cache_misses,
            "size": len(self._cache),
            "max_size": self._cache_max,
        }

    async def embed(self, text: str) -> list[float]:
        """Return a single dense embedding for IEmbeddingProvider compatibility.

        The ONNX path is CPU-bound and synchronous, so it is executed in a
        worker thread to avoid blocking the event loop used by API routes and
        background workers.
        """
        if not text:
            return []

        output = await asyncio.to_thread(
            self.encode,
            [text],
            True,
            False,
            False,
        )

        dense_vecs = output.get("dense_vecs", [])
        if not dense_vecs or not isinstance(dense_vecs[0], list):
            return []
        return dense_vecs[0]

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Return dense embeddings for a batch of texts."""
        if not texts:
            return []

        output = await asyncio.to_thread(
            self.encode,
            texts,
            True,
            False,
            False,
        )

        dense_vecs = output.get("dense_vecs", [])
        return dense_vecs if isinstance(dense_vecs, list) else [[] for _ in texts]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Alias for embed_documents."""
        return await self.embed_documents(texts)

    @property
    def dimension(self) -> int:
        """Dense vector dimension for BGE-M3 ONNX output."""
        return self._DENSE_DIM

    def _extract_dense(
        self,
        output_map: dict[str, Any],
        attention_mask: Any,
        expected_count: int,
    ) -> list[list[float]]:
        """Extract dense vectors from ONNX output.

        Tries sentence_embedding first (already pooled), then falls back to
        mean-pooling token_embeddings with the attention mask.
        """
        # Prefer sentence_embedding (pooled output, shape [batch, dim])
        sentence_emb = output_map.get("sentence_embedding")
        if sentence_emb is not None:
            vecs = sentence_emb.tolist()
            if len(vecs) == expected_count:
                return vecs

        # Fallback: mean-pool token_embeddings with attention_mask
        token_emb = output_map.get("token_embeddings")
        if token_emb is None:
            token_emb = output_map.get("last_hidden_state")
        if token_emb is not None:
            return self._mean_pool_numpy(token_emb, attention_mask).tolist()

        # Last resort: use first available output
        for _name, arr in output_map.items():
            if hasattr(arr, "shape") and len(arr.shape) == 2 and arr.shape[0] == expected_count:
                return arr.tolist()

        logger.warning(
            "ONNX BGE-M3: no usable dense output found",
            extra={"output_names": list(output_map.keys())},
        )
        return [[] for _ in range(expected_count)]

    @staticmethod
    def _extract_sparse(input_ids: Any, attention_mask: Any) -> list[dict[int, float]]:
        """Create sparse token-weight vectors from token ids.

        Uses normalized term-frequency weights in [0, 1].
        """
        results: list[dict[int, float]] = []
        try:
            for ids_row, mask_row in zip(input_ids, attention_mask, strict=False):
                sparse: dict[int, float] = {}
                for token_id, active in zip(ids_row, mask_row, strict=False):
                    if int(active) != 1:
                        continue
                    idx = int(token_id)
                    if idx <= 0:  # skip PAD/invalid ids
                        continue
                    sparse[idx] = sparse.get(idx, 0.0) + 1.0

                if sparse:
                    max_weight = max(sparse.values())
                    if max_weight > 0:
                        sparse = {k: v / max_weight for k, v in sparse.items()}

                results.append(sparse)
        except Exception:
            return [{} for _ in range(len(input_ids))]

        return results

    @staticmethod
    def _extract_colbert(
        output_map: dict[str, Any],
        attention_mask: Any,
    ) -> list[list[list[float]]]:
        """Extract token-level vectors for ColBERT reranking."""
        token_emb = output_map.get("token_embeddings")
        if token_emb is None:
            token_emb = output_map.get("last_hidden_state")
        if token_emb is None:
            return [[] for _ in range(len(attention_mask))]

        results: list[list[list[float]]] = []
        try:
            for token_row, mask_row in zip(token_emb, attention_mask, strict=False):
                valid_indices = np.nonzero(mask_row.astype(np.int64))[0]
                if valid_indices.size == 0:
                    results.append([])
                    continue

                if _COLBERT_MAX_TOKENS > 0:
                    valid_indices = valid_indices[:_COLBERT_MAX_TOKENS]

                vectors = token_row[valid_indices]
                norms = np.linalg.norm(vectors, axis=1, keepdims=True)
                norms = np.clip(norms, a_min=1e-12, a_max=None)
                normalized = vectors / norms
                results.append(normalized.astype(np.float32).tolist())
        except Exception:
            return [[] for _ in range(len(attention_mask))]

        return results

    def close(self) -> None:
        """Release ONNX session resources."""
        self._session = None

    @staticmethod
    def _mean_pool_numpy(token_embeddings: Any, attention_mask: Any) -> Any:
        """Mean-pool token embeddings using attention mask (numpy)."""
        mask_expanded = np.expand_dims(attention_mask, axis=-1).astype(np.float32)
        sum_embeddings = np.sum(token_embeddings * mask_expanded, axis=1)
        sum_mask = np.clip(mask_expanded.sum(axis=1), a_min=1e-9, a_max=None)
        return sum_embeddings / sum_mask
