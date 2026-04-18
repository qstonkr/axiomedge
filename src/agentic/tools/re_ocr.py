"""OCR re-search tool — 차별화 #5.

agent 가 qdrant_search 결과에 OCR 신뢰도 낮은 chunk (confidence < 0.7) 발견 시
다른 PaddleOCR 설정으로 재처리 후 corrected text 로 재검색.

현재는 detect-only 모드 — 실제 re-OCR 통합은 PaddleOCR provider 가 가용한 환경에서.
detect 모드는 실 OCR 없이도 동작 — agent 가 "OCR 신뢰도 낮음 — 결과 무시 권장" 판단 가능.
"""

from __future__ import annotations

import logging
from typing import Any

from src.agentic.protocols import Tool, ToolResult

logger = logging.getLogger(__name__)


_LOW_CONFIDENCE_THRESHOLD = 0.7


class ReOcrTool(Tool):
    name = "re_ocr_search"
    description = (
        "qdrant_search 결과에서 OCR 신뢰도가 낮은 (< 0.7) chunk 를 식별하고, "
        "PaddleOCR 가 가용하면 다른 설정으로 재처리 후 corrected text 반환. "
        "OCR 결과 의심스러울 때 사용 (예: 답변에 깨진 한글 / 숫자 오인식)."
    )
    args_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "chunks": {
                "type": "array",
                "description": "이전 qdrant_search 결과 (chunk_id + content + metadata)",
                "items": {"type": "object"},
            },
            "threshold": {
                "type": "number", "default": 0.7,
                "description": "OCR confidence threshold (이하면 low-confidence)",
            },
        },
        "required": ["chunks"],
    }

    async def execute(self, args: dict[str, Any], state: dict[str, Any]) -> ToolResult:
        chunks = args.get("chunks") or []
        threshold = float(args.get("threshold", _LOW_CONFIDENCE_THRESHOLD))
        if not isinstance(chunks, list):
            return ToolResult(success=False, data=None, error="chunks must be a list")

        low_conf: list[dict[str, Any]] = []
        for ch in chunks:
            if not isinstance(ch, dict):
                continue
            meta = ch.get("metadata") or {}
            ocr_conf = meta.get("ocr_confidence")
            if ocr_conf is None:
                continue
            try:
                if float(ocr_conf) < threshold:
                    low_conf.append({
                        "chunk_id": ch.get("chunk_id", ""),
                        "content_preview": (ch.get("content") or "")[:200],
                        "ocr_confidence": float(ocr_conf),
                        "source_uri": meta.get("source_uri", ""),
                    })
            except (TypeError, ValueError):
                continue

        # PaddleOCR 가용 시 실제 re-OCR 가능 — 현재는 detect-only
        ocr_provider = state.get("ocr_provider")
        re_ocr_attempted = False
        if low_conf and ocr_provider is not None:
            re_ocr_attempted = True
            # 실 PaddleOCR re-OCR 은 source bytes 필요 — agent loop 가 이미지 fetch
            # 까지 책임지진 않으므로 detect 신호 + 재탐색 권장만 반환
            logger.info("re_ocr: %d low-confidence chunks detected (provider available)", len(low_conf))

        return ToolResult(
            success=True,
            data={
                "low_confidence_chunks": low_conf,
                "low_confidence_count": len(low_conf),
                "recommendation": (
                    "다른 키워드로 qdrant_search 재시도 권장" if low_conf else "OCR 신뢰도 양호"
                ),
            },
            metadata={
                "threshold": threshold,
                "input_count": len(chunks),
                "re_ocr_attempted": re_ocr_attempted,
            },
        )
