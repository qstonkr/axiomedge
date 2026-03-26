"""Cross-Encoder Reranker for search result re-ranking.

Adapted from oreo-ecosystem cross_encoder.py.
Uses BGE-Reranker-v2-m3 (multilingual/Korean) via sentence-transformers.

Features:
- Batch inference with configurable batch size
- Singleton model loading (lazy)
- Graceful degradation when model unavailable
- Sigmoid score normalization
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

logger = logging.getLogger(__name__)

CROSS_ENCODER_MODEL = os.getenv(
    "CROSS_ENCODER_MODEL", "BAAI/bge-reranker-v2-m3"
)
CROSS_ENCODER_BATCH_SIZE = int(os.getenv("CROSS_ENCODER_BATCH_SIZE", "32"))
CROSS_ENCODER_MAX_LENGTH = int(os.getenv("CROSS_ENCODER_MAX_LENGTH", "1024"))

_model = None
_model_lock = threading.Lock()
_executor = ThreadPoolExecutor(max_workers=1)


def _load_model():
    """Load cross-encoder model (singleton)."""
    global _model
    if _model is not None:
        return _model

    with _model_lock:
        if _model is not None:
            return _model
        try:
            from sentence_transformers import CrossEncoder
            _model = CrossEncoder(
                CROSS_ENCODER_MODEL,
                max_length=CROSS_ENCODER_MAX_LENGTH,
            )
            logger.info("Cross-encoder loaded: %s", CROSS_ENCODER_MODEL)
        except Exception as e:
            logger.warning("Cross-encoder load failed (graceful degradation): %s", e)
            _model = None
    return _model


def _sigmoid(x: float, temperature: float = 3.0) -> float:
    """Normalize raw score to [0, 1] via sigmoid (clamped to prevent overflow)."""
    clamped = max(-500, min(500, x / temperature))
    return 1.0 / (1.0 + math.exp(-clamped))


def rerank_with_cross_encoder(
    query: str,
    chunks: list[dict[str, Any]],
    top_k: int = 10,
    score_key: str = "cross_encoder_score",
) -> list[dict[str, Any]]:
    """Rerank chunks using cross-encoder model.

    Args:
        query: Search query.
        chunks: Retrieved chunks with 'content' field.
        top_k: Number of results to return.
        score_key: Key to store cross-encoder score in chunk.

    Returns:
        Reranked chunks sorted by cross-encoder score.
    """
    model = _load_model()
    if model is None or not chunks:
        return chunks[:top_k]

    # Build pairs
    pairs = []
    for chunk in chunks:
        content = chunk.get("content", "")
        # Truncate to max_length for cross-encoder
        pairs.append([query, content[:CROSS_ENCODER_MAX_LENGTH]])

    # Batch predict
    try:
        scores = model.predict(
            pairs,
            batch_size=CROSS_ENCODER_BATCH_SIZE,
            show_progress_bar=False,
        )

        # Attach normalized scores
        for i, chunk in enumerate(chunks):
            raw_score = float(scores[i])
            chunk[score_key] = _sigmoid(raw_score)
            # Also set in metadata for CompositeReranker
            if "metadata" not in chunk:
                chunk["metadata"] = {}
            chunk["metadata"]["cross_encoder_score"] = _sigmoid(raw_score)

        # Sort by cross-encoder score
        chunks.sort(key=lambda c: c.get(score_key, 0), reverse=True)

    except Exception as e:
        logger.warning("Cross-encoder predict failed: %s", e)

    return chunks[:top_k]


async def async_rerank_with_cross_encoder(
    query: str,
    chunks: list[dict[str, Any]],
    top_k: int = 10,
) -> list[dict[str, Any]]:
    """Async wrapper for cross-encoder reranking."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        _executor,
        lambda: rerank_with_cross_encoder(query, chunks, top_k),
    )


def warmup():
    """Pre-load model in background (fire-and-forget)."""
    _executor.submit(_load_model)
