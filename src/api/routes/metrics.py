"""Simple in-memory metrics endpoint (no external dependency).

Supports both JSON (default) and Prometheus text format via query parameter:
    GET /metrics              → JSON (backward compatible)
    GET /metrics?format=prometheus  → Prometheus text exposition format
"""

from __future__ import annotations

import threading
import time
from typing import Annotated, Any

from fastapi import APIRouter, Query
from fastapi.responses import PlainTextResponse

router = APIRouter(tags=["Metrics"])

_lock = threading.Lock()

_counters: dict[str, int] = {
    "search_requests": 0,
    "search_cache_hits": 0,
    "search_cache_misses": 0,
    "ingest_documents": 0,
    "ingest_chunks": 0,
    "ocr_requests": 0,
    "errors": 0,
}

_start_time: float = time.time()

# Request metrics: keyed by (method, path, status_code)
_request_counts: dict[tuple[str, str, int], int] = {}

# Histogram buckets for request duration
_DURATION_BUCKETS = (0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, float("inf"))
_request_duration_buckets: dict[tuple[str, str, float], int] = {}
_request_duration_sum: dict[tuple[str, str], float] = {}
_request_duration_count: dict[tuple[str, str], int] = {}

# Search duration histogram
_search_duration_buckets: dict[float, int] = dict.fromkeys(_DURATION_BUCKETS, 0)
_search_duration_sum: float = 0.0
_search_duration_count: int = 0

# LLM token usage — keyed by (kb_id, model) to control cardinality
# kb_id is bounded (<100), model is bounded (<10) — total <1000 series
_llm_prompt_tokens: dict[tuple[str, str], int] = {}
_llm_completion_tokens: dict[tuple[str, str], int] = {}
_llm_estimated_cost_usd: dict[tuple[str, str], float] = {}
_llm_request_count: dict[tuple[str, str], int] = {}

# Per-model USD pricing (input, output) per 1K tokens.
# Conservative defaults; override by setting LLM_PRICING_OVERRIDE env (JSON).
_LLM_PRICING_PER_1K: dict[str, tuple[float, float]] = {
    "sagemaker-exaone": (0.0010, 0.0030),
    "exaone3.5": (0.0, 0.0),  # local
    "ollama": (0.0, 0.0),
}

# Cache hit/miss by layer (l1, l2) — bounded cardinality
_cache_hits: dict[str, int] = {}
_cache_misses: dict[str, int] = {}

# Per-KB request status — bounded by KB count (<100) × status set (success|client_error|server_error)
_kb_request_count: dict[tuple[str, str], int] = {}

# RAG pipeline stage duration — keyed by stage name (bounded ~12 stages)
_rag_stage_duration_buckets: dict[tuple[str, float], int] = {}
_rag_stage_duration_sum: dict[str, float] = {}
_rag_stage_duration_count: dict[str, int] = {}

# PR-10 (I) — Ingest metrics. Cardinality 가드:
#   - kb_id: 64자 truncate (KB 수 < 1000)
#   - status: {"success", "failed", "skipped"} (3종)
#   - stage: 6종 bounded (stage1_parse … stage5_index)
#   - error_class: 12종 bounded (KnowledgeBaseError 11 + unknown)
_ingest_documents_total: dict[tuple[str, str], int] = {}  # (kb_id, status)
_ingest_stage_duration_buckets: dict[tuple[str, float], int] = {}
_ingest_stage_duration_sum: dict[str, float] = {}
_ingest_stage_duration_count: dict[str, int] = {}
_ingest_failures_total: dict[tuple[str, str], int] = {}  # (stage, error_class)
_ingest_in_flight: int = 0

# Gauge
_active_connections: int = 0

# Info labels (set once at startup)
_info_labels: dict[str, str] = {}


def inc(name: str, value: int = 1) -> None:
    """Increment a counter metric."""
    with _lock:
        _counters[name] = _counters.get(name, 0) + value


