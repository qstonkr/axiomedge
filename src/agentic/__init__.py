"""Agentic RAG — multi-step reasoning agent over axiomedge's Korean GraphRAG.

Differentiation 5축 (시중 vector-only Agentic RAG 와의 차별점):
1. Korean NLP — KiwiPy 기반 sub-query decomposition
2. GraphRAG routing — Plan 단계에서 vector vs graph 선택
3. Edge ↔ HQ LLM — 복잡도 기반 routing (sub-second edge / multi-step HQ)
4. CRAG + Tiered — confidence 기반 retry + query-type 별 plan 깊이
5. OCR aware — confidence 낮으면 re-OCR with different settings

LLM provider 일원화:
  단일 ``LLM_PROVIDER`` env (sagemaker | ollama | openai | anthropic | edge | ...).
  AWS 제거 시 env 한 줄로 swap — 코드 무수정.

Public API:
  from src.agentic import Agent, AgentTrace, run_agent
"""

from src.agentic.protocols import (
    AgentLLM,
    AgentStep,
    AgentTrace,
    Critique,
    Plan,
    Tool,
    ToolResult,
    ToolSpec,
)

__all__ = [
    "AgentLLM",
    "AgentStep",
    "AgentTrace",
    "Critique",
    "Plan",
    "Tool",
    "ToolResult",
    "ToolSpec",
]
