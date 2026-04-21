"""GoogleSheetsConnector — Sheets v4 grid → markdown table.

각 spreadsheet 의 모든 sheet (worksheet) 를 ``valueRenderOption=FORMATTED_VALUE``
로 가져와 markdown table 변환. 빈 row/col 은 skip. 1 sheet = 1 RawDocument.

Version fingerprint: ``google_sheets:{sha8(ids)}:{count}`` — Sheets API 의
modifiedTime 직접 노출 X (Drive API 호출이 별도라 비용 절약 위해 omit).
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import AsyncIterator
from typing import Any

from src.connectors._google import GoogleAPIError, GoogleClient, resolve_access_token
from src.connectors._google.auth import GoogleAuthError
from src.core.models import ConnectorResult, RawDocument

from .config import GoogleSheetsConnectorConfig

logger = logging.getLogger(__name__)

_BASE_URL = "https://sheets.googleapis.com/v4"
_SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
_FINGERPRINT_PREFIX = "google_sheets:"


class GoogleSheetsConnector:
    """Google Sheets connector — IKnowledgeConnector 구현."""

    def __init__(self) -> None:
        pass

    @property
    def source_type(self) -> str:
        return "google_sheets"

    async def health_check(self) -> bool:
        return True

    async def fetch(
        self,
        config: dict[str, Any],
        *,
        force: bool = False,  # noqa: ARG002
        last_fingerprint: str | None = None,  # noqa: ARG002
    ) -> ConnectorResult:
        try:
            cfg = GoogleSheetsConnectorConfig.from_source(
                {"crawl_config": config, **config},
            )
        except ValueError as exc:
            return ConnectorResult(
                success=False, source_type=self.source_type, error=str(exc),
            )

        try:
            access_token = await resolve_access_token(cfg.auth_token, _SCOPES)
        except GoogleAuthError as e:
            return ConnectorResult(
                success=False, source_type=self.source_type,
                error=f"auth: {e}",
            )

        documents: list[RawDocument] = []
        skipped_ids: list[str] = []

        async with GoogleClient(access_token, base_url=_BASE_URL) as client:
            for ss_id in cfg.spreadsheet_ids:
                try:
                    docs = await self._fetch_spreadsheet(client, cfg, ss_id)
                except GoogleAPIError as e:
                    if e.status in (403, 404):
                        logger.warning(
                            "google_sheets: skip %s (%s)", ss_id, e.reason,
                        )
                        skipped_ids.append(ss_id)
                        continue
                    return ConnectorResult(
                        success=False, source_type=self.source_type,
                        error=f"spreadsheet {ss_id}: {e}", documents=documents,
                    )
                documents.extend(docs)

        ids_hash = hashlib.sha256(
            ",".join(sorted(cfg.spreadsheet_ids)).encode("utf-8"),
        ).hexdigest()[:8]
        fingerprint = f"{_FINGERPRINT_PREFIX}{ids_hash}:{len(documents)}"

        return ConnectorResult(
            success=True, source_type=self.source_type,
            documents=documents, version_fingerprint=fingerprint,
            metadata={
                "spreadsheets_total": len(cfg.spreadsheet_ids),
                "spreadsheets_skipped": skipped_ids,
                "documents_emitted": len(documents),
            },
        )

    async def lazy_fetch(
        self,
        config: dict[str, Any],
        *,
        force: bool = False,
        last_fingerprint: str | None = None,
    ) -> AsyncIterator[RawDocument]:
        result = await self.fetch(
            config, force=force, last_fingerprint=last_fingerprint,
        )
        if not result.success or result.skipped:
            return
        for doc in result.documents:
            yield doc

    async def _fetch_spreadsheet(
        self,
        client: GoogleClient,
        cfg: GoogleSheetsConnectorConfig,
        ss_id: str,
    ) -> list[RawDocument]:
        """1 spreadsheet → N RawDocument (sheet 별)."""
        meta = await client.get(
            f"/spreadsheets/{ss_id}",
            params={"fields": "properties.title,sheets.properties"},
        )
        title = str((meta.get("properties") or {}).get("title") or ss_id)
        sheets = meta.get("sheets") or []

        documents: list[RawDocument] = []
        for sh in sheets:
            sh_props = sh.get("properties") or {}
            sh_title = str(sh_props.get("title") or "Sheet")
            # values.get 는 simple — A1 notation: 시트명 그대로
            range_a1 = f"'{sh_title}'"
            values_resp = await client.get(
                f"/spreadsheets/{ss_id}/values/{range_a1}",
                params={"valueRenderOption": "FORMATTED_VALUE"},
            )
            values = values_resp.get("values") or []
            if not values:
                continue

            body = _values_to_markdown(values, cfg.max_rows_per_sheet, cfg.max_cols)
            if not body.strip():
                continue

            doc_id = f"google_sheets:{ss_id}:{sh_title}"
            documents.append(RawDocument(
                doc_id=doc_id,
                title=f"{title} — {sh_title}",
                content=body,
                source_uri=f"https://docs.google.com/spreadsheets/d/{ss_id}/edit",
                author="",
                updated_at=None,
                content_hash=RawDocument.sha256(body),
                metadata={
                    "source_type": "google_sheets",
                    "spreadsheet_id": ss_id,
                    "spreadsheet_title": title,
                    "sheet_title": sh_title,
                    "rows": min(len(values), cfg.max_rows_per_sheet),
                    "knowledge_type": cfg.name or "google_sheets",
                },
            ))
        return documents


def _values_to_markdown(
    values: list[list[Any]], max_rows: int, max_cols: int,
) -> str:
    """2D values → markdown table. 첫 row = header. 빈 cell = "" 로 채움."""
    if not values:
        return ""
    rows = values[:max_rows]
    width = min(max(len(r) for r in rows), max_cols)
    if width == 0:
        return ""

    def _row(r: list[Any]) -> str:
        cells = [str(r[i]) if i < len(r) else "" for i in range(width)]
        # | 와 newline 은 markdown table 깨므로 escape.
        cells = [c.replace("|", "\\|").replace("\n", " ") for c in cells]
        return "| " + " | ".join(cells) + " |"

    lines = [_row(rows[0])]
    lines.append("| " + " | ".join(["---"] * width) + " |")
    for r in rows[1:]:
        lines.append(_row(r))
    return "\n".join(lines)
