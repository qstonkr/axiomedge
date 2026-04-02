"""Centralized weights, thresholds, and tuning parameters.

All search-accuracy-affecting numerical values are defined here.
Change values here instead of hunting through individual files.

Usage:
    from src.config_weights import weights
    threshold = weights.reranker.model_weight
"""

from __future__ import annotations

import os
from dataclasses import dataclass, fields, asdict
from typing import Any


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

    # Graph distance weight (0.0 = disabled, 0.15 = recommended)
    graph_distance_weight: float = _env_float("RERANKER_GRAPH_DISTANCE_WEIGHT", 0.15)

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


# ============================================================================
# Enhanced Similarity Matcher (3-Layer)
# ============================================================================

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
    min_content_length: int = 50
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

    paddle_model: str = "korean_PP-OCRv5_mobile_rec"
    use_gpu: bool = False
    enable_orientation: bool = True
    min_confidence: float = 0.3

    # Scanned PDF detection
    min_text_chars_per_page: int = 30  # Below this → scanned page

    # Image preprocessing
    max_image_dimension: int = 2048

    # CV Pipeline
    cv_max_workers: int = 2

    # Vision analysis: when True, use /analyze endpoint for shape/arrow detection
    enable_vision_analysis: bool = False


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
    graphrag_max_document_length: int = 15000


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

    # File size guard
    max_file_size_mb: int = 200  # 200MB (PPTX/PDF 대용량 허용)

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
# Dedup Pipeline
# ============================================================================

@dataclass(frozen=True)
class DedupConfig:
    """4-Stage dedup pipeline thresholds."""

    near_duplicate_threshold: float = 0.80    # Jaccard (Stage 2)
    semantic_duplicate_threshold: float = 0.90  # Cosine (Stage 3)
    stage3_skip_threshold: float = 0.85        # Jaccard below this -> skip Stage 3
    enable_stage4: bool = True                 # LLM conflict detection
    bloom_expected_items: int = 100000
    bloom_false_positive_rate: float = 0.01


# ============================================================================
# Search Defaults
# ============================================================================

@dataclass(frozen=True)
class SearchDefaults:
    """Default search parameters."""

    top_k: int = 5
    rerank_pool_multiplier: int = 5  # top_k * 5 before reranking (wider pool for keyword accuracy)
    max_query_length: int = 2000

    # DenseTermIndex
    term_search_top_k: int = 50
    term_build_batch_size: int = 500

    # Neo4j loader
    neo4j_upsert_batch_size: int = 500
    neo4j_max_retries: int = 3


# ============================================================================
# Trust Score (KTS)
# ============================================================================

@dataclass(frozen=True)
class TrustScoreWeights:
    """Knowledge Trust Score signal weights.

    Matches oreo-ecosystem KTS_WEIGHTS SSOT:
        KTS = 0.20 * source_credibility
            + 0.20 * freshness
            + 0.25 * user_validation (was 0.20 in v1, raised for local)
            + 0.10 * usage
            + 0.15 * hallucination
            + 0.10 * consistency

    Env-var overridable with KTS_WEIGHT_* prefix.
    """

    source_credibility_weight: float = 0.20
    freshness_weight: float = 0.20
    user_validation_weight: float = 0.25
    usage_weight: float = 0.10
    hallucination_weight: float = 0.15
    consistency_weight: float = 0.10

    # Confidence tier score boundaries
    tier_high: float = 85.0
    tier_medium: float = 70.0
    tier_low: float = 50.0

    # Verification threshold (documents below this KTS need review)
    verification_threshold: float = 50.0


# ============================================================================
# Cache (Multi-Layer)
# ============================================================================

@dataclass(frozen=True)
class CacheConfig:
    """Multi-layer cache parameters."""

    l1_max_entries: int = 10000
    l1_ttl_seconds: int = 300
    l2_similarity_threshold: float = 0.92
    l2_max_entries: int = 50000
    l2_ttl_seconds: int = 3600   # 1 hour default
    enable_semantic_cache: bool = True
    idempotency_ttl_seconds: int = 60

    # Domain-specific similarity thresholds
    threshold_policy: float = 1.0
    threshold_code: float = 0.95
    threshold_kb: float = 0.92
    threshold_general: float = 0.85

    # Domain-specific TTLs (seconds)
    ttl_policy: int = 1800       # 30 min — policies must be fresh
    ttl_code: int = 1800         # 30 min — code changes frequently
    ttl_kb_search: int = 3600    # 1 hour — balanced
    ttl_general: int = 7200      # 2 hours — least critical

    # Auto-generated — do not set manually
    cache_version: str = ""


