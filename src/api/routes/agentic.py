"""Agentic RAG API — POST /api/v1/agentic/ask + GET /api/v1/agentic/traces/{id}.

기존 ``/api/v1/search/hub`` 와 별도 endpoint — 회귀 위험 0.
trace_id 기반 결과 캐시 (Redis 가용 시) — Streamlit Trace viewer 가 후속 조회.
"""

# pyright: reportGeneralTypeIssues=false, reportReturnType=false

from __future__ import annotations

import logging
from dataclasses import asdict, is_dataclass
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.agentic.agent import Agent
from src.agentic.protocols import AgentTrace

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/agentic", tags=["Agentic"])

# In-memory trace cache (process-local). Production: Redis-backed via state["redis_dedup"]
_trace_cache: dict[str, dict[str, Any]] = {}
_TRACE_CACHE_MAX = 200


def _get_state() -> dict:  # noqa: ANN201
    """Late-bound accessor — patches in tests."""
    from src.api.app import _get_state as _gs
    return _gs()


class AgenticAskRequest(BaseModel):
    query: str = Field(..., max_length=2000, description="사용자 질문 (한국어)")
    kb_ids: list[str] | None = Field(default=None, description="검색 대상 KB scope (선택)")


class AgenticAskResponse(BaseModel):
    trace_id: str
    answer: str
    llm_provider: str
    iteration_count: int
    total_steps_executed: int
    total_duration_ms: float
    estimated_cost_usd: float
    confidence: float


def _trace_to_dict(trace: AgentTrace) -> dict[str, Any]:
    """AgentTrace → JSON-serializable dict (Streamlit viz consumes this)."""

    def _serialize(obj: Any) -> Any:
        if is_dataclass(obj) and not isinstance(obj, type):
            return {k: _serialize(v) for k, v in asdict(obj).items()}
        if isinstance(obj, list):
            return [_serialize(x) for x in obj]
        if isinstance(obj, dict):
            return {k: _serialize(v) for k, v in obj.items()}
        if isinstance(obj, set):
            return sorted(_serialize(x) for x in obj)
        return obj

    return _serialize(trace)  # type: ignore[no-any-return]


def _cache_trace(trace_id: str, trace_dict: dict[str, Any]) -> None:
    """LRU-ish in-memory cache — 200개 한도."""
    if len(_trace_cache) >= _TRACE_CACHE_MAX:
        # Remove oldest (first inserted)
        first_key = next(iter(_trace_cache))
        _trace_cache.pop(first_key, None)
    _trace_cache[trace_id] = trace_dict


@router.post("/ask", response_model=AgenticAskResponse)
async def agentic_ask(request: AgenticAskRequest) -> AgenticAskResponse:
    """Agentic RAG — plan → execute tools → reflect → (retry).

    기존 /search/hub 와 별도 — Korean planner + GraphRAG routing + Tiered planning +
    OCR re-search + Edge ↔ HQ LLM routing 5축 차별화 활용.
    """
    state = _get_state()
    if state is None:
        raise HTTPException(status_code=503, detail="App state not initialized")

    try:
        agent = Agent()  # env-driven LLM provider + default tool registry
        trace = await agent.run(request.query, state=dict(state))
    except Exception as e:  # noqa: BLE001 — surface as 500 with detail
        logger.exception("Agentic ask failed")
        raise HTTPException(status_code=500, detail=f"agentic ask failed: {e}") from e

    trace_dict = _trace_to_dict(trace)
    _cache_trace(trace.trace_id, trace_dict)

    last_critique = trace.critiques[-1] if trace.critiques else None
    return AgenticAskResponse(
        trace_id=trace.trace_id,
        answer=trace.final_answer,
        llm_provider=trace.llm_provider,
        iteration_count=trace.iteration_count,
        total_steps_executed=trace.total_steps_executed,
        total_duration_ms=trace.total_duration_ms,
        estimated_cost_usd=trace.tokens.estimated_cost_usd,
        confidence=last_critique.confidence if last_critique else 0.0,
    )


@router.get("/traces/{trace_id}", response_model=dict)
async def get_trace(trace_id: str) -> dict:
    """Streamlit Trace viewer 가 호출 — 단계별 상세 trace 반환."""
    trace = _trace_cache.get(trace_id)
    if trace is None:
        raise HTTPException(status_code=404, detail=f"trace {trace_id} not found or expired")
    return trace


@router.get("/traces", response_model=dict)
async def list_traces(limit: int = 20) -> dict:
    """최근 trace 목록 — Trace viewer 의 history 표시용."""
    items = list(_trace_cache.items())[-limit:][::-1]  # 최신 순
    return {
        "count": len(items),
        "traces": [
            {
                "trace_id": tid,
                "query": t.get("query", ""),
                "answer_preview": (t.get("final_answer") or "")[:120],
                "llm_provider": t.get("llm_provider", ""),
                "iteration_count": len(t.get("iterations") or []),
                "total_duration_ms": t.get("total_duration_ms", 0),
            }
            for tid, t in items
        ],
    }


# Smoke export for tests
def _clear_trace_cache() -> None:
    _trace_cache.clear()
