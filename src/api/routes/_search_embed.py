"""Search embedding step.

Extracted from _search_steps.py for module size management.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import HTTPException

from src.config.weights import weights


async def _step_embed(
    search_query: str,
    state: dict[str, Any],
) -> tuple[list[float], dict[int, float] | None, list | None]:
    """Step 3: Embed query. Returns (dense_vector, sparse_vector, colbert_vectors)."""
    embedder = state.get("embedder")
    if not embedder:
        raise HTTPException(status_code=503, detail="Embedding provider not initialized")

    colbert_enabled = weights.hybrid_search.enable_colbert_reranking
    encoded = await asyncio.to_thread(
        lambda: embedder.encode(
            [search_query],
            return_dense=True,
            return_sparse=True,
            return_colbert_vecs=colbert_enabled,
        ),
    )
    dense_vector = encoded["dense_vecs"][0]
    sparse_weights_raw = encoded["lexical_weights"][0] if encoded.get("lexical_weights") else {}
    sparse_vector = (
        {int(k): float(v) for k, v in sparse_weights_raw.items()}
        if sparse_weights_raw else None
    )
    colbert_vectors = (
        encoded["colbert_vecs"][0]
        if colbert_enabled and encoded.get("colbert_vecs")
        else None
    )
    return dense_vector, sparse_vector, colbert_vectors
