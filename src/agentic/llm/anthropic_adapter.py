"""Anthropic AgentLLM stub — Day 12 에서 tool use 활성화 예정."""

from __future__ import annotations

import os

from src.agentic.llm.base import JsonAgentLLM


class AnthropicAgentLLM(JsonAgentLLM):
    def __init__(self) -> None:
        if not os.getenv("ANTHROPIC_API_KEY"):
            raise RuntimeError(
                "ANTHROPIC_API_KEY env var not set — cannot use 'anthropic' provider.",
            )
        raise NotImplementedError(
            "AnthropicAgentLLM not yet implemented — planned for Day 12. "
            "Use LLM_PROVIDER=ollama or sagemaker for now.",
        )
