"""Ollama HTTP client for local LLM inference.

Extracted from oreo-agents ExaoneLLMService with only the Ollama HTTP path.
All oreo-specific framework dependencies (StatsD, input_sanitizer, LiteLLM,
domain vocabulary) have been removed.

Usage:
    from src.nlp.llm.ollama_client import OllamaClient, OllamaConfig

    config = OllamaConfig(base_url="http://localhost:11434", model="exaone3.5:7.8b")
    client = OllamaClient(config)
    response = await client.generate_response(query, context_chunks)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import AsyncIterator

import httpx

from src.config import DEFAULT_LLM_MODEL, get_settings
from src.config.weights import weights
from .prompts import RAG_PROMPT, SYSTEM_PROMPT
from .utils import sanitize_text as _sanitize_text, estimate_token_count as _estimate_token_count_fn

logger = logging.getLogger(__name__)


@dataclass
class OllamaConfig:
    """Ollama LLM runtime configuration.

    All settings are sourced from environment variables with sensible defaults.
    """

    base_url: str = field(
        default_factory=lambda: get_settings().ollama.base_url
    )
    model: str = field(
        default_factory=lambda: os.getenv("OLLAMA_MODEL", DEFAULT_LLM_MODEL)
    )
    timeout: float = field(default_factory=lambda: weights.timeouts.ollama_llm)
    max_tokens: int = field(default_factory=lambda: weights.llm.max_tokens)
    temperature: float = field(default_factory=lambda: weights.llm.temperature)
    context_length: int = field(default_factory=lambda: weights.llm.context_length)

    def __post_init__(self) -> None:
        self.model = self.model.strip()


class OllamaClient:
    """Ollama HTTP client for local LLM inference.

    Features:
    - Local Ollama inference (no data leakage)
    - Streaming response support
    - Fallback model support
    - Generic text generation and batch classification
    """

    def __init__(
        self,
        config: OllamaConfig | None = None,
        *,
        base_url: str | None = None,
        model: str | None = None,
    ) -> None:
        if config is not None:
            self._config = config
        elif base_url is not None or model is not None:
            kwargs: dict[str, str] = {}
            if base_url is not None:
                kwargs["base_url"] = base_url
            if model is not None:
                kwargs["model"] = model
            self._config = OllamaConfig(**kwargs)
        else:
            self._config = OllamaConfig()
        self._client: httpx.AsyncClient | None = None
        self._client_lock: asyncio.Lock = asyncio.Lock()

    @staticmethod
    def _estimate_token_count(value: str) -> int:
        return _estimate_token_count_fn(value)

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            async with self._client_lock:
                if self._client is None:
                    self._client = httpx.AsyncClient(timeout=self._config.timeout)
        return self._client

    async def generate_response(
        self,
        query: str,
        context: list[dict],
        *,
        system_prompt: str | None = None,
    ) -> str:
        """RAG response generation.

        Args:
            query: User query.
            context: Retrieved document context list.
                     [{"content": str, "metadata": dict, "similarity": float}, ...]
            system_prompt: Custom system prompt (optional).

        Returns:
            Generated response text.
        """
        formatted_context = self._format_context(context)
        safe_query = _sanitize_text(query, max_length=2000)

        system = system_prompt or SYSTEM_PROMPT
        user_prompt = RAG_PROMPT.format(
            query=safe_query,
            context=formatted_context,
        )

        full_prompt = f"{system}\n\n{user_prompt}"
        return await self._generate_via_ollama(
            query=query, prompt=full_prompt, context_count=len(context)
        )

    async def generate(
        self,
        prompt: str,
        *,
        system_prompt: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> str:
        """Generic text generation entrypoint for classification/extraction tasks."""
        safe_prompt = _sanitize_text(prompt, max_length=12000)
        effective_max_tokens = int(max_tokens or self._config.max_tokens)
        effective_temperature = float(
            temperature if temperature is not None else self._config.temperature
        )

        composed_prompt = safe_prompt
        if system_prompt:
            composed_prompt = f"{system_prompt}\n\n{safe_prompt}"

        client = await self._get_client()
        response = await client.post(
            f"{self._config.base_url}/api/generate",
            json={
                "model": self._config.model,
                "prompt": composed_prompt,
                "stream": False,
                "options": {
                    "num_predict": effective_max_tokens,
                    "temperature": effective_temperature,
                    "num_ctx": self._config.context_length,
                },
            },
        )
        response.raise_for_status()
        return response.json().get("response", "").strip()

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> str:
        """Chat-style generation using Ollama /api/chat endpoint.

        Args:
            messages: List of {"role": "system"|"user"|"assistant", "content": str}.
            max_tokens: Max tokens to generate.
            temperature: Sampling temperature.

        Returns:
            Assistant response content.
        """
        effective_max_tokens = int(max_tokens or self._config.max_tokens)
        effective_temperature = float(
            temperature if temperature is not None else self._config.temperature
        )

        client = await self._get_client()
        response = await client.post(
            f"{self._config.base_url}/api/chat",
            json={
                "model": self._config.model,
                "messages": messages,
                "stream": False,
                "options": {
                    "num_predict": effective_max_tokens,
                    "temperature": effective_temperature,
                    "num_ctx": self._config.context_length,
                },
            },
        )
        response.raise_for_status()
        return response.json().get("message", {}).get("content", "").strip()

    async def classify_batch(
        self,
        prompts: list[str],
        *,
        system_prompt: str | None = None,
        max_concurrency: int = 4,
        max_tokens: int = weights.llm.classify_max_tokens,
        temperature: float = weights.llm.classify_temperature,
    ) -> list[str]:
        """Run classification prompts in bounded concurrency."""
        if not prompts:
            return []

        semaphore = asyncio.Semaphore(max(1, max_concurrency))

        async def _run(prompt: str) -> str:
            async with semaphore:
                return await self.generate(
                    prompt,
                    system_prompt=system_prompt,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )

        return await asyncio.gather(*[_run(prompt) for prompt in prompts])

    async def generate_response_stream(
        self,
        query: str,
        context: list[dict],
        *,
        system_prompt: str | None = None,
    ) -> AsyncIterator[str]:
        """Streaming RAG response generation.

        Args:
            query: User query.
            context: Retrieved document context list.
            system_prompt: Custom system prompt (optional).

        Yields:
            Response token stream.
        """
        formatted_context = self._format_context(context)
        safe_query = _sanitize_text(query, max_length=2000)

        system = system_prompt or SYSTEM_PROMPT
        user_prompt = RAG_PROMPT.format(
            query=safe_query,
            context=formatted_context,
        )

        full_prompt = f"{system}\n\n{user_prompt}"

        client = await self._get_client()
        async with client.stream(
            "POST",
            f"{self._config.base_url}/api/generate",
            json={
                "model": self._config.model,
                "prompt": full_prompt,
                "stream": True,
                "options": {
                    "num_predict": self._config.max_tokens,
                    "temperature": self._config.temperature,
                },
            },
        ) as response:
            response.raise_for_status()

            async for line in response.aiter_lines():
                if line:
                    try:
                        data = json.loads(line)
                        if token := data.get("response", ""):
                            yield token
                        if data.get("done", False):
                            break
                    except json.JSONDecodeError:
                        continue

    def _format_context(self, context: list[dict]) -> str:
        """Format context chunks into a string for the prompt."""
        if not context:
            return "(관련 문서를 찾지 못했습니다.)"

        formatted_parts = []
        for i, doc in enumerate(context[:5], 1):  # Use top 5 only
            metadata = doc.get("metadata", {})
            title = _sanitize_text(str(metadata.get("title", "제목 없음")), max_length=200)
            content = _sanitize_text(str(doc.get("content", "")), max_length=2000)
            similarity = doc.get("similarity", 0)
            source = _sanitize_text(str(metadata.get("source", "unknown")), max_length=200)

            formatted_parts.append(
                f"### 문서 {i}: {title}\n"
                f"- 출처: {source}\n"
                f"- 관련도: {similarity:.1%}\n"
                f"- 내용:\n{content}\n"
            )

        return "\n---\n".join(formatted_parts)

    async def _generate_via_ollama(self, *, query: str, prompt: str, context_count: int) -> str:
        start_time = time.perf_counter()
        client = await self._get_client()
        response = await client.post(
            f"{self._config.base_url}/api/generate",
            json={
                "model": self._config.model,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "num_predict": self._config.max_tokens,
                    "temperature": self._config.temperature,
                    "num_ctx": self._config.context_length,
                },
            },
        )
        response.raise_for_status()

        result = response.json()
        answer = result.get("response", "").strip()
        duration_ms = (time.perf_counter() - start_time) * 1000

        logger.info(
            "Ollama response generated",
            extra={
                "backend": "ollama",
                "model": self._config.model,
                "query_preview": query[:80],
                "context_count": context_count,
                "answer_length": len(answer),
                "duration_ms": round(duration_ms, 1),
                "input_tokens": self._estimate_token_count(prompt),
                "output_tokens": self._estimate_token_count(answer),
            },
        )
        return answer

    async def check_health(self) -> dict:
        """Check Ollama service status."""
        try:
            client = await self._get_client()
            response = await client.get(f"{self._config.base_url}/api/tags")
            if response.status_code == 200:
                models = response.json().get("models", [])
                model_names = [m.get("name") for m in models]
                return {
                    "status": "healthy",
                    "backend": "ollama",
                    "models_available": model_names,
                    "primary_model_ready": self._config.model in model_names
                    or any(self._config.model in m for m in model_names),
                }
            return {"status": "unhealthy", "error": f"HTTP {response.status_code}"}
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            return {"status": "unhealthy", "error": str(e)}

    async def generate_with_context(self, query: str, context: str) -> str:
        """Convenience method: build a RAG prompt from query + context string and generate.

        Args:
            query: User question.
            context: Pre-formatted context text (e.g. numbered chunks).

        Returns:
            Generated answer string.
        """
        safe_query = _sanitize_text(query, max_length=2000)
        system = SYSTEM_PROMPT
        user_prompt = RAG_PROMPT.format(query=safe_query, context=context)
        full_prompt = f"{system}\n\n{user_prompt}"
        return await self._generate_via_ollama(
            query=query, prompt=full_prompt, context_count=1,
        )

    async def generate_stream(self, query: str, context: str) -> AsyncIterator[str]:
        """Streaming convenience method: build a RAG prompt and stream tokens.

        Args:
            query: User question.
            context: Pre-formatted context text.

        Yields:
            Response token stream.
        """
        safe_query = _sanitize_text(query, max_length=2000)
        system = SYSTEM_PROMPT
        user_prompt = RAG_PROMPT.format(query=safe_query, context=context)
        full_prompt = f"{system}\n\n{user_prompt}"

        client = await self._get_client()
        async with client.stream(
            "POST",
            f"{self._config.base_url}/api/generate",
            json={
                "model": self._config.model,
                "prompt": full_prompt,
                "stream": True,
                "options": {
                    "num_predict": self._config.max_tokens,
                    "temperature": self._config.temperature,
                    "num_ctx": self._config.context_length,
                },
            },
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if line:
                    try:
                        data = json.loads(line)
                        if token := data.get("response", ""):
                            yield token
                        if data.get("done", False):
                            break
                    except json.JSONDecodeError:
                        continue

    async def close(self) -> None:
        """Close the HTTP client."""
        async with self._client_lock:
            if self._client is None:
                return
            if not self._client.is_closed:
                await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> "OllamaClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        await self.close()

    def __del__(self) -> None:
        client = self._client
        if client is None:
            return
        try:
            if getattr(client, "is_closed", False):
                return
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError):
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        try:
            if loop and loop.is_running():
                loop.create_task(client.aclose())
            else:
                asyncio.run(client.aclose())
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.debug("Failed to close httpx client during __del__: %s", e)


# Convenience alias for backward compatibility
OllamaLLMClient = OllamaClient
