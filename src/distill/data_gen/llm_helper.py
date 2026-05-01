"""LLM 호출 + JSON 파싱 + Qdrant 청크 스크롤 헬퍼."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx
from tenacity import (
    AsyncRetrying,
    RetryError,
    before_sleep_log,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from src.config.weights import weights as _w

# botocore 는 boto3 의존성으로 설치되지만, edge 환경 등에서 부재 가능 — soft import.
try:
    from botocore.exceptions import BotoCoreError, ClientError  # type: ignore[assignment]
except ImportError:  # pragma: no cover - boto3 미설치 환경 fallback.
    class ClientError(Exception):  # type: ignore[no-redef]
        """Fallback when botocore is unavailable."""

    class BotoCoreError(Exception):  # type: ignore[no-redef]
        """Fallback when botocore is unavailable."""

logger = logging.getLogger(__name__)
# Retry rate metric — INFO 레벨로 retry 발생 시 attempt count + exception 로깅.
# 운영 dashboard 에서 "LLM call retry" pattern grep 으로 retry rate 추적 가능.
_RETRY_LOGGER = logging.getLogger(f"{__name__}.retry")

# Transient 예외 — 재시도하면 성공 가능성 있음 (네트워크/서버 일시 장애).
# ValueError 는 의도적 제외 — 응답 파싱 실패는 재시도해도 같은 결과.
# ClientError/BotoCoreError 는 LLMHelper 레벨에서 retry 안 함 (sagemaker_client
# 의 tenacity 가 transient 코드만 재시도) — 단지 fail-soft catch 용 (아래 except).
_RETRYABLE_LLM_EXCEPTIONS: tuple[type[BaseException], ...] = (
    httpx.HTTPError, asyncio.TimeoutError, RuntimeError,
)


class LLMHelper:
    """Teacher LLM 호출 + Qdrant 청크 조회."""

    def __init__(self, llm_client, qdrant_url: str, concurrency: int, timeout_sec: int) -> None:
        self.llm = llm_client
        self.qdrant_url = qdrant_url
        self._semaphore = asyncio.Semaphore(concurrency)
        self._timeout = timeout_sec

    async def _invoke_once(self, prompt: str, temperature: float) -> str:
        """단일 LLM 호출 — retry 데코레이터로 감쌀 internal."""
        coro = None
        if hasattr(self.llm, "generate"):
            coro = self.llm.generate(prompt, temperature=temperature)
        elif hasattr(self.llm, "generate_response"):
            coro = self.llm.generate_response(
                query=prompt, context=[], system_prompt="",
            )
        if coro is None:
            return ""
        result = await asyncio.wait_for(coro, timeout=self._timeout)
        return result if isinstance(result, str) else str(result)

    async def call(self, prompt: str, temperature: float = 0.7) -> str:
        """Teacher LLM 호출 (세마포어 + 타임아웃 + tenacity retry).

        Retry 정책:
        - 최대 3회 시도 (max 2 retries on transient failure)
        - exponential backoff (1s → 2s, max 8s)
        - retryable: httpx.HTTPError / asyncio.TimeoutError / RuntimeError
          (네트워크 일시 장애, SageMaker invoke transient, Ollama HTTP)
        - non-retryable: ValueError (응답 파싱 실패는 재시도 무의미)

        모든 시도 실패 또는 non-retryable 발생 시 빈 string 반환 (fail-soft).
        """
        async with self._semaphore:
            try:
                async for attempt in AsyncRetrying(
                    stop=stop_after_attempt(3),
                    wait=wait_exponential(multiplier=1, min=1, max=8),
                    retry=retry_if_exception_type(_RETRYABLE_LLM_EXCEPTIONS),
                    before_sleep=before_sleep_log(_RETRY_LOGGER, logging.INFO),
                    reraise=True,
                ):
                    with attempt:
                        return await self._invoke_once(prompt, temperature)
            except RetryError as e:
                logger.warning("LLM call exhausted retries: %s", e)
                return ""
            except asyncio.TimeoutError:
                # AsyncRetrying 의 reraise=True 로 마지막 시도 timeout 이 leak.
                logger.warning("LLM call timed out after %ds", self._timeout)
                return ""
            except _RETRYABLE_LLM_EXCEPTIONS as e:
                # reraise=True 로 retryable 의 마지막 raise 가 leak.
                logger.warning("LLM call failed after retries: %s", e)
                return ""
            except (ClientError, BotoCoreError) as e:
                # SageMaker invoke leak — sagemaker_client tenacity 가 retry 소진
                # 후 ClientError 그대로 reraise. fail-soft 보장.
                logger.warning("LLM call AWS client error: %s", e)
                return ""
            except ValueError as e:
                # non-retryable — 응답 파싱 실패.
                logger.warning("LLM call output invalid: %s", e)
                return ""
            return ""

    async def scroll_chunks(
        self, kb_id: str, limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Qdrant에서 KB 청크 스크롤."""
        chunks: list[dict[str, Any]] = []
        offset = None

        while len(chunks) < limit:
            body: dict[str, Any] = {
                "limit": min(100, limit - len(chunks)),
                "with_payload": True,
                "with_vector": False,
            }
            if offset is not None:
                body["offset"] = offset

            try:
                async with httpx.AsyncClient(timeout=_w.timeouts.httpx_default) as client:
                    resp = await client.post(
                        f"{self.qdrant_url}/collections/{kb_id}/points/scroll",
                        json=body,
                    )
                    resp.raise_for_status()
                    data = resp.json().get("result", {})
            except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
                logger.warning("Qdrant scroll failed for %s: %s", kb_id, e)
                break

            points = data.get("points", [])
            for point in points:
                payload = point.get("payload", {})
                chunks.append({
                    "content": payload.get("content", ""),
                    "document_name": payload.get("document_name", ""),
                    "source_uri": payload.get("source_uri", ""),
                })

            offset = data.get("next_page_offset")
            if offset is None or not points:
                break

        return chunks

    @staticmethod
    def parse_qa_json(response: str) -> list[dict[str, Any]]:
        """LLM 응답에서 QA JSON 파싱."""
        try:
            start = response.find("[")
            end = response.rfind("]") + 1
            if start >= 0 and end > start:
                from json_repair import repair_json
                repaired = repair_json(response[start:end])
                parsed = json.loads(repaired)
                if isinstance(parsed, list):
                    return parsed
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError):
            pass

        try:
            results = []
            for line in response.split("\n"):
                line = line.strip()
                if line.startswith("{") and line.endswith("}"):
                    results.append(json.loads(line))
            return results
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError):
            pass

        return []
