"""Extra coverage tests (batch 3).

Targets: dedup_pipeline data classes + check flow (65 uncov),
enhanced_similarity_matcher L1/L2 internal methods (128 uncov).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from dataclasses import dataclass, field

import pytest

# ===========================================================================
# DedupPipeline data classes & flow
# ===========================================================================

from src.pipeline.dedup.dedup_pipeline import (
    Document,
    DedupResult,
    DedupStatus,
    Resolution,
    PipelineMetrics,
    DedupPipeline,
)


class TestDedupDocument:
    def test_title_hash(self):
        doc = Document(doc_id="d1", title="Test Doc", content="content")
        assert isinstance(doc.title_hash, str)
        assert len(doc.title_hash) == 16

    def test_url_hash(self):
        doc = Document(doc_id="d1", title="T", content="C", url="http://example.com")
        assert isinstance(doc.url_hash, str)
        assert len(doc.url_hash) == 16

    def test_url_hash_none(self):
        doc = Document(doc_id="d1", title="T", content="C")
        assert doc.url_hash is None

    def test_content_hash(self):
        doc = Document(doc_id="d1", title="T", content="hello world")
        assert isinstance(doc.content_hash, str)
        assert len(doc.content_hash) == 32

    def test_same_title_same_hash(self):
        d1 = Document(doc_id="d1", title="Same Title", content="c1")
        d2 = Document(doc_id="d2", title="Same Title", content="c2")
        assert d1.title_hash == d2.title_hash


class TestDedupResult:
    def test_defaults(self):
        r = DedupResult(doc_id="d1")
        assert r.status == DedupStatus.UNIQUE
        assert r.resolution == Resolution.NONE

    def test_to_dict(self):
        r = DedupResult(
            doc_id="d1",
            status=DedupStatus.EXACT_DUPLICATE,
            duplicate_of="d0",
            similarity_score=1.0,
            resolution=Resolution.KEEP_NEWEST,
        )
        d = r.to_dict()
        assert d["status"] == "exact_duplicate"
        assert d["resolution"] == "keep_newest"


class TestPipelineMetrics:
    def test_empty(self):
        m = PipelineMetrics()
        assert m.avg_processing_time_ms == 0.0
        assert m.stage1_filter_rate == 0.0

    def test_with_data(self):
        m = PipelineMetrics(
            total_processed=10,
            stage1_filtered=3,
            total_processing_time_ms=100.0,
        )
        assert m.avg_processing_time_ms == 10.0
        assert m.stage1_filter_rate == 0.3

    def test_to_dict(self):
        m = PipelineMetrics(total_processed=5, stage1_filtered=1)
        d = m.to_dict()
        assert "total_processed" in d
        assert "stage1_filter_rate" in d


class TestDedupPipeline:
    async def test_unique_document(self):
        pipeline = DedupPipeline(enable_stage4=False)
        doc = Document(doc_id="d1", title="Unique", content="This is unique content.")
        result = await pipeline.check(doc)
        assert result.status == DedupStatus.UNIQUE

    async def test_add_and_check_exact_dup(self):
        pipeline = DedupPipeline(enable_stage4=False)
        doc1 = Document(doc_id="d1", title="Title", content="Same content here.")
        doc2 = Document(doc_id="d2", title="Title", content="Same content here.")
        await pipeline.add(doc1)
        result = await pipeline.check(doc2)
        assert result.status == DedupStatus.EXACT_DUPLICATE
        assert result.duplicate_of == "d1"

    async def test_metrics_tracked(self):
        pipeline = DedupPipeline(enable_stage4=False)
        doc = Document(doc_id="d1", title="T", content="C")
        await pipeline.check(doc)
        assert pipeline.get_metrics().total_processed == 1

    def test_init_defaults(self):
        pipeline = DedupPipeline()
        assert pipeline._near_threshold > 0
        assert pipeline._semantic_threshold > 0

    def test_stage1_no_match(self):
        pipeline = DedupPipeline()
        doc = Document(doc_id="d1", title="New", content="brand new")
        result = pipeline._stage1_prefilter(doc)
        assert result is None

    def test_stage2_no_match(self):
        pipeline = DedupPipeline()
        doc = Document(doc_id="d1", title="T", content="Some content")
        result = pipeline._stage2_lshbloom(doc)
        assert result is None


# Check the add method exists
class TestDedupPipelineAdd:
    async def test_add(self):
        pipeline = DedupPipeline(enable_stage4=False)
        doc = Document(doc_id="d1", title="T", content="C")
        await pipeline.add(doc)
        assert "d1" in pipeline._documents

    async def test_add_multiple(self):
        pipeline = DedupPipeline(enable_stage4=False)
        for i in range(5):
            doc = Document(doc_id=f"d{i}", title=f"T{i}", content=f"Content {i}")
            await pipeline.add(doc)
        assert pipeline.get_metrics().total_processed == 5  # add calls check internally


class TestDedupPipelineStages:
    """Test deeper stage flows."""

    async def test_stage1_url_hash_match_different_content(self):
        """Same URL but different content → NOT exact duplicate (fixed: only content hash matters)."""
        pipeline = DedupPipeline(enable_stage4=False)
        doc1 = Document(doc_id="d1", title="T1", content="C1", url="http://example.com/page")
        await pipeline.add(doc1)
        doc2 = Document(doc_id="d2", title="T2", content="C2", url="http://example.com/page")
        result = await pipeline.check(doc2)
        assert result.status != DedupStatus.EXACT_DUPLICATE

    async def test_stage1_url_hash_match_same_content(self):
        """Same URL AND same content → exact duplicate."""
        pipeline = DedupPipeline(enable_stage4=False)
        doc1 = Document(doc_id="d1", title="T1", content="Same body", url="http://example.com/page")
        await pipeline.add(doc1)
        doc2 = Document(doc_id="d2", title="T2", content="Same body", url="http://example.com/page")
        result = await pipeline.check(doc2)
        assert result.status == DedupStatus.EXACT_DUPLICATE

    async def test_stage1_content_hash_match(self):
        pipeline = DedupPipeline(enable_stage4=False)
        doc1 = Document(doc_id="d1", title="Title A", content="identical content here")
        await pipeline.add(doc1)
        doc2 = Document(doc_id="d2", title="Title B", content="identical content here")
        result = await pipeline.check(doc2)
        assert result.status == DedupStatus.EXACT_DUPLICATE

    async def test_check_and_add(self):
        pipeline = DedupPipeline(enable_stage4=False)
        doc = Document(doc_id="d1", title="New", content="New unique content")
        result = await pipeline.add(doc)
        assert result.status == DedupStatus.UNIQUE
        assert "d1" in pipeline._documents

    async def test_add_to_semhash(self):
        pipeline = DedupPipeline(enable_stage4=False)
        doc = Document(doc_id="d1", title="T", content="C")
        await pipeline.add_to_semhash(doc)

    def test_reset_metrics(self):
        pipeline = DedupPipeline(enable_stage4=False)
        pipeline._metrics.total_processed = 100
        pipeline.reset_metrics()
        assert pipeline.get_metrics().total_processed == 0

    def test_get_metrics(self):
        pipeline = DedupPipeline(enable_stage4=False)
        metrics = pipeline.get_metrics()
        assert metrics.total_processed == 0


# ===========================================================================
# EnhancedSimilarityMatcher - L1 internal methods
# ===========================================================================

from src.search.enhanced_similarity_matcher import (
    EnhancedSimilarityMatcher,
    EnhancedMatcherConfig,
    _try_strip_particle,
    _strip_particles,
)


@dataclass
class _FakeTerm:
    term: str
    term_ko: str = ""
    synonyms: list[str] = field(default_factory=list)
    abbreviations: list[str] = field(default_factory=list)
    physical_meaning: str = ""
    term_type: str = "TERM"
    definition: str = ""


class TestStripParticles:
    def test_strip_single(self):
        assert _strip_particles("시스템에서") == "시스템"

    def test_strip_multiple(self):
        assert _strip_particles("시스템에서까지") == "시스템"

    def test_no_particle(self):
        assert _strip_particles("시스템") == "시스템"

    def test_short_word_no_strip(self):
        # "서의" -> len("서") = 1 + len("의") = 1, total 2 <= 1 + 2 -> no strip
        result = _strip_particles("서의")
        assert result == "서의"


class TestESM_L1:
    def _build(self, terms):
        m = EnhancedSimilarityMatcher(
            config=EnhancedMatcherConfig(
                enable_rapidfuzz=False,
                enable_dense_search=False,
                enable_cross_encoder=False,
            ),
        )
        m.load_standard_terms(terms)
        return m

    def test_l1_exact_match_found(self):
        m = self._build([_FakeTerm(term="GraphRAG", term_ko="그래프래그")])
        result = m._l1_exact_match("GraphRAG")
        assert result is not None
        assert result.zone == "AUTO_MATCH"

    def test_l1_exact_match_not_found(self):
        m = self._build([_FakeTerm(term="GraphRAG")])
        result = m._l1_exact_match("Unknown")
        assert result is None

    def test_l1_exact_match_empty(self):
        m = self._build([_FakeTerm(term="GraphRAG")])
        result = m._l1_exact_match("")
        assert result is None

    def test_l1_particle_match(self):
        m = self._build([_FakeTerm(term="시스템", term_ko="시스템")])
        result = m._l1_exact_match("시스템에서")
        assert result is not None
        assert result.match_type == "particle"

    def test_l1_korean_match(self):
        m = self._build([_FakeTerm(term="K8s", term_ko="쿠버네티스")])
        result = m._l1_exact_match("쿠버네티스")
        assert result is not None

    async def test_match_batch_empty(self):
        m = self._build([_FakeTerm(term="Test")])
        results = await m.match_batch([])
        assert results == []

    async def test_match_batch_single(self):
        m = self._build([_FakeTerm(term="GraphRAG")])
        results = await m.match_batch([_FakeTerm(term="GraphRAG")])
        assert len(results) == 1
        assert results[0].zone == "AUTO_MATCH"

    def test_l2_rapidfuzz_no_choices(self):
        m = EnhancedSimilarityMatcher(
            config=EnhancedMatcherConfig(enable_rapidfuzz=False),
        )
        m.load_standard_terms([_FakeTerm(term="Test")])
        result = m._l2_rapidfuzz("Test")
        assert result == []

    def test_load_with_get_term_type(self):
        """Test load_standard_terms with custom get_term_type callable."""
        m = EnhancedSimilarityMatcher()
        terms = [
            _FakeTerm(term="word1"),
            _FakeTerm(term="term1"),
        ]
        m.load_standard_terms(
            terms,
            get_term_type=lambda t: "WORD" if t.term == "word1" else "TERM",
        )
        assert len(m._word_lookup) >= 1
        assert len(m._precomputed) == 1  # only term1

    def test_load_collision_handling(self):
        """Synonym collisions should be handled gracefully."""
        m = EnhancedSimilarityMatcher()
        terms = [
            _FakeTerm(term="A", synonyms=["shared"]),
            _FakeTerm(term="B", synonyms=["shared"]),
        ]
        m.load_standard_terms(terms)
        # "shared" should map to first term, second is collision
        assert "shared" in m._normalized_lookup
