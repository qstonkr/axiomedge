"""API client for Agentic RAG endpoints — used by Streamlit Trace viewer."""

from __future__ import annotations

from typing import Any

from services.api._core import _get, _post, logger


def agentic_ask(query: str, kb_ids: list[str] | None = None) -> dict[str, Any]:
    """POST /api/v1/agentic/ask — agent loop 실행 + summary 반환."""
    body: dict[str, Any] = {"query": query}
    if kb_ids:
        body["kb_ids"] = kb_ids
    logger.info("agentic ask: %s (kb=%s)", query[:60], kb_ids)
    return _post("/api/v1/agentic/ask", json_body=body, timeout=120)


def get_agent_trace(trace_id: str) -> dict[str, Any]:
    """GET /api/v1/agentic/traces/{id} — 상세 trace 조회."""
    return _get(f"/api/v1/agentic/traces/{trace_id}")


def list_agent_traces(limit: int = 20) -> dict[str, Any]:
    """GET /api/v1/agentic/traces — 최근 trace 목록."""
    return _get("/api/v1/agentic/traces", params={"limit": limit})
