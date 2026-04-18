"""Edge AgentLLM — src/edge HTTP /ask wrapper.

엣지 서버 (llama.cpp + GGUF) 의 단순 텍스트 생성 endpoint 를 LLMClient 로 wrap.
plan/reflect 같은 JSON 구조화 응답은 작은 edge 모델이 신뢰성 떨어지므로,
EdgeAgentLLM 은 주로 ``synthesize`` (단순 답변 생성) 에 활용.

Routing pattern (src/agentic/routing.py):
  plan with HQ → if complexity ≤ 2 → synthesize with edge → reflect with HQ
"""

# pyright: reportGeneralTypeIssues=false

from __future__ import annotations

import logging
import os
from typing import AsyncIterator

import httpx

from src.agentic.llm.base import JsonAgentLLM

logger = logging.getLogger(__name__)


class _EdgeHttpClient:
    """LLMClient Protocol 만족 — src/edge/server POST /ask 호출."""

    def __init__(
        self, base_url: str, api_key: str | None = None, timeout: float = 30.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout

    @property
    def model(self) -> str:
        return "edge-gguf"  # for token attribution metric

    async def generate(
        self, prompt: str, *, system_prompt: str | None = None,
        max_tokens: int | None = None, temperature: float | None = None,
    ) -> str:
        # edge /ask 는 max_tokens / temperature / system_prompt 미지원 (단순 인터페이스)
        # system_prompt 가 있으면 prompt 앞에 prepend
        full_prompt = f"{system_prompt}\n\n{prompt}" if system_prompt else prompt
        headers = {}
        if self._api_key:
            headers["X-API-Key"] = self._api_key
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{self._base_url}/ask",
                json={"query": full_prompt[:500]},  # edge 는 500자 제한
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
            if not data.get("success"):
                raise RuntimeError(f"edge /ask failed: {data}")
            return data.get("answer", "")

    async def generate_response(
        self, query: str, context: list[dict], *,
        system_prompt: str | None = None,
    ) -> str:
        # context 를 짧게 stringify 후 generate 호출
        ctx_text = "\n\n".join(
            f"[참고 {i+1}]\n{c.get('content', '')[:500]}"
            for i, c in enumerate(context[:3])
        )
        prompt = f"{ctx_text}\n\n질문: {query}\n답변:"
        return await self.generate(prompt, system_prompt=system_prompt)

    async def chat(self, messages, *, max_tokens=None, temperature=None) -> str:
        # 마지막 user 메시지만 사용
        for msg in reversed(messages):
            if msg.get("role") == "user":
                return await self.generate(msg.get("content", ""))
        return ""

    async def check_health(self) -> dict:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{self._base_url}/health")
            return resp.json()

    async def generate_response_stream(
        self, query: str, context: list[dict], *, system_prompt: str | None = None,
    ) -> AsyncIterator[str]:
        # streaming 미지원 — full response 한번에 yield
        result = await self.generate_response(query, context, system_prompt=system_prompt)
        yield result


class EdgeAgentLLM(JsonAgentLLM):
    """엣지 추론 서버 wrap — env-driven 설정.

    Env:
      - AGENTIC_EDGE_URL: e.g., http://store-001.edge.local:8001 (필수)
      - AGENTIC_EDGE_API_KEY: X-API-Key (선택)
      - AGENTIC_EDGE_TIMEOUT: 초 (default 30)
    """

    def __init__(
        self, base_url: str | None = None,
        api_key: str | None = None,
        timeout: float | None = None,
    ) -> None:
        url = base_url or os.getenv("AGENTIC_EDGE_URL", "").strip()
        if not url:
            raise RuntimeError(
                "AGENTIC_EDGE_URL not set — cannot use 'edge' provider. "
                "Set the env var or choose another LLM_PROVIDER (sagemaker/ollama/...).",
            )
        client = _EdgeHttpClient(
            base_url=url,
            api_key=api_key or os.getenv("AGENTIC_EDGE_API_KEY"),
            timeout=timeout or float(os.getenv("AGENTIC_EDGE_TIMEOUT", "30")),
        )
        super().__init__(client=client, provider_name="edge")
        logger.info("EdgeAgentLLM initialized — url=%s", url)
