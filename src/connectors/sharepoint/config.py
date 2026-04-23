"""SharePoint connector configuration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SharePointConnectorConfig:
    """Resolved configuration for a SharePoint-backed data source.

    Attributes:
        auth_token: MSGraph app-only access token (admin shared SecretBox 에서
            launcher 가 inject).
        site_id: SharePoint site ID — ``{hostname},{site-collection-id},{site-id}``
            형식 (Graph API 표준). 또는 sites/root, sites/{hostname}:/sites/{name}.
        list_ids: 동기화할 list ID list. 비어있으면 전체 list.
        max_items: 한 list 당 최대 item 수 (default 1000). API throttle 보호.
        include_document_libraries: ``True`` (default) — site 의 Document Library
            (driveItem) 전체 크롤. SharePoint 는 실제 문서 대부분이 Document
            Library 에 있어서 default on.
        drive_ids: 크롤할 drive ID list. 비어있으면 site 의 모든 drive 크롤.
        max_files: Document Library BFS 당 최대 파일 수. 대형 사이트 throttle 보호.
        include_extensions: 허용 확장자 화이트리스트 (``.pdf``, ``.docx`` 등).
            None 이면 전체 허용 (parse_file 이 미지원 MIME 은 자동 skip).
        name: human readable source name.
    """

    auth_token: str
    site_id: str
    list_ids: tuple[str, ...]
    max_items: int = 1000
    include_document_libraries: bool = True
    drive_ids: tuple[str, ...] = field(default_factory=tuple)
    max_files: int = 1000
    include_extensions: tuple[str, ...] | None = None
    name: str = ""

    @classmethod
    def from_source(cls, source: dict[str, Any]) -> SharePointConnectorConfig:
        crawl_cfg = source.get("crawl_config") or {}

        token = str(crawl_cfg.get("auth_token") or "").strip()
        if not token:
            raise ValueError(
                "sharepoint connector requires auth_token (shared SecretBox 에 admin 등록 필요)",
            )

        site_id = str(crawl_cfg.get("site_id") or "").strip()
        if not site_id:
            raise ValueError("sharepoint connector requires crawl_config.site_id")

        raw_lists = crawl_cfg.get("list_ids") or []
        if isinstance(raw_lists, str):
            raw_lists = [s.strip() for s in raw_lists.split(",") if s.strip()]
        list_ids = tuple(str(lid).strip() for lid in raw_lists if str(lid).strip())

        raw_drives = crawl_cfg.get("drive_ids") or []
        if isinstance(raw_drives, str):
            raw_drives = [s.strip() for s in raw_drives.split(",") if s.strip()]
        drive_ids = tuple(str(did).strip() for did in raw_drives if str(did).strip())

        include_exts_raw = crawl_cfg.get("include_extensions")
        include_extensions: tuple[str, ...] | None = None
        if include_exts_raw:
            if isinstance(include_exts_raw, str):
                include_exts_raw = [
                    s.strip() for s in include_exts_raw.split(",") if s.strip()
                ]
            include_extensions = tuple(
                (e if e.startswith(".") else f".{e}").lower()
                for e in include_exts_raw if e
            )

        raw_idl = crawl_cfg.get("include_document_libraries", True)
        # YAML/JSON → 문자열 "false"/"0" 도 허용. bool("false") is True 함정 회피.
        if isinstance(raw_idl, bool):
            include_document_libraries = raw_idl
        else:
            include_document_libraries = (
                str(raw_idl).strip().lower() not in ("false", "0", "no", "off", "")
            )

        # .get("max_items") or 1000 은 max_items=0 을 default 1000 으로 뒤집음.
        # 명시적으로 ``None`` 체크 — 0 은 유효 (무의미하지만 의도적) 값.
        max_items_raw = crawl_cfg.get("max_items")
        max_items = int(max_items_raw) if max_items_raw is not None else 1000
        max_files_raw = crawl_cfg.get("max_files")
        max_files = int(max_files_raw) if max_files_raw is not None else 1000

        return cls(
            auth_token=token,
            site_id=site_id,
            list_ids=list_ids,
            max_items=max_items,
            include_document_libraries=include_document_libraries,
            drive_ids=drive_ids,
            max_files=max_files,
            include_extensions=include_extensions,
            name=str(source.get("name") or ""),
        )
