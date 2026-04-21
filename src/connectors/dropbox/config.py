"""Dropbox connector configuration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class DropboxConnectorConfig:
    """Resolved configuration for a Dropbox-backed data source.

    Attributes:
        auth_token: Dropbox App access token (shared SecretBox).
        folder_path: 시작 폴더 path. 빈 string = root ("/").
        recursive: 하위 폴더 재귀 (default True).
        max_files: 최대 파일 수 (default 500).
        include_extensions: 허용 확장자 (default: pdf/docx/pptx/md/txt).
        name: human readable.
    """

    auth_token: str
    folder_path: str = ""
    recursive: bool = True
    max_files: int = 500
    include_extensions: tuple[str, ...] = (".pdf", ".docx", ".pptx", ".md", ".txt")
    name: str = ""

    @classmethod
    def from_source(cls, source: dict[str, Any]) -> DropboxConnectorConfig:
        crawl_cfg = source.get("crawl_config") or {}
        token = str(crawl_cfg.get("auth_token") or "").strip()
        if not token:
            raise ValueError(
                "dropbox connector requires auth_token (shared SecretBox 에 admin 등록 필요)",
            )

        # Dropbox API 는 root path 가 빈 string — "/" 도 허용.
        folder = str(crawl_cfg.get("folder_path") or "").strip()
        if folder in ("/", ""):
            folder = ""
        elif not folder.startswith("/"):
            folder = "/" + folder
        folder = folder.rstrip("/")

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
            folder_path=folder,
            recursive=bool(crawl_cfg.get("recursive", True)),
            max_files=int(crawl_cfg.get("max_files") or 500),
            include_extensions=include_exts,
            name=str(source.get("name") or ""),
        )
