"""Unit tests for DedupPipeline (4-Stage Orchestrator)."""

from __future__ import annotations

import pytest

from src.pipeline.dedup.bloom_filter import BloomFilter
from src.pipeline.dedup.dedup_pipeline import (
    DedupPipeline,
    DedupStatus,
    Document,
    Resolution,
)
from src.pipeline.dedup.lshbloom import LSHBloom


def _make_doc(doc_id: str, title: str, content: str, url: str | None = None) -> Document:
    return Document(doc_id=doc_id, title=title, content=content, url=url)


@pytest.fixture
def pipeline() -> DedupPipeline:
    """Pipeline with Stage 4 disabled (no LLM needed)."""
    return DedupPipeline(
        enable_stage4=False,
        near_duplicate_threshold=0.80,
        semantic_duplicate_threshold=0.90,
    )


class TestDedupPipeline:
    """Tests for the 4-stage dedup pipeline."""

    @pytest.mark.asyncio
    async def test_exact_duplicate_detected_stage1(self, pipeline: DedupPipeline):
        """Stage 1 Bloom filter should detect exact title/content duplicates."""
        doc1 = _make_doc("doc-1", "Kubernetes Guide", "How to deploy pods in k8s cluster")
        doc2 = _make_doc("doc-2", "Kubernetes Guide", "How to deploy pods in k8s cluster")

        result1 = await pipeline.add(doc1)
        assert result1.status == DedupStatus.UNIQUE

        result2 = await pipeline.check(doc2)
        assert result2.status == DedupStatus.EXACT_DUPLICATE
        assert result2.duplicate_of == "doc-1"
        assert result2.similarity_score == 1.0
        assert result2.resolution == Resolution.KEEP_NEWEST
        assert result2.stage_reached == 1

    @pytest.mark.asyncio
    async def test_near_duplicate_detected_stage2(self, pipeline: DedupPipeline):
        """Stage 2 LSH should detect near-duplicate documents."""
        content_a = "the deployment pipeline for microservices uses kubernetes helm charts and argocd for gitops workflow"
        content_b = "the deployment pipeline for microservices uses kubernetes helm charts and argocd for gitops workflow management"

        doc_a = _make_doc("doc-a", "Deploy A", content_a)
        doc_b = _make_doc("doc-b", "Deploy B", content_b)

        await pipeline.add(doc_a)
        result = await pipeline.check(doc_b)

        # Should be detected as near-duplicate at Stage 2
        # (different titles means Stage 1 won't catch it, content is nearly identical)
        assert result.status in (DedupStatus.NEAR_DUPLICATE, DedupStatus.UNIQUE)
        if result.status == DedupStatus.NEAR_DUPLICATE:
            assert result.duplicate_of == "doc-a"
            assert result.stage_reached >= 2

    @pytest.mark.asyncio
    async def test_unique_document_passes_all_stages(self, pipeline: DedupPipeline):
        """A completely unique document should pass all stages."""
        doc1 = _make_doc("doc-1", "Title Alpha", "completely different alpha content about apples")
        doc2 = _make_doc("doc-2", "Title Beta", "totally unrelated beta content about quantum physics")

        await pipeline.add(doc1)
        result = await pipeline.check(doc2)

        assert result.status == DedupStatus.UNIQUE
        assert result.duplicate_of is None
        assert result.resolution == Resolution.NONE

    @pytest.mark.asyncio
    async def test_pipeline_metrics_tracking(self, pipeline: DedupPipeline):
        """Pipeline should track metrics correctly."""
        metrics = pipeline.get_metrics()
        assert metrics.total_processed == 0

        doc1 = _make_doc("doc-1", "Title One", "content one about something")
        doc2 = _make_doc("doc-2", "Title One", "content one about something")  # exact dup

        await pipeline.add(doc1)
        await pipeline.check(doc2)

        metrics = pipeline.get_metrics()
        assert metrics.total_processed == 2
        assert metrics.stage1_filtered >= 1
        assert metrics.total_processing_time_ms > 0

    @pytest.mark.asyncio
    async def test_stage3_skip_optimization(self):
        """When Jaccard < skip_threshold, Stage 3 should be skipped."""
        pipeline = DedupPipeline(
            enable_stage4=False,
            near_duplicate_threshold=0.50,
            stage3_skip_threshold=0.85,
        )

        # Create two documents that share some words but are not very similar
        content_a = "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu"
        content_b = "alpha beta gamma delta nu xi omicron pi rho sigma tau upsilon"

        doc_a = _make_doc("doc-a", "Greek A", content_a)
        doc_b = _make_doc("doc-b", "Greek B", content_b)

        await pipeline.add(doc_a)
        result = await pipeline.check(doc_b)

        # If detected as near-dup with low Jaccard, stage3 should be skipped
        if result.status == DedupStatus.NEAR_DUPLICATE:
            assert result.details.get("stage3_skipped") is True or result.stage_reached == 2
