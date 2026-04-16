"""LLM, embedding, and timeout parameters."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LLMConfig:
    """LLM generation parameters."""

    temperature: float = 0.7
    max_tokens: int = 2048
    context_length: int = 32768
    timeout: float = 120.0

    classify_temperature: float = 0.1
    classify_max_tokens: int = 512

    graphrag_temperature: float = 0.0

    max_query_length: int = 2000
    max_prompt_length: int = 12000
    max_context_per_doc: int = 2000
    max_title_length: int = 200
    max_source_length: int = 200

    graph_normalizer_timeout: float = 120.0


@dataclass(frozen=True)
class EmbeddingConfig:
    """Embedding provider parameters.

    ``dimension`` is the BGE-M3 fixed dimension — SSOT for the entire codebase.
    ``batch_size`` is the encode batch size (forward-pass), not the pipeline batch.
    """

    cache_size: int = 512
    onnx_max_length: int = 512
    dimension: int = 1024
    batch_size: int = 32
    ollama_timeout: float = 60.0


@dataclass(frozen=True)
class TimeoutConfig:
    """All timeout values in one place."""

    qdrant_connection: int = 30
    qdrant_search_ms: int = 5000
    qdrant_clone_batch: int = 120
    qdrant_scroll: float = 8.0
    qdrant_count: int = 5

    neo4j_query: int = 30
    neo4j_batch: int = 5000

    ollama_llm: float = 120.0
    ollama_embedding: float = 60.0

    api_default: int = 30
    api_search: int = 60

    dashboard_api: int = 30
    dashboard_search: int = 60
