"""Crawl Result Knowledge Connector (local-only).

- Reads crawler JSON/JSONL files from local filesystem.
- Converts each crawled page into a standardized RawDocument.
- Computes deterministic version_fingerprint for change detection.
Usage:
    connector = CrawlResultConnector()
    result = await connector.fetch(
        {"entry_point": "/data/crawl", "source": "infra"},
        force=False,
    )
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.core.models import ConnectorResult, RawDocument

_COMBINED_JSON = "crawl_combined.json"
_COMBINED_JSONL = "crawl_combined.jsonl"

logger = logging.getLogger(__name__)


class CrawlResultConnector:
    """Filesystem-backed connector for crawler JSON/JSONL results."""

    DEFAULT_OUTPUT_DIR = "/data/crawl"

    def __init__(self, default_output_dir: str | None = None) -> None:
        self._default_output_dir = Path(default_output_dir or self.DEFAULT_OUTPUT_DIR)

    @property
    def source_type(self) -> str:
        return "crawl_result"

    async def health_check(self) -> bool:
        await asyncio.sleep(0)
        return True

    async def _load_jsonl_file(
        self,
        fp: Path,
        pages_by_id: dict[str, dict[str, Any]],
        source_infos: list[dict[str, Any]],
    ) -> ConnectorResult | None:
        """Load a JSONL file into pages_by_id. Returns error ConnectorResult on failure."""
        try:
            for obj in await asyncio.to_thread(self._read_jsonl_lines, fp):
                if not isinstance(obj, dict):
                    continue
                if isinstance(obj.get("source_info"), dict):
                    source_infos.append(obj["source_info"])
                if isinstance(obj.get("pages"), list):
                    for page in obj["pages"]:
                        if isinstance(page, dict):
                            self._upsert_page(pages_by_id, page)
                    continue
                self._upsert_page(pages_by_id, obj)
        except (RuntimeError, OSError, json.JSONDecodeError, ValueError) as e:
            return ConnectorResult(
                success=False, source_type=self.source_type,
                error=f"Failed to read crawl JSONL: {fp} ({e})",
            )
        return None

    async def _load_json_file(
        self,
        fp: Path,
        pages_by_id: dict[str, dict[str, Any]],
        source_infos: list[dict[str, Any]],
    ) -> ConnectorResult | None:
        """Load a JSON file into pages_by_id. Returns error ConnectorResult on failure."""
        try:
            raw = await asyncio.to_thread(fp.read_text, encoding="utf-8")
            data = json.loads(raw)
        except (RuntimeError, OSError, json.JSONDecodeError, ValueError) as e:
            return ConnectorResult(
                success=False, source_type=self.source_type,
                error=f"Failed to read crawl JSON: {fp} ({e})",
            )
        if isinstance(data.get("source_info"), dict):
            source_infos.append(data["source_info"])
        pages = data.get("pages", [])
        if isinstance(pages, list):
            for page in pages:
                if isinstance(page, dict):
                    self._upsert_page(pages_by_id, page)
        return None

    def _page_to_document(self, page: dict[str, Any], config: dict[str, Any]) -> RawDocument | None:
        """Convert a page dict to a RawDocument, or None if empty."""
        page_id = str(page.get("page_id") or "")
        if not page_id:
            return None
        content = self._build_content(page)
        if not content:
            return None
        title = str(page.get("title") or "").strip()
        author = str(
            page.get("creator_email") or page.get("creator_name")
            or page.get("creator") or ""
        ).strip()
        return RawDocument(
            doc_id=page_id,
            title=title or f"confluence:{page_id}",
            content=content,
            source_uri=str(page.get("url") or "").strip(),
            author=author,
            updated_at=self._parse_datetime(str(page.get("updated_at") or "")),
            content_hash=RawDocument.sha256(content),
            metadata={
                "knowledge_type": config.get("name", ""),
                "page_id": page_id,
                "space_key": page.get("space_key"),
                "version": page.get("version"),
                "labels": self._label_names(page.get("labels")),
            },
        )

    async def fetch(
        self,
        config: dict[str, Any],
        *,
        force: bool = False,
        last_fingerprint: str | None = None,
    ) -> ConnectorResult:
        input_path = self._resolve_input_path(config)
        source_selector = str(
            config.get("source") or config.get("source_key")
            or config.get("name") or "all"
        ).strip()

        input_files = await asyncio.to_thread(
            self._select_input_files, input_path, source_selector=source_selector
        )
        if not input_files:
            return ConnectorResult(
                success=False, source_type=self.source_type,
                error=f"No crawl JSON files found under: {input_path}",
                metadata={"input_path": str(input_path), "source_selector": source_selector},
            )

        pages_by_id: dict[str, dict[str, Any]] = {}
        source_infos: list[dict[str, Any]] = []

        err = await self._load_all_files(input_files, pages_by_id, source_infos)
        if err is not None:
            return err

        pages_list = sorted(pages_by_id.values(), key=lambda p: str(p.get("page_id") or ""))

        fp_current = self._fingerprint_pages(pages_list)
        if not force and last_fingerprint and last_fingerprint == fp_current:
            return ConnectorResult(
                success=True, source_type=self.source_type, documents=[],
                version_fingerprint=fp_current,
                metadata={"skipped": True, "reason": "No changes detected"},
            )

        documents, empty_pages = self._convert_pages_to_documents(pages_list, config)

        return ConnectorResult(
            success=True, source_type=self.source_type,
            documents=documents, version_fingerprint=fp_current,
            metadata={
                "input_path": str(input_path), "pages_found": len(pages_list),
                "pages_empty": empty_pages, "documents_emitted": len(documents),
            },
        )

    async def _load_all_files(
        self,
        input_files: list,
        pages_by_id: dict[str, dict[str, Any]],
        source_infos: list[dict[str, Any]],
    ) -> ConnectorResult | None:
        """Load all input files into pages_by_id. Returns error result or None."""
        for fp in input_files:
            if fp.suffix.lower() == ".jsonl":
                err = await self._load_jsonl_file(fp, pages_by_id, source_infos)
            else:
                err = await self._load_json_file(fp, pages_by_id, source_infos)
            if err is not None:
                return err
        return None

    def _convert_pages_to_documents(
        self, pages_list: list[dict[str, Any]], config: dict[str, Any],
    ) -> tuple[list, int]:
        """Convert page dicts to RawDocument list. Returns (documents, empty_count)."""
        documents: list = []
        empty_pages = 0
        for page in pages_list:
            doc = self._page_to_document(page, config)
            if doc is None:
                page_id = str(page.get("page_id") or "")
                if page_id:
                    empty_pages += 1
                continue
            documents.append(doc)
        return documents, empty_pages

    async def lazy_fetch(
        self,
        config: dict[str, Any],
        *,
        force: bool = False,
        last_fingerprint: str | None = None,
    ) -> AsyncIterator[RawDocument]:
        result = await self.fetch(config, force=force, last_fingerprint=last_fingerprint)
        if not result.success or result.skipped:
            return
        for doc in result.documents:
            yield doc

    # -----------------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------------

    def _resolve_input_path(self, config: dict[str, Any]) -> Path:
        entry = str(config.get("entry_point") or "").strip()
        if entry:
            return Path(entry)
        out = str(config.get("output_dir") or "").strip()
        if out:
            return Path(out)
        return self._default_output_dir

    @staticmethod
    def _read_jsonl_lines(path: Path) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as f:
            for raw_line in f:
                raw = raw_line.strip()
                if not raw:
                    continue
                try:
                    row = json.loads(raw)
                except (RuntimeError, json.JSONDecodeError, ValueError):
                    continue
                if isinstance(row, dict):
                    rows.append(row)
        return rows

    def _select_input_files(self, input_path: Path, *, source_selector: str) -> list[Path]:
        if input_path.is_file():
            return [input_path]
        if not input_path.is_dir():
            return []

        def _all_candidates(path: Path) -> list[Path]:
            return sorted(
                list(path.glob("crawl_*.json"))
                + list(path.glob("crawl_*.jsonl"))
                + list(path.glob("file_parse_*.jsonl"))
            )

        if source_selector in ("all", "*", "combined"):
            combined = input_path / _COMBINED_JSON
            combined_jsonl = input_path / _COMBINED_JSONL
            if combined.is_file():
                return [combined]
            if combined_jsonl.is_file():
                return [combined_jsonl]
            return [
                p for p in _all_candidates(input_path)
                if p.name not in (_COMBINED_JSON, _COMBINED_JSONL)
            ]

        safe = re.sub(r"[^\w]", "_", source_selector)
        direct = input_path / f"crawl_{safe}.json"
        direct_jsonl = input_path / f"crawl_{safe}.jsonl"
        if direct.is_file():
            return [direct]
        if direct_jsonl.is_file():
            return [direct_jsonl]

        candidates = [
            p for p in _all_candidates(input_path)
            if p.name not in (_COMBINED_JSON, _COMBINED_JSONL)
        ]

        # Try fuzzy match
        safe_norm = safe.lower().replace("_", "")
        fuzzy = [p for p in candidates if safe_norm and safe_norm in p.name.lower().replace("_", "")]
        if fuzzy:
            return fuzzy

        return candidates if candidates else []

    def _upsert_page(self, pages_by_id: dict[str, dict[str, Any]], page: dict[str, Any]) -> None:
        page_id = str(page.get("page_id") or "")
        if not page_id:
            return
        existing = pages_by_id.get(page_id)
        if existing is None or self._page_sort_key(page) >= self._page_sort_key(existing):
            pages_by_id[page_id] = page

    @staticmethod
    def _page_sort_key(page: dict[str, Any]) -> tuple[int, str]:
        try:
            version = int(page.get("version") or 0)
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError):
            version = 0
        return (version, str(page.get("updated_at") or ""))

    @staticmethod
    def _format_attachments(attachments: Any, page_title: str) -> list[str]:
        """Format attachment entries into content parts."""
        if not isinstance(attachments, list):
            return []
        parts: list[str] = []
        for att in attachments:
            if not isinstance(att, dict):
                continue
            extracted = str(att.get("extracted_text") or "").strip()
            if not extracted:
                continue
            filename = str(att.get("filename") or "attachment").strip()
            header = f"[Attachment: {filename}]"
            if page_title:
                header += f" [parent: {page_title}]"
            parts.append(f"{header}\n{extracted}")
        return parts

    @staticmethod
    def _format_comments(comments: Any) -> list[str]:
        """Format comment entries into content parts."""
        if not isinstance(comments, list):
            return []
        parts: list[str] = []
        for comment in comments:
            if not isinstance(comment, dict):
                continue
            text = str(comment.get("content") or "").strip()
            if not text:
                continue
            author = str(
                comment.get("author") or comment.get("author_email") or "unknown"
            ).strip()
            parts.append(f"[Comment: {author}]\n{text}")
        return parts

    def _build_content(self, page: dict[str, Any]) -> str:
        """Build document content from normalized page fields."""
        content_text = str(page.get("content_text") or "").strip()
        parts: list[str] = []
        if content_text:
            parts.append(content_text)

        page_title = str(page.get("title") or "").strip()
        parts.extend(self._format_attachments(page.get("attachments") or [], page_title))
        parts.extend(self._format_comments(page.get("comments") or []))

        return "\n\n".join([p for p in parts if p.strip()]).strip()

    @staticmethod
    def _label_names(labels: Any) -> list[str]:
        if not isinstance(labels, list):
            return []
        names = []
        for label in labels:
            if isinstance(label, dict):
                name = str(label.get("name") or "").strip()
                if name:
                    names.append(name)
        names.sort()
        return names

    def _fingerprint_pages(self, pages: list[dict[str, Any]]) -> str:
        signatures: list[str] = []
        for page in pages:
            page_id = str(page.get("page_id") or "")
            if not page_id:
                continue
            content_text = str(page.get("content_text") or "")
            try:
                version = int(page.get("version") or 0)
            except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError):
                version = 0
            sig = f"{page_id}:{version}:{RawDocument.sha256(content_text)}"
            signatures.append(sig)
        signatures.sort()
        return RawDocument.sha256("\n".join(signatures))

    @staticmethod
    def _parse_datetime(value: str | None) -> datetime | None:
        raw = str(value or "").strip()
        if not raw:
            return None
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        raw = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", raw)
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
