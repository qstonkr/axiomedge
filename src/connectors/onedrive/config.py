"""OneDrive connector configuration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class OneDriveConnectorConfig:
    """Resolved configuration for a OneDrive-backed data source.

    Attributes:
        auth_token: MSGraph app-only access token (shared SecretBox).
        drive_path: drive 식별 path. 예시:
            ``users/{user-upn-or-id}/drive`` (특정 사용자 drive)
            ``sites/{site-id}/drive``         (SharePoint site 의 default drive)
            ``drives/{drive-id}``             (drive ID 직접)
        folder_path: drive 안의 시작 폴더 path. 빈 string = root.
            예시: ``Documents/2026`` (relative path).
        max_files: 최대 파일 수 (default 500). API throttle 보호.
        include_extensions: 허용 확장자 (lower-case dot 포함). 비어있으면 모두.
        name: human readable.
    """

    auth_token: str
    drive_path: str
    folder_path: str = ""
    max_files: int = 500
    include_extensions: tuple[str, ...] = (".pdf", ".docx", ".pptx", ".md", ".txt")
    name: str = ""

    @classmethod
    def from_source(cls, source: dict[str, Any]) -> OneDriveConnectorConfig:
        crawl_cfg = source.get("crawl_config") or {}

        token = str(crawl_cfg.get("auth_token") or "").strip()
        if not token:
            raise ValueError(
                "onedrive connector requires auth_token (shared SecretBox 에 admin 등록 필요)",
            )

        drive_path = str(crawl_cfg.get("drive_path") or "").strip().strip("/")
        if not drive_path:
            raise ValueError(
                "onedrive connector requires crawl_config.drive_path "
                "(예: users/{upn}/drive, sites/{site_id}/drive, drives/{drive_id})",
            )

        raw_exts = crawl_cfg.get("include_extensions")
        if raw_exts is None:
            include_exts = (".pdf", ".docx", ".pptx", ".md", ".txt")
        else:
            if isinstance(raw_exts, str):
                raw_exts = [s.strip() for s in raw_exts.split(",") if s.strip()]
            include_exts = tuple(
                (e if e.startswith(".") else "." + e).lower()
                for e in raw_exts if e
            )

        return cls(
            auth_token=token,
            drive_path=drive_path,
            folder_path=str(crawl_cfg.get("folder_path") or "").strip().strip("/"),
            max_files=int(crawl_cfg.get("max_files") or 500),
            include_extensions=include_exts,
            name=str(source.get("name") or ""),
        )
