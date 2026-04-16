"""QdrantCollectionManager -- collection lifecycle, aliases, and blue-green deployment.

Standalone version extracted from oreo-ecosystem.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from .client import COLLECTION_CACHE_TTL_S, QdrantClientProvider

logger = logging.getLogger(__name__)


class QdrantCollectionManager:
    """Collection lifecycle, schema validation, alias management, and blue-green deployment."""

    def __init__(self, provider: QdrantClientProvider) -> None:
        self._provider = provider
        self._collection_exists_cache: set[str] = set()
        self._collection_cache_lock = asyncio.Lock()
        self._collection_cache_ts: float = 0.0
        self._alias_resolution_cache: dict[str, tuple[str, float]] = {}
        self._alias_target_cache: dict[str, str] = {}
        self._missing_alias_cache: set[str] = set()
        self._alias_cache_lock = asyncio.Lock()

    # ==================== Collection name resolution ====================

    def get_collection_name(self, kb_id: str) -> str:
        if kb_id in self._provider.config.collection_name_overrides:
            return self._provider.config.collection_name_overrides[kb_id]
        safe_id = kb_id.replace("-", "_").replace(" ", "_").lower()
        return f"{self._provider.config.collection_prefix}_{safe_id}"

    def get_write_collection_name(self, kb_id: str) -> str:
        return self.get_collection_name(kb_id)

    def get_live_alias_name(self, kb_id: str) -> str:
        return f"{self.get_collection_name(kb_id)}__live"

    def get_versioned_live_collection_name(
        self, kb_id: str, version_suffix: str,
    ) -> str:
        safe_suffix = version_suffix.replace("-", "_").replace(" ", "_").lower()
        return f"{self.get_live_alias_name(kb_id)}_{safe_suffix}"

    # ==================== Collection existence ====================

    async def collection_exists(self, kb_id: str) -> bool:
        collection_name = self.get_collection_name(kb_id)
        return await self._collection_exists(collection_name)

    async def _collection_exists(self, collection_name: str) -> bool:
        if collection_name in self._collection_exists_cache:
            return True
        async with self._collection_cache_lock:
            return await self._collection_exists_unlocked(collection_name)

    async def _collection_exists_unlocked(self, collection_name: str) -> bool:
        if collection_name in self._collection_exists_cache:
            return True
        client = await self._provider.ensure_client()
        collections = await client.get_collections()
        existing_names = {c.name for c in collections.collections}
        self._collection_exists_cache.update(existing_names)
        return collection_name in existing_names

    async def get_existing_collection_names(
        self, ttl: float = COLLECTION_CACHE_TTL_S,
    ) -> set[str]:
        now = time.monotonic()
        if self._collection_cache_ts and now - self._collection_cache_ts < ttl:
            return set(self._collection_exists_cache)

        async with self._collection_cache_lock:
            now = time.monotonic()
            if self._collection_cache_ts and now - self._collection_cache_ts < ttl:
                return set(self._collection_exists_cache)

            client = await self._provider.ensure_client()
            collections = await client.get_collections()
            names = {c.name for c in collections.collections}
            try:
                aliases_resp = await client.get_aliases()
                for alias in aliases_resp.aliases:
                    names.add(alias.alias_name)
            except Exception as e:  # noqa: BLE001
                logger.debug("Failed to fetch Qdrant aliases: %s", e)
            self._collection_exists_cache = names
            self._collection_cache_ts = now
            return set(self._collection_exists_cache)

    def invalidate_cache(self) -> None:
        self._collection_exists_cache.clear()
        self._collection_cache_ts = 0.0
        self._alias_resolution_cache.clear()
        self._alias_target_cache.clear()
        self._missing_alias_cache.clear()

    # ==================== Collection creation ====================

    async def ensure_collection(self, kb_id: str) -> None:
        client = await self._provider.ensure_client()
        collection_name = self.get_collection_name(kb_id)

        from qdrant_client.models import (
            Distance,
            SparseIndexParams,
            SparseVectorParams,
            VectorParams,
        )

        if collection_name in self._collection_exists_cache:
            return

        async with self._collection_cache_lock:
            if await self._collection_exists_unlocked(collection_name):
                if await self._validate_collection_schema(client, collection_name, kb_id):
                    await self._ensure_payload_indexes(client, collection_name)
                    return

            try:
                await client.create_collection(
                    collection_name=collection_name,
                    vectors_config={
                        self._provider.config.dense_vector_name: VectorParams(
                            size=self._provider.config.dense_dimension,
                            distance=Distance.COSINE,
                        ),
                    },
                    sparse_vectors_config={
                        self._provider.config.sparse_vector_name: SparseVectorParams(
                            index=SparseIndexParams(on_disk=False),
                        ),
                    },
                    on_disk_payload=True,
                )
            except Exception as exc:
                err_msg = str(exc)
                if "Alias with the same name already exists" in err_msg:
                    logger.warning(
                        "Alias conflicts with collection name, deleting stale alias",
                        extra={"collection": collection_name, "kb_id": kb_id},
                    )
                    await self._delete_conflicting_alias(client, collection_name)
                    await client.create_collection(
                        collection_name=collection_name,
                        vectors_config={
                            self._provider.config.dense_vector_name: VectorParams(
                                size=self._provider.config.dense_dimension,
                                distance=Distance.COSINE,
                            ),
                        },
                        sparse_vectors_config={
                            self._provider.config.sparse_vector_name: SparseVectorParams(
                                index=SparseIndexParams(on_disk=False),
                            ),
                        },
                        on_disk_payload=True,
                    )
                else:
                    raise
            logger.info(
                "Qdrant collection created",
                extra={
                    "collection": collection_name,
                    "kb_id": kb_id,
                    "dense_vector_name": self._provider.config.dense_vector_name,
                    "sparse_vector_name": self._provider.config.sparse_vector_name,
                },
            )
            self._collection_exists_cache.add(collection_name)
            await self._ensure_payload_indexes(client, collection_name)

    async def _ensure_payload_indexes(
        self, client: Any, collection_name: str,
    ) -> None:
        from qdrant_client.http.models import PayloadSchemaType

        for field_name in (
            "source_uri", "kb_id", "source_type",
            "document_name", "store_name", "author_name",
            "l1_category", "chunk_type", "doc_type",
        ):
            try:
                await client.create_payload_index(
                    collection_name=collection_name,
                    field_name=field_name,
                    field_schema=PayloadSchemaType.KEYWORD,
                    wait=True,
                )
            except Exception:  # noqa: BLE001
                logger.debug(
                    "Payload index create skipped: %s.%s",
                    collection_name, field_name,
                )

    async def _validate_collection_schema(
        self, client: Any, collection_name: str, kb_id: str,
    ) -> bool:
        expected_dense = self._provider.config.dense_vector_name
        try:
            info = await client.get_collection(collection_name)
            vectors_config = info.config.params.vectors

            if isinstance(vectors_config, dict):
                if expected_dense in vectors_config:
                    return True
                logger.warning(
                    "Collection schema mismatch: missing expected dense vector",
                    extra={
                        "collection": collection_name, "kb_id": kb_id,
                        "expected": expected_dense, "found": list(vectors_config.keys()),
                    },
                )
            else:
                logger.warning(
                    "Collection has legacy unnamed-vector schema",
                    extra={"collection": collection_name, "kb_id": kb_id, "expected": expected_dense},
                )

            try:
                count_result = await client.count(collection_name, exact=True)
                point_count = count_result.count if hasattr(count_result, "count") else 0
            except Exception:  # noqa: BLE001
                point_count = -1

            if point_count == 0:
                logger.info(
                    "Recreating empty collection with correct named-vector schema",
                    extra={"collection": collection_name, "kb_id": kb_id},
                )
                await client.delete_collection(collection_name)
                self._collection_exists_cache.discard(collection_name)
                return False
            else:
                logger.error(
                    "Collection schema mismatch but contains data -- cannot auto-recreate.",
                    extra={"collection": collection_name, "kb_id": kb_id, "point_count": point_count},
                )
                return True
        except Exception as e:  # noqa: BLE001
            logger.debug("Schema validation skipped: %s", e, extra={"collection": collection_name})
            return True

    @staticmethod
    async def _delete_conflicting_alias(client: Any, alias_name: str) -> None:
        from qdrant_client.models import DeleteAlias, DeleteAliasOperation

        try:
            await client.update_collection_aliases(
                change_aliases_operations=[
                    DeleteAliasOperation(delete_alias=DeleteAlias(alias_name=alias_name)),
                ],
            )
            logger.info("Deleted conflicting alias", extra={"alias_name": alias_name})
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "Failed to delete conflicting alias",
                extra={"alias_name": alias_name, "error": str(exc)},
            )

    # ==================== Alias management ====================

    async def resolve_collection_name(self, kb_id: str) -> str:
        cached = self._alias_resolution_cache.get(kb_id)
        if cached:
            resolved_name, expiry = cached
            if time.time() < expiry:
                return resolved_name
            del self._alias_resolution_cache[kb_id]

        base_name = self.get_collection_name(kb_id)
        alias_name = f"{base_name}__alias"
        client = await self._provider.ensure_client()
        try:
            await client.get_collection(alias_name)
            self._alias_resolution_cache[kb_id] = (alias_name, time.time() + 60)
            return alias_name
        except Exception:  # noqa: BLE001
            self._alias_resolution_cache[kb_id] = (base_name, time.time() + 60)
            return base_name

    async def _get_alias_target(self, alias_name: str) -> str | None:
        if alias_name in self._alias_target_cache:
            return self._alias_target_cache[alias_name]
        if alias_name in self._missing_alias_cache:
            return None

        async with self._alias_cache_lock:
            if alias_name in self._alias_target_cache:
                return self._alias_target_cache[alias_name]
            if alias_name in self._missing_alias_cache:
                return None

            client = await self._provider.ensure_client()
            try:
                aliases = await client.get_aliases()
            except Exception as exc:  # noqa: BLE001
                logger.debug("Qdrant alias lookup failed: %s", exc)
                return None

            alias_items = getattr(aliases, "aliases", []) or []
            self._alias_target_cache = {
                str(getattr(item, "alias_name", "")): str(getattr(item, "collection_name", ""))
                for item in alias_items
                if getattr(item, "alias_name", None) and getattr(item, "collection_name", None)
            }
            self._missing_alias_cache = {
                a for a in self._missing_alias_cache if a not in self._alias_target_cache
            }
            target = self._alias_target_cache.get(alias_name)
            if target is None:
                self._missing_alias_cache.add(alias_name)
            return target

    async def resolve_read_collection_name(self, kb_id: str) -> str:
        alias_name = self.get_live_alias_name(kb_id)
        target = await self._get_alias_target(alias_name)
        return alias_name if target else await self.resolve_collection_name(kb_id)

    async def get_live_alias_target(self, kb_id: str) -> str | None:
        return await self._get_alias_target(self.get_live_alias_name(kb_id))

    async def switch_live_alias(
        self, *, kb_id: str, target_collection_name: str,
    ) -> str | None:
        client = await self._provider.ensure_client()
        alias_name = self.get_live_alias_name(kb_id)
        previous_target = await self.get_live_alias_target(kb_id)

        if previous_target == target_collection_name:
            return target_collection_name  # No-op: alias already points here

        from qdrant_client.models import (
            CreateAlias, CreateAliasOperation,
            DeleteAlias, DeleteAliasOperation,
        )

        operations: list[Any] = []
        if previous_target and previous_target != target_collection_name:
            operations.append(
                DeleteAliasOperation(delete_alias=DeleteAlias(alias_name=alias_name))
            )
        operations.append(
            CreateAliasOperation(
                create_alias=CreateAlias(
                    collection_name=target_collection_name,
                    alias_name=alias_name,
                )
            )
        )

        async with self._alias_cache_lock:
            await client.update_collection_aliases(operations)
            self._alias_target_cache[alias_name] = target_collection_name
            self._missing_alias_cache.discard(alias_name)
        return previous_target

    # ==================== Blue-green deployment ====================

    async def clone_collection(
        self, *, source_collection_name: str, destination_collection_name: str,
    ) -> int:
        client = await self._provider.ensure_client()
        logger.info(
            "clone_collection start: %s -> %s",
            source_collection_name, destination_collection_name,
        )

        from qdrant_client.models import (
            Distance, PointStruct,
            SparseIndexParams, SparseVectorParams, VectorParams,
        )

        if not await self._collection_exists(destination_collection_name):
            await client.create_collection(
                collection_name=destination_collection_name,
                vectors_config={
                    self._provider.config.dense_vector_name: VectorParams(
                        size=self._provider.config.dense_dimension,
                        distance=Distance.COSINE,
                    ),
                },
                sparse_vectors_config={
                    self._provider.config.sparse_vector_name: SparseVectorParams(
                        index=SparseIndexParams(on_disk=False),
                    ),
                },
            )
            self._collection_exists_cache.add(destination_collection_name)

        copied = 0
        offset = None
        while True:
            points, offset = await client.scroll(
                collection_name=source_collection_name,
                limit=self._provider.config.clone_batch_size,
                offset=offset,
                with_payload=True,
                with_vectors=True,
            )
            if points:
                batch = [
                    PointStruct(
                        id=point.id,
                        vector=point.vector,
                        payload=point.payload or {},
                    )
                    for point in points
                ]
                await self._clone_upsert_batch(
                    client=client,
                    destination_collection_name=destination_collection_name,
                    batch=batch,
                )
                copied += len(points)
                if copied > 0 and copied % 500 == 0:
                    logger.info("clone_collection progress: %d points copied", copied)
            if offset is None:
                break

        logger.info(
            "clone_collection done: %d points from %s -> %s",
            copied, source_collection_name, destination_collection_name,
        )
        return copied

    async def _clone_upsert_batch(
        self, *, client: Any, destination_collection_name: str, batch: list[Any],
    ) -> None:
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                await client.upsert(
                    collection_name=destination_collection_name,
                    points=batch,
                    wait=True,
                )
                return
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if attempt < 2:
                    logger.warning(
                        "clone_collection upsert retry %d/3 for %s (batch_size=%d, error=%s)",
                        attempt + 2, destination_collection_name, len(batch), type(exc).__name__,
                    )
                    await asyncio.sleep(2**attempt)

        if len(batch) <= 1:
            assert last_error is not None
            raise last_error

        midpoint = max(1, len(batch) // 2)
        logger.warning(
            "clone_collection splitting batch for %s after %s (batch_size=%d -> %d/%d)",
            destination_collection_name,
            type(last_error).__name__ if last_error is not None else "unknown_error",
            len(batch), midpoint, len(batch) - midpoint,
        )
        await self._clone_upsert_batch(
            client=client, destination_collection_name=destination_collection_name,
            batch=batch[:midpoint],
        )
        await self._clone_upsert_batch(
            client=client, destination_collection_name=destination_collection_name,
            batch=batch[midpoint:],
        )
