"""QdrantSearchEngine -- hybrid search, ColBERT rerank, and hydration.

Standalone version extracted from the upstream codebase.
FeatureFlags replaced with config booleans on QdrantConfig.
StatsD metric calls are retained as no-ops via QdrantClientProvider stubs.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import numpy as np

from .client import (
    DEFAULT_COLBERT_WEIGHT,
    DEFAULT_DENSE_WEIGHT,
    DEFAULT_HYDRATION_EXCLUDE_FIELDS,
    DEFAULT_SPARSE_WEIGHT,
    RETRIEVAL_PAYLOAD_FIELDS,
    QdrantClientProvider,
    QdrantSearchResult,
)
from .collections import QdrantCollectionManager

logger = logging.getLogger(__name__)


class QdrantServerError(RuntimeError):
    """Raised when Qdrant returns a 5xx server error."""


def _raise_if_qdrant_server_error(
    error: Exception, collection_name: str, kb_id: str,
) -> None:
    """Re-raise as QdrantServerError if the error is a Qdrant 5xx."""
    status = getattr(error, "status_code", None)
    if status is not None and status >= 500:
        logger.error(
            "Qdrant server error %s for collection %s, failing fast",
            status, collection_name,
            extra={"status_code": status, "kb_id": kb_id},
        )
        raise QdrantServerError(
            f"Qdrant server error {status} for {collection_name}"
        ) from error


class QdrantSearchEngine:
    """Hybrid search (RRF fusion) and ColBERT late-interaction reranking.

    Two-phase search pipeline:
    1. Candidate retrieval via ``query_points`` (dense + sparse prefetch).
    2. Payload hydration via ``retrieve`` (exclude heavy fields).
    3. Result merging.
    """

    def __init__(
        self,
        provider: QdrantClientProvider,
        collection_mgr: QdrantCollectionManager,
    ) -> None:
        self._provider = provider
        self._collection_mgr = collection_mgr

    # ==================== Query candidates ====================

    @staticmethod
    def _build_qdrant_filter(
        filter_conditions: dict[str, Any] | None,
    ) -> Any:
        """Build a Qdrant Filter from filter_conditions dict."""
        if not filter_conditions:
            return None

        from qdrant_client.models import FieldCondition, Filter, MatchAny, MatchValue

        conditions = []
        for key, value in filter_conditions.items():
            if isinstance(value, list):
                if not value:
                    continue
                conditions.append(FieldCondition(key=key, match=MatchAny(any=value)))
            elif isinstance(value, dict) and "match_text" in value:
                from qdrant_client.models import MatchText
                conditions.append(FieldCondition(key=key, match=MatchText(text=value["match_text"])))
            else:
                conditions.append(FieldCondition(key=key, match=MatchValue(value=value)))
        return Filter(must=conditions) if conditions else None

    async def _query_hybrid(
        self,
        client: Any,
        dense_vector: list[float],
        sparse_vector: dict[int, float],
        config: Any,
        prefetch_limit: int,
        qdrant_filter: Any,
        query_kwargs: dict[str, Any],
        collection_name: str,
        kb_id: str,
        error_cls: type[Exception],
    ) -> Any:
        """Execute hybrid (dense + sparse) query with RRF fusion."""
        from qdrant_client.models import Fusion, FusionQuery, Prefetch, SparseVector

        sparse_indices = sorted(sparse_vector.keys())
        sparse_values = [sparse_vector[i] for i in sparse_indices]

        def _build_prefetches(dense_name: str, sparse_name: str) -> list:
            return [
                Prefetch(query=dense_vector, using=dense_name,
                         limit=prefetch_limit, filter=qdrant_filter),
                Prefetch(query=SparseVector(indices=sparse_indices, values=sparse_values),
                         using=sparse_name, limit=prefetch_limit, filter=qdrant_filter),
            ]

        try:
            return await client.query_points(
                prefetch=_build_prefetches(config.dense_vector_name, config.sparse_vector_name),
                query=FusionQuery(fusion=Fusion.RRF),
                **query_kwargs,
            )
        except (error_cls, ValueError) as primary_error:
            _raise_if_qdrant_server_error(primary_error, collection_name, kb_id)
            logger.warning("Named vector query failed, retrying with legacy names: %s", primary_error)
            return await client.query_points(
                prefetch=_build_prefetches("dense", "sparse"),
                query=FusionQuery(fusion=Fusion.RRF),
                **query_kwargs,
            )

    async def _query_dense_only(
        self,
        client: Any,
        dense_vector: list[float],
        config: Any,
        qdrant_filter: Any,
        query_kwargs: dict[str, Any],
        collection_name: str,
        kb_id: str,
        error_cls: type[Exception],
    ) -> Any:
        """Execute dense-only query."""
        try:
            return await client.query_points(
                query=dense_vector, using=config.dense_vector_name,
                query_filter=qdrant_filter, **query_kwargs,
            )
        except (error_cls, ValueError) as primary_error:
            _raise_if_qdrant_server_error(primary_error, collection_name, kb_id)
            logger.warning("Dense named vector query failed, retrying with legacy name: %s", primary_error)
            return await client.query_points(
                query=dense_vector, using="dense",
                query_filter=qdrant_filter, **query_kwargs,
            )

    async def query_candidates(
        self,
        *,
        kb_id: str,
        dense_vector: list[float],
        sparse_vector: dict[int, float] | None,
        top_k: int,
        score_threshold: float | None,
        filter_conditions: dict[str, Any] | None,
        with_payload: Any | None,
        prefetch_multiplier: int | None = None,
        prefetch_max: int | None = None,
    ) -> list[QdrantSearchResult]:
        """Run a Qdrant query_points request and normalize points into results."""
        client = await self._provider.ensure_client()
        collection_name = await self._collection_mgr.resolve_collection_name(kb_id)
        cfg = self._provider.config
        prefetch_limit = min(
            max(1, top_k)
            * (prefetch_multiplier if prefetch_multiplier is not None else cfg.hybrid_prefetch_multiplier),
            prefetch_max if prefetch_max is not None else cfg.hybrid_prefetch_max,
        )

        try:
            from qdrant_client.http.exceptions import UnexpectedResponse as _unexpected_resp_cls
        except (ImportError, AttributeError):
            _unexpected_resp_cls = ValueError  # type: ignore[assignment,misc]

        qdrant_filter = self._build_qdrant_filter(filter_conditions)
        query_kwargs: dict[str, Any] = {"collection_name": collection_name, "limit": top_k}
        if with_payload is not None:
            query_kwargs["with_payload"] = with_payload

        if sparse_vector:
            results = await self._query_hybrid(
                client, dense_vector, sparse_vector, cfg, prefetch_limit,
                qdrant_filter, query_kwargs, collection_name, kb_id, _unexpected_resp_cls,
            )
        else:
            results = await self._query_dense_only(
                client, dense_vector, cfg, qdrant_filter, query_kwargs,
                collection_name, kb_id, _unexpected_resp_cls,
            )

        search_results: list[QdrantSearchResult] = []
        for point in results.points:
            score = point.score or 0.0
            if score_threshold is not None and score < score_threshold:
                continue
            payload = point.payload or {}
            search_results.append(
                QdrantSearchResult(
                    point_id=str(point.id),
                    score=score,
                    content=str(payload.get("content", "")),
                    metadata={k: v for k, v in payload.items() if k != "content"},
                )
            )
        return search_results

    # ==================== Hybrid search ====================

    async def search(
        self,
        kb_id: str,
        dense_vector: list[float],
        sparse_vector: dict[int, float] | None = None,
        top_k: int = 10,
        _dense_weight: float = DEFAULT_DENSE_WEIGHT,
        _sparse_weight: float = DEFAULT_SPARSE_WEIGHT,
        score_threshold: float | None = None,
        filter_conditions: dict[str, Any] | None = None,
        prefetch_multiplier: int | None = None,
        prefetch_max: int | None = None,
        metric_tags: list[str] | None = None,
    ) -> list[QdrantSearchResult]:
        """Hybrid search with RRF fusion and optional two-phase projection."""
        projection_enabled = self._provider.config.retrieval_projection_enabled
        retrieval_selector = QdrantClientProvider.build_retrieval_payload_selector(
            RETRIEVAL_PAYLOAD_FIELDS
        )
        retrieval_start = time.perf_counter()
        base_results = await self.query_candidates(
            kb_id=kb_id,
            dense_vector=dense_vector,
            sparse_vector=sparse_vector,
            top_k=top_k,
            score_threshold=score_threshold,
            filter_conditions=filter_conditions,
            with_payload=retrieval_selector if projection_enabled else None,
            prefetch_multiplier=prefetch_multiplier,
            prefetch_max=prefetch_max,
        )
        retrieval_ms = (time.perf_counter() - retrieval_start) * 1000
        self._provider.emit_search_metric(
            "knowledge_search.retrieval_ms",
            retrieval_ms,
            tags=[f"kb_id:{kb_id}", *(metric_tags or [])],
        )

        if not projection_enabled or not base_results:
            return base_results

        finalist_ids = list(dict.fromkeys(result.point_id for result in base_results))

        hydration_start = time.perf_counter()
        hydrated_results = await self.hydrate_by_ids(
            kb_id=kb_id,
            point_ids=finalist_ids,
        )
        hydration_ms = (time.perf_counter() - hydration_start) * 1000
        self._provider.emit_search_metric(
            "knowledge_search.hydration_ms",
            hydration_ms,
            tags=[f"kb_id:{kb_id}", *(metric_tags or [])],
        )

        merged_results = [
            QdrantClientProvider.merge_search_result_payloads(result, hydrated_results.get(result.point_id))
            for result in base_results
        ]
        return merged_results

    # ==================== ColBERT reranking ====================

    async def search_with_colbert_rerank(
        self,
        kb_id: str,
        dense_vector: list[float],
        sparse_vector: dict[int, float] | None = None,
        colbert_vectors: list[list[float]] | None = None,
        top_k: int = 10,
        dense_weight: float = DEFAULT_DENSE_WEIGHT,
        sparse_weight: float = DEFAULT_SPARSE_WEIGHT,
        colbert_weight: float = DEFAULT_COLBERT_WEIGHT,
        score_threshold: float | None = None,
        filter_conditions: dict[str, Any] | None = None,
        prefetch_multiplier: int | None = None,
        prefetch_max: int | None = None,
        metric_tags: list[str] | None = None,
    ) -> list[QdrantSearchResult]:
        """ColBERT late-interaction reranking on top of hybrid search."""
        cfg = self._provider.config
        projection_enabled = cfg.retrieval_projection_enabled
        candidate_multiplier = cfg.colbert_rerank_candidate_multiplier
        candidate_top_k = max(1, top_k) * candidate_multiplier

        if not projection_enabled:
            base_results = await self.search(
                kb_id=kb_id,
                dense_vector=dense_vector,
                sparse_vector=sparse_vector,
                top_k=candidate_top_k,
                _dense_weight=dense_weight,
                _sparse_weight=sparse_weight,
                score_threshold=None,
                filter_conditions=filter_conditions,
                prefetch_multiplier=prefetch_multiplier,
                prefetch_max=prefetch_max,
                metric_tags=metric_tags,
            )
        else:
            base_results = await self.query_candidates(
                kb_id=kb_id,
                dense_vector=dense_vector,
                sparse_vector=sparse_vector,
                top_k=candidate_top_k,
                score_threshold=None,
                filter_conditions=filter_conditions,
                with_payload=QdrantClientProvider.build_retrieval_payload_selector(
                    RETRIEVAL_PAYLOAD_FIELDS
                ),
                prefetch_multiplier=prefetch_multiplier,
                prefetch_max=prefetch_max,
            )

        if not base_results or not colbert_vectors:
            return base_results[:top_k]

        candidate_ids = list(dict.fromkeys(result.point_id for result in base_results))
        colbert_by_id = (
            await self._fetch_colbert_vectors(
                kb_id=kb_id,
                point_ids=candidate_ids,
            )
            if projection_enabled
            else {
                result.point_id: result.metadata.get("colbert_vectors")
                for result in base_results
            }
        )

        # Phase 2: ColBERT MaxSim reranking (vectorized)
        reranked: list[tuple[float, QdrantSearchResult]] = []
        for result in base_results:
            stored_colbert = colbert_by_id.get(result.point_id)
            if not stored_colbert or not isinstance(stored_colbert, list):
                combined = (1.0 - colbert_weight) * result.score
                reranked.append((combined, result))
                continue

            maxsim_score = self._compute_maxsim(colbert_vectors, stored_colbert)

            combined = (1.0 - colbert_weight) * result.score + colbert_weight * maxsim_score
            reranked.append((combined, result))

        reranked.sort(key=lambda x: x[0], reverse=True)

        final: list[QdrantSearchResult] = []
        finalists = reranked[:top_k]
        hydrated = (
            await self.hydrate_by_ids(
                kb_id=kb_id,
                point_ids=[result.point_id for _score, result in finalists],
            )
            if projection_enabled
            else {}
        )
        for score, result in finalists:
            if score_threshold is not None and score < score_threshold:
                continue
            merged = QdrantClientProvider.merge_search_result_payloads(
                result,
                hydrated.get(result.point_id),
            )
            final.append(
                QdrantSearchResult(
                    point_id=merged.point_id,
                    score=score,
                    content=merged.content,
                    metadata=merged.metadata,
                )
            )
        return final

    # ==================== Hydration ====================

    async def hydrate_by_ids(
        self,
        kb_id: str,
        point_ids: list[str],
        exclude_fields: list[str] | None = None,
    ) -> dict[str, QdrantSearchResult]:
        """Batch-hydrate finalist points with richer payload, excluding heavy fields."""
        if not point_ids:
            return {}

        client = await self._provider.ensure_client()
        collection_name = await self._collection_mgr.resolve_collection_name(kb_id)
        unique_point_ids = list(dict.fromkeys(str(pid) for pid in point_ids if pid))
        if not unique_point_ids:
            return {}

        payload_selector = QdrantClientProvider.build_hydration_payload_selector(
            exclude_fields or list(DEFAULT_HYDRATION_EXCLUDE_FIELDS)
        )
        try:
            records = await client.retrieve(
                collection_name=collection_name,
                ids=unique_point_ids,
                with_vectors=False,
                with_payload=payload_selector,
            )
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            logger.warning(
                "Qdrant hydrate_by_ids failed for kb_id=%s collection=%s: %s",
                kb_id, collection_name, exc,
            )
            return {}

        hydrated: dict[str, QdrantSearchResult] = {}
        for record in records:
            if record is None:
                continue
            payload = record.payload or {}
            record_id = str(record.id)
            hydrated[record_id] = QdrantSearchResult(
                point_id=record_id,
                score=float(record.score) if hasattr(record, "score") and record.score is not None else 0.75,
                content=str(payload.get("content", "")),
                metadata={k: v for k, v in payload.items() if k != "content"},
            )
        return hydrated

    # ==================== ColBERT vector fetch ====================

    async def _fetch_colbert_vectors(
        self, *, kb_id: str, point_ids: list[str],
    ) -> dict[str, list[list[float]]]:
        if not point_ids:
            return {}

        client = await self._provider.ensure_client()
        collection_name = await self._collection_mgr.resolve_collection_name(kb_id)
        unique_point_ids = list(dict.fromkeys(str(pid) for pid in point_ids if pid))
        if not unique_point_ids:
            return {}

        try:
            records = await client.retrieve(
                collection_name=collection_name,
                ids=unique_point_ids,
                with_vectors=False,
                with_payload=["colbert_vectors"],
            )
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            logger.warning(
                "Failed to fetch ColBERT vectors for kb_id=%s collection=%s: %s",
                kb_id, collection_name, exc,
            )
            return {}

        colbert_by_id: dict[str, list[list[float]]] = {}
        for record in records:
            if record is None:
                continue
            payload = record.payload or {}
            vectors = payload.get("colbert_vectors")
            if isinstance(vectors, list):
                colbert_by_id[str(record.id)] = vectors
        return colbert_by_id

    # ==================== Math utilities ====================

    @staticmethod
    def _compute_maxsim(
        query_vectors: list[list[float]],
        doc_vectors: list[list[float]],
    ) -> float:
        """Vectorized MaxSim: mean of max cosine similarities.

        Replaces per-pair _cosine_sim loop with a single matrix multiply
        for 10-50x speedup (50-500ms -> 5-20ms).
        """
        if not query_vectors or not doc_vectors:
            return 0.0
        q_matrix = np.array(query_vectors, dtype=np.float32)  # [n_q, dim]
        d_matrix = np.array(doc_vectors, dtype=np.float32)    # [n_d, dim]
        # L2 normalize
        q_norms = np.linalg.norm(q_matrix, axis=1, keepdims=True)
        d_norms = np.linalg.norm(d_matrix, axis=1, keepdims=True)
        q_matrix = q_matrix / np.clip(q_norms, 1e-12, None)
        d_matrix = d_matrix / np.clip(d_norms, 1e-12, None)
        # Similarity matrix [n_q, n_d]
        sim_matrix = q_matrix @ d_matrix.T
        # MaxSim: for each query token, take max similarity across doc tokens
        max_sims = sim_matrix.max(axis=1)
        return float(max_sims.mean())

    @staticmethod
    def _cosine_sim(a: list[float], b: list[float]) -> float:
        """Compute cosine similarity between two vectors (kept for backward compat)."""
        a_arr = np.asarray(a, dtype=np.float32)
        b_arr = np.asarray(b, dtype=np.float32)
        norm_a = np.linalg.norm(a_arr)
        norm_b = np.linalg.norm(b_arr)
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return float(np.dot(a_arr, b_arr) / (norm_a * norm_b))
