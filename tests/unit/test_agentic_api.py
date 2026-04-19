"""Tests for Agentic API endpoint + trace cache + serialization."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.agentic.protocols import (
    AgentStep,
    AgentTrace,
    Critique,
    Plan,
    TokenUsage,
    ToolResult,
)
from src.api.routes import agentic as agentic_route

# B-0 RBAC bypass — agentic 엔드포인트가 Depends(get_current_user/org) 를 쓰므로
# fake user/org 주입. 단독 실행은 통과하지만 풀 스위트에서 fixture pollution
# (다른 test 가 ASGITransport / state mock 을 leak) 으로 401 발생.
pytestmark = pytest.mark.usefixtures("bypass_route_auth")


# =============================================================================
# Trace serialization
# =============================================================================


def test_trace_to_dict_serializes_dataclasses() -> None:
    plan = Plan(query="q", sub_queries=["q1"], steps=[], estimated_complexity=2)
    trace = AgentTrace(
        trace_id="t1", query="q", plan=plan,
        iterations=[[
            AgentStep(
                step_id="s1", plan_index=0, tool="x", args={"a": 1},
                rationale="r", result=ToolResult(success=True, data="hello"),
                duration_ms=12.3,
            ),
        ]],
        critiques=[Critique(is_sufficient=True, confidence=0.9, next_action="answer")],
        final_answer="hello",
        tokens=TokenUsage(prompt_tokens=10, completion_tokens=5, estimated_cost_usd=0.001),
        llm_provider="fake",
    )
    d = agentic_route._trace_to_dict(trace)
    assert d["trace_id"] == "t1"
    assert d["plan"]["estimated_complexity"] == 2
    assert d["iterations"][0][0]["tool"] == "x"
    assert d["iterations"][0][0]["result"]["data"] == "hello"
    assert d["tokens"]["estimated_cost_usd"] == 0.001


def test_trace_to_dict_handles_set_data() -> None:
    """Tool results sometimes contain set (e.g., source URIs from graph_query)."""
    plan = Plan(query="q", sub_queries=[], steps=[], estimated_complexity=1)
    trace = AgentTrace(
        trace_id="t2", query="q", plan=plan,
        iterations=[[
            AgentStep(
                step_id="s1", plan_index=0, tool="graph", args={},
                rationale="", result=ToolResult(success=True, data={"a", "b", "c"}),
            ),
        ]],
        critiques=[],
        llm_provider="fake",
    )
    d = agentic_route._trace_to_dict(trace)
    # Set is sorted into list
    assert isinstance(d["iterations"][0][0]["result"]["data"], list)
    assert sorted(d["iterations"][0][0]["result"]["data"]) == ["a", "b", "c"]


# =============================================================================
# Trace cache
# =============================================================================


def test_cache_trace_lru_eviction() -> None:
    agentic_route._clear_trace_cache()
    # Set max=3 for test
    original_max = agentic_route._TRACE_CACHE_MAX
    try:
        agentic_route._TRACE_CACHE_MAX = 3
        for i in range(5):
            agentic_route._cache_trace(f"t{i}", {"query": f"q{i}"})
        # Only last 3 retained: t2, t3, t4
        assert "t0" not in agentic_route._trace_cache
        assert "t1" not in agentic_route._trace_cache
        assert "t2" in agentic_route._trace_cache
        assert "t4" in agentic_route._trace_cache
    finally:
        agentic_route._TRACE_CACHE_MAX = original_max
        agentic_route._clear_trace_cache()


# =============================================================================
# API endpoints (FastAPI TestClient)
# =============================================================================


@pytest.fixture
def app() -> FastAPI:
    agentic_route._clear_trace_cache()
    application = FastAPI()
    application.include_router(agentic_route.router)
    return application


def _patch_get_state() -> Any:
    return patch("src.api.routes.agentic._get_state", return_value={})


def _patch_agent_run(trace: AgentTrace) -> Any:
    mock_agent = MagicMock()
    mock_agent.run = AsyncMock(return_value=trace)
    return patch("src.api.routes.agentic.Agent", return_value=mock_agent)


def _make_trace(trace_id: str = "test-trace") -> AgentTrace:
    plan = Plan(query="q", sub_queries=[], steps=[], estimated_complexity=2)
    return AgentTrace(
        trace_id=trace_id, query="q", plan=plan,
        iterations=[[
            AgentStep(
                step_id="s1", plan_index=0, tool="qdrant_search",
                args={}, rationale="", result=ToolResult(success=True, data=[]),
            ),
        ]],
        critiques=[Critique(is_sufficient=True, confidence=0.85, next_action="answer")],
        final_answer="test answer",
        total_duration_ms=120.5,
        tokens=TokenUsage(prompt_tokens=50, completion_tokens=20, estimated_cost_usd=0.0007),
        llm_provider="ollama",
    )


def test_post_ask_returns_response(app: FastAPI) -> None:
    trace = _make_trace("trace-1")
    with _patch_get_state(), _patch_agent_run(trace):
        client = TestClient(app)
        resp = client.post("/api/v1/agentic/ask", json={"query": "test"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["trace_id"] == "trace-1"
        assert body["answer"] == "test answer"
        assert body["llm_provider"] == "ollama"
        assert body["confidence"] == 0.85
        assert body["estimated_cost_usd"] == 0.0007


def test_post_ask_caches_trace_for_later_lookup(app: FastAPI) -> None:
    trace = _make_trace("trace-cache-test")
    with _patch_get_state(), _patch_agent_run(trace):
        client = TestClient(app)
        client.post("/api/v1/agentic/ask", json={"query": "test"})
        resp = client.get("/api/v1/agentic/traces/trace-cache-test")
        assert resp.status_code == 200
        body = resp.json()
        assert body["trace_id"] == "trace-cache-test"
        assert body["final_answer"] == "test answer"


def test_get_trace_404_when_not_found(app: FastAPI) -> None:
    client = TestClient(app)
    resp = client.get("/api/v1/agentic/traces/nonexistent")
    assert resp.status_code == 404


def test_post_ask_state_unavailable_returns_503(app: FastAPI) -> None:
    with patch("src.api.routes.agentic._get_state", return_value=None):
        client = TestClient(app)
        resp = client.post("/api/v1/agentic/ask", json={"query": "x"})
        assert resp.status_code == 503


def test_post_ask_agent_failure_returns_500(app: FastAPI) -> None:
    failing_agent = MagicMock()
    failing_agent.run = AsyncMock(side_effect=RuntimeError("agent failed"))
    with _patch_get_state(), patch(
        "src.api.routes.agentic.Agent", return_value=failing_agent,
    ):
        client = TestClient(app)
        resp = client.post("/api/v1/agentic/ask", json={"query": "x"})
        assert resp.status_code == 500
        assert "agent failed" in resp.json()["detail"]


def test_list_traces_empty(app: FastAPI) -> None:
    client = TestClient(app)
    resp = client.get("/api/v1/agentic/traces?limit=10")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 0
    assert body["traces"] == []


def test_list_traces_returns_recent(app: FastAPI) -> None:
    # Manually populate cache
    for i in range(3):
        agentic_route._cache_trace(f"t{i}", {
            "query": f"q{i}", "final_answer": f"ans{i}",
            "llm_provider": "ollama", "iterations": [[]],
            "total_duration_ms": 100.0 * i,
        })
    client = TestClient(app)
    resp = client.get("/api/v1/agentic/traces?limit=20")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 3
    # 최신 순 (t2 first)
    assert body["traces"][0]["trace_id"] == "t2"
    assert body["traces"][2]["trace_id"] == "t0"


def test_post_ask_validates_query_length(app: FastAPI) -> None:
    client = TestClient(app)
    resp = client.post("/api/v1/agentic/ask", json={"query": "x" * 5000})
    assert resp.status_code == 422  # Pydantic validation
