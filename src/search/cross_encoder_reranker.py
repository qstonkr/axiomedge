"""Cross-Encoder Reranker for search result re-ranking.

py.
Uses BGE-Reranker-v2-m3 (multilingual/Korean) via sentence-transformers.

Features:
- Background model loading (never blocks search requests)
- Batch inference with configurable batch size
- Graceful degradation when model unavailable or loading
- Sigmoid score normalization
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Any

logger = logging.getLogger(__name__)

CROSS_ENCODER_MODEL = os.getenv(
    "CROSS_ENCODER_MODEL", "BAAI/bge-reranker-v2-m3"
)
CROSS_ENCODER_BATCH_SIZE = int(os.getenv("CROSS_ENCODER_BATCH_SIZE", "32"))
CROSS_ENCODER_MAX_LENGTH = int(os.getenv("CROSS_ENCODER_MAX_LENGTH", "1024"))

_model = None
_loading = False  # True while background load is in progress
_load_attempted = False  # True after first load attempt (success or fail)
_executor = ThreadPoolExecutor(max_workers=1)

# Cloud TEI reranker
def _get_reranker_tei_url() -> str:
    from src.config import get_settings
    return get_settings().tei.reranker_url

RERANKER_TEI_URL = _get_reranker_tei_url()
_use_cloud_reranker = (
    os.getenv("USE_CLOUD_EMBEDDING", "true").lower() in ("true", "1", "yes")
    and bool(RERANKER_TEI_URL)
)
_tei_client = None


def _get_tei_client() -> Any:
    """Lazy-init httpx client for TEI reranker."""
    global _tei_client
    if _tei_client is None:
        import httpx
        from src.config.weights import weights as _cw
        _tei_client = httpx.Client(timeout=_cw.timeouts.httpx_reranker)
    return _tei_client


def _load_model_sync() -> None:
    """Load cross-encoder model. Called only from background thread."""
    global _model, _loading, _load_attempted

    if _use_cloud_reranker:
        # Cloud mode — no local model needed
        _load_attempted = True
        logger.info("Reranker using cloud TEI: %s", RERANKER_TEI_URL)
        return

    _loading = True
    try:
        # Force offline mode — use cached model, skip HuggingFace version check
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"

        # SSL: Use corporate CA bundle if available, bypass only as last resort
        if not os.environ.get("REQUESTS_CA_BUNDLE") and not os.environ.get("CURL_CA_BUNDLE"):
            import ssl
            ssl._create_default_https_context = ssl._create_unverified_context
            os.environ.setdefault("CURL_CA_BUNDLE", "")
            os.environ.setdefault("REQUESTS_CA_BUNDLE", "")
            logger.warning("No CA bundle configured — SSL verification disabled for model download")

        import urllib3
        urllib3.disable_warnings()

        import requests
        _orig_get = requests.Session.get
        _orig_post = requests.Session.post

        def _get_no_verify(self, *a, **kw) -> Any:
            kw.setdefault("verify", False)
            return _orig_get(self, *a, **kw)

        def _post_no_verify(self, *a, **kw) -> Any:
            kw.setdefault("verify", False)
            return _orig_post(self, *a, **kw)

        requests.Session.get = _get_no_verify  # type: ignore
        requests.Session.post = _post_no_verify  # type: ignore

        from sentence_transformers import CrossEncoder
        _model = CrossEncoder(
            CROSS_ENCODER_MODEL,
            max_length=CROSS_ENCODER_MAX_LENGTH,
        )

        requests.Session.get = _orig_get  # type: ignore
        requests.Session.post = _orig_post  # type: ignore
        logger.info("Cross-encoder loaded: %s", CROSS_ENCODER_MODEL)
    except (OSError, ImportError, RuntimeError) as e:
        logger.warning("Cross-encoder load failed (graceful degradation): %s", e)
        _model = None
    finally:
        _loading = False
        _load_attempted = True


def _sigmoid(x: float, temperature: float = 3.0) -> float:
    """Normalize raw score to [0, 1] via sigmoid (clamped to prevent overflow)."""
    clamped = max(-500, min(500, x / temperature))
    return 1.0 / (1.0 + math.exp(-clamped))


def _rerank_via_tei(
    query: str,
    chunks: list[dict[str, Any]],
    top_k: int,
    score_key: str,
) -> list[dict[str, Any]]:
    """Rerank via cloud TEI /rerank endpoint."""
    texts = [chunk.get("content", "")[:CROSS_ENCODER_MAX_LENGTH] for chunk in chunks]
    client = _get_tei_client()
    resp = client.post(
        f"{RERANKER_TEI_URL}/rerank",
        json={"query": query, "texts": texts, "truncate": True},
    )
    resp.raise_for_status()
    results = resp.json()

    for item in results:
        idx = item["index"]
        score = _sigmoid(item["score"])
        chunks[idx][score_key] = score
        if "metadata" not in chunks[idx]:
            chunks[idx]["metadata"] = {}
        chunks[idx]["metadata"]["cross_encoder_score"] = score

    chunks.sort(key=lambda c: c.get(score_key, 0), reverse=True)
    return chunks[:top_k]


def rerank_with_cross_encoder(
    query: str,
    chunks: list[dict[str, Any]],
    top_k: int = 10,
    score_key: str = "cross_encoder_score",
) -> list[dict[str, Any]]:
    """Rerank chunks using cross-encoder model (cloud TEI or local).

    If model is still loading (warmup), returns chunks unchanged (no blocking).
    """
    if not chunks:
        return chunks[:top_k]

    # Cloud TEI reranker
    if _use_cloud_reranker:
        try:
            return _rerank_via_tei(query, chunks, top_k, score_key)
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.warning("Cloud reranker failed, fallback to local: %s", e)

    # Local cross-encoder
    if _model is None:
        return chunks[:top_k]

    pairs = [[query, chunk.get("content", "")[:CROSS_ENCODER_MAX_LENGTH]] for chunk in chunks]

    try:
        scores = _model.predict(
            pairs,
            batch_size=CROSS_ENCODER_BATCH_SIZE,
            show_progress_bar=False,
        )

        for i, chunk in enumerate(chunks):
            raw_score = float(scores[i])
            chunk[score_key] = _sigmoid(raw_score)
            if "metadata" not in chunk:
                chunk["metadata"] = {}
            chunk["metadata"]["cross_encoder_score"] = _sigmoid(raw_score)

        chunks.sort(key=lambda c: c.get(score_key, 0), reverse=True)

    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
        logger.warning("Cross-encoder predict failed: %s", e)

    return chunks[:top_k]


async def async_rerank_with_cross_encoder(
    query: str,
    chunks: list[dict[str, Any]],
    top_k: int = 10,
) -> list[dict[str, Any]]:
    """Async wrapper. Never blocks — skips if model not ready."""
    if _model is None:
        return chunks[:top_k]
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        _executor,
        lambda: rerank_with_cross_encoder(query, chunks, top_k),
    )


def warmup() -> None:
    """Pre-load model in background thread. Never blocks the caller."""
    if _load_attempted or _loading:
        return
    _executor.submit(_load_model_sync)
