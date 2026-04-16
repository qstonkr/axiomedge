"""Deduplication Infrastructure - 4-Stage Pipeline.

Stage 1: Metadata Pre-filter (<1ms)
    -> Bloom Filter, URL/title hash, 30-40% filtering

Stage 2: LSH (<10ms)
    -> MinHash LSH, Jaccard similarity, 10-15% flagged

Stage 3: SemHash (~50ms)
    -> Embedding-based, Cosine >= 0.90, 5-8% confirmed

Stage 4: Conflict Detection (~100ms)
    -> LLM conflict analysis (local Ollama), date/version/policy conflicts

Adapted from oreo-ecosystem infrastructure/dedup/ for local deployment.
"""

from .bloom_filter import BloomFilter, ScalableBloomFilter
from .lshbloom import LSHBloom, MinHasher, MinHashSignature, SimilarPair
from .semhash import SemHash, SemanticMatch, DocumentEmbedding
from .conflict_detector import (
    ConflictDetector,
    ConflictType,
    ConflictSeverity,
    ConflictDetail,
    ConflictAnalysisResult,
)
from .dedup_pipeline import (
    DedupPipeline,
    DedupResult,
    DedupStatus,
    Resolution,
    Document,
    PipelineMetrics,
)
from .result_tracker import DedupResultTracker
from .redis_index import RedisDedupIndex

__all__ = [
    # Pipeline
    "DedupPipeline",
    "DedupResult",
    "DedupStatus",
    "Resolution",
    "Document",
    "PipelineMetrics",
    # Stage 1
    "BloomFilter",
    "ScalableBloomFilter",
    # Stage 2
    "LSHBloom",
    "MinHasher",
    "MinHashSignature",
    "SimilarPair",
    # Stage 3
    "SemHash",
    "SemanticMatch",
    "DocumentEmbedding",
    # Stage 4
    "ConflictDetector",
    "ConflictType",
    "ConflictSeverity",
    "ConflictDetail",
    "ConflictAnalysisResult",
    # Tracking
    "DedupResultTracker",
    "RedisDedupIndex",
]
