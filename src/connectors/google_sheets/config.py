"""Google Sheets connector configuration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class GoogleSheetsConnectorConfig:
    """Resolved configuration for a Google Sheets-backed data source.

    Attributes:
        auth_token: service account JSON 또는 raw access_token.
        spreadsheet_ids: 동기화할 spreadsheet ID list.
        max_rows_per_sheet: 한 sheet 당 최대 row (default 5000). 대용량 dataset 보호.
        max_cols: 최대 column (default 50). 너무 wide 한 sheet 의 markdown 폭주 방지.
        name: human readable.
    """

    auth_token: str
    spreadsheet_ids: tuple[str, ...]
    max_rows_per_sheet: int = 5000
    max_cols: int = 50
    name: str = ""

    @classmethod
    def from_source(cls, source: dict[str, Any]) -> GoogleSheetsConnectorConfig:
        crawl_cfg = source.get("crawl_config") or {}
        token = str(crawl_cfg.get("auth_token") or "").strip()
        if not token:
            raise ValueError(
                "google_sheets connector requires auth_token (shared SecretBox 에 admin 등록 필요)",
            )

        raw_ids = crawl_cfg.get("spreadsheet_ids") or []
        if isinstance(raw_ids, str):
            raw_ids = [s.strip() for s in raw_ids.split(",") if s.strip()]
        ids = tuple(str(s).strip() for s in raw_ids if str(s).strip())
        if not ids:
            raise ValueError(
                "google_sheets connector requires crawl_config.spreadsheet_ids "
                "(예: ['1AbCdEf...'])",
            )

        return cls(
            auth_token=token,
            spreadsheet_ids=ids,
            max_rows_per_sheet=int(crawl_cfg.get("max_rows_per_sheet") or 5000),
            max_cols=int(crawl_cfg.get("max_cols") or 50),
            name=str(source.get("name") or ""),
        )
