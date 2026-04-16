"""Qdrant pipeline shared utilities.

Extracted from oreo-ecosystem qdrant_utils.py.
Deterministic UUID conversion, payload truncation, and Qdrant connection helpers.

Created: 2026-02-11
"""
from __future__ import annotations

import uuid
import logging
from src.config.weights import weights as _w

logger = logging.getLogger(__name__)

# 결정론적 UUID 변환 (동일 string -> 동일 UUID)
QDRANT_NAMESPACE = uuid.UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")

# Payload content 최대 크기 (Qdrant payload 안정성)
MAX_PAYLOAD_CONTENT_LENGTH = 8000


def str_to_uuid(string_id: str) -> str:
    """문자열 ID를 Qdrant UUID로 결정론적 변환.

    동일 string -> 항상 동일 UUID 생성.
    원본 ID는 payload의 'original_id' 필드에 별도 저장 권장.
    """
    return str(uuid.uuid5(QDRANT_NAMESPACE, string_id))


def truncate_content(content: str, max_length: int = MAX_PAYLOAD_CONTENT_LENGTH) -> str:
    """Payload content 크기 제한.

    Qdrant payload는 segment 크기에 영향을 받으므로
    대용량 content를 안전한 크기로 truncate.
    """
    if len(content) <= max_length:
        return content
    return content[:max_length] + "...[truncated]"


def get_qdrant_url() -> str:
    """Qdrant URL — SSOT: ``get_settings().qdrant.url``."""
    from src.config import get_settings
    return get_settings().qdrant.url


def create_qdrant_client():
    """Qdrant sync 클라이언트 생성 (타임아웃 포함)."""
    from qdrant_client import QdrantClient

    return QdrantClient(
        url=get_qdrant_url(),
        timeout=_w.timeouts.httpx_default,
    )
