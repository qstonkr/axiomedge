"""LLM 호출 + JSON 파싱 + Qdrant 청크 스크롤 헬퍼."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


class LLMHelper:
    """Teacher LLM 호출 + Qdrant 청크 조회."""

    def __init__(self, llm_client, qdrant_url: str, concurrency: int, timeout_sec: int):
        self.llm = llm_client
        self.qdrant_url = qdrant_url
        self._semaphore = asyncio.Semaphore(concurrency)
        self._timeout = timeout_sec

    async def call(self, prompt: str, temperature: float = 0.7) -> str:
        """Teacher LLM 호출 (세마포어 + 타임아웃)."""
        async with self._semaphore:
            try:
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
            except asyncio.TimeoutError:
                logger.warning("LLM call timed out after %ds", self._timeout)
                return ""
            except Exception as e:
                logger.warning("LLM call failed: %s", e)
            return ""

    async def scroll_chunks(
        self, kb_id: str, limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Qdrant에서 KB 청크 스크롤."""
        import httpx

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
                async with httpx.AsyncClient(timeout=30) as client:
                    resp = await client.post(
                        f"{self.qdrant_url}/collections/{kb_id}/points/scroll",
                        json=body,
                    )
                    resp.raise_for_status()
                    data = resp.json().get("result", {})
            except Exception as e:
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
        except Exception:
            pass

        try:
            results = []
            for line in response.split("\n"):
                line = line.strip()
                if line.startswith("{") and line.endswith("}"):
                    results.append(json.loads(line))
            return results
        except Exception:
            pass

        return []
