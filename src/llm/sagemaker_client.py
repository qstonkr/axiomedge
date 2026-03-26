"""AWS SageMaker LLM client — drop-in replacement for OllamaClient.

Same interface as OllamaClient so it can be swapped via USE_SAGEMAKER_LLM=true.
Supports: generate_response, generate, chat, classify_batch, check_health,
generate_with_context. Streaming is NOT supported (falls back to non-streaming).

Usage:
    from src.llm.sagemaker_client import SageMakerLLMClient

    client = SageMakerLLMClient()
    response = await client.generate_response(query, context_chunks)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from src.config_weights import weights
from .prompts import RAG_PROMPT, SYSTEM_PROMPT

logger = logging.getLogger(__name__)

# Reuse token estimation patterns from ollama_client
_LATIN_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")
_CJK_TOKEN_RE = re.compile(r"[\uAC00-\uD7A3]")
_PUNCT_TOKEN_RE = re.compile(r"[^\sA-Za-z0-9\uAC00-\uD7A3]")


def _sanitize_text(text: str, max_length: int = weights.llm.max_query_length) -> str:
    if not text:
        return ""
    sanitized = text.strip()
    return sanitized[:max_length] if len(sanitized) > max_length else sanitized


@dataclass
class SageMakerConfig:
    """SageMaker LLM configuration."""

    endpoint_name: str = field(
        default_factory=lambda: os.getenv("SAGEMAKER_ENDPOINT_NAME", "oreo-exaone-dev")
    )
    region: str = field(
        default_factory=lambda: os.getenv("SAGEMAKER_REGION", "ap-northeast-2")
    )
    profile: str = field(
        default_factory=lambda: os.getenv("AWS_PROFILE", "jeongbeomkim")
    )
    max_tokens: int = field(default_factory=lambda: weights.llm.max_tokens)
    temperature: float = field(default_factory=lambda: weights.llm.temperature)
    model: str = "sagemaker-exaone"  # For logging/identification


class SageMakerLLMClient:
    """AWS SageMaker LLM client with OllamaClient-compatible interface."""

    def __init__(self, config: SageMakerConfig | None = None) -> None:
        self._config = config or SageMakerConfig()
        self._client = None

    def _get_client(self):
        if self._client is None:
            import boto3
            session = boto3.Session(
                profile_name=self._config.profile,
                region_name=self._config.region,
            )
            self._client = session.client("sagemaker-runtime")
        return self._client

    def _invoke_sync(
        self,
        messages: list[dict[str, str]],
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> str:
        """Synchronous SageMaker invoke."""
        effective_max_tokens = int(max_tokens or self._config.max_tokens)
        effective_temperature = float(
            temperature if temperature is not None else self._config.temperature
        )
        body = {
            "messages": messages,
            "max_tokens": effective_max_tokens,
            "temperature": effective_temperature,
        }
        resp = self._get_client().invoke_endpoint(
            EndpointName=self._config.endpoint_name,
            ContentType="application/json",
            Body=json.dumps(body),
        )
        result = json.loads(resp["Body"].read())
        return result["choices"][0]["message"]["content"].strip()

    async def _invoke(
        self,
        messages: list[dict[str, str]],
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> str:
        """Async wrapper around synchronous boto3 call."""
        return await asyncio.to_thread(
            self._invoke_sync, messages, max_tokens, temperature
        )

    @staticmethod
    def _estimate_token_count(value: str) -> int:
        if not value:
            return 0
        normalized = value.strip()
        if not normalized:
            return 0
        latin = len(_LATIN_TOKEN_RE.findall(normalized))
        cjk = len(_CJK_TOKEN_RE.findall(normalized))
        punct = len(_PUNCT_TOKEN_RE.findall(normalized))
        estimated = latin + cjk + punct
        return max(1, estimated) if estimated else max(1, len(normalized) // 4)

    # ── OllamaClient-compatible interface ──

    async def generate_response(
        self,
        query: str,
        context: list[dict],
        *,
        system_prompt: str | None = None,
    ) -> str:
        """RAG response generation."""
        formatted_context = self._format_context(context)
        safe_query = _sanitize_text(query, max_length=2000)

        system = system_prompt or SYSTEM_PROMPT
        user_prompt = RAG_PROMPT.format(query=safe_query, context=formatted_context)

        start_time = time.perf_counter()
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_prompt},
        ]
        answer = await self._invoke(messages)
        duration_ms = (time.perf_counter() - start_time) * 1000

        logger.info(
            "SageMaker response generated",
            extra={
                "backend": "sagemaker",
                "endpoint": self._config.endpoint_name,
                "query_preview": query[:80],
                "context_count": len(context),
                "answer_length": len(answer),
                "duration_ms": round(duration_ms, 1),
            },
        )
        return answer

    async def generate(
        self,
        prompt: str,
        *,
        system_prompt: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> str:
        """Generic text generation."""
        safe_prompt = _sanitize_text(prompt, max_length=12000)
        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": safe_prompt})
        return await self._invoke(messages, max_tokens, temperature)

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> str:
        """Chat-style generation."""
        return await self._invoke(messages, max_tokens, temperature)

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
                    prompt, system_prompt=system_prompt,
                    max_tokens=max_tokens, temperature=temperature,
                )
        return await asyncio.gather(*[_run(p) for p in prompts])

    async def generate_response_stream(
        self,
        query: str,
        context: list[dict],
        *,
        system_prompt: str | None = None,
    ) -> AsyncIterator[str]:
        """Non-streaming fallback — yields full response as one chunk."""
        response = await self.generate_response(
            query, context, system_prompt=system_prompt,
        )
        yield response

    async def generate_with_context(self, query: str, context: str) -> str:
        """Convenience: build RAG prompt from query + context string."""
        safe_query = _sanitize_text(query, max_length=2000)
        system = SYSTEM_PROMPT
        user_prompt = RAG_PROMPT.format(query=safe_query, context=context)
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_prompt},
        ]
        return await self._invoke(messages)

    async def generate_stream(self, query: str, context: str) -> AsyncIterator[str]:
        """Non-streaming fallback for generate_stream."""
        response = await self.generate_with_context(query, context)
        yield response

    async def check_health(self) -> dict:
        """Check SageMaker endpoint status."""
        try:
            import boto3
            session = boto3.Session(
                profile_name=self._config.profile,
                region_name=self._config.region,
            )
            sm = session.client("sagemaker")
            desc = sm.describe_endpoint(EndpointName=self._config.endpoint_name)
            status = desc["EndpointStatus"]
            return {
                "status": "healthy" if status == "InService" else "unhealthy",
                "backend": "sagemaker",
                "endpoint": self._config.endpoint_name,
                "endpoint_status": status,
            }
        except Exception as e:
            return {"status": "unhealthy", "backend": "sagemaker", "error": str(e)}

    def _format_context(self, context: list[dict]) -> str:
        if not context:
            return "(관련 문서를 찾지 못했습니다.)"
        formatted_parts = []
        for i, doc in enumerate(context[:5], 1):
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
