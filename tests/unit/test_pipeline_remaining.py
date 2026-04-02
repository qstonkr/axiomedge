"""Comprehensive tests for remaining pipeline modules."""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.pipeline.document_parser import ParseResult


# ===========================================================================
# JsonlCheckpoint
# ===========================================================================

class TestJsonlCheckpoint:
    def test_serialize_deserialize_roundtrip(self):
        from src.pipeline.jsonl_checkpoint import serialize_parse_result, deserialize_record

        pr = ParseResult(
            text="hello world",
            tables=[[["a", "b"], ["c", "d"]]],
            ocr_text="ocr data",
            images_processed=2,
            visual_analyses=[{"type": "chart"}],
        )
        line = serialize_parse_result(
            doc_id="d1",
            filename="test.pdf",
            source_path="/tmp/test.pdf",
            content_hash="abc123",
            parse_result=pr,
            metadata={"key": "value"},
        )

        record, restored_pr = deserialize_record(line)
        assert record.doc_id == "d1"
        assert record.filename == "test.pdf"
        assert record.content_hash == "abc123"
        assert restored_pr.text == "hello world"
        assert restored_pr.ocr_text == "ocr data"
        assert restored_pr.images_processed == 2
        assert len(restored_pr.tables) == 1

    def test_get_jsonl_path(self):
        from src.pipeline.jsonl_checkpoint import get_jsonl_path

        path = get_jsonl_path("my-kb")
        assert "my-kb" in str(path)
        assert path.name == "parsed_documents.jsonl"

    def test_get_already_parsed_ids_empty(self):
        from src.pipeline.jsonl_checkpoint import get_already_parsed_ids

        with tempfile.NamedTemporaryFile(suffix=".jsonl", mode="w", delete=False) as f:
            f.write("")
            path = f.name

        ids = get_already_parsed_ids(path)
        assert ids == set()

    def test_get_already_parsed_ids_with_data(self):
        from src.pipeline.jsonl_checkpoint import get_already_parsed_ids

        with tempfile.NamedTemporaryFile(suffix=".jsonl", mode="w", delete=False) as f:
            f.write(json.dumps({"doc_id": "d1"}) + "\n")
            f.write(json.dumps({"doc_id": "d2"}) + "\n")
            f.write("\n")  # empty line
            f.write("malformed json\n")  # bad line
            path = f.name

        ids = get_already_parsed_ids(path)
        assert ids == {"d1", "d2"}

    def test_get_already_parsed_ids_nonexistent(self):
        from src.pipeline.jsonl_checkpoint import get_already_parsed_ids

        ids = get_already_parsed_ids("/nonexistent/path.jsonl")
        assert ids == set()


class TestJsonlWriter:
    def test_write_and_read(self):
        from src.pipeline.jsonl_checkpoint import (
            JsonlCheckpointWriter,
            JsonlCheckpointReader,
            serialize_parse_result,
        )

        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            path = f.name

        pr = ParseResult(text="test content")
        line = serialize_parse_result("d1", "test.pdf", "/tmp/test.pdf", "hash1", pr)

        with JsonlCheckpointWriter(path) as writer:
            writer.write_record(line)

        reader = JsonlCheckpointReader(path)
        records = list(reader)
        assert len(records) == 1
        record, restored = records[0]
        assert record.doc_id == "d1"
        assert restored.text == "test content"

    def test_reader_count(self):
        from src.pipeline.jsonl_checkpoint import JsonlCheckpointWriter, JsonlCheckpointReader

        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            path = f.name

        with open(path, "w") as f:
            f.write('{"doc_id":"d1","filename":"a","text":"t"}\n')
            f.write('{"doc_id":"d2","filename":"b","text":"t"}\n')
            f.write("\n")

        reader = JsonlCheckpointReader(path)
        assert reader.count() == 2

    def test_reader_nonexistent(self):
        from src.pipeline.jsonl_checkpoint import JsonlCheckpointReader

        reader = JsonlCheckpointReader("/nonexistent/file.jsonl")
        records = list(reader)
        assert records == []
        assert reader.count() == 0

    def test_reader_skips_malformed(self):
        from src.pipeline.jsonl_checkpoint import JsonlCheckpointReader

        with tempfile.NamedTemporaryFile(suffix=".jsonl", mode="w", delete=False) as f:
            f.write("not valid json\n")
            f.write('{"doc_id":"d1","filename":"a","text":"t"}\n')
            path = f.name

        reader = JsonlCheckpointReader(path)
        records = list(reader)
        assert len(records) == 1


