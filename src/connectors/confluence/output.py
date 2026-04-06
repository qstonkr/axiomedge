"""Confluence crawl result output helpers.

Functions for saving crawled Confluence pages to JSON files, either from
in-memory ``FullPageContent`` objects or by streaming from a JSONL checkpoint.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from .models import FullPageContent, page_to_dict

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared statistics helpers
# ---------------------------------------------------------------------------

def _new_parsed_stats() -> dict[str, int]:
    """Return a fresh attachment-type counter dict."""
    return {
        "pdf": 0, "excel": 0, "word": 0, "ppt": 0,
        "image": 0, "other": 0, "failed": 0,
    }


def _classify_attachment(attachment: dict, parsed_stats: dict[str, int]) -> None:
    """Increment the appropriate counter in *parsed_stats* for one attachment."""
    media_type = str(attachment.get("media_type", "")).lower()
    if attachment.get("parse_error"):
        parsed_stats["failed"] += 1
    elif "pdf" in media_type:
        parsed_stats["pdf"] += 1
    elif "excel" in media_type or "spreadsheet" in media_type:
        parsed_stats["excel"] += 1
    elif "word" in media_type or "document" in media_type:
        parsed_stats["word"] += 1
    elif "presentation" in media_type or "powerpoint" in media_type:
        parsed_stats["ppt"] += 1
    elif "image" in media_type:
        parsed_stats["image"] += 1
    else:
        parsed_stats["other"] += 1


def _accumulate_page_text_length(page: dict) -> int:
    """Return total extracted text length for body + attachments of one page."""
    length = 0
    page_body = page.get("content_text", "")
    if page_body:
        length += len(str(page_body))
    for att in page.get("attachments", []):
        extracted_text = att.get("extracted_text")
        if extracted_text:
            length += len(str(extracted_text))
    return length


def _count_extra_fields(pages: list[dict]) -> dict[str, int]:
    """Sum counts for labels, comments, emails, macros, links, etc."""
    fields = [
        "labels", "comments", "emails", "macros",
        "internal_links", "external_links", "restrictions", "version_history",
    ]
    return {
        f"total_{f}": sum(len(page.get(f, [])) for page in pages)
        for f in fields
    }


def _log_parsed_stats(parsed_stats: dict[str, int], total_text: int) -> None:
    """Log attachment parsing statistics."""
    for file_type, count in parsed_stats.items():
        if count > 0:
            logger.info("  첨부파일 파싱 — %s: %d개", file_type, count)
    logger.info("  총 추출 텍스트: %s자", f"{total_text:,}")


def _log_zero_content_warning(
    total_pages: int, total_text: int, pages: list[dict] | None = None,
) -> None:
    """Log warnings when crawl output has no content."""
    if total_pages == 0:
        logger.error(
            "Output has 0 pages. "
            "Possible causes: (1) --resume with stale checkpoint marking all pages "
            "as visited, (2) Confluence API permission issue, (3) all pages returned "
            "empty body. Try running with --fresh-full to start clean."
        )
    elif total_text == 0:
        pages_with_body = (
            sum(1 for p in pages if p.get("content_text")) if pages else 0
        )
        logger.error(
            "%d pages collected but total extracted text is 0 chars "
            "(pages with body: %d). "
            "Possible cause: Confluence returns metadata but empty body.storage "
            "(PAT permission issue or view restriction on pages).",
            total_pages, pages_with_body,
        )


def _log_extra_stats(extra: dict[str, int]) -> None:
    """Log non-zero extra field counts."""
    label_map = {
        "total_labels": "라벨(태그)",
        "total_comments": "댓글",
        "total_emails": "이메일 링크",
        "total_macros": "매크로",
        "total_internal_links": "내부 링크",
        "total_external_links": "외부 링크",
        "total_restrictions": "접근 제한",
    }
    if not any(extra.get(k, 0) for k in label_map):
        return
    for key, label in label_map.items():
        val = extra.get(key, 0)
        if val > 0:
            logger.info("  %s: %d개", label, val)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def save_results(
    pages: list[FullPageContent],
    output_path: Path,
    source_info: dict | None = None,
    page_dicts: list[dict] | None = None,
) -> None:
    """결과 저장 (첨부파일 테이블 포함)."""

    serialized_pages = (
        page_dicts if page_dicts is not None else [page_to_dict(page) for page in pages]
    )

    parsed_stats = _new_parsed_stats()
    total_extracted_text_length = 0

    for page in serialized_pages:
        total_extracted_text_length += _accumulate_page_text_length(page)
        for attachment in page.get("attachments", []):
            _classify_attachment(attachment, parsed_stats)

    extra = _count_extra_fields(serialized_pages)

    result = {
        "crawled_at": datetime.now(timezone.utc).isoformat(),
        "source_info": source_info,
        "statistics": {
            "total_pages": len(serialized_pages),
            "total_tables_in_pages": sum(
                len(page.get("tables", [])) for page in serialized_pages
            ),
            "total_code_blocks": sum(
                len(page.get("code_blocks", [])) for page in serialized_pages
            ),
            "total_ir_chunks": sum(
                (page.get("content_ir") or {}).get("chunk_count", 0)
                for page in serialized_pages
            ),
            "total_attachments": sum(
                len(page.get("attachments", [])) for page in serialized_pages
            ),
            "attachment_parsing": parsed_stats,
            "total_extracted_text_chars": total_extracted_text_length,
            **extra,
        },
        "pages": serialized_pages,
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    logger.info("결과 저장: %s", output_path)
    _log_parsed_stats(parsed_stats, total_extracted_text_length)
    _log_zero_content_warning(
        len(serialized_pages), total_extracted_text_length, serialized_pages,
    )
    _log_extra_stats(extra)


def save_results_from_jsonl(
    jsonl_path: Path,
    output_path: Path,
    source_info: dict | None = None,
) -> int:
    """JSONL을 스트리밍으로 JSON 변환 (메모리 효율적, 대규모 크롤링용).

    전체 페이지를 메모리에 로드하지 않고 JSONL -> JSON을 line-by-line 스트리밍.
    통계도 스트리밍 중 계산하여 상수 메모리 사용.

    Returns:
        저장된 페이지 수
    """
    total_pages = 0
    total_tables = 0
    total_code_blocks = 0
    total_ir_chunks = 0
    total_attachments = 0
    parsed_stats = _new_parsed_stats()
    total_extracted_text_length = 0
    extra_counters = {
        "labels": 0, "comments": 0, "emails": 0, "macros": 0,
        "internal_links": 0, "external_links": 0,
        "restrictions": 0, "version_history": 0,
    }
    seen_ids: set[str] = set()

    with open(output_path, "w", encoding="utf-8") as out_f:
        _write_json_header(out_f, source_info)

        first = True
        with open(jsonl_path, "r", encoding="utf-8") as in_f:
            for line in in_f:
                page = _parse_jsonl_line(line, seen_ids)
                if page is None:
                    continue

                if not first:
                    out_f.write(",")
                out_f.write(json.dumps(page, ensure_ascii=False))
                first = False
                total_pages += 1

                total_extracted_text_length += _accumulate_page_text_length(page)

                total_tables += len(page.get("tables", []))
                total_code_blocks += len(page.get("code_blocks", []))
                total_ir_chunks += (page.get("content_ir") or {}).get("chunk_count", 0)

                for key in extra_counters:
                    extra_counters[key] += len(page.get(key, []))

                for att in page.get("attachments", []):
                    total_attachments += 1
                    _classify_attachment(att, parsed_stats)

        out_f.write('],"statistics":')
        stats = {
            "total_pages": total_pages,
            "total_tables_in_pages": total_tables,
            "total_code_blocks": total_code_blocks,
            "total_ir_chunks": total_ir_chunks,
            "total_attachments": total_attachments,
            "attachment_parsing": parsed_stats,
            "total_extracted_text_chars": total_extracted_text_length,
            **{f"total_{k}": v for k, v in extra_counters.items()},
        }
        out_f.write(json.dumps(stats, ensure_ascii=False))
        out_f.write("}")

    logger.info("스트리밍 저장 완료: %s (%d페이지)", output_path, total_pages)
    _log_parsed_stats(parsed_stats, total_extracted_text_length)

    if total_pages == 0:
        logger.error(
            "Output has 0 pages (streaming). "
            "JSONL may be empty or corrupted. Try --fresh-full."
        )
    elif total_extracted_text_length == 0:
        logger.error(
            "%d pages streamed but total extracted text is 0 chars. "
            "Possible cause: Confluence returned empty body.storage for all pages.",
            total_pages,
        )

    return total_pages


# ---------------------------------------------------------------------------
# Streaming I/O helpers
# ---------------------------------------------------------------------------

def _write_json_header(out_f: object, source_info: dict | None) -> None:
    """Write the JSON envelope header (crawled_at, source_info, pages array open)."""
    out_f.write('{"crawled_at":"')
    out_f.write(datetime.now(timezone.utc).isoformat())
    out_f.write('","source_info":')
    out_f.write(json.dumps(source_info, ensure_ascii=False) if source_info else "null")
    out_f.write(',"pages":[')


def _parse_jsonl_line(line: str, seen_ids: set[str]) -> dict | None:
    """Parse a single JSONL line, dedup by page_id. Returns page dict or None."""
    line = line.strip()
    if not line:
        return None
    try:
        page = json.loads(line)
    except Exception:
        return None
    if not isinstance(page, dict):
        return None
    pid = page.get("page_id", "")
    if pid and pid in seen_ids:
        return None
    if pid:
        seen_ids.add(pid)
    return page
