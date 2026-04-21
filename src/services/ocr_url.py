"""PaddleOCR base URL discovery — cross-process via Redis.

문제: EC2 stop/start 마다 public IP 가 바뀜. ``_start_ocr_instance`` 가 새
URL 을 ``os.environ`` 에 set 하지만 별도 프로세스 (arq worker, CLI ingest) 는
보지 못해 stale ``.env`` 의 옛 IP 로 OCR 호출 → timeout.

해결: 새 URL 을 Redis key ``ocr:paddleocr:url`` 에 저장. parser 는 Redis →
env → default 순으로 조회 (sync/async 둘 다 지원). Redis 가 SSOT.

실패 안전: Redis 다운 시 env var fallback (현 동작 유지).
"""

from __future__ import annotations

import logging
import os
from typing import Final

logger = logging.getLogger(__name__)

_REDIS_KEY: Final[str] = "ocr:paddleocr:url"
# 7일 — EC2 가 그 이상 stop 상태면 어차피 stale, 자동 만료 후 env fallback.
_REDIS_TTL_SECONDS: Final[int] = 7 * 24 * 3600
_DEFAULT_URL: Final[str] = "http://localhost:8866"


def _redis_url() -> str:
    return os.getenv("REDIS_URL", "redis://localhost:6379")


def get_paddleocr_url_sync() -> str:
    """Sync 조회 — pipeline parser (sync) 에서 사용.

    우선순위: Redis(``ocr:paddleocr:url``) → env(``PADDLEOCR_API_URL``) → default.
    Redis 다운/미설정 시 즉시 env fallback (silent — log spam 회피).
    """
    try:
        import redis as redis_sync

        client = redis_sync.from_url(_redis_url(), socket_timeout=1.0)
        value = client.get(_REDIS_KEY)
        if value:
            url = value.decode("utf-8") if isinstance(value, bytes) else str(value)
            if url:
                return url
    except Exception:  # noqa: BLE001 — Redis 미설치/다운/network 모두 fallback
        pass
    return os.getenv("PADDLEOCR_API_URL", _DEFAULT_URL)


async def set_paddleocr_url(url: str) -> None:
    """``_start_ocr_instance`` 가 새 URL 해소 시 호출. Redis 에 set + TTL.

    실패 안전 — Redis 다운이어도 caller 측 in-process env update 는 이미 됨
    (data_source_sync.py:166). worker/CLI 만 stale.
    """
    if not url:
        return
    try:
        from redis import asyncio as redis_async

        client = redis_async.from_url(_redis_url(), socket_timeout=2.0)
        try:
            await client.set(_REDIS_KEY, url, ex=_REDIS_TTL_SECONDS)
            logger.info("Redis set %s=%s (TTL=%ds)", _REDIS_KEY, url, _REDIS_TTL_SECONDS)
        finally:
            await client.aclose()
    except Exception as e:  # noqa: BLE001 — Redis 다운도 비치명
        logger.warning("Redis set %s 실패: %s — env fallback 계속 동작", _REDIS_KEY, e)
