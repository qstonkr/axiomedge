"""Centralized weights, thresholds, and tuning parameters.

All search-accuracy-affecting numerical values are defined here.
Change values here instead of hunting through individual files.

Usage:
    from src.config_weights import weights
    threshold = weights.reranker.model_weight
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env_float(key: str, default: float) -> float:
    """Read float from env var with fallback."""
    raw = os.getenv(key, "")
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    return default


def _env_int(key: str, default: int) -> int:
    raw = os.getenv(key, "")
    if raw:
        try:
            return int(raw)
        except ValueError:
            pass
    return default


# ============================================================================
# Composite Reranker
# ============================================================================

@dataclass(frozen=True)
class RerankerWeights:
    """CompositeReranker score fusion weights."""

    model_weight: float = 0.6
    base_weight: float = 0.3
    source_weight: float = 0.1
    position_weight: float = 0.0
    faq_boost: float = 1.2
    mmr_lambda: float = 0.7

    # Source type priors
    source_qdrant: float = 1.0
    source_web: float = 0.95
    source_graph: float = 0.98
    source_cross_kb_graph: float = 1.0
    source_faq: float = 1.0

    # Cross-KB axis boosts
    axis_causal: float = 1.2
    axis_definitional: float = 1.0
    axis_concept: float = 0.9
    axis_temporal: float = 0.9
    axis_process: float = 1.0
    axis_actor: float = 0.8


# ============================================================================
# Hybrid Search (Qdrant RRF)
# ============================================================================

@dataclass(frozen=True)
class HybridSearchWeights:
    """Qdrant hybrid search vector weights."""

    dense_weight: float = 0.4
    sparse_weight: float = 0.3
    colbert_weight: float = 0.3

    # Prefetch
    prefetch_multiplier: int = 3
    prefetch_max: int = 100

    # ColBERT rerank
    colbert_rerank_candidate_multiplier: int = 3
    colbert_max_tokens: int = 128


# ============================================================================
# Enhanced Similarity Matcher (3-Layer)
# ============================================================================

@dataclass(frozen=True)
class SimilarityThresholds:
    """EnhancedSimilarityMatcher thresholds."""

    # L3 decision zones
    auto_match: float = 0.85
    review: float = 0.50

    # L2 RRF fusion weights
    rrf_edit_weight: float = 0.25
    rrf_sparse_weight: float = 0.25
    rrf_dense_weight: float = 0.50
    rrf_k: int = 60

    # L2 RapidFuzz
    rapidfuzz_score_cutoff: int = 50
    rapidfuzz_length_ratio_min: float = 0.5
    rapidfuzz_length_ratio_floor: float = 0.1

    # L2 n-gram
    ngram_size: int = 3
    min_shared_ngrams: int = 1
    max_candidates: int = 500

    # Graceful degradation
    full_pipeline_max_terms: int = 3000
    reduced_ce_max_terms: int = 10000


# ============================================================================
# Query Preprocessor
# ============================================================================

@dataclass(frozen=True)
class PreprocessorConfig:
    """Query preprocessing parameters."""

    fuzzy_cutoff: float = 0.89
    fuzzy_enabled: bool = True
    fuzzy_min_token_length: int = 4


# ============================================================================
# Confidence Thresholds
# ============================================================================

@dataclass(frozen=True)
class ConfidenceConfig:
    """Confidence scoring thresholds."""

    high: float = 0.85
    medium: float = 0.70
    low: float = 0.50

    # Retrieval
    retrieval_correct: float = 0.70
    retrieval_ambiguous: float = 0.40

    # CRAG
    crag_correct: float = 0.60
    crag_weakness: float = 0.50

    # Response thresholds by query type
    factual_min: float = 0.70
    analytical_min: float = 0.70
    advisory_min: float = 0.50
    multi_hop_min: float = 0.75

    # Quality gate
    quality_gate_faithfulness: float = 0.70
    quality_gate_context_relevancy: float = 0.65
    quality_gate_answer_relevancy: float = 0.70


# ============================================================================
# Tiered Response
# ============================================================================

@dataclass(frozen=True)
class ResponseConfig:
    """Tiered response generation parameters."""

    # RRF scores are typically 0.2-0.6, so use low threshold
    factual_relevance_threshold: float = 0.3
    analytical_relevance_threshold: float = 0.5
    advisory_relevance_threshold: float = 0.5
    default_relevance_threshold: float = 0.5


# ============================================================================
# Quality Processor (Ingestion)
# ============================================================================

@dataclass(frozen=True)
class QualityConfig:
    """Document quality tier thresholds."""

    gold_min_chars: int = 2000
    gold_structured_min_chars: int = 1000
    silver_min_chars: int = 500
    silver_structured_min_chars: int = 200
    bronze_min_chars: int = 50
    noise_max_chars: int = 50

    # Freshness
    stale_threshold_days: int = 730
    stale_weight: float = 0.7
    fresh_boost: float = 1.2
    stale_penalty: float = 0.8
    outdated_penalty: float = 0.5

    # Freshness boundaries (days)
    fresh_max_days: int = 90
    stale_max_days: int = 365


# ============================================================================
# OCR
# ============================================================================

@dataclass(frozen=True)
class OCRConfig:
    """OCR processing parameters."""

    paddle_model: str = "korean_PP-OCRv5_server_rec"
    use_gpu: bool = False
    enable_orientation: bool = True
    min_confidence: float = 0.3

    # Scanned PDF detection
    min_text_chars_per_page: int = 30  # Below this → scanned page

    # Image preprocessing
    max_image_dimension: int = 2048

    # CV Pipeline
    cv_max_workers: int = 2


# ============================================================================
# LLM (EXAONE via Ollama)
# ============================================================================

@dataclass(frozen=True)
class LLMConfig:
    """LLM generation parameters."""

    # RAG response
    temperature: float = 0.7
    max_tokens: int = 2048
    context_length: int = 32768
    timeout: float = 120.0

    # Classification / extraction (low creativity)
    classify_temperature: float = 0.1
    classify_max_tokens: int = 512

    # GraphRAG entity extraction (deterministic)
    graphrag_temperature: float = 0.0

    # Input sanitization
    max_query_length: int = 2000
    max_prompt_length: int = 12000
    max_context_per_doc: int = 2000
    max_title_length: int = 200
    max_source_length: int = 200

    # Graph normalizer
    graph_normalizer_timeout: float = 120.0


# ============================================================================
# Embedding
# ============================================================================

@dataclass(frozen=True)
class EmbeddingConfig:
    """Embedding provider parameters."""

    cache_size: int = 512
    onnx_max_length: int = 512
    dimension: int = 1024
    batch_size: int = 32
    ollama_timeout: float = 60.0


# ============================================================================
# Chunking
# ============================================================================

@dataclass(frozen=True)
class ChunkingConfig:
    """Text chunking parameters."""

    max_chunk_chars: int = 2500
    overlap_sentences: int = 1
    max_chunks_per_document: int = 500
    graphrag_max_document_length: int = 3000


# ============================================================================
# Pipeline
# ============================================================================

@dataclass(frozen=True)
class PipelineConfig:
    """Ingestion pipeline parameters."""

    max_chunk_chars: int = 2500
    chunk_overlap_sentences: int = 1
    max_chunks_per_document: int = 500
    max_payload_content_length: int = 8000

    # Batch sizes
    embedding_batch_size: int = 32
    qdrant_upsert_batch_size: int = 64
    neo4j_batch_size: int = 5000

    # Workers
    max_workers: int = 4


# ============================================================================
# Timeouts (seconds)
# ============================================================================

@dataclass(frozen=True)
class TimeoutConfig:
    """All timeout values in one place."""

    qdrant_connection: int = 30
    qdrant_search_ms: int = 5000
    qdrant_clone_batch: int = 120
    qdrant_scroll: float = 8.0
    qdrant_count: int = 5

    neo4j_query: int = 30
    neo4j_batch: int = 5000  # batch_size, not timeout

    ollama_llm: float = 120.0
    ollama_embedding: float = 60.0

    api_default: int = 30
    api_search: int = 60

    dashboard_api: int = 30
    dashboard_search: int = 60


# ============================================================================
# Search Defaults
# ============================================================================

@dataclass(frozen=True)
class SearchDefaults:
    """Default search parameters."""

    top_k: int = 5
    rerank_pool_multiplier: int = 3  # top_k * 3 before reranking
    max_query_length: int = 2000

    # DenseTermIndex
    term_search_top_k: int = 50
    term_build_batch_size: int = 500

    # Neo4j loader
    neo4j_upsert_batch_size: int = 500
    neo4j_max_retries: int = 3


# ============================================================================
# Aggregated Config
# ============================================================================

@dataclass(frozen=True)
class Weights:
    """All weights and thresholds in one place.

    Usage:
        from src.config_weights import weights

        weights.reranker.model_weight      # 0.6
        weights.llm.temperature            # 0.7
        weights.chunking.max_chunk_chars   # 2500
        weights.timeouts.ollama_llm        # 120.0
        weights.search.top_k               # 5
    """

    reranker: RerankerWeights = field(default_factory=RerankerWeights)
    hybrid_search: HybridSearchWeights = field(default_factory=HybridSearchWeights)
    similarity: SimilarityThresholds = field(default_factory=SimilarityThresholds)
    preprocessor: PreprocessorConfig = field(default_factory=PreprocessorConfig)
    confidence: ConfidenceConfig = field(default_factory=ConfidenceConfig)
    response: ResponseConfig = field(default_factory=ResponseConfig)
    quality: QualityConfig = field(default_factory=QualityConfig)
    ocr: OCRConfig = field(default_factory=OCRConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    embedding: EmbeddingConfig = field(default_factory=EmbeddingConfig)
    chunking: ChunkingConfig = field(default_factory=ChunkingConfig)
    pipeline: PipelineConfig = field(default_factory=PipelineConfig)
    timeouts: TimeoutConfig = field(default_factory=TimeoutConfig)
    search: SearchDefaults = field(default_factory=SearchDefaults)


# Singleton
weights = Weights()