# ===========================================================================
# FreshnessRanker
# ===========================================================================

class TestFreshnessRanker:
    def setup_method(self):
        from src.pipeline.freshness_ranker import FreshnessRanker, FreshnessConfig
        self.ranker = FreshnessRanker(FreshnessConfig(
            fresh_days=90,
            stale_days=365,
            outdated_days=730,
            fresh_boost=1.1,
            stale_penalty=0.9,
            outdated_penalty=0.7,
            warning_threshold_days=365,
        ))

    def test_rank_empty(self):
        result = self.ranker.rank([])
        assert result == []

    def test_rank_fresh_document(self):
        today = datetime.now().strftime("%Y-%m-%d")
        results = [{"content": "test", "metadata": {"updated_at": today}, "similarity": 0.8}]
        ranked = self.ranker.rank(results)
        assert len(ranked) == 1
        assert ranked[0].adjusted_score > ranked[0].original_score  # fresh boost

    def test_rank_stale_document(self):
        old_date = "2020-01-01"
        results = [{"content": "test", "metadata": {"updated_at": old_date}, "similarity": 0.8}]
        ranked = self.ranker.rank(results)
        assert ranked[0].adjusted_score < ranked[0].original_score

    def test_rank_no_date(self):
        results = [{"content": "test", "metadata": {}, "similarity": 0.8}]
        ranked = self.ranker.rank(results)
        assert ranked[0].adjusted_score == pytest.approx(0.8)
        assert ranked[0].freshness_warning is None

    def test_rank_no_penalty(self):
        old_date = "2020-01-01"
        results = [{"content": "test", "metadata": {"updated_at": old_date}, "similarity": 0.8}]
        ranked = self.ranker.rank(results, apply_penalty=False)
        assert ranked[0].adjusted_score == pytest.approx(0.8)

    def test_rank_version_bonus(self):
        today = datetime.now().strftime("%Y-%m-%d")
        results = [
            {"content": "test", "metadata": {"updated_at": today, "version_count": 15}, "similarity": 0.8},
        ]
        ranked = self.ranker.rank(results)
        # High version count should give bonus
        assert ranked[0].adjusted_score > 0.8

    def test_filter_outdated(self):
        from src.pipeline.freshness_ranker import RankedResult
        results = [
            RankedResult(content="a", metadata={}, original_score=0.5, adjusted_score=0.5, freshness_warning=None, days_since_update=100),
            RankedResult(content="b", metadata={}, original_score=0.5, adjusted_score=0.5, freshness_warning=None, days_since_update=1000),
            RankedResult(content="c", metadata={}, original_score=0.5, adjusted_score=0.5, freshness_warning=None, days_since_update=None),
        ]
        filtered = self.ranker.filter_outdated(results, max_days=500)
        assert len(filtered) == 2

    def test_format_result_with_warning(self):
        from src.pipeline.freshness_ranker import RankedResult
        result = RankedResult(
            content="test content",
            metadata={},
            original_score=0.5,
            adjusted_score=0.3,
            freshness_warning="2년 이상 미수정 문서",
            days_since_update=800,
        )
        formatted = self.ranker.format_result_with_warning(result)
        assert "2년 이상 미수정 문서" in formatted
        assert "test content" in formatted

    def test_to_int(self):
        from src.pipeline.freshness_ranker import FreshnessRanker
        assert FreshnessRanker._to_int(10) == 10
        assert FreshnessRanker._to_int("5") == 5
        assert FreshnessRanker._to_int(None) == 0
        assert FreshnessRanker._to_int("invalid") == 0

    def test_get_warning_very_old(self):
        warning = self.ranker._get_warning(800)
        assert "년 이상" in warning

    def test_get_warning_stale(self):
        warning = self.ranker._get_warning(400)
        assert "개월" in warning

    def test_get_warning_fresh(self):
        warning = self.ranker._get_warning(30)
        assert warning is None


