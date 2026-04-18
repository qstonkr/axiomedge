"""Deduplication Pipeline - 4-Stage Orchestrator.

4-Stage duplicate/conflict detection pipeline:

Stage 1: Metadata Pre-filter (<1ms)
    -> Bloom Filter, URL/title hash, 30-40% filtering

Stage 2: LSHBloom (<10ms)
    -> MinHash LSH, Jaccard similarity, 10-15% flagged

Stage 3: SemHash (~50ms)
    -> Embedding-based, Cosine >= 0.90, 5-8% confirmed

Stage 4: Conflict Detection (~100ms)
    -> LLM conflict analysis (local Ollama)

Resolution strategy (SSOT):
| Status         | Strategy                        |
|----------------|---------------------------------|
| Exact dup      | Keep newest, delete old         |
| Near dup       | Queue for merge                 |
| Content conflict| Owner notification + review queue|

py.
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from .bloom_filter import BloomFilter
from .lshbloom import LSHBloom, SimilarPair
from .semhash import SemHash, SemanticMatch, IEmbeddingProvider
from .conflict_detector import (
    ConflictDetector,
    ConflictAnalysisResult,
    ConflictType,
    ILLMClient,
)

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    """Current UTC time."""
    return datetime.now(UTC)


class DedupStatus(StrEnum):
    """Dedup status."""

    UNIQUE = "unique"
    EXACT_DUPLICATE = "exact_duplicate"
    NEAR_DUPLICATE = "near_duplicate"
    SEMANTIC_DUPLICATE = "semantic_duplicate"
    CONTENT_CONFLICT = "content_conflict"


class Resolution(StrEnum):
    """Resolution strategy."""

    KEEP_NEWEST = "keep_newest"
    KEEP_OLDEST = "keep_oldest"
    MERGE = "merge"
    REVIEW = "review"
    NONE = "none"


@dataclass
class Document:
    """Document data for dedup pipeline.

    Attributes:
        doc_id: Document ID
        title: Title
        content: Content
        url: URL (optional)
        created_at: Creation time
        updated_at: Update time
        metadata: Additional metadata
    """

    doc_id: str
    title: str
    content: str
    url: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def title_hash(self) -> str:
        """Title hash."""
        normalized = self.title.lower().strip()
        return hashlib.sha256(normalized.encode()).hexdigest()[:16]

    @property
    def url_hash(self) -> str | None:
        """URL hash."""
        if not self.url:
            return None
        normalized = self.url.lower().strip()
        return hashlib.sha256(normalized.encode()).hexdigest()[:16]

    @property
    def content_hash(self) -> str:
        """Content hash."""
        normalized = self.content.lower().strip()
        return hashlib.sha256(normalized.encode()).hexdigest()[:32]


@dataclass
class DedupResult:
    """Dedup detection result.

    Attributes:
        doc_id: Target document ID
        status: Dedup status
        duplicate_of: Duplicate target document ID
        similarity_score: Similarity score
        conflict_types: List of conflict types
        resolution: Recommended resolution
        stage_reached: Pipeline stage reached
        processing_time_ms: Processing time (ms)
        details: Additional details
    """

    doc_id: str
    status: DedupStatus = DedupStatus.UNIQUE
    duplicate_of: str | None = None
    similarity_score: float = 0.0
    conflict_types: list[ConflictType] = field(default_factory=list)
    resolution: Resolution = Resolution.NONE
    stage_reached: int = 0
    processing_time_ms: float = 0.0
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize."""
        return {
            "doc_id": self.doc_id,
            "status": self.status.value,
            "duplicate_of": self.duplicate_of,
            "similarity_score": self.similarity_score,
            "conflict_types": [ct.value for ct in self.conflict_types],
            "resolution": self.resolution.value,
            "stage_reached": self.stage_reached,
            "processing_time_ms": self.processing_time_ms,
            "details": self.details,
        }