def observe_request(method: str, path: str, status_code: int, duration: float) -> None:
    """Record a request with method, path, status, and duration."""
    # Normalize path to remove IDs (keep route patterns stable)
    normalized = _normalize_path(path)
    key = (method, normalized, status_code)

    with _lock:
        _request_counts[key] = _request_counts.get(key, 0) + 1

        dur_key = (method, normalized)
        _request_duration_sum[dur_key] = _request_duration_sum.get(dur_key, 0.0) + duration
        _request_duration_count[dur_key] = _request_duration_count.get(dur_key, 0) + 1

        for bucket in _DURATION_BUCKETS:
            bkey = (method, normalized, bucket)
            if bkey not in _request_duration_buckets:
                _request_duration_buckets[bkey] = 0
            if duration <= bucket:
                _request_duration_buckets[bkey] += 1


def observe_search_duration(duration: float) -> None:
    """Record a search request duration."""
    global _search_duration_sum, _search_duration_count
    with _lock:
        _search_duration_sum += duration
        _search_duration_count += 1
        for bucket in _DURATION_BUCKETS:
            if duration <= bucket:
                _search_duration_buckets[bucket] += 1


def observe_cache(layer: str, hit: bool) -> None:
    """Record a cache lookup outcome. ``layer`` should be 'l1' or 'l2'."""
    safe_layer = (layer or "unknown")[:16]
    with _lock:
        if hit:
            _cache_hits[safe_layer] = _cache_hits.get(safe_layer, 0) + 1
        else:
            _cache_misses[safe_layer] = _cache_misses.get(safe_layer, 0) + 1


def observe_kb_request(kb_id: str | None, status_code: int) -> None:
    """Record a KB-scoped request outcome (for per-KB error rate dashboards).

    Cardinality control:
      - kb_id truncated to 64 chars
      - status_code bucketed to {success | client_error | server_error}
    """
    safe_kb_id = (kb_id or "_unknown")[:64]
    if status_code < 400:
        bucket = "success"
    elif status_code < 500:
        bucket = "client_error"
    else:
        bucket = "server_error"
    key = (safe_kb_id, bucket)
    with _lock:
        _kb_request_count[key] = _kb_request_count.get(key, 0) + 1


def observe_rag_stage(stage: str, duration_seconds: float) -> None:
    """Record duration of one RAG pipeline stage.

    Stages should be a small bounded set (cache_check, preprocess, expand, classify,
    embed, qdrant_search, cross_encoder_rerank, composite_rerank, graph_expand,
    crag_evaluate, generate_answer). Truncated to 32 chars.
    """
    safe_stage = (stage or "unknown")[:32]
    with _lock:
        _rag_stage_duration_sum[safe_stage] = _rag_stage_duration_sum.get(safe_stage, 0.0) + duration_seconds
        _rag_stage_duration_count[safe_stage] = _rag_stage_duration_count.get(safe_stage, 0) + 1
        for bucket in _DURATION_BUCKETS:
            bkey = (safe_stage, bucket)
            if bkey not in _rag_stage_duration_buckets:
                _rag_stage_duration_buckets[bkey] = 0
            if duration_seconds <= bucket:
                _rag_stage_duration_buckets[bkey] += 1


_INGEST_STATUSES = frozenset({"success", "failed", "skipped"})

# PR-10 + P1-7 — 메트릭 라벨용 stage enum.
#
# Plan §H 의 6-stage 와 ingestion.py 의 ``_stage`` 변수 값(11종) 사이의
# 매핑은 ``INGEST_STAGE_ALIAS`` 가 담당한다. 메트릭/추적 시리즈는 항상 6종
# canonical 만 발생 → cardinality 안정 + dashboard 분산 방지.
_INGEST_STAGES = frozenset({
    "stage1_parse",
    "stage2_embed",
    "stage2_store",
    "stage3_quality",
    "stage4_graph",
    "stage5_index",
    "pipeline",   # except 블록 fallback
    "caller",     # CLI/API 외부 raise
    "unknown",    # 보호용 기본
})