# ===========================================================================
# ConflictDetector
# ===========================================================================

class TestConflictDetector:
    @pytest.mark.asyncio
    async def test_analyze_no_conflict(self):
        from src.pipeline.dedup.conflict_detector import ConflictDetector, NoOpLLMClient

        detector = ConflictDetector(llm_client=NoOpLLMClient())
        result = await detector.analyze("d1", "Content A", "d2", "Content B")
        assert result.has_conflict is False
        assert result.doc_id_1 == "d1"
        assert result.doc_id_2 == "d2"

    @pytest.mark.asyncio
    async def test_analyze_with_conflict(self):
        from src.pipeline.dedup.conflict_detector import ConflictDetector, ILLMClient

        class MockLLM(ILLMClient):
            async def complete(self, prompt, model="", temperature=0.0):
                return json.dumps({
                    "has_conflict": True,
                    "confidence": 0.9,
                    "conflicts": [{
                        "conflict_type": "date_conflict",
                        "severity": "high",
                        "description": "Date mismatch",
                        "doc1_excerpt": "Jan 2024",
                        "doc2_excerpt": "Feb 2024",
                        "resolution_suggestion": "Check dates",
                    }],
                })

        detector = ConflictDetector(llm_client=MockLLM())
        result = await detector.analyze("d1", "Jan 2024", "d2", "Feb 2024")
        assert result.has_conflict is True
        assert len(result.conflicts) == 1
        assert result.max_severity is not None

    @pytest.mark.asyncio
    async def test_analyze_timeout(self):
        from src.pipeline.dedup.conflict_detector import ConflictDetector, ILLMClient
        import asyncio

        class SlowLLM(ILLMClient):
            async def complete(self, prompt, model="", temperature=0.0):
                await asyncio.sleep(100)
                return ""

        detector = ConflictDetector(llm_client=SlowLLM())
        detector.LLM_TIMEOUT_SECONDS = 0.01
        result = await detector.analyze("d1", "A", "d2", "B")
        assert result.confidence == 0.0

    @pytest.mark.asyncio
    async def test_analyze_batch(self):
        from src.pipeline.dedup.conflict_detector import ConflictDetector, NoOpLLMClient

        detector = ConflictDetector(llm_client=NoOpLLMClient())
        results = await detector.analyze_batch([
            ("d1", "A", "d2", "B"),
            ("d3", "C", "d4", "D"),
        ])
        assert len(results) == 2

    def test_quick_conflict_check_dates(self):
        from src.pipeline.dedup.conflict_detector import ConflictDetector, NoOpLLMClient

        detector = ConflictDetector(llm_client=NoOpLLMClient())
        hints = detector.quick_conflict_check(
            "배포일: 2024-01-15", "배포일: 2024-02-20"
        )
        assert any("Date" in h for h in hints)

    def test_quick_conflict_check_versions(self):
        from src.pipeline.dedup.conflict_detector import ConflictDetector, NoOpLLMClient

        detector = ConflictDetector(llm_client=NoOpLLMClient())
        hints = detector.quick_conflict_check("v1.0.0", "v2.0.0")
        assert any("Version" in h for h in hints)

    def test_quick_conflict_check_no_conflict(self):
        from src.pipeline.dedup.conflict_detector import ConflictDetector, NoOpLLMClient

        detector = ConflictDetector(llm_client=NoOpLLMClient())
        hints = detector.quick_conflict_check("same text", "same text")
        assert hints == []