@dataclass
class PipelineMetrics:
    """Pipeline metrics.

    Attributes:
        total_processed: Total documents processed
        stage1_filtered: Filtered at Stage 1
        stage2_flagged: Flagged at Stage 2
        stage3_confirmed: Confirmed at Stage 3
        stage4_conflicts: Conflicts found at Stage 4
        total_processing_time_ms: Total processing time
    """

    total_processed: int = 0
    stage1_filtered: int = 0
    stage2_flagged: int = 0
    stage3_confirmed: int = 0
    stage4_conflicts: int = 0
    total_processing_time_ms: float = 0.0

    @property
    def avg_processing_time_ms(self) -> float:
        """Average processing time."""
        if self.total_processed == 0:
            return 0.0
        return self.total_processing_time_ms / self.total_processed

    @property
    def stage1_filter_rate(self) -> float:
        """Stage 1 filter rate."""
        if self.total_processed == 0:
            return 0.0
        return self.stage1_filtered / self.total_processed

    def to_dict(self) -> dict[str, Any]:
        """Serialize."""
        return {
            "total_processed": self.total_processed,
            "stage1_filtered": self.stage1_filtered,
            "stage1_filter_rate": round(self.stage1_filter_rate, 4),
            "stage2_flagged": self.stage2_flagged,
            "stage3_confirmed": self.stage3_confirmed,
            "stage4_conflicts": self.stage4_conflicts,
            "avg_processing_time_ms": round(self.avg_processing_time_ms, 2),
        }


