"""QdrantStoreOperations -- CRUD, statistics, and scroll operations.

Standalone version extracted from oreo-ecosystem.
StatsD metric calls retained as no-ops via QdrantClientProvider stubs.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any

from ..config_weights import weights as _w

from .client import (
    ADMIN_STATS_CACHE_TTL_S,
    DEFAULT_HYDRATION_EXCLUDE_FIELDS,
    QdrantClientProvider,
    QdrantSearchResult,
)
from .collections import QdrantCollectionManager

logger = logging.getLogger(__name__)


class QdrantStoreOperations:
    """CRUD, statistics, and scroll operations for Qdrant vector store."""

    def __init__(
        self,
        provider: QdrantClientProvider,
        collection_mgr: QdrantCollectionManager,
    ) -> None:
        self._provider = provider
        self._collection_mgr = collection_mgr
        self._admin_stats_cache: dict[str, tuple[int, float]] = {}

    # ==================== Admin stats cache ====================

    def _get_cached_stat(self, cache_key: str) -> int | None:
        entry = self._admin_stats_cache.get(cache_key)
        if entry is not None:
            value, expiry = entry
            if time.monotonic() < expiry:
                return value
            del self._admin_stats_cache[cache_key]
        return None

    def _set_cached_stat(self, cache_key: str, value: int) -> None:
        self._admin_stats_cache[cache_key] = (value, time.monotonic() + ADMIN_STATS_CACHE_TTL_S)

    def invalidate_admin_stats_cache(self) -> None:
        self._admin_stats_cache.clear()

    # ==================== Upsert ====================

    async def upsert(
        self,
        kb_id: str,
        content: str,
        dense_vector: list[float],
        sparse_vector: dict[int, float] | None = None,
        metadata: dict[str, Any] | None = None,
        point_id: str | None = None,
    ) -> str:
        """Store a document vector point."""
        client = await self._provider.ensure_client()
        collection_name = self._collection_mgr.get_collection_name(kb_id)
        await self._collection_mgr.ensure_collection(kb_id)

        from qdrant_client.models import PointStruct, SparseVector

        pid = point_id or str(uuid.uuid4())

        actual_dim = len(dense_vector)
        expected_dim = self._provider.config.dense_dimension
        if actual_dim != expected_dim:
            logger.warning(
                "Vector dimension mismatch: actual=%d, configured=%d",
                actual_dim, expected_dim,
                extra={"kb_id": kb_id, "point_id": pid},
            )

        payload = {
            "content": content,
            "kb_id": kb_id,
            **(metadata or {}),
        }
        payload = QdrantClientProvider.augment_payload_with_embedding_tracking(
            payload,
            dense_dimension=actual_dim,
            config=self._provider.config,
        )

        config = self._provider.config
        vectors: dict[str, Any] = {config.dense_vector_name: dense_vector}
        if sparse_vector:
            indices = sorted(sparse_vector.keys())
            values = [sparse_vector[i] for i in indices]
            vectors[config.sparse_vector_name] = SparseVector(indices=indices, values=values)

        await client.upsert(
            collection_name=collection_name,
            points=[
                PointStruct(id=pid, vector=vectors, payload=payload),
            ],
        )

        return pid

    async def upsert_batch(
        self,
        kb_id: str,
        items: list[dict[str, Any]],
    ) -> list[str]:
        """Batch store document vector points."""
        client = await self._provider.ensure_client()
        collection_name = self._collection_mgr.get_collection_name(kb_id)
        await self._collection_mgr.ensure_collection(kb_id)

        from qdrant_client.models import PointStruct, SparseVector

        if not items:
            return []

        valid_items = []
        for item in items:
            dv = item.get("dense_vector")
            if not dv or not isinstance(dv, list) or len(dv) == 0:
                logger.warning("Skipping item with empty dense_vector: %s", item.get("point_id", "unknown"))
                continue
            valid_items.append(item)

        if not valid_items:
            return []

        config = self._provider.config
        point_ids = []

        BATCH_CHUNK_SIZE = _w.pipeline.qdrant_upsert_batch_size

        def _build_point(item: dict[str, Any]) -> tuple[str, PointStruct]:
            pid = item.get("point_id") or str(uuid.uuid4())

            payload = {
                "content": item["content"],
                "kb_id": kb_id,
                **(item.get("metadata") or {}),
            }
            payload = QdrantClientProvider.augment_payload_with_embedding_tracking(
                payload,
                dense_dimension=len(item.get("dense_vector") or []),
                config=config,
            )

            colbert = item.get("colbert_vectors")
            if colbert:
                payload["colbert_vectors"] = colbert

            vectors: dict[str, Any] = {config.dense_vector_name: item["dense_vector"]}
            sparse = item.get("sparse_vector")
            if sparse:
                indices = sorted(sparse.keys())
                values = [sparse[i] for i in indices]
                vectors[config.sparse_vector_name] = SparseVector(indices=indices, values=values)

            return pid, PointStruct(id=pid, vector=vectors, payload=payload)

        # Build all points and upsert in chunks (P2-3 perf fix)
        all_points: list[PointStruct] = []
        for item in valid_items:
            pid, point = _build_point(item)
            point_ids.append(pid)
            all_points.append(point)

        for i in range(0, len(all_points), BATCH_CHUNK_SIZE):
            batch = all_points[i:i + BATCH_CHUNK_SIZE]
            await client.upsert(
                collection_name=collection_name,
                points=batch,
            )

        logger.info(
            "Batch upsert completed",
            extra={"collection": collection_name, "count": len(all_points)},
        )
        return point_ids

    # ==================== Delete ====================

    async def delete_by_filter(
        self,
        kb_id: str,
        filter_conditions: dict[str, Any],
        exclude_point_ids: set[str] | None = None,
    ) -> bool:
        if not filter_conditions:
            return True

        client = await self._provider.ensure_client()
        collection_name = self._collection_mgr.get_collection_name(kb_id)

        from qdrant_client.models import (
            FieldCondition, Filter, FilterSelector, MatchValue, PointIdsList,
        )

        conditions = []
        for key, value in filter_conditions.items():
            conditions.append(FieldCondition(key=key, match=MatchValue(value=value)))

        if not conditions:
            return True

        try:
            if exclude_point_ids:
                stale_ids: list[str] = []
                offset = None
                while True:
                    scroll_kwargs: dict[str, Any] = {
                        "collection_name": collection_name,
                        "scroll_filter": Filter(must=conditions),
                        "limit": 100,
                        "with_payload": False,
                        "with_vectors": False,
                    }
                    if offset is not None:
                        scroll_kwargs["offset"] = offset
                    points, next_offset = await client.scroll(**scroll_kwargs)
                    for pt in points:
                        pid = str(pt.id)
                        if pid not in exclude_point_ids:
                            stale_ids.append(pid)
                    if next_offset is None:
                        break
                    offset = next_offset

                if stale_ids:
                    await client.delete(
                        collection_name=collection_name,
                        points_selector=PointIdsList(points=stale_ids),
                        wait=True,
                    )
                    logger.debug(
                        "Deleted stale points (post-upsert cleanup)",
                        extra={
                            "kb_id": kb_id,
                            "stale_count": len(stale_ids),
                            "kept_count": len(exclude_point_ids),
                        },
                    )
                return True

            await client.delete(
                collection_name=collection_name,
                points_selector=FilterSelector(filter=Filter(must=conditions)),
                wait=True,
            )
            return True
        except Exception as e:
            err_msg = str(e)
            if "doesn't exist" in err_msg or "Not found" in err_msg:
                logger.debug(
                    "delete_by_filter skipped: collection not found",
                    extra={"kb_id": kb_id, "collection": collection_name},
                )
                return True
            logger.error(
                "Failed to delete points by filter",
                extra={"kb_id": kb_id, "filter": filter_conditions, "error": err_msg},
            )
            return False

    async def delete_by_kb(self, kb_id: str) -> bool:
        client = await self._provider.ensure_client()
        collection_name = self._collection_mgr.get_write_collection_name(kb_id)

        try:
            await client.delete_collection(collection_name)
            self._collection_mgr.invalidate_cache()
            self._admin_stats_cache.clear()
            logger.info(
                "Collection deleted",
                extra={"collection": collection_name, "kb_id": kb_id},
            )
            return True
        except Exception as e:
            logger.error("Failed to delete collection %s: %s", collection_name, e)
            return False

    async def delete_points(self, kb_id: str, point_ids: list[str]) -> bool:
        client = await self._provider.ensure_client()
        collection_name = self._collection_mgr.get_write_collection_name(kb_id)

        from qdrant_client.models import PointIdsList

        try:
            await client.delete(
                collection_name=collection_name,
                points_selector=PointIdsList(points=point_ids),
            )
            return True
        except Exception as e:
            logger.error("Failed to delete points: %s", e)
            return False

    # ==================== Fetch ====================

    async def fetch_by_ids(
        self, kb_id: str, point_ids: list[str],
    ) -> list[QdrantSearchResult]:
        if not point_ids:
            return []

        client = await self._provider.ensure_client()
        collection_name = await self._collection_mgr.resolve_collection_name(kb_id)
        unique_point_ids = list(dict.fromkeys(str(pid) for pid in point_ids if pid))
        if not unique_point_ids:
            return []

        from qdrant_client.models import Record

        try:
            records = await client.retrieve(
                collection_name=collection_name,
                ids=unique_point_ids,
                with_vectors=False,
                with_payload=True,
            )
        except Exception as primary_error:
            logger.warning(
                "Qdrant retrieve() failed, returning empty fallback: %s",
                primary_error,
            )
            return []

        results: list[QdrantSearchResult] = []
        record_lookup: dict[str, Record] = {
            str(record.id): record
            for record in records
            if record is not None
        }

        for point_id in unique_point_ids:
            record = record_lookup.get(point_id)
            if record is None:
                continue

            payload = record.payload or {}
            content = payload.get("content", "")
            score = float(record.score) if hasattr(record, "score") and record.score is not None else 0.0

            results.append(
                QdrantSearchResult(
                    point_id=point_id,
                    score=score,
                    content=content,
                    metadata={k: v for k, v in payload.items() if k != "content"},
                )
            )

        return results

    # ==================== Count / Statistics ====================

    async def count(self, kb_id: str, *, exact: bool = True) -> int:
        cache_key = f"count:{kb_id}:{exact}"
        cached = self._get_cached_stat(cache_key)
        if cached is not None:
            return cached

        client = await self._provider.ensure_client()
        collection_name = await self._collection_mgr.resolve_read_collection_name(kb_id)

        try:
            result = await client.count(collection_name=collection_name, exact=exact)
            value = result.count if hasattr(result, "count") else int(result)
            self._set_cached_stat(cache_key, value)
            return value
        except Exception as e:
            logger.warning(
                "Failed to count vectors for kb_id=%s collection=%s: %s",
                kb_id, collection_name, e,
            )
            return 0

    async def count_distinct_documents(self, kb_id: str) -> int:
        cache_key = f"distinct_docs:{kb_id}"
        cached = self._get_cached_stat(cache_key)
        if cached is not None:
            return cached

        client = await self._provider.ensure_client()
        collection_name = await self._collection_mgr.resolve_read_collection_name(kb_id)

        try:
            facet_result = await asyncio.wait_for(
                client.facet(
                    collection_name=collection_name,
                    key="source_uri",
                    limit=200_000,
                    exact=False,
                    timeout=_w.timeouts.qdrant_count,
                ),
                timeout=_w.timeouts.qdrant_scroll,
            )
            base_uris = {
                str(hit.value).strip().split("#")[0]
                for hit in getattr(facet_result, "hits", []) or []
                if getattr(hit, "value", None)
            }
            value = len(base_uris)
            self._set_cached_stat(cache_key, value)
            return value
        except Exception as facet_err:
            logger.debug(
                "Facet API unavailable or timed out for kb_id=%s collection=%s: %s. "
                "Returning 0.",
                kb_id, collection_name, type(facet_err).__name__,
            )
            return 0

    async def facet_l1_categories(self, kb_id: str) -> dict[str, int]:
        """Return {l1_category: chunk_count} via Qdrant facet API."""
        client = await self._provider.ensure_client()
        collection_name = await self._collection_mgr.resolve_read_collection_name(kb_id)

        try:
            facet_result = await asyncio.wait_for(
                client.facet(
                    collection_name=collection_name,
                    key="l1_category",
                    limit=100,
                    exact=False,
                    timeout=_w.timeouts.qdrant_count,
                ),
                timeout=_w.timeouts.qdrant_scroll,
            )
            return {
                str(hit.value): hit.count
                for hit in getattr(facet_result, "hits", []) or []
                if getattr(hit, "value", None)
            }
        except Exception as e:
            logger.debug(
                "Facet l1_category unavailable for kb_id=%s: %s", kb_id, e,
            )
            return {}

    # ==================== Source URI listing ====================

    async def list_distinct_source_uris(self, kb_id: str, limit: int = 200_000) -> list[str]:
        client = await self._provider.ensure_client()
        collection_name = await self._collection_mgr.resolve_read_collection_name(kb_id)
        safe_limit = max(1, int(limit))

        try:
            facet_response = await client.facet(
                collection_name=collection_name,
                key="source_uri",
                limit=safe_limit,
                exact=True,
            )
            facet_hits = getattr(facet_response, "hits", []) or []
            source_uri_set: set[str] = set()
            for hit in facet_hits:
                value = getattr(hit, "value", None)
                if isinstance(value, str) and value.strip():
                    source_uri_set.add(value.strip())
            return sorted(source_uri_set)
        except Exception as facet_exc:
            logger.info(
                "Facet distinct source_uri unavailable, falling back to scroll for %s: %s",
                collection_name, facet_exc,
            )

        try:
            source_uris_set: set[str] = set()
            offset = None
            while True:
                points, offset = await client.scroll(
                    collection_name=collection_name,
                    limit=1000,
                    offset=offset,
                    with_payload=["source_uri"],
                    with_vectors=False,
                )
                for p in points:
                    uri = (p.payload or {}).get("source_uri")
                    if isinstance(uri, str) and uri.strip():
                        source_uris_set.add(uri.strip())
                        if len(source_uris_set) >= safe_limit:
                            return sorted(source_uris_set)
                if offset is None:
                    break
            return sorted(source_uris_set)
        except Exception as e:
            logger.warning(
                "Failed to list distinct documents for kb_id=%s: %s",
                kb_id, e,
            )
            return []

    # ==================== Scroll ====================

    async def scroll_by_source_uris(
        self,
        kb_id: str,
        source_uris: list[str],
        limit: int = 100,
    ) -> list[QdrantSearchResult]:
        if not source_uris:
            return []

        client = await self._provider.ensure_client()
        collection_name = await self._collection_mgr.resolve_read_collection_name(kb_id)

        try:
            from qdrant_client.models import FieldCondition, Filter, MatchAny

            qdrant_filter = Filter(
                must=[
                    FieldCondition(
                        key="source_uri",
                        match=MatchAny(any=source_uris),
                    )
                ]
            )

            points, _ = await client.scroll(
                collection_name=collection_name,
                scroll_filter=qdrant_filter,
                limit=limit,
                with_payload=QdrantClientProvider.build_hydration_payload_selector(
                    list(DEFAULT_HYDRATION_EXCLUDE_FIELDS)
                ),
                with_vectors=False,
            )

            results: list[QdrantSearchResult] = []
            for point in points:
                payload = point.payload or {}
                content = payload.get("content", "")
                results.append(
                    QdrantSearchResult(
                        point_id=str(point.id),
                        score=0.75,
                        content=content,
                        metadata={k: v for k, v in payload.items() if k != "content"},
                    )
                )

            logger.debug(
                "scroll_by_source_uris: %d chunks found for %d URIs in %s",
                len(results), len(source_uris), collection_name,
            )
            return results

        except Exception as e:
            logger.warning(
                "scroll_by_source_uris failed for kb_id=%s: %s",
                kb_id, e,
            )
            return []
