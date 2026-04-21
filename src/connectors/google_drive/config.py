"""Google Drive connector configuration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class GoogleDriveConnectorConfig:
    """Resolved configuration for a Google Drive-backed data source.

    Attributes:
        auth_token: service account JSON 또는 raw access_token (shared SecretBox).
        folder_id: BFS 시작 folder ID. 빈 string = "root" (My Drive root).
        recursive: 하위 폴더도 재귀 (default True).
        max_files: 최대 파일 수 (default 500).
        include_mime_types: 허용 MIME 타입 (lower-case). 비어있으면 default set.
        name: human readable.
    """

    auth_token: str
    folder_id: str = "root"
    recursive: bool = True
    max_files: int = 500
    include_mime_types: tuple[str, ...] = (
        "application/vnd.google-apps.document",
        "application/vnd.google-apps.presentation",
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "text/plain",
        "text/markdown",
    )
    name: str = ""

    @classmethod
    def from_source(cls, source: dict[str, Any]) -> GoogleDriveConnectorConfig:
        crawl_cfg = source.get("crawl_config") or {}
        token = str(crawl_cfg.get("auth_token") or "").strip()
        if not token:
            raise ValueError(
                "google_drive connector requires auth_token (shared SecretBox 에 admin 등록 필요)",
            )

        raw_mimes = crawl_cfg.get("include_mime_types")
        if raw_mimes is None:
            include_mimes = cls.include_mime_types
        else:
            if isinstance(raw_mimes, str):
                raw_mimes = [s.strip() for s in raw_mimes.split(",") if s.strip()]
            include_mimes = tuple(str(m).strip().lower() for m in raw_mimes if m)

        return cls(
            auth_token=token,
            folder_id=str(crawl_cfg.get("folder_id") or "root").strip(),
            recursive=bool(crawl_cfg.get("recursive", True)),
            max_files=int(crawl_cfg.get("max_files") or 500),
            include_mime_types=include_mimes,
            name=str(source.get("name") or ""),
        )
