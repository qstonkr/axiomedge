"""Ollama AgentLLM — 로컬 GGUF/EXAONE 등.

기존 ``OllamaClient`` 를 wrap. AGENTIC 변형 prompt 는 base 가 처리.
"""

from __future__ import annotations

from src.agentic.llm.base import JsonAgentLLM
from src.nlp.llm.types import LLMClient


class OllamaAgentLLM(JsonAgentLLM):
    def __init__(self, client: LLMClient | None = None) -> None:
        if client is None:
            from src.config import get_settings
            from src.nlp.llm.ollama_client import OllamaClient, OllamaConfig
            settings = get_settings()
            client = OllamaClient(config=OllamaConfig(
                base_url=settings.ollama.base_url,
                model=settings.ollama.model,
                context_length=settings.ollama.context_length,
            ))
        super().__init__(client=client, provider_name="ollama")
