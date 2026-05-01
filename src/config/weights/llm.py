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

    # Qdrant
    qdrant_connection: int = 30
    qdrant_search_ms: int = 5000
    qdrant_clone_batch: int = 120
    qdrant_scroll: float = 8.0
    qdrant_count: int = 5

    # Neo4j
    neo4j_query: int = 30
    neo4j_batch: int = 5000

    # Ollama
    # ollama_llm: tree expansion 이 정상 작동하면 LLM context 가 커져 (~34 chunks)
    # local exaone3.5:7.8b prefill 이 길어진다. BFF undici headersTimeout(300s) 안에
    # 안전하게 들어가도록 240s 로 잡는다 — 이를 넘기면 chunk capping 또는 streaming
    # 으로 대응해야 한다.
    ollama_llm: float = 240.0
    ollama_embedding: float = 60.0

    # API / Dashboard
    api_default: int = 30
    api_search: int = 60
    dashboard_api: int = 30
    dashboard_search: int = 60

    # HTTP client defaults (src/ 내부 httpx 호출용)
    httpx_default: float = 30.0
    httpx_ocr: float = 60.0
    httpx_reranker: float = 60.0
    httpx_sagemaker_read: int = 180
    httpx_sagemaker_connect: int = 30

    # Route-level httpx (KB scroll, search helpers, quality, distill)
    httpx_kb_scroll: float = 10.0
    httpx_search_scroll: float = 8.0
    httpx_quality: float = 30.0
    httpx_distill_teacher: float = 60.0
    httpx_rag: float = 30.0
    httpx_confluence: float = 5.0

    # Subprocess (quantize, convert, OCR CLI)
    subprocess_convert: int = 600
    subprocess_quantize: int = 600
    subprocess_validate: int = 300
    subprocess_ocr_cli: int = 120