def _compute_cache_version(cfg: CacheConfig) -> str:
    """Generate cache version hash from config values.

    Any change to thresholds or TTLs auto-invalidates all cached results.
    """
    import hashlib
    import json
    sig = json.dumps({
        "th": [cfg.threshold_policy, cfg.threshold_code, cfg.threshold_kb, cfg.threshold_general],
        "ttl": [cfg.ttl_policy, cfg.ttl_code, cfg.ttl_kb_search, cfg.ttl_general],
        "pipe": "v3",  # Bump manually only for major pipeline changes
    }, sort_keys=True)
    return "v3_" + hashlib.sha256(sig.encode()).hexdigest()[:8]


# ============================================================================
# Aggregated Config
# ============================================================================

class Weights:
    """All weights and thresholds in one place.

    Mutable singleton: supports runtime hot-reload via ``update_from_dict``
    and ``reset``.

    Usage:
        from src.config_weights import weights

        weights.reranker.model_weight      # 0.6
        weights.llm.temperature            # 0.7
        weights.chunking.max_chunk_chars   # 2500
        weights.timeouts.ollama_llm        # 120.0
        weights.search.top_k               # 5
    """

    _SECTION_CLASSES: dict[str, type] = {
        "reranker": RerankerWeights,
        "hybrid_search": HybridSearchWeights,
        "similarity": SimilarityThresholds,
        "preprocessor": PreprocessorConfig,
        "confidence": ConfidenceConfig,
        "response": ResponseConfig,
        "quality": QualityConfig,
        "ocr": OCRConfig,
        "llm": LLMConfig,
        "embedding": EmbeddingConfig,
        "chunking": ChunkingConfig,
        "pipeline": PipelineConfig,
        "timeouts": TimeoutConfig,
        "search": SearchDefaults,
        "trust_score": TrustScoreWeights,
        "dedup": DedupConfig,
        "cache": CacheConfig,
    }

    def __init__(self) -> None:
        self._init_defaults()

    def _init_defaults(self) -> None:
        """Initialize all sections with their default values."""
        for name, cls in self._SECTION_CLASSES.items():
            object.__setattr__(self, name, cls())
        # Auto-compute cache version from config hash
        cache_cfg: CacheConfig = getattr(self, "cache")
        object.__setattr__(cache_cfg, "cache_version", _compute_cache_version(cache_cfg))

    # ----- Serialization -----

    def to_dict(self) -> dict[str, dict[str, Any]]:
        """Serialize all sections to a nested dict."""
        result: dict[str, dict[str, Any]] = {}
        for name in self._SECTION_CLASSES:
            section = getattr(self, name)
            result[name] = asdict(section)
        return result

    # ----- Hot-reload -----

    def update_from_dict(self, overrides: dict[str, Any]) -> dict[str, Any]:
        """Apply partial updates to weight sections.

        ``overrides`` is ``{"section.field": value}`` or ``{"section": {"field": value}}``.
        Returns a dict of applied changes ``{"section.field": {"old": ..., "new": ...}}``.
        """
        applied: dict[str, Any] = {}

        # Normalize: accept both flat "section.field" keys and nested dicts
        normalized: dict[str, dict[str, Any]] = {}
        for key, value in overrides.items():
            if isinstance(value, dict) and key in self._SECTION_CLASSES:
                normalized[key] = value
            elif "." in key:
                section_name, field_name = key.split(".", 1)
                normalized.setdefault(section_name, {})[field_name] = value

        for section_name, field_overrides in normalized.items():
            if section_name not in self._SECTION_CLASSES:
                continue
            cls = self._SECTION_CLASSES[section_name]
            current = getattr(self, section_name)
            current_dict = asdict(current)
            valid_fields = {f.name for f in fields(cls)}

            changes: dict[str, Any] = {}
            for field_name, new_value in field_overrides.items():
                if field_name not in valid_fields:
                    continue
                old_value = current_dict.get(field_name)
                # Coerce type to match the dataclass field type
                expected_type = next(f.type for f in fields(cls) if f.name == field_name)
                try:
                    coerced = _coerce_value(new_value, expected_type)
                except (ValueError, TypeError):
                    continue
                changes[field_name] = coerced
                applied[f"{section_name}.{field_name}"] = {"old": old_value, "new": coerced}

            if changes:
                merged = {**current_dict, **changes}
                object.__setattr__(self, section_name, cls(**merged))

        return applied

    # ----- Reset -----

    def reset(self) -> None:
        """Reset all sections to their default values."""
        self._init_defaults()


def _coerce_value(value: Any, type_hint: str) -> Any:
    """Best-effort type coercion for JSON values to dataclass field types."""
    type_map: dict[str, type] = {
        "float": float,
        "int": int,
        "bool": bool,
        "str": str,
    }
    target = type_map.get(type_hint)
    if target is None:
        return value
    if target is bool and isinstance(value, str):
        return value.lower() in ("true", "1", "yes")
    return target(value)


# Singleton
weights = Weights()