# ingestion.py 의 ``_stage`` 로컬 변수 → canonical 6-stage 매핑.
# Failure rows 와 metric labels 모두 이 alias 를 통해 정규화된다.
INGEST_STAGE_ALIAS: dict[str, str] = {
    # parse
    "init": "stage1_parse",
    "ingestion_gate": "stage1_parse",
    "dedup": "stage1_parse",
    "classify": "stage1_parse",
    "chunk": "stage1_parse",
    # quality
    "quality_check": "stage3_quality",
    # embed
    "embed": "stage2_embed",
    # store
    "store": "stage2_store",
    # graph
    "graph_edges": "stage4_graph",
    "graphrag": "stage4_graph",
    # index/auxiliary
    "tree_index": "stage5_index",
    "summary_tree": "stage5_index",
    "term_extraction": "stage5_index",
    "synonym_discovery": "stage5_index",
}


def normalize_ingest_stage(stage: str | None) -> str:
    """Map raw ingestion.py ``_stage`` to one of ``_INGEST_STAGES``.

    - ``None`` / 빈 문자열 → ``"unknown"``
    - canonical (이미 6-stage) → 그대로
    - alias 적중 → canonical 매핑
    - 그 외 → ``"unknown"`` (cardinality 보호)
    """
    if not stage:
        return "unknown"
    if stage in _INGEST_STAGES:
        return stage
    return INGEST_STAGE_ALIAS.get(stage, "unknown")


def inc_ingest(kb_id: str | None, status: str) -> None:
    """Increment ingest_documents_total counter (PR-10 I)."""
    safe_kb = (kb_id or "_unknown")[:64]
    safe_status = status if status in _INGEST_STATUSES else "unknown"
    with _lock:
        key = (safe_kb, safe_status)
        _ingest_documents_total[key] = _ingest_documents_total.get(key, 0) + 1


def observe_ingest_stage(stage: str, duration_seconds: float) -> None:
    """Record ingest stage duration (PR-10 I + P1-7).

    Stage label is normalized to one of 6 canonical values to prevent
    cardinality drift between ingestion.py raw stage names and metric labels.
    """
    safe_stage = normalize_ingest_stage(stage)
    with _lock:
        _ingest_stage_duration_sum[safe_stage] = (
            _ingest_stage_duration_sum.get(safe_stage, 0.0) + duration_seconds
        )
        _ingest_stage_duration_count[safe_stage] = (
            _ingest_stage_duration_count.get(safe_stage, 0) + 1
        )
        for bucket in _DURATION_BUCKETS:
            bkey = (safe_stage, bucket)
            if bkey not in _ingest_stage_duration_buckets:
                _ingest_stage_duration_buckets[bkey] = 0
            if duration_seconds <= bucket:
                _ingest_stage_duration_buckets[bkey] += 1


def inc_ingest_failure(stage: str, error_class: str) -> None:
    """Increment ingest_failures_total{stage, error_class} (PR-10 I + P1-7)."""
    safe_stage = normalize_ingest_stage(stage)
    safe_error = (error_class or "unknown")[:48]
    with _lock:
        key = (safe_stage, safe_error)
        _ingest_failures_total[key] = _ingest_failures_total.get(key, 0) + 1


def inc_ingest_in_flight() -> None:
    """Increment ingest_in_flight gauge."""
    global _ingest_in_flight
    with _lock:
        _ingest_in_flight += 1


def dec_ingest_in_flight() -> None:
    """Decrement ingest_in_flight gauge."""
    global _ingest_in_flight
    with _lock:
        _ingest_in_flight -= 1


