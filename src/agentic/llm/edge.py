"""Edge AgentLLM stub — Day 10 에서 src/edge HTTP /ask 통합 예정.

현재는 placeholder — Day 10 에 routing.py 와 함께 구현.
"""

from __future__ import annotations

from src.agentic.llm.base import JsonAgentLLM


class EdgeAgentLLM(JsonAgentLLM):
    def __init__(self) -> None:
        raise NotImplementedError(
            "EdgeAgentLLM not yet implemented — planned for Day 10. "
            "Use LLM_PROVIDER=ollama (local) or sagemaker (HQ) for now.",
        )
