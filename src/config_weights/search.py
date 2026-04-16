"""Search-related weights and thresholds."""

from __future__ import annotations

from dataclasses import dataclass

from ._helpers import _env_float


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

    # Graph distance weight (0.0 = disabled, 0.15 = recommended)
    graph_distance_weight: float = _env_float("RERANKER_GRAPH_DISTANCE_WEIGHT", 0.15)
    graph_distance_decay: float = 0.3  # Decay factor: 1/(1 + (d-1)*decay)
    graph_hop3_multiplier: float = 0.5  # Boost multiplier for 3+ hops

    # Cross-KB axis boosts
    axis_causal: float = 1.2
    axis_definitional: float = 1.0
    axis_concept: float = 0.9
    axis_temporal: float = 0.9
    axis_process: float = 1.0
    axis_actor: float = 0.8


@dataclass(frozen=True)
class HybridSearchWeights:
    """Qdrant hybrid search vector weights."""

    dense_weight: float = 0.35
    sparse_weight: float = 0.35
    colbert_weight: float = 0.30

    # Prefetch
    prefetch_multiplier: int = 5
    prefetch_max: int = 150

    # ColBERT rerank
    enable_colbert_reranking: bool = True
    colbert_rerank_candidate_multiplier: int = 3
    colbert_max_tokens: int = 128

    # Query type-specific weight overrides (dense, sparse)
    concept_dense_weight: float = 0.45
    concept_sparse_weight: float = 0.25
    procedure_dense_weight: float = 0.25
    procedure_sparse_weight: float = 0.45
    # Date-containing queries: boost sparse for document_name/morphemes matching
    date_query_dense_weight: float = 0.25
    date_query_sparse_weight: float = 0.45


@dataclass(frozen=True)
class SimilarityThresholds:
    """EnhancedSimilarityMatcher thresholds."""

    # L3 decision zones
    auto_match: float = 0.85
    review: float = 0.50

    # Fallback zone thresholds (when cross-encoder unavailable)
    fallback_auto_match: float = 0.90
    fallback_review: float = 0.60

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

    # Exact-match self-exclusion threshold (glossary duplicate detection)
    exact_match_threshold: float = 0.999

    # Term-level matching defaults
    jaccard_threshold: float = 0.7
    levenshtein_threshold: float = 0.8
    token_overlap_threshold: float = 0.7

    # Graceful degradation
    full_pipeline_max_terms: int = 3000
    reduced_ce_max_terms: int = 10000


@dataclass(frozen=True)
class PreprocessorConfig:
    """Query preprocessing parameters."""

    fuzzy_cutoff: float = 0.89
    fuzzy_enabled: bool = True
    fuzzy_min_token_length: int = 4


@dataclass(frozen=True)
class SearchDefaults:
    """Default search parameters."""

    top_k: int = 5
    rerank_pool_multiplier: int = 8
    max_query_length: int = 2000

    keyword_boost_weight: float = 0.3
    crag_block_threshold: float = 0.3

    confidence_display_high: float = 0.8
    confidence_display_medium: float = 0.5

    conflict_overlap_threshold: float = 0.1

    # DenseTermIndex
    term_search_top_k: int = 50
    term_build_batch_size: int = 500

    # Neo4j loader
    neo4j_upsert_batch_size: int = 500
    neo4j_max_retries: int = 3
