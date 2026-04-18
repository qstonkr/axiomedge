"""OpenAI AgentLLM stub — Day 12 에서 function calling 활성화 예정.

현재는 placeholder — `OPENAI_API_KEY` 만 검증.
실 호출은 Day 12 에 ``openai`` 라이브러리 추가 후 구현.
"""

from __future__ import annotations

import os

from src.agentic.llm.base import JsonAgentLLM


class OpenAIAgentLLM(JsonAgentLLM):
    def __init__(self) -> None:
        if not os.getenv("OPENAI_API_KEY"):
            raise RuntimeError(
                "OPENAI_API_KEY env var not set — cannot use 'openai' provider. "
                "Set the env var or choose another LLM_PROVIDER (sagemaker/ollama/...)",
            )
        raise NotImplementedError(
            "OpenAIAgentLLM not yet implemented — planned for Day 12 of Agentic RAG plan. "
            "Use LLM_PROVIDER=ollama or sagemaker for now.",
        )