class DedupPipeline:
    """4-Stage duplicate/conflict detection pipeline.

    SSOT resolution strategy:
    - Exact dup: KEEP_NEWEST
    - Near dup (>= 0.90): MERGE (manual review)
    - Semantic dup (>= 0.90): REVIEW
    - Content conflict: REVIEW (owner notification)
    """

    # Thresholds — SSOT: config_weights.DedupConfig
    # Class-level defaults kept as import-free fallback only; runtime uses config_weights.
    _FALLBACK_NEAR = 0.80
    _FALLBACK_SEMANTIC = 0.90
    _FALLBACK_SKIP = 0.85

    def __init__(
        self,
        embedding_provider: IEmbeddingProvider | None = None,
        llm_client: ILLMClient | None = None,
        bloom_filter: BloomFilter | None = None,
        lsh_bloom: LSHBloom | None = None,
        sem_hash: SemHash | None = None,
        conflict_detector: ConflictDetector | None = None,
        enable_stage4: bool = True,
        near_duplicate_threshold: float | None = None,
        semantic_duplicate_threshold: float | None = None,
        stage3_skip_threshold: float | None = None,
    ) -> None:
        """Initialize.

        Args:
            embedding_provider: Embedding provider (Stage 3)
            llm_client: LLM client (Stage 4)
            bloom_filter: Stage 1 Bloom Filter
            lsh_bloom: Stage 2 LSH backend
            sem_hash: Stage 3 SemHash
            conflict_detector: Stage 4 ConflictDetector
            enable_stage4: Whether to enable Stage 4
            near_duplicate_threshold: Jaccard threshold override
            semantic_duplicate_threshold: Cosine threshold override
            stage3_skip_threshold: Stage 3 skip Jaccard threshold override
        """
        # Thresholds: constructor > config_weights > class fallback
        try:
            from src.config.weights import weights
            dedup_cfg = weights.dedup
            _cfg_near = dedup_cfg.near_duplicate_threshold
            _cfg_semantic = dedup_cfg.semantic_duplicate_threshold
            _cfg_skip = dedup_cfg.stage3_skip_threshold
        except (ImportError, AttributeError):
            _cfg_near = self._FALLBACK_NEAR
            _cfg_semantic = self._FALLBACK_SEMANTIC
            _cfg_skip = self._FALLBACK_SKIP
        self._near_threshold = near_duplicate_threshold if near_duplicate_threshold is not None else _cfg_near
        self._semantic_threshold = semantic_duplicate_threshold if semantic_duplicate_threshold is not None else _cfg_semantic  # noqa: E501
        self._skip_threshold = stage3_skip_threshold if stage3_skip_threshold is not None else _cfg_skip

        self._bloom = bloom_filter or BloomFilter()
        self._lsh = lsh_bloom or LSHBloom()
        self._sem = sem_hash or SemHash(embedding_provider=embedding_provider)
        self._conflict = conflict_detector or ConflictDetector(llm_client=llm_client)
        self._enable_stage4 = enable_stage4

        self._metrics = PipelineMetrics()

        # Document store (for conflict analysis)
        self._documents: dict[str, Document] = {}
        # H7 fix: Hash -> doc_id reverse index (O(n) -> O(1) lookup)
        self._url_hash_index: dict[str, str] = {}
        self._title_hash_index: dict[str, str] = {}
        self._content_hash_index: dict[str, str] = {}

    def _apply_stage1(self, document: Document, result: DedupResult) -> bool:
        """Stage 1: Metadata Pre-filter. Returns True if exact duplicate found."""
        stage_start = time.perf_counter()
        stage1_result = self._stage1_prefilter(document)
        _stage1_ms = (time.perf_counter() - stage_start) * 1000
        result.stage_reached = 1

        if not stage1_result:
            return False

        result.status = DedupStatus.EXACT_DUPLICATE
        result.duplicate_of = stage1_result
        result.similarity_score = 1.0
        result.resolution = Resolution.KEEP_NEWEST
        self._metrics.stage1_filtered += 1
        result.details["stage1"] = "exact_match"
        logger.debug(
            "Stage 1 exact match: doc=%s dup_of=%s (%.1fms)",
            document.doc_id, stage1_result, _stage1_ms,
        )
        return True

    async def _apply_stage2_and_beyond(
        self, document: Document, result: DedupResult
    ) -> None:
        """Stage 2 (LSHBloom) + Stage 3 (SemHash) + Stage 4 (Conflict)."""
        stage_start = time.perf_counter()
        stage2_result = self._stage2_lshbloom(document)
        _stage2_ms = (time.perf_counter() - stage_start) * 1000
        result.stage_reached = 2

        if not stage2_result:
            return

        self._metrics.stage2_flagged += 1

        # C7 fix: Jaccard < skip_threshold -> Stage 3 skip (50ms savings)
        if stage2_result.estimated_similarity < self._skip_threshold:
            result.status = DedupStatus.NEAR_DUPLICATE
            result.duplicate_of = stage2_result.doc_id_2
            result.similarity_score = stage2_result.estimated_similarity
            result.resolution = Resolution.REVIEW
            result.details["stage3_skipped"] = True
            logger.debug(
                "Stage 2 near-dup (skip S3): doc=%s dup_of=%s jaccard=%.3f (%.1fms)",
                document.doc_id, stage2_result.doc_id_2,
                stage2_result.estimated_similarity, _stage2_ms,
            )
            return

        # Stage 3: SemHash (only when Jaccard >= STAGE3_SKIP_THRESHOLD)
        stage_start = time.perf_counter()
        stage3_result = await self._stage3_semhash(document)
        _stage3_ms = (time.perf_counter() - stage_start) * 1000
        result.stage_reached = 3

        if not stage3_result:
            # Near duplicate (Jaccard only, no semantic verification)
            result.status = DedupStatus.NEAR_DUPLICATE
            result.duplicate_of = stage2_result.doc_id_2
            result.similarity_score = stage2_result.estimated_similarity
            result.resolution = Resolution.MERGE
            return

        self._metrics.stage3_confirmed += 1
        result.status = DedupStatus.SEMANTIC_DUPLICATE
        result.duplicate_of = stage3_result.doc_id_2
        result.similarity_score = stage3_result.similarity
        result.resolution = Resolution.REVIEW
        logger.debug(
            "Stage 3 semantic dup: doc=%s dup_of=%s cosine=%.3f (%.1fms)",
            document.doc_id, stage3_result.doc_id_2,
            stage3_result.similarity, _stage3_ms,
        )

        # Stage 4: Conflict Detection (optional)
        if not (self._enable_stage4 and result.duplicate_of):
            return
        stage_start = time.perf_counter()
        stage4_result = await self._stage4_conflict(document, result.duplicate_of)
        _stage4_ms = (time.perf_counter() - stage_start) * 1000
        result.stage_reached = 4

        if stage4_result and stage4_result.has_conflict:
            self._metrics.stage4_conflicts += 1
            result.status = DedupStatus.CONTENT_CONFLICT
            result.conflict_types = [
                c.conflict_type for c in stage4_result.conflicts
            ]
            result.resolution = Resolution.REVIEW
            result.details["conflicts"] = [
                c.to_dict() for c in stage4_result.conflicts
            ]
            logger.debug(
                "Stage 4 conflict: doc=%s dup_of=%s conflicts=%d (%.1fms)",
                document.doc_id, result.duplicate_of,
                len(stage4_result.conflicts), _stage4_ms,
            )

    async def check(self, document: Document) -> DedupResult:
        """Check a document for duplicates WITHOUT adding it to the index.

        WARNING: Callers must call add() after check() if they want the document
        to be tracked for future dedup checks.

        Args:
            document: Document to check

        Returns:
            Dedup detection result
        """
        start_time = time.time()
        result = DedupResult(doc_id=document.doc_id)

        if not self._apply_stage1(document, result):
            await self._apply_stage2_and_beyond(document, result)

        # Metrics update
        processing_time = (time.time() - start_time) * 1000
        result.processing_time_ms = processing_time
        self._metrics.total_processed += 1
        self._metrics.total_processing_time_ms += processing_time

        return result

    def _stage1_prefilter(self, document: Document) -> str | None:
        """Stage 1: Metadata Pre-filter.

        Only **content hash** identity counts as an exact duplicate.
        URL/title collisions are intentionally ignored here — documents
        with the same title but different content (e.g. 시행규칙.md vs
        시행규칙(기획재정부령).md, different ministry-era versions of the
        same regulation) must NOT be treated as duplicates.  Stage 2
        (LSH Jaccard) will catch near-duplicates if the bodies are
        actually similar.

        Returns:
            Duplicate document ID or None
        """
        # Content hash check — the only reliable exact-duplicate signal.
        if document.content_hash in self._bloom:
            dup_id = self._content_hash_index.get(document.content_hash)
            if dup_id and dup_id in self._documents:
                return dup_id

        return None

    def _stage2_lshbloom(self, document: Document) -> SimilarPair | None:
        """Stage 2: LSHBloom.

        MinHash LSH for Jaccard similar document detection.

        Returns:
            Similar document pair or None
        """
        similar_pairs = self._lsh.find_similar(document.doc_id, document.content)

        for pair in similar_pairs:
            if pair.estimated_similarity >= self._near_threshold:
                return pair

        return None

    async def _stage3_semhash(self, document: Document) -> SemanticMatch | None:
        """Stage 3: SemHash.

        Embedding-based semantic similar document detection.

        Returns:
            Semantic match or None
        """
        match = await self._sem.check_duplicate(document.doc_id, document.content)

        if match and match.similarity >= self._semantic_threshold:
            return match

        return None

    async def _stage4_conflict(
        self, document: Document, duplicate_id: str
    ) -> ConflictAnalysisResult | None:
        """Stage 4: Conflict Detection.

        LLM-based content conflict analysis.

        Returns:
            Conflict analysis result or None
        """
        duplicate_doc = self._documents.get(duplicate_id)
        if not duplicate_doc:
            return None

        result = await self._conflict.analyze(
            document.doc_id,
            document.content,
            duplicate_id,
            duplicate_doc.content,
        )

        return result

    async def add(self, document: Document) -> DedupResult:
        """Add a document and check for duplicates.

        Args:
            document: Document to add

        Returns:
            Dedup detection result
        """
        result = await self.check(document)

        # Add unique, near-dup, and semantic-dup to index
        # (so a 3rd copy can detect existing near/semantic dups)
        if result.status in (
            DedupStatus.UNIQUE,
            DedupStatus.NEAR_DUPLICATE,
            DedupStatus.SEMANTIC_DUPLICATE,
        ):
            await self._add_to_index(document)

        return result

    async def add_to_semhash(self, document: Document) -> None:
        """Add a document to the Stage 3 SemHash index (backward compatibility)."""
        await self._sem.add(document.doc_id, document.content)

    async def _add_to_index(self, document: Document) -> None:
        """Add document to all indexes (Bloom + LSH + SemHash)."""
        # Bloom Filter
        self._bloom.add(document.title_hash)
        if document.url_hash:
            self._bloom.add(document.url_hash)
        self._bloom.add(document.content_hash)

        # H7 fix: Hash -> doc_id reverse index update
        self._title_hash_index[document.title_hash] = document.doc_id
        if document.url_hash:
            self._url_hash_index[document.url_hash] = document.doc_id
        self._content_hash_index[document.content_hash] = document.doc_id

        # LSHBloom (Stage 2)
        self._lsh.add(document.doc_id, document.content)

        # SemHash (Stage 3)
        await self._sem.add(document.doc_id, document.content)

        # Store document
        self._documents[document.doc_id] = document

    def get_metrics(self) -> PipelineMetrics:
        """Return pipeline metrics."""
        return self._metrics

    def reset_metrics(self) -> None:
        """Reset metrics."""
        self._metrics = PipelineMetrics()

    @property
    def document_count(self) -> int:
        """Number of stored documents."""
        return len(self._documents)

    def clear(self) -> None:
        """Reset all state."""
        self._bloom.clear()
        self._lsh.clear()
        self._sem.clear()
        self._documents.clear()
        self._url_hash_index.clear()
        self._title_hash_index.clear()
        self._content_hash_index.clear()
        self._metrics = PipelineMetrics()
