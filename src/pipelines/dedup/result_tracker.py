"""DedupResultTracker - Redis Streams result/conflict tracking.

Persists dedup pipeline results to Redis Streams for admin dashboard queries.

Redis key structure:
- dedup:results     (Stream, maxlen 100,000) - all dedup check results
- dedup:conflicts   (Stream, maxlen 50,000)  - Stage 4 conflict events only
- dedup:conflict:{id} (Hash, TTL 30 days)    - conflict state/resolution info
- dedup:resolutions (Stream, maxlen 50,000)  - resolution audit trail

Pattern: fire-and-forget, error isolation (never blocks ingestion).

Adapted from oreo-ecosystem infrastructure/dedup/dedup_result_tracker.py.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

# Redis key constants
DEDUP_RESULTS_STREAM = "dedup:results"
DEDUP_CONFLICTS_STREAM = "dedup:conflicts"
DEDUP_RESOLUTIONS_STREAM = "dedup:resolutions"
DEDUP_CONFLICT_HASH_PREFIX = "dedup:conflict:"

DEDUP_RESULTS_MAXLEN = 100_000
DEDUP_CONFLICTS_MAXLEN = 50_000
DEDUP_RESOLUTIONS_MAXLEN = 50_000
DEDUP_CONFLICT_TTL_DAYS = 30


def _enum_val(v: Any) -> str:
    """Extract .value from Enum, else str()."""
    return v.value if hasattr(v, "value") else str(v)


class DedupResultTracker:
    """Redis Stream based dedup result tracker.

    Fire-and-forget: storage failure never blocks ingestion.
    """

    def __init__(self, redis_client: Any | None = None) -> None:
        self._redis = redis_client
        self._enabled = redis_client is not None

    @property
    def redis(self) -> Any:
        return self._redis

    @property
    def enabled(self) -> bool:
        return self._enabled and self._redis is not None

    async def track_result(
        self,
        result: Any,
        kb_id: str,
        doc_title: str = "",
    ) -> None:
        """Store dedup check result to dedup:results stream (fire-and-forget)."""
        if not self.enabled:
            return

        try:
            entry = {
                "doc_id": str(getattr(result, "doc_id", "")),
                "status": _enum_val(getattr(result, "status", "unknown")),
                "duplicate_of": str(getattr(result, "duplicate_of", "") or ""),
                "similarity_score": str(getattr(result, "similarity_score", 0.0)),
                "stage_reached": str(getattr(result, "stage_reached", 0)),
                "processing_time_ms": str(getattr(result, "processing_time_ms", 0.0)),
                "resolution": _enum_val(getattr(result, "resolution", "none")),
                "conflict_types": json.dumps(
                    [_enum_val(ct) for ct in getattr(result, "conflict_types", [])]
                ),
                "kb_id": kb_id,
                "doc_title": doc_title,
                "timestamp": datetime.now(UTC).isoformat(),
            }

            await self._redis.xadd(
                DEDUP_RESULTS_STREAM,
                entry,
                maxlen=DEDUP_RESULTS_MAXLEN,
                approximate=True,
            )
        except Exception as e:  # noqa: BLE001
            logger.debug("dedup_tracker.track_result_failed: %s", e)

    async def track_conflict(
        self,
        result: Any,
        conflict_detail: Any | None,
        kb_id: str,
        doc_title: str = "",
        duplicate_doc_title: str = "",
    ) -> str:
        """Store Stage 4 conflict to dedup:conflicts stream + hash.

        Returns:
            Generated conflict_id
        """
        if not self.enabled:
            return ""

        conflict_id = f"conflict-{uuid.uuid4().hex[:12]}"
        now = datetime.now(UTC).isoformat()

        try:
            # Extract conflict details
            conflict_type = ""
            severity = ""
            description = ""
            doc_a_excerpt = ""
            doc_b_excerpt = ""
            if conflict_detail is not None:
                conflict_type = _enum_val(getattr(conflict_detail, "conflict_type", ""))
                severity = _enum_val(getattr(conflict_detail, "severity", ""))
                description = str(getattr(conflict_detail, "description", ""))
                doc_a_excerpt = str(getattr(conflict_detail, "doc1_excerpt", ""))
                doc_b_excerpt = str(getattr(conflict_detail, "doc2_excerpt", ""))

            # Stream entry
            stream_entry = {
                "conflict_id": conflict_id,
                "doc_id": str(getattr(result, "doc_id", "")),
                "duplicate_of": str(getattr(result, "duplicate_of", "") or ""),
                "conflict_type": conflict_type,
                "severity": severity,
                "description": description,
                "doc_a_title": doc_title,
                "doc_a_excerpt": doc_a_excerpt[:500],
                "doc_b_title": duplicate_doc_title,
                "doc_b_excerpt": doc_b_excerpt[:500],
                "similarity_score": str(getattr(result, "similarity_score", 0.0)),
                "kb_id": kb_id,
                "status": "pending",
                "timestamp": now,
            }

            await self._redis.xadd(
                DEDUP_CONFLICTS_STREAM,
                stream_entry,
                maxlen=DEDUP_CONFLICTS_MAXLEN,
                approximate=True,
            )

            # Hash for mutable state
            hash_key = f"{DEDUP_CONFLICT_HASH_PREFIX}{conflict_id}"
            hash_data = {
                "conflict_id": conflict_id,
                "status": "pending",
                "resolution": "",
                "resolved_by": "",
                "resolved_at": "",
                "conflict_type": conflict_type,
                "severity": severity,
                "description": description,
                "doc_a_title": doc_title,
                "doc_a_excerpt": doc_a_excerpt[:500],
                "doc_b_title": duplicate_doc_title,
                "doc_b_excerpt": doc_b_excerpt[:500],
                "similarity_score": str(getattr(result, "similarity_score", 0.0)),
                "kb_id": kb_id,
                "created_at": now,
            }
            await self._redis.hset(hash_key, mapping=hash_data)
            await self._redis.expire(
                hash_key, int(timedelta(days=DEDUP_CONFLICT_TTL_DAYS).total_seconds())
            )

            return conflict_id
        except Exception as e:  # noqa: BLE001
            logger.debug("dedup_tracker.track_conflict_failed: %s", e)
            return ""

    async def resolve_conflict(
        self,
        conflict_id: str,
        resolution: str,
        resolved_by: str = "admin",
    ) -> bool:
        """Update conflict resolution status + audit trail.

        Returns:
            Success flag
        """
        if not self.enabled:
            return False

        now = datetime.now(UTC).isoformat()
        hash_key = f"{DEDUP_CONFLICT_HASH_PREFIX}{conflict_id}"

        try:
            exists = await self._redis.exists(hash_key)
            if not exists:
                return False

            await self._redis.hset(hash_key, mapping={
                "status": "resolved",
                "resolution": resolution,
                "resolved_by": resolved_by,
                "resolved_at": now,
            })

            # Audit trail
            await self._redis.xadd(
                DEDUP_RESOLUTIONS_STREAM,
                {
                    "conflict_id": conflict_id,
                    "resolution": resolution,
                    "resolved_by": resolved_by,
                    "timestamp": now,
                },
                maxlen=DEDUP_RESOLUTIONS_MAXLEN,
                approximate=True,
            )

            return True
        except Exception as e:  # noqa: BLE001
            logger.warning("dedup_tracker.resolve_failed: %s", e)
            return False

    async def get_stats(self) -> dict[str, Any]:
        """Get dedup pipeline statistics from Redis streams."""
        if not self.enabled:
            return _empty_stats()

        try:
            results_len = await self._redis.xlen(DEDUP_RESULTS_STREAM)
            conflicts_len = await self._redis.xlen(DEDUP_CONFLICTS_STREAM)

            # Count pending conflicts by scanning conflict hashes
            pending = 0
            resolved = 0
            async for key in self._redis.scan_iter(match=f"{DEDUP_CONFLICT_HASH_PREFIX}*"):
                status = await self._redis.hget(key, "status")
                if status == "pending":
                    pending += 1
                elif status == "resolved":
                    resolved += 1

            return {
                "total_duplicates_found": results_len,
                "total_resolved": resolved,
                "pending": pending,
                "total_conflicts": conflicts_len,
            }
        except Exception as e:  # noqa: BLE001
            logger.debug("dedup_tracker.get_stats_failed: %s", e)
            return _empty_stats()

    async def get_conflicts(
        self,
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        """Get paginated conflict list from Redis stream."""
        if not self.enabled:
            return {"conflicts": [], "total": 0, "page": page, "page_size": page_size}

        try:
            # Read latest entries in reverse
            total = await self._redis.xlen(DEDUP_CONFLICTS_STREAM)
            entries = await self._redis.xrevrange(
                DEDUP_CONFLICTS_STREAM,
                count=page_size,
            )

            conflicts = []
            for entry_id, entry_data in entries:
                entry_data["stream_id"] = entry_id
                # Enrich with resolution status from hash
                conflict_id = entry_data.get("conflict_id", "")
                if conflict_id:
                    hash_key = f"{DEDUP_CONFLICT_HASH_PREFIX}{conflict_id}"
                    try:
                        status = await self._redis.hget(hash_key, "status")
                        entry_data["resolution_status"] = status or "unknown"
                    except Exception:  # noqa: BLE001
                        entry_data["resolution_status"] = "unknown"
                conflicts.append(entry_data)

            return {
                "conflicts": conflicts,
                "total": total,
                "page": page,
                "page_size": page_size,
            }
        except Exception as e:  # noqa: BLE001
            logger.debug("dedup_tracker.get_conflicts_failed: %s", e)
            return {"conflicts": [], "total": 0, "page": page, "page_size": page_size}


def _empty_stats() -> dict[str, Any]:
    return {
        "total_duplicates_found": 0,
        "total_resolved": 0,
        "pending": 0,
        "total_conflicts": 0,
    }
