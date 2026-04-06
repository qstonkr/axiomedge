"""Structured Intermediate Representation for RAG.

Extracted from ``confluence_full_crawler.py`` (lines 2261-2425).
Generates a structured IR from parsed Confluence page content — sections,
tables, code blocks, and paragraphs — suitable for downstream chunking.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from .html_parsers import CodeBlockExtractor
from .models import ExtractedMention, ExtractedTable

logger = logging.getLogger(__name__)


@dataclass
class StructuredIR:
    """Structured Intermediate Representation for RAG"""

    chunks: list[dict]  # 의미적 청크들
    sections: list[dict]  # 섹션 계층
    tables: list[dict]  # 구조화된 테이블
    code_blocks: list[dict]  # 코드 블록
    mentions: list[dict]  # @멘션


def generate_structured_ir(
    content_text: str,
    content_html: str,
    title: str,
    tables: list[ExtractedTable],
    sections: list[dict],
    mentions: list[ExtractedMention],
) -> dict:
    """
    HTML에서 Structured IR 생성

    RAG 최적화된 청킹을 위한 중간 표현 형식:
    - 테이블은 원자 단위로 보존
    - 코드 블록은 분리하지 않음
    - 섹션 계층 정보 포함
    """
    chunks: list[dict] = []
    chunk_id = 0

    # 코드 블록 추출
    code_extractor = CodeBlockExtractor()
    try:
        code_extractor.feed(content_html)
    except Exception:
        logger.debug("HTML 코드 블록 파싱 중 오류 발생 (무시)")
    code_blocks = code_extractor.code_blocks

    # 1. 섹션 헤더 청크
    for section in sections:
        chunk_id += 1
        chunks.append({
            "chunk_id": f"sec-{chunk_id:04d}",
            "type": "section_header",
            "level": section.get("level", 2),
            "content": section.get("title", ""),
        })

    # 2. 테이블 청크 (원자 단위 - 분리하지 않음)
    for i, table in enumerate(tables):
        chunk_id += 1
        # 테이블을 읽기 쉬운 텍스트로 변환
        table_text = _table_to_markdown(table)
        chunks.append({
            "chunk_id": f"tbl-{chunk_id:04d}",
            "type": "table",
            "headers": table.headers,
            "rows": table.rows,
            "table_type": table.table_type,
            "content": table_text,  # RAG용 텍스트 표현
            "row_count": len(table.rows),
        })

    # 3. 코드 블록 청크 (원자 단위)
    for i, code in enumerate(code_blocks):
        chunk_id += 1
        chunks.append({
            "chunk_id": f"code-{chunk_id:04d}",
            "type": "code_block",
            "language": code.get("language"),
            "content": code.get("content", ""),
        })

    # 4. 본문 단락 청크 (테이블, 코드 제외한 나머지)
    paragraphs = _split_into_paragraphs(content_text)
    for para in paragraphs:
        if len(para.strip()) > 50:  # 최소 청크 크기
            chunk_id += 1
            chunks.append({
                "chunk_id": f"para-{chunk_id:04d}",
                "type": "paragraph",
                "content": para.strip(),
            })

    # IR 구조 반환
    return {
        "title": title,
        "chunk_count": len(chunks),
        "chunks": chunks,
        "sections": [
            {"level": s.get("level", 2), "title": s.get("title", "")}
            for s in sections
        ],
        "tables": [
            {
                "headers": t.headers,
                "rows": t.rows,
                "table_type": t.table_type,
            }
            for t in tables
        ],
        "code_blocks": [
            {
                "language": c.get("language"),
                "content": c.get("content", ""),
            }
            for c in code_blocks
        ],
        "mentions": [
            {
                "user_id": m.user_id,
                "display_name": m.display_name,
                "context": m.context[:100] if m.context else "",
            }
            for m in mentions
        ],
    }


def _table_to_markdown(table: ExtractedTable) -> str:
    """테이블을 Markdown 형식으로 변환 (RAG용)"""
    lines = []
    if table.headers:
        lines.append("| " + " | ".join(table.headers) + " |")
        lines.append("|" + "|".join(["---"] * len(table.headers)) + "|")
    for row in table.rows:
        values = [str(row.get(h, "")).strip().replace("\n", " ") for h in table.headers]
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def _split_into_paragraphs(text: str) -> list[str]:
    """본문을 단락으로 분리"""
    # 빈 줄 2개 이상으로 분리
    paragraphs = re.split(r"\n\n+", text)
    # 너무 긴 단락은 추가 분리
    result = []
    for para in paragraphs:
        if len(para) > 1000:
            # 문장 단위로 분리
            sentences = re.split(r"(?<=[.!?])\s+", para)
            current = ""
            for sent in sentences:
                if len(current) + len(sent) > 800:
                    if current:
                        result.append(current)
                    current = sent
                else:
                    current += (" " if current else "") + sent
            if current:
                result.append(current)
        else:
            result.append(para)
    return result


def extract_creator_info(creator: str) -> tuple[str | None, str | None]:
    """작성자에서 이름/팀 추출"""
    match = re.search(r"([가-힣]+)/([가-힣A-Za-z0-9]+(?:팀|스쿼드|실|부문|센터))", creator)
    if match:
        return match.group(1), match.group(2)
    return None, None
