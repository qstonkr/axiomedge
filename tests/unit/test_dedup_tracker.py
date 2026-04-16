"""Unit tests for DedupResultTracker."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.pipelines.dedup.result_tracker import (
    DEDUP_CONFLICT_HASH_PREFIX,
    DEDUP_CONFLICT_TTL_DAYS,
    DEDUP_CONFLICTS_MAXLEN,
    DEDUP_CONFLICTS_STREAM,
    DEDUP_RESOLUTIONS_MAXLEN,
    DEDUP_RESOLUTIONS_STREAM,
    DEDUP_RESULTS_MAXLEN,
    DEDUP_RESULTS_STREAM,
    DedupResultTracker,
    _empty_stats,
    _enum_val,
)


# --- Test helpers ---

class FakeStatus(Enum):
    DUPLICATE = "duplicate"
    UNIQUE = "unique"


class FakeResolution(Enum):
    KEEP_BOTH = "keep_both"
    NONE = "none"


class FakeConflictType(Enum):
    CONTRADICTION = "contradiction"


@dataclass
class FakeResult:
    doc_id: str = "doc-1"
    status: FakeStatus = FakeStatus.DUPLICATE
    duplicate_of: str = "doc-0"
    similarity_score: float = 0.95
    stage_reached: int = 3
    processing_time_ms: float = 42.5
    resolution: FakeResolution = FakeResolution.NONE
    conflict_types: list = None

    def __post_init__(self):
        if self.conflict_types is None:
            self.conflict_types = []


@dataclass
class FakeConflictDetail:
    conflict_type: FakeConflictType = FakeConflictType.CONTRADICTION
    severity: str = "high"
    description: str = "Documents contradict each other"
    doc1_excerpt: str = "Excerpt from doc A"
    doc2_excerpt: str = "Excerpt from doc B"


def make_mock_redis() -> AsyncMock:
    """Create a mock Redis client with all needed async methods."""
    redis = AsyncMock()
    redis.xadd = AsyncMock(return_value="1234-0")
    redis.xlen = AsyncMock(return_value=10)
    redis.xrevrange = AsyncMock(return_value=[])
    redis.hset = AsyncMock()
    redis.hget = AsyncMock(return_value="pending")
    redis.expire = AsyncMock()
    redis.exists = AsyncMock(return_value=True)
    return redis


# --- Tests ---

class TestEnumVal:
    def test_enum_value(self):
        assert _enum_val(FakeStatus.DUPLICATE) == "duplicate"

    def test_plain_string(self):
        assert _enum_val("hello") == "hello"

    def test_integer(self):
        assert _enum_val(42) == "42"


class TestEmptyStats:
    def test_keys(self):
        stats = _empty_stats()
        assert stats["total_duplicates_found"] == 0
        assert stats["total_resolved"] == 0
        assert stats["pending"] == 0
        assert stats["total_conflicts"] == 0


class TestDedupResultTrackerInit:
    def test_disabled_when_no_redis(self):
        tracker = DedupResultTracker(redis_client=None)
        assert tracker.enabled is False
        assert tracker.redis is None

    def test_enabled_with_redis(self):
        redis = make_mock_redis()
        tracker = DedupResultTracker(redis_client=redis)
        assert tracker.enabled is True
        assert tracker.redis is redis


class TestTrackResult:
    @pytest.mark.asyncio
    async def test_noop_when_disabled(self):
        tracker = DedupResultTracker(redis_client=None)
        await tracker.track_result(FakeResult(), kb_id="kb1")
        # Should not raise

    @pytest.mark.asyncio
    async def test_calls_xadd(self):
        redis = make_mock_redis()
        tracker = DedupResultTracker(redis_client=redis)
        await tracker.track_result(FakeResult(), kb_id="kb1", doc_title="Test Doc")
        redis.xadd.assert_called_once()
        call_args = redis.xadd.call_args
        assert call_args[0][0] == DEDUP_RESULTS_STREAM
        entry = call_args[0][1]
        assert entry["doc_id"] == "doc-1"
        assert entry["status"] == "duplicate"
        assert entry["kb_id"] == "kb1"
        assert entry["doc_title"] == "Test Doc"
        assert call_args[1]["maxlen"] == DEDUP_RESULTS_MAXLEN

    @pytest.mark.asyncio
    async def test_swallows_exception(self):
        redis = make_mock_redis()
        redis.xadd.side_effect = ConnectionError("redis down")
        tracker = DedupResultTracker(redis_client=redis)
        # Should not raise
        await tracker.track_result(FakeResult(), kb_id="kb1")

    @pytest.mark.asyncio
    async def test_conflict_types_serialized_as_json(self):
        redis = make_mock_redis()
        tracker = DedupResultTracker(redis_client=redis)
        result = FakeResult(conflict_types=[FakeConflictType.CONTRADICTION])
        await tracker.track_result(result, kb_id="kb1")
        entry = redis.xadd.call_args[0][1]
        assert '"contradiction"' in entry["conflict_types"]


class TestTrackConflict:
    @pytest.mark.asyncio
    async def test_noop_when_disabled(self):
        tracker = DedupResultTracker(redis_client=None)
        conflict_id = await tracker.track_conflict(
            FakeResult(), FakeConflictDetail(), kb_id="kb1"
        )
        assert conflict_id == ""

    @pytest.mark.asyncio
    async def test_returns_conflict_id(self):
        redis = make_mock_redis()
        tracker = DedupResultTracker(redis_client=redis)
        conflict_id = await tracker.track_conflict(
            FakeResult(), FakeConflictDetail(), kb_id="kb1", doc_title="Doc A"
        )
        assert conflict_id.startswith("conflict-")
        assert len(conflict_id) > 10

    @pytest.mark.asyncio
    async def test_calls_xadd_and_hset(self):
        redis = make_mock_redis()
        tracker = DedupResultTracker(redis_client=redis)
        await tracker.track_conflict(
            FakeResult(), FakeConflictDetail(), kb_id="kb1"
        )
        assert redis.xadd.call_count == 1
        assert redis.hset.call_count == 1
        assert redis.expire.call_count == 1

    @pytest.mark.asyncio
    async def test_conflict_detail_none(self):
        redis = make_mock_redis()
        tracker = DedupResultTracker(redis_client=redis)
        conflict_id = await tracker.track_conflict(
            FakeResult(), None, kb_id="kb1"
        )
        assert conflict_id.startswith("conflict-")

    @pytest.mark.asyncio
    async def test_swallows_exception(self):
        redis = make_mock_redis()
        redis.xadd.side_effect = ConnectionError("redis down")
        tracker = DedupResultTracker(redis_client=redis)
        conflict_id = await tracker.track_conflict(
            FakeResult(), FakeConflictDetail(), kb_id="kb1"
        )
        assert conflict_id == ""

    @pytest.mark.asyncio
    async def test_excerpt_truncated(self):
        redis = make_mock_redis()
        tracker = DedupResultTracker(redis_client=redis)
        detail = FakeConflictDetail(doc1_excerpt="x" * 1000, doc2_excerpt="y" * 1000)
        await tracker.track_conflict(FakeResult(), detail, kb_id="kb1")
        stream_entry = redis.xadd.call_args[0][1]
        assert len(stream_entry["doc_a_excerpt"]) <= 500
        assert len(stream_entry["doc_b_excerpt"]) <= 500


class TestResolveConflict:
    @pytest.mark.asyncio
    async def test_noop_when_disabled(self):
        tracker = DedupResultTracker(redis_client=None)
        result = await tracker.resolve_conflict("conflict-abc", "keep_both")
        assert result is False

    @pytest.mark.asyncio
    async def test_resolve_success(self):
        redis = make_mock_redis()
        redis.exists.return_value = True
        tracker = DedupResultTracker(redis_client=redis)
        result = await tracker.resolve_conflict("conflict-abc", "keep_both", "admin")
        assert result is True
        # Should update hash
        redis.hset.assert_called_once()
        hset_mapping = redis.hset.call_args[1]["mapping"]
        assert hset_mapping["status"] == "resolved"
        assert hset_mapping["resolution"] == "keep_both"
        assert hset_mapping["resolved_by"] == "admin"
        # Should add audit trail
        redis.xadd.assert_called_once()

    @pytest.mark.asyncio
    async def test_resolve_not_found(self):
        redis = make_mock_redis()
        redis.exists.return_value = False
        tracker = DedupResultTracker(redis_client=redis)
        result = await tracker.resolve_conflict("conflict-missing", "keep_both")
        assert result is False

    @pytest.mark.asyncio
    async def test_resolve_swallows_exception(self):
        redis = make_mock_redis()
        redis.exists.side_effect = ConnectionError("redis down")
        tracker = DedupResultTracker(redis_client=redis)
        result = await tracker.resolve_conflict("conflict-abc", "keep_both")
        assert result is False


class TestGetStats:
    @pytest.mark.asyncio
    async def test_disabled_returns_empty(self):
        tracker = DedupResultTracker(redis_client=None)
        stats = await tracker.get_stats()
        assert stats == _empty_stats()

    @pytest.mark.asyncio
    async def test_returns_counts(self):
        redis = make_mock_redis()
        redis.xlen.side_effect = [100, 20]  # results, conflicts

        # scan_iter yields keys
        async def fake_scan_iter(match=None):
            for key in ["dedup:conflict:a", "dedup:conflict:b", "dedup:conflict:c"]:
                yield key

        redis.scan_iter = fake_scan_iter
        redis.hget.side_effect = ["pending", "resolved", "pending"]

        tracker = DedupResultTracker(redis_client=redis)
        stats = await tracker.get_stats()
        assert stats["total_duplicates_found"] == 100
        assert stats["total_conflicts"] == 20
        assert stats["pending"] == 2
        assert stats["total_resolved"] == 1

    @pytest.mark.asyncio
    async def test_swallows_exception(self):
        redis = make_mock_redis()
        redis.xlen.side_effect = ConnectionError("redis down")
        tracker = DedupResultTracker(redis_client=redis)
        stats = await tracker.get_stats()
        assert stats == _empty_stats()


class TestGetConflicts:
    @pytest.mark.asyncio
    async def test_disabled_returns_empty(self):
        tracker = DedupResultTracker(redis_client=None)
        result = await tracker.get_conflicts()
        assert result["conflicts"] == []
        assert result["total"] == 0

    @pytest.mark.asyncio
    async def test_returns_paginated(self):
        redis = make_mock_redis()
        redis.xlen.return_value = 5
        redis.xrevrange.return_value = [
            ("1234-0", {"conflict_id": "conflict-aaa", "doc_id": "d1"}),
            ("1234-1", {"conflict_id": "conflict-bbb", "doc_id": "d2"}),
        ]
        redis.hget.return_value = "pending"

        tracker = DedupResultTracker(redis_client=redis)
        result = await tracker.get_conflicts(page=1, page_size=10)
        assert result["total"] == 5
        assert len(result["conflicts"]) == 2
        assert result["conflicts"][0]["resolution_status"] == "pending"

    @pytest.mark.asyncio
    async def test_swallows_exception(self):
        redis = make_mock_redis()
        redis.xlen.side_effect = ConnectionError("redis down")
        tracker = DedupResultTracker(redis_client=redis)
        result = await tracker.get_conflicts()
        assert result["conflicts"] == []


class TestConstants:
    def test_stream_keys(self):
        assert DEDUP_RESULTS_STREAM == "dedup:results"
        assert DEDUP_CONFLICTS_STREAM == "dedup:conflicts"
        assert DEDUP_RESOLUTIONS_STREAM == "dedup:resolutions"

    def test_maxlen_values(self):
        assert DEDUP_RESULTS_MAXLEN == 100_000
        assert DEDUP_CONFLICTS_MAXLEN == 50_000
        assert DEDUP_RESOLUTIONS_MAXLEN == 50_000

    def test_ttl(self):
        assert DEDUP_CONFLICT_TTL_DAYS == 30
