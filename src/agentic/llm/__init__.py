"""AgentLLM factory — single env (`LLM_PROVIDER`) drives all LLM choice.

GraphRAG / Agentic 모두 같은 env 사용 (별도 `AGENTIC_LLM_PROVIDER` 만들지 않음).
AWS 제거 시 env 한 줄로 swap — 코드 무수정.

Usage:
    from src.agentic.llm import create_agent_llm
    llm = create_agent_llm()              # env-driven
    llm = create_agent_llm("ollama")      # explicit
"""

from __future__ import annotations

import logging

from src.agentic.llm.base import JsonAgentLLM
from src.agentic.protocols import AgentLLM
from src.core.providers.llm import _resolve_provider_name

logger = logging.getLogger(__name__)


def create_agent_llm(provider: str | None = None) -> AgentLLM:
    """Resolve provider name (same logic as main RAG) and instantiate AgentLLM."""
    resolved = _resolve_provider_name(provider)
    logger.info("AgentLLM provider: %s", resolved)
    if resolved == "ollama":
        from src.agentic.llm.ollama import OllamaAgentLLM
        return OllamaAgentLLM()
    if resolved == "sagemaker":
        from src.agentic.llm.sagemaker import SageMakerAgentLLM
        return SageMakerAgentLLM()
    if resolved == "openai":
        from src.agentic.llm.openai_adapter import OpenAIAgentLLM
        return OpenAIAgentLLM()
    if resolved == "anthropic":
        from src.agentic.llm.anthropic_adapter import AnthropicAgentLLM
        return AnthropicAgentLLM()
    if resolved == "edge":
        from src.agentic.llm.edge import EdgeAgentLLM
        return EdgeAgentLLM()
    raise ValueError(
        f"Unknown agent LLM provider: {resolved!r}. "
        "Supported: ollama, sagemaker, openai, anthropic, edge",
    )


__all__ = ["JsonAgentLLM", "create_agent_llm"]