class TestConflictAnalysisResult:
    def test_max_severity_empty(self):
        from src.pipeline.dedup.conflict_detector import ConflictAnalysisResult
        result = ConflictAnalysisResult(doc_id_1="d1", doc_id_2="d2")
        assert result.max_severity is None

    def test_max_severity_ordered(self):
        from src.pipeline.dedup.conflict_detector import (
            ConflictAnalysisResult, ConflictDetail, ConflictType, ConflictSeverity,
        )
        result = ConflictAnalysisResult(
            doc_id_1="d1", doc_id_2="d2",
            conflicts=[
                ConflictDetail(ConflictType.DATE_CONFLICT, ConflictSeverity.LOW, "low"),
                ConflictDetail(ConflictType.VERSION_CONFLICT, ConflictSeverity.CRITICAL, "critical"),
            ],
        )
        assert result.max_severity == ConflictSeverity.CRITICAL

    def test_to_dict(self):
        from src.pipeline.dedup.conflict_detector import ConflictAnalysisResult
        result = ConflictAnalysisResult(doc_id_1="d1", doc_id_2="d2", has_conflict=False)
        d = result.to_dict()
        assert d["doc_id_1"] == "d1"
        assert d["has_conflict"] is False


# ===========================================================================
# DedupResultTracker
# ===========================================================================

class TestDedupResultTracker:
    def test_disabled(self):
        from src.pipeline.dedup.result_tracker import DedupResultTracker
        tracker = DedupResultTracker(redis_client=None)
        assert tracker.enabled is False

    @pytest.mark.asyncio
    async def test_track_result_disabled(self):
        from src.pipeline.dedup.result_tracker import DedupResultTracker
        tracker = DedupResultTracker(redis_client=None)
        await tracker.track_result(MagicMock(), "kb1")  # Should not raise

    @pytest.mark.asyncio
    async def test_track_result_enabled(self):
        from src.pipeline.dedup.result_tracker import DedupResultTracker
        redis = AsyncMock()
        tracker = DedupResultTracker(redis_client=redis)

        result = MagicMock()
        result.doc_id = "d1"
        result.status = "unique"
        result.duplicate_of = None
        result.similarity_score = 0.0
        result.stage_reached = 1
        result.processing_time_ms = 10.0
        result.resolution = "none"
        result.conflict_types = []

        await tracker.track_result(result, "kb1", doc_title="Test")
        redis.xadd.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_track_conflict_disabled(self):
        from src.pipeline.dedup.result_tracker import DedupResultTracker
        tracker = DedupResultTracker(redis_client=None)
        result = await tracker.track_conflict(MagicMock(), None, "kb1")
        assert result == ""

    @pytest.mark.asyncio
    async def test_resolve_conflict_disabled(self):
        from src.pipeline.dedup.result_tracker import DedupResultTracker
        tracker = DedupResultTracker(redis_client=None)
        assert await tracker.resolve_conflict("c1", "keep_both") is False

    @pytest.mark.asyncio
    async def test_get_stats_disabled(self):
        from src.pipeline.dedup.result_tracker import DedupResultTracker
        tracker = DedupResultTracker(redis_client=None)
        stats = await tracker.get_stats()
        assert stats["total_duplicates_found"] == 0

    @pytest.mark.asyncio
    async def test_get_conflicts_disabled(self):
        from src.pipeline.dedup.result_tracker import DedupResultTracker
        tracker = DedupResultTracker(redis_client=None)
        result = await tracker.get_conflicts()
        assert result["conflicts"] == []


# ===========================================================================
# RedisDedupIndex
# ===========================================================================

