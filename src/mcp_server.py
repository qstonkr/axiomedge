"""MCP Server - Knowledge Hub를 MCP tool로 래핑.

FastAPI API(:8000)에 HTTP 요청을 보내는 thin wrapper.
Usage:
    uv run python -m src.mcp_server          # stdio (default)
    uv run python -m src.mcp_server --sse    # SSE transport
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)

KH_API_BASE = os.getenv("KH_API_BASE", "http://localhost:8000")
KH_API_KEY = os.getenv("KH_API_KEY", "")
KH_API_TIMEOUT = float(os.getenv("KH_API_TIMEOUT", "60"))

MCP_PORT = int(os.getenv("MCP_PORT", "5010"))

mcp = FastMCP(
    "knowledge-hub",
    instructions=(
        "Knowledge Hub - 사내 지식 검색 및 질의응답 시스템. "
        "문서 검색(search), RAG 질의응답(ask), 담당자/전문가 조회(find_expert) 기능 제공."
    ),
    host="0.0.0.0",
    port=MCP_PORT,
)


def _http_client() -> httpx.AsyncClient:
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if KH_API_KEY:
        headers["Authorization"] = f"Bearer {KH_API_KEY}"
    return httpx.AsyncClient(
        base_url=KH_API_BASE,
        headers=headers,
        timeout=KH_API_TIMEOUT,
    )


# ---------------------------------------------------------------------------
# Tool 1: search
# ---------------------------------------------------------------------------
@mcp.tool()
async def search(
    query: str,
    kb_ids: list[str] | None = None,
    top_k: int = 5,
) -> dict[str, Any]:
    """지식 베이스에서 문서를 검색합니다.

    하이브리드 검색(Dense + Sparse + ColBERT)과 Cross-Encoder 리랭킹을 수행합니다.
    검색 결과와 함께 LLM이 생성한 답변도 반환합니다.

    Args:
        query: 검색 질의 (예: "VPN 접속 절차", "서버 장애 대응")
        kb_ids: 검색할 지식 베이스 ID 목록. None이면 전체 검색.
        top_k: 반환할 최대 청크 수 (1-50, 기본 5)
    """
    payload: dict[str, Any] = {
        "query": query,
        "top_k": top_k,
        "include_answer": True,
    }
    if kb_ids:
        payload["kb_ids"] = kb_ids

    async with _http_client() as client:
        resp = await client.post("/api/v1/search/hub", json=payload)
        resp.raise_for_status()
        data = resp.json()

    return {
        "query": data.get("query", query),
        "answer": data.get("answer"),
        "chunks": [
            {
                "content": c.get("content", ""),
                "score": c.get("score"),
                "document_name": c.get("document_name", ""),
                "kb_id": c.get("kb_id", ""),
            }
            for c in data.get("chunks", [])
        ],
        "total_chunks": data.get("total_chunks", 0),
        "query_type": data.get("query_type", ""),
        "confidence": data.get("confidence", ""),
        "search_time_ms": data.get("search_time_ms", 0),
    }


# ---------------------------------------------------------------------------
# Tool 2: ask
# ---------------------------------------------------------------------------
@mcp.tool()
async def ask(
    query: str,
    kb_ids: list[str] | None = None,
) -> dict[str, Any]:
    """지식 베이스에 질문하고 RAG 기반 답변을 받습니다.

    질의 분류(OWNER/PROCEDURE/TROUBLESHOOT/CONCEPT/GENERAL) 후
    적절한 파이프라인으로 라우팅하여 답변을 생성합니다.
    답변에는 출처(sources)와 신뢰도(confidence)가 포함됩니다.

    Args:
        query: 질문 (예: "VPN 접속이 안 될 때 어떻게 하나요?")
        kb_ids: 질의할 지식 베이스 ID 목록. None이면 전체.
    """
    payload: dict[str, Any] = {"query": query}
    if kb_ids:
        payload["kb_ids"] = kb_ids

    async with _http_client() as client:
        resp = await client.post("/api/v1/knowledge/ask", json=payload)
        resp.raise_for_status()
        data = resp.json()

    return {
        "query": data.get("query", query),
        "answer": data.get("answer"),
        "sources": data.get("sources", []),
        "query_type": data.get("query_type", ""),
        "confidence": data.get("confidence", 0),
    }


# ---------------------------------------------------------------------------
# Tool 3: find_expert
# ---------------------------------------------------------------------------
@mcp.tool()
async def find_expert(
    topic: str,
    limit: int = 10,
) -> dict[str, Any]:
    """특정 주제에 대한 담당자/전문가를 Neo4j 그래프에서 조회합니다.

    문서 소유자, GraphRAG 엔티티 연결, 직접 연결 3가지 경로로 검색합니다.

    Args:
        topic: 전문가를 찾을 주제 (예: "네트워크 장비", "SAP 시스템")
        limit: 최대 반환 인원 (1-50, 기본 10)
    """
    async with _http_client() as client:
        resp = await client.get(
            "/api/v1/admin/graph/experts",
            params={"topic": topic, "limit": limit},
        )
        resp.raise_for_status()
        data = resp.json()

    return {
        "topic": data.get("topic", topic),
        "experts": data.get("experts", []),
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys

    transport = "sse" if "--sse" in sys.argv else "stdio"
    mcp.run(transport=transport)