def observe_llm_tokens(
    kb_id: str | None,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
) -> None:
    """Record LLM token usage with USD cost estimate.

    Cardinality: kb_id and model must be bounded sets. Pass "" for kb_id
    when called outside KB context (e.g., classification, GraphRAG extraction).
    Unknown models attribute zero cost — extend ``_LLM_PRICING_PER_1K``.
    """
    safe_kb_id = (kb_id or "_unknown")[:64]
    safe_model = (model or "unknown")[:64]
    key = (safe_kb_id, safe_model)
    pricing = _LLM_PRICING_PER_1K.get(safe_model, (0.0, 0.0))
    cost = (prompt_tokens / 1000.0) * pricing[0] + (completion_tokens / 1000.0) * pricing[1]
    with _lock:
        _llm_prompt_tokens[key] = _llm_prompt_tokens.get(key, 0) + prompt_tokens
        _llm_completion_tokens[key] = _llm_completion_tokens.get(key, 0) + completion_tokens
        _llm_estimated_cost_usd[key] = _llm_estimated_cost_usd.get(key, 0.0) + cost
        _llm_request_count[key] = _llm_request_count.get(key, 0) + 1


def set_info(key: str, value: str) -> None:
    """Set an info label (e.g., embedding_provider)."""
    _info_labels[key] = value


def inc_connections() -> None:
    """Increment active connection gauge."""
    global _active_connections
    with _lock:
        _active_connections += 1


def dec_connections() -> None:
    """Decrement active connection gauge."""
    global _active_connections
    with _lock:
        _active_connections -= 1


def _normalize_path(path: str) -> str:
    """Collapse UUID/ID segments to {id} for stable metric labels."""
    import re
    # Replace UUIDs
    path = re.sub(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        "{id}",
        path,
    )
    # Replace pure numeric segments
    path = re.sub(r"/\d+(?=/|$)", "/{id}", path)
    return path


def _get_snapshot() -> dict[str, Any]:
    """Return a metrics snapshot dict (JSON format, backward compatible)."""
    uptime = time.time() - _start_time
    with _lock:
        return {
            **_counters,
            "uptime_seconds": round(uptime, 1),
            "active_connections": _active_connections,
            "request_counts": {
                f"{m} {p} {s}": c for (m, p, s), c in _request_counts.items()
            },
            "search_duration_seconds_sum": round(_search_duration_sum, 4),
            "search_duration_seconds_count": _search_duration_count,
            "info": _info_labels,
        }


