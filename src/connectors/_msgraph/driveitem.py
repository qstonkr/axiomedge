"""MSGraph driveItem 공유 helper — OneDrive/SharePoint Document Library 공통.

OneDrive 와 SharePoint site 의 Document Library 는 동일한 driveItem schema/
endpoint (``/drives/{drive_id}/items/*``) 를 공유한다. 다운로드 → 임시 파일 →
``parse_file()`` → ``RawDocument`` 변환 흐름도 동일하므로 본 모듈에서 중앙화.

### 설계 결정

- **source_type 주입 방식**: doc_id prefix / metadata.source_type 이 connector
  별로 달라야 하므로, 호출자가 `source_type` 인자로 명시 (onedrive/sharepoint).
  helper 가 알아서 분기하지 않는다.
- **인증**: MSGraphClient 의 auth_token 을 그대로 사용. /content endpoint 는
  redirect 를 사용해 직접 httpx 로 호출 (MSGraphClient 는 JSON-only).
- **size cap**: 50MB — 50MB 넘는 파일은 drop. 현재 조직 평균 파일이 PDF 수백
  페이지도 30MB 이하이고, 그 이상은 스캔본이라 OCR 비용 대비 정보 밀도 낮음.
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from src.core.models import RawDocument
from src.pipelines.document_parser import parse_file

logger = logging.getLogger(__name__)

_MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024


async def download_drive_item(
    auth_token: str,
    item: dict[str, Any],
    *,
    source_type: str,
    knowledge_type: str = "",
    include_extensions: tuple[str, ...] | None = None,
) -> RawDocument | None:
    """driveItem → 다운로드 → parse → RawDocument.

    Args:
        auth_token: Bearer token (Graph API).
        item: driveItem JSON (``/children`` 또는 ``/items/{id}`` 응답).
        source_type: ``"onedrive"`` / ``"sharepoint"``. doc_id prefix +
            metadata.source_type 에 사용.
        knowledge_type: metadata.knowledge_type (분류용, 선택).
        include_extensions: 허용 확장자 화이트리스트 (소문자, dot 포함). None 이면
            전체 허용.

    Returns:
        본문 추출 성공 시 ``RawDocument``. skip 또는 parse 실패 시 ``None``.
    """
    if "file" not in item:
        return None

    name = str(item.get("name") or "")
    item_id = str(item.get("id") or "")
    size = int(item.get("size") or 0)

    ext = Path(name).suffix.lower()
    if include_extensions and ext not in include_extensions:
        return None

    if size > _MAX_DOWNLOAD_BYTES:
        logger.info(
            "%s: skip oversized file %s (%d bytes)", source_type, name, size,
        )
        return None

    ref = item.get("parentReference") or {}
    drive_id = str(ref.get("driveId") or "")
    if not drive_id or not item_id:
        return None

    content_path = f"/drives/{drive_id}/items/{item_id}/content"
    download_url = f"https://graph.microsoft.com/v1.0{content_path}"
    async with httpx.AsyncClient(
        headers={"Authorization": f"Bearer {auth_token}"},
        timeout=120.0, follow_redirects=True,
    ) as http:
        resp = await http.get(download_url)
        if resp.status_code != 200:
            raise RuntimeError(
                f"{source_type} download failed ({resp.status_code}): "
                f"{resp.text[:200]}",
            )
        data = resp.content

    suffix = ext or ".bin"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(data)
        tmp_path = Path(tmp.name)
    try:
        text = await asyncio.to_thread(parse_file, tmp_path)
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass

    body = (text or "").strip()
    if not body:
        return None

    modified = _parse_iso_date(item.get("lastModifiedDateTime"))
    web_url = str(item.get("webUrl") or "")
    author = (
        ((item.get("createdBy") or {}).get("user") or {}).get("displayName", "")
    )
    return RawDocument(
        doc_id=f"{source_type}:{drive_id}:{item_id}",
        title=name or f"item-{item_id}",
        content=body,
        source_uri=web_url,
        author=author,
        updated_at=modified,
        content_hash=RawDocument.sha256(body),
        metadata={
            "source_type": source_type,
            "drive_id": drive_id,
            "item_id": item_id,
            "file_name": name,
            "file_ext": ext or ".bin",
            "file_size_bytes": size,
            "knowledge_type": knowledge_type or source_type,
        },
    )


def _parse_iso_date(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


__all__ = ["download_drive_item"]
