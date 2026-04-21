"""OCR EC2 lifecycle facade — CLI/worker 가 layering 위반 없이 호출.

배경: ``_start_ocr_instance`` / ``_stop_ocr_instance`` 는 historically
``src/api/routes/data_source_sync.py`` 에 있음 (Confluence trigger 가 OCR
자동 기동/종료 담당). 같은 helper 가 CLI ingest 에도 필요해 ``cli/ingest.py``
가 ``api.routes`` 를 직접 import 했는데, presentation → service direction
이라 layering 위반.

본 모듈은 thin re-export — 함수 본체는 그대로 유지하고 stable public API
이름만 제공. 향후 본체 코드를 본 모듈로 이동하면 ``data_source_sync`` 도
이쪽에서 import 하면 됨.
"""

from __future__ import annotations

from src.api.routes.data_source_sync import (
    _start_ocr_instance as _impl_start,
    _stop_ocr_instance as _impl_stop,
)


async def start_ocr_instance() -> str | None:
    """Start PaddleOCR EC2 — health-checked URL 반환 (or None if not configured).

    부수효과: 새 IP 를 ``os.environ`` + Redis (``ocr:paddleocr:url``) 에 propagate
    → arq worker / 다른 CLI 프로세스 자동 반영.
    """
    return await _impl_start()


async def stop_ocr_instance() -> None:
    """Stop PaddleOCR EC2 — 비용 절감. ``PADDLEOCR_INSTANCE_ID`` 미설정 시 noop."""
    await _impl_stop()