def _render_prometheus() -> str:
    """Render metrics in Prometheus text exposition format."""
    lines: list[str] = []

    uptime = time.time() - _start_time

    with _lock:
        # -- Counters --
        lines.append("# HELP ingest_documents_total Total documents ingested")
        lines.append("# TYPE ingest_documents_total counter")
        lines.append(f"ingest_documents_total {_counters.get('ingest_documents', 0)}")

        lines.append("# HELP ingest_chunks_total Total chunks created")
        lines.append("# TYPE ingest_chunks_total counter")
        lines.append(f"ingest_chunks_total {_counters.get('ingest_chunks', 0)}")

        lines.append("# HELP search_requests_total Total search requests")
        lines.append("# TYPE search_requests_total counter")
        lines.append(f"search_requests_total {_counters.get('search_requests', 0)}")

        lines.append("# HELP search_cache_hits_total Total search cache hits")
        lines.append("# TYPE search_cache_hits_total counter")
        lines.append(f"search_cache_hits_total {_counters.get('search_cache_hits', 0)}")

        lines.append("# HELP search_cache_misses_total Total search cache misses")
        lines.append("# TYPE search_cache_misses_total counter")
        lines.append(f"search_cache_misses_total {_counters.get('search_cache_misses', 0)}")

        lines.append("# HELP ocr_requests_total Total OCR requests")
        lines.append("# TYPE ocr_requests_total counter")
        lines.append(f"ocr_requests_total {_counters.get('ocr_requests', 0)}")

        lines.append("# HELP errors_total Total errors")
        lines.append("# TYPE errors_total counter")
        lines.append(f"errors_total {_counters.get('errors', 0)}")

        # -- Request counts by method/path/status --
        lines.append("# HELP request_count Total HTTP requests by method, path, status")
        lines.append("# TYPE request_count counter")
        for (method, path, status), count in sorted(_request_counts.items()):
            lines.append(
                f'request_count{{method="{method}",path="{path}",status="{status}"}} {count}'
            )

        # -- Request duration histogram --
        lines.append("# HELP request_duration_seconds HTTP request duration in seconds")
        lines.append("# TYPE request_duration_seconds histogram")
        for (method, path), total in sorted(_request_duration_count.items()):
            for bucket in _DURATION_BUCKETS:
                bkey = (method, path, bucket)
                val = _request_duration_buckets.get(bkey, 0)
                le = "+Inf" if bucket == float("inf") else str(bucket)
                lines.append(
                    f'request_duration_seconds_bucket{{method="{method}",path="{path}",le="{le}"}} {val}'
                )
            lines.append(
                f'request_duration_seconds_sum{{method="{method}",path="{path}"}} '
                f"{_request_duration_sum.get((method, path), 0.0):.4f}"
            )
            lines.append(
                f'request_duration_seconds_count{{method="{method}",path="{path}"}} {total}'
            )

        # -- Search duration histogram --
        lines.append("# HELP search_duration_seconds Search request duration in seconds")
        lines.append("# TYPE search_duration_seconds histogram")
        for bucket in _DURATION_BUCKETS:
            le = "+Inf" if bucket == float("inf") else str(bucket)
            lines.append(
                f'search_duration_seconds_bucket{{le="{le}"}} {_search_duration_buckets.get(bucket, 0)}'
            )
        lines.append(f"search_duration_seconds_sum {_search_duration_sum:.4f}")
        lines.append(f"search_duration_seconds_count {_search_duration_count}")

        # -- Cache hit/miss by layer --
        if _cache_hits or _cache_misses:
            lines.append("# HELP cache_hits_total Cache hits by layer")
            lines.append("# TYPE cache_hits_total counter")
            for layer, val in sorted(_cache_hits.items()):
                lines.append(f'cache_hits_total{{layer="{layer}"}} {val}')
            lines.append("# HELP cache_misses_total Cache misses by layer")
            lines.append("# TYPE cache_misses_total counter")
            for layer, val in sorted(_cache_misses.items()):
                lines.append(f'cache_misses_total{{layer="{layer}"}} {val}')

        # -- Per-KB request count by status bucket (success / client_error / server_error) --
        if _kb_request_count:
            lines.append("# HELP kb_request_total Requests by KB and status bucket")
            lines.append("# TYPE kb_request_total counter")
            for (kb_id, bucket), val in sorted(_kb_request_count.items()):
                lines.append(
                    f'kb_request_total{{kb_id="{kb_id}",status="{bucket}"}} {val}'
                )

        # -- RAG stage latency histogram --
        if _rag_stage_duration_count:
            lines.append("# HELP rag_stage_duration_seconds RAG pipeline stage latency")
            lines.append("# TYPE rag_stage_duration_seconds histogram")
            for stage, total in sorted(_rag_stage_duration_count.items()):
                for bucket in _DURATION_BUCKETS:
                    le = "+Inf" if bucket == float("inf") else str(bucket)
                    val = _rag_stage_duration_buckets.get((stage, bucket), 0)
                    lines.append(
                        f'rag_stage_duration_seconds_bucket{{stage="{stage}",le="{le}"}} {val}'
                    )
                lines.append(
                    f'rag_stage_duration_seconds_sum{{stage="{stage}"}} '
                    f"{_rag_stage_duration_sum.get(stage, 0.0):.4f}"
                )
                lines.append(
                    f'rag_stage_duration_seconds_count{{stage="{stage}"}} {total}'
                )

        # -- LLM token usage by (kb_id, model) --
        if _llm_prompt_tokens:
            lines.append("# HELP llm_prompt_tokens_total LLM input tokens consumed")
            lines.append("# TYPE llm_prompt_tokens_total counter")
            for (kb_id, model), val in sorted(_llm_prompt_tokens.items()):
                lines.append(
                    f'llm_prompt_tokens_total{{kb_id="{kb_id}",model="{model}"}} {val}'
                )
            lines.append("# HELP llm_completion_tokens_total LLM output tokens generated")
            lines.append("# TYPE llm_completion_tokens_total counter")
            for (kb_id, model), val in sorted(_llm_completion_tokens.items()):
                lines.append(
                    f'llm_completion_tokens_total{{kb_id="{kb_id}",model="{model}"}} {val}'
                )
            lines.append("# HELP llm_estimated_cost_usd_total Estimated USD cost (rough)")
            lines.append("# TYPE llm_estimated_cost_usd_total counter")
            for (kb_id, model), cost in sorted(_llm_estimated_cost_usd.items()):
                lines.append(
                    f'llm_estimated_cost_usd_total{{kb_id="{kb_id}",model="{model}"}} {cost:.6f}'
                )
            lines.append("# HELP llm_request_count_total LLM generation requests")
            lines.append("# TYPE llm_request_count_total counter")
            for (kb_id, model), n in sorted(_llm_request_count.items()):
                lines.append(
                    f'llm_request_count_total{{kb_id="{kb_id}",model="{model}"}} {n}'
                )

        # -- PR-10 (I) Ingest metrics --
        if _ingest_documents_total:
            lines.append(
                "# HELP ingest_documents_total_v2 "
                "Documents processed by KB + status (success/failed/skipped)"
            )
            lines.append("# TYPE ingest_documents_total_v2 counter")
            for (kb_id, status), val in sorted(_ingest_documents_total.items()):
                lines.append(
                    f'ingest_documents_total_v2{{kb_id="{kb_id}",status="{status}"}} {val}'
                )

        if _ingest_stage_duration_count:
            lines.append(
                "# HELP ingest_duration_seconds Ingestion stage latency"
            )
            lines.append("# TYPE ingest_duration_seconds histogram")
            for stage, total in sorted(_ingest_stage_duration_count.items()):
                for bucket in _DURATION_BUCKETS:
                    le = "+Inf" if bucket == float("inf") else str(bucket)
                    val = _ingest_stage_duration_buckets.get((stage, bucket), 0)
                    lines.append(
                        f'ingest_duration_seconds_bucket{{stage="{stage}",le="{le}"}} {val}'
                    )
                lines.append(
                    f'ingest_duration_seconds_sum{{stage="{stage}"}} '
                    f"{_ingest_stage_duration_sum.get(stage, 0.0):.4f}"
                )
                lines.append(
                    f'ingest_duration_seconds_count{{stage="{stage}"}} {total}'
                )

        if _ingest_failures_total:
            lines.append(
                "# HELP ingest_failures_total Ingestion failures by stage + error class"
            )
            lines.append("# TYPE ingest_failures_total counter")
            for (stage, err), val in sorted(_ingest_failures_total.items()):
                lines.append(
                    f'ingest_failures_total{{stage="{stage}",error_class="{err}"}} {val}'
                )

        lines.append("# HELP ingest_in_flight Currently in-flight ingest tasks")
        lines.append("# TYPE ingest_in_flight gauge")
        lines.append(f"ingest_in_flight {_ingest_in_flight}")

        # -- Gauges --
        lines.append("# HELP active_connections Current active connections")
        lines.append("# TYPE active_connections gauge")
        lines.append(f"active_connections {_active_connections}")

        lines.append("# HELP uptime_seconds API server uptime in seconds")
        lines.append("# TYPE uptime_seconds gauge")
        lines.append(f"uptime_seconds {uptime:.1f}")

        # -- Info --
        if _info_labels:
            lines.append("# HELP knowledge_api_info API metadata")
            lines.append("# TYPE knowledge_api_info gauge")
            label_str = ",".join(f'{k}="{v}"' for k, v in sorted(_info_labels.items()))
            lines.append(f"knowledge_api_info{{{label_str}}} 1")

    lines.append("")
    return "\n".join(lines)


@router.get("/metrics")
async def metrics(format: Annotated[str, Query(alias="format")] = "json") -> Any:
    """Return current metrics snapshot.

    Query parameters:
        format: "json" (default) or "prometheus"
    """
    if format == "prometheus":
        return PlainTextResponse(
            _render_prometheus(),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )
    return _get_snapshot()
