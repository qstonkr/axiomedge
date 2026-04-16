"""LLM client Protocol — structural interface for all LLM providers.

Both OllamaClient and SageMakerLLMClient satisfy this Protocol
without explicit inheritance (structural/duck typing).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class LLMClient(Protocol):
    """Protocol for LLM clients (Ollama, SageMaker)."""

    async def generate_response(
        self,
        query: str,
        context: list[dict],
        *,
        system_prompt: str | None = None,
    ) -> str:
        """Generate a RAG response from query + context chunks."""
        ...

    async def generate(
        self,
        prompt: str,
        *,
        system_prompt: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> str:
        """Generic text generation."""
        ...

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> str:
        """Chat-style generation from message list."""
        ...

    async def check_health(self) -> dict:
        """Health check returning status dict."""
        ...
