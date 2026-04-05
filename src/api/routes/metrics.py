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
async def metrics(format: Annotated[str, Query(alias="format")] = "json"):
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