class TestRedisDedupIndex:
    def test_disabled(self):
        from src.pipeline.dedup.redis_index import RedisDedupIndex
        idx = RedisDedupIndex(redis_client=None)
        assert idx.enabled is False

    @pytest.mark.asyncio
    async def test_contains_disabled(self):
        from src.pipeline.dedup.redis_index import RedisDedupIndex
        idx = RedisDedupIndex(redis_client=None)
        assert await idx.contains("kb1", "hash") is False

    @pytest.mark.asyncio
    async def test_add_disabled(self):
        from src.pipeline.dedup.redis_index import RedisDedupIndex
        idx = RedisDedupIndex(redis_client=None)
        assert await idx.add("kb1", "hash") is False

    @pytest.mark.asyncio
    async def test_add_enabled(self):
        from src.pipeline.dedup.redis_index import RedisDedupIndex
        redis = AsyncMock()
        redis.sadd.return_value = 1
        redis.ttl.return_value = -1
        idx = RedisDedupIndex(redis_client=redis)

        result = await idx.add("kb1", "hash123")
        assert result is True
        redis.sadd.assert_awaited_once()
        redis.expire.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_contains_enabled(self):
        from src.pipeline.dedup.redis_index import RedisDedupIndex
        redis = AsyncMock()
        redis.sismember.return_value = True
        idx = RedisDedupIndex(redis_client=redis)

        assert await idx.contains("kb1", "hash") is True

    @pytest.mark.asyncio
    async def test_add_batch_empty(self):
        from src.pipeline.dedup.redis_index import RedisDedupIndex
        idx = RedisDedupIndex(redis_client=None)
        assert await idx.add_batch("kb1", []) == 0

    @pytest.mark.asyncio
    async def test_clear(self):
        from src.pipeline.dedup.redis_index import RedisDedupIndex
        redis = AsyncMock()
        idx = RedisDedupIndex(redis_client=redis)
        result = await idx.clear("kb1")
        assert result is True

    @pytest.mark.asyncio
    async def test_size(self):
        from src.pipeline.dedup.redis_index import RedisDedupIndex
        redis = AsyncMock()
        redis.scard.return_value = 42
        idx = RedisDedupIndex(redis_client=redis)
        assert await idx.size("kb1") == 42

    @pytest.mark.asyncio
    async def test_contains_doc_disabled(self):
        from src.pipeline.dedup.redis_index import RedisDedupIndex
        idx = RedisDedupIndex(redis_client=None)
        assert await idx.contains_doc("kb1", "hash") is False

    @pytest.mark.asyncio
    async def test_add_doc(self):
        from src.pipeline.dedup.redis_index import RedisDedupIndex
        redis = AsyncMock()
        redis.sadd.return_value = 1
        redis.ttl.return_value = -1
        idx = RedisDedupIndex(redis_client=redis)
        assert await idx.add_doc("kb1", "hash") is True

    @pytest.mark.asyncio
    async def test_clear_docs(self):
        from src.pipeline.dedup.redis_index import RedisDedupIndex
        redis = AsyncMock()
        idx = RedisDedupIndex(redis_client=redis)
        assert await idx.clear_docs("kb1") is True


# ===========================================================================
# Neo4jKnowledgeLoader
# ===========================================================================

class TestNeo4jKnowledgeLoader:
    def test_sanitize_label_whitelist(self):
        from src.pipeline.neo4j_loader import Neo4jKnowledgeLoader, Neo4jConfig, ALLOWED_NODE_TYPES

        loader = Neo4jKnowledgeLoader(Neo4jConfig())
        assert loader._sanitize_label("Person", ALLOWED_NODE_TYPES, "Entity") == "Person"
        assert loader._sanitize_label("person", ALLOWED_NODE_TYPES, "Entity") == "Person"

    def test_sanitize_label_not_allowed(self):
        from src.pipeline.neo4j_loader import Neo4jKnowledgeLoader, Neo4jConfig, ALLOWED_NODE_TYPES

        loader = Neo4jKnowledgeLoader(Neo4jConfig())
        assert loader._sanitize_label("MaliciousType", ALLOWED_NODE_TYPES, "Entity") == "Entity"

    def test_sanitize_label_empty(self):
        from src.pipeline.neo4j_loader import Neo4jKnowledgeLoader, Neo4jConfig, ALLOWED_NODE_TYPES

        loader = Neo4jKnowledgeLoader(Neo4jConfig())
        assert loader._sanitize_label("", ALLOWED_NODE_TYPES, "Entity") == "Entity"

    def test_safe_identifier_pattern(self):
        from src.pipeline.neo4j_loader import SAFE_IDENTIFIER_PATTERN
        assert SAFE_IDENTIFIER_PATTERN.match("Person")
        assert SAFE_IDENTIFIER_PATTERN.match("RELATED_TO")
        assert not SAFE_IDENTIFIER_PATTERN.match("123Bad")
        assert not SAFE_IDENTIFIER_PATTERN.match("")
