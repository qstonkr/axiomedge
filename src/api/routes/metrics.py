"""Simple in-memory metrics endpoint (no external dependency)."""

from __future__ import annotations

import time

from fastapi import APIRouter

router = APIRouter(tags=["Metrics"])

_metrics: dict[str, int | float] = {
    "search_requests": 0,
    "search_cache_hits": 0,
    "search_cache_misses": 0,
    "ingest_documents": 0,
    "ingest_chunks": 0,
    "ocr_requests": 0,
    "errors": 0,
    "start_time": time.time(),
}


def inc(name: str, value: int = 1) -> None:
    """Increment a counter metric."""
    _metrics[name] = _metrics.get(name, 0) + value


@router.get("/metrics")
async def metrics():
    """Return current metrics snapshot."""
    uptime = time.time() - _metrics["start_time"]
    return {**_metrics, "uptime_seconds": round(uptime, 1)}
