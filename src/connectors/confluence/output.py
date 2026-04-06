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

    # 첨부파일 파싱 통계
    parsed_stats = {
        "pdf": 0,
        "excel": 0,
        "word": 0,
        "ppt": 0,
        "image": 0,
        "other": 0,
        "failed": 0,
    }
    total_extracted_text_length = 0

    for page in serialized_pages:
        # Count page body text
        page_body = page.get("content_text", "")
        if page_body:
            total_extracted_text_length += len(str(page_body))

        for attachment in page.get("attachments", []):
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

            extracted_text = attachment.get("extracted_text")
            if extracted_text:
                total_extracted_text_length += len(str(extracted_text))

    # 추가 통계
    total_labels = sum(len(page.get("labels", [])) for page in serialized_pages)
    total_comments = sum(len(page.get("comments", [])) for page in serialized_pages)
    total_emails = sum(len(page.get("emails", [])) for page in serialized_pages)
    total_macros = sum(len(page.get("macros", [])) for page in serialized_pages)
    total_internal_links = sum(len(page.get("internal_links", [])) for page in serialized_pages)
    total_external_links = sum(len(page.get("external_links", [])) for page in serialized_pages)
    total_restrictions = sum(len(page.get("restrictions", [])) for page in serialized_pages)
    total_version_history = sum(len(page.get("version_history", [])) for page in serialized_pages)

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
            "total_labels": total_labels,
            "total_comments": total_comments,
            "total_emails": total_emails,
            "total_macros": total_macros,
            "total_internal_links": total_internal_links,
            "total_external_links": total_external_links,
            "total_restrictions": total_restrictions,
            "total_version_history": total_version_history,
        },
        "pages": serialized_pages,
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    logger.info("결과 저장: %s", output_path)

    # 파싱 통계 출력
    for file_type, count in parsed_stats.items():
        if count > 0:
            logger.info("  첨부파일 파싱 — %s: %d개", file_type, count)
    logger.info("  총 추출 텍스트: %s자", f"{total_extracted_text_length:,}")

    # Zero-content validation
    if len(serialized_pages) == 0:
        logger.error(
            "Output has 0 pages. "
            "Possible causes: (1) --resume with stale checkpoint marking all pages as visited, "
            "(2) Confluence API permission issue, (3) all pages returned empty body. "
            "Try running with --fresh-full to start clean."
        )
    elif total_extracted_text_length == 0:
        pages_with_body = sum(1 for p in serialized_pages if p.get("content_text"))
        logger.error(
            "%d pages collected but total extracted text is 0 chars "
            "(pages with body: %d). "
            "Possible cause: Confluence returns metadata but empty body.storage "
            "(PAT permission issue or view restriction on pages).",
            len(serialized_pages),
            pages_with_body,
        )

    # 추가 데이터 통계 출력
    has_extra = any([
        total_labels, total_comments, total_emails, total_macros,
        total_internal_links, total_external_links, total_restrictions,
    ])
    if has_extra:
        if total_labels > 0:
            logger.info("  라벨(태그): %d개", total_labels)
        if total_comments > 0:
            logger.info("  댓글: %d개", total_comments)
        if total_emails > 0:
            logger.info("  이메일 링크: %d개", total_emails)
        if total_macros > 0:
            logger.info("  매크로: %d개", total_macros)
        if total_internal_links > 0:
            logger.info("  내부 링크: %d개", total_internal_links)
        if total_external_links > 0:
            logger.info("  외부 링크: %d개", total_external_links)
        if total_restrictions > 0:
            logger.info("  접근 제한: %d개", total_restrictions)


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
    # 통계 카운터
    total_pages = 0
    total_tables = 0
    total_code_blocks = 0
    total_ir_chunks = 0
    total_attachments = 0
    parsed_stats = {
        "pdf": 0,
        "excel": 0,
        "word": 0,
        "ppt": 0,
        "image": 0,
        "other": 0,
        "failed": 0,
    }
    total_extracted_text_length = 0
    total_labels = 0
    total_comments = 0
    total_emails = 0
    total_macros = 0
    total_internal_links = 0
    total_external_links = 0
    total_restrictions = 0
    total_version_history = 0
    seen_ids: set[str] = set()

    with open(output_path, "w", encoding="utf-8") as out_f:
        # JSON header
        out_f.write('{"crawled_at":"')
        out_f.write(datetime.now(timezone.utc).isoformat())
        out_f.write('","source_info":')
        out_f.write(json.dumps(source_info, ensure_ascii=False) if source_info else "null")
        out_f.write(',"pages":[')

        first = True
        with open(jsonl_path, "r", encoding="utf-8") as in_f:
            for line in in_f:
                line = line.strip()
                if not line:
                    continue
                try:
                    page = json.loads(line)
                except Exception:
                    continue
                if not isinstance(page, dict):
                    continue

                pid = page.get("page_id", "")
                if pid and pid in seen_ids:
                    continue
                if pid:
                    seen_ids.add(pid)

                # Write page
                if not first:
                    out_f.write(",")
                out_f.write(json.dumps(page, ensure_ascii=False))
                first = False
                total_pages += 1

                # Count page body text
                page_body = page.get("content_text", "")
                if page_body:
                    total_extracted_text_length += len(str(page_body))

                # Accumulate statistics
                total_tables += len(page.get("tables", []))
                total_code_blocks += len(page.get("code_blocks", []))
                total_ir_chunks += (page.get("content_ir") or {}).get("chunk_count", 0)
                total_labels += len(page.get("labels", []))
                total_comments += len(page.get("comments", []))
                total_emails += len(page.get("emails", []))
                total_macros += len(page.get("macros", []))
                total_internal_links += len(page.get("internal_links", []))
                total_external_links += len(page.get("external_links", []))
                total_restrictions += len(page.get("restrictions", []))
                total_version_history += len(page.get("version_history", []))

                for att in page.get("attachments", []):
                    total_attachments += 1
                    media_type = str(att.get("media_type", "")).lower()
                    if att.get("parse_error"):
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
                    extracted_text = att.get("extracted_text")
                    if extracted_text:
                        total_extracted_text_length += len(str(extracted_text))

        # Close pages array, write statistics, close root object
        out_f.write('],"statistics":')
        stats = {
            "total_pages": total_pages,
            "total_tables_in_pages": total_tables,
            "total_code_blocks": total_code_blocks,
            "total_ir_chunks": total_ir_chunks,
            "total_attachments": total_attachments,
            "attachment_parsing": parsed_stats,
            "total_extracted_text_chars": total_extracted_text_length,
            "total_labels": total_labels,
            "total_comments": total_comments,
            "total_emails": total_emails,
            "total_macros": total_macros,
            "total_internal_links": total_internal_links,
            "total_external_links": total_external_links,
            "total_restrictions": total_restrictions,
            "total_version_history": total_version_history,
        }
        out_f.write(json.dumps(stats, ensure_ascii=False))
        out_f.write("}")

    logger.info("스트리밍 저장 완료: %s (%d페이지)", output_path, total_pages)

    # 파싱 통계 출력
    for file_type, count in parsed_stats.items():
        if count > 0:
            logger.info("  첨부파일 파싱 — %s: %d개", file_type, count)
    logger.info("  총 추출 텍스트: %s자", f"{total_extracted_text_length:,}")

    # Zero-content validation (streaming mode)
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
