"""Confluence crawler data models.

Dataclasses representing extracted page content, attachments, and crawl results
from Confluence spaces. Originally defined in ``scripts/confluence_full_crawler.py``
and extracted here for reuse across the ingestion pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Attachment parsing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AttachmentOCRPolicy:
    attachment_ocr_mode: str
    ocr_min_text_chars: int
    ocr_max_pdf_pages: int
    ocr_max_ppt_slides: int
    ocr_max_images_per_attachment: int
    slide_render_enabled: bool
    layout_analysis_enabled: bool


@dataclass
class AttachmentParseResult:
    extracted_text: str
    extracted_tables: list[dict]
    confidence: float
    ocr_mode: str | None = None
    ocr_applied: bool = False
    ocr_skip_reason: str | None = None
    ocr_units_attempted: int = 0
    ocr_units_extracted: int = 0
    ocr_units_deferred: int = 0
    native_text_chars: int = 0
    ocr_text_chars: int = 0


# ---------------------------------------------------------------------------
# Extracted content elements
# ---------------------------------------------------------------------------


@dataclass
class ExtractedTable:
    """추출된 테이블"""
    headers: list[str]
    rows: list[dict[str, str]]
    section: str | None = None
    table_type: str | None = None  # owner, system, schedule, status


@dataclass
class ExtractedMention:
    """추출된 @멘션"""
    user_id: str | None
    display_name: str | None
    context: str  # 멘션 주변 텍스트
    email: str | None = None  # 이메일 주소


@dataclass
class ExtractedEmail:
    """추출된 이메일 링크"""
    email: str
    display_name: str | None
    context: str  # 주변 텍스트


@dataclass
class ExtractedMacro:
    """추출된 Confluence 매크로"""
    macro_type: str  # expand, panel, note, info, warning, status, etc.
    title: str | None
    content: str
    parameters: dict = field(default_factory=dict)


@dataclass
class ExtractedComment:
    """추출된 댓글"""
    comment_id: str
    author: str
    author_email: str | None
    content: str
    created_at: str
    parent_id: str | None = None  # 대댓글인 경우


@dataclass
class ExtractedLabel:
    """추출된 라벨(태그)"""
    name: str
    prefix: str | None = None  # global, my, etc.


@dataclass
class ExtractedLink:
    """추출된 링크"""
    link_type: str  # "internal" or "external"
    target_page_id: str | None = None  # 내부 링크인 경우 페이지 ID
    target_url: str | None = None  # 외부 링크인 경우 URL
    anchor_text: str | None = None  # 링크 텍스트
    context: str = ""  # 주변 텍스트


@dataclass
class ExtractedRestriction:
    """추출된 접근 제한 정보"""
    operation: str  # "read" or "update"
    restriction_type: str  # "user" or "group"
    name: str  # 사용자명 또는 그룹명
    account_id: str | None = None  # 사용자인 경우 account ID


# ---------------------------------------------------------------------------
# Attachment & page content
# ---------------------------------------------------------------------------


@dataclass
class AttachmentContent:
    """첨부파일 내용"""
    id: str
    filename: str
    media_type: str
    file_size: int
    download_path: str | None = None
    download_url: str | None = None
    extracted_text: str | None = None
    extracted_tables: list[dict] = field(default_factory=list)
    ocr_confidence: float | None = None
    parse_error: str | None = None
    has_visual_content: bool = False
    visual_analysis_version: str | None = None
    ocr_mode: str | None = None
    ocr_applied: bool | None = None
    ocr_skip_reason: str | None = None
    ocr_units_attempted: int = 0
    ocr_units_extracted: int = 0
    ocr_units_deferred: int = 0
    native_text_chars: int = 0
    ocr_text_chars: int = 0


@dataclass
class FullPageContent:
    """전체 페이지 내용"""
    # 필수 필드 (기본값 없음)
    page_id: str
    title: str
    # 본문 (3중 저장)
    content_text: str           # 1. 전체 plain text (검색용)
    content_html: str           # 2. HTML 원본 (폴백/재파싱용)
    content_preview: str        # 미리보기 (UI용)
    # 구조
    tables: list[ExtractedTable]
    mentions: list[ExtractedMention]
    sections: list[dict]        # {"level": 1, "title": "...", "content": "..."}
    # 메타
    creator: str
    last_modifier: str
    version: int
    url: str
    created_at: str
    updated_at: str
    # 선택적 필드 (기본값 있음 - 반드시 필수 필드 뒤에 위치)
    content_ir: dict | None = None  # 3. Structured IR (RAG/임베딩용)
    code_blocks: list[dict] = field(default_factory=list)  # 코드 블록
    creator_name: str | None = None
    creator_team: str | None = None
    creator_email: str | None = None  # NEW: 작성자 이메일
    attachments: list[AttachmentContent] = field(default_factory=list)
    # NEW: 추가 메타데이터
    labels: list[ExtractedLabel] = field(default_factory=list)  # 라벨/태그
    comments: list[ExtractedComment] = field(default_factory=list)  # 댓글
    emails: list[ExtractedEmail] = field(default_factory=list)  # 이메일 링크
    macros: list[ExtractedMacro] = field(default_factory=list)  # 매크로
    space_key: str | None = None  # 스페이스 키
    ancestors: list[dict] = field(default_factory=list)  # 상위 페이지 계층
    # NEW: 링크 및 권한
    internal_links: list[ExtractedLink] = field(default_factory=list)  # 내부 문서 링크
    external_links: list[ExtractedLink] = field(default_factory=list)  # 외부 URL 링크
    restrictions: list[ExtractedRestriction] = field(default_factory=list)  # 접근 제한
    version_history: list[dict] = field(default_factory=list)  # 버전 이력 메타데이터


@dataclass
class CrawlSpaceResult:
    """단일 소스 크롤링 결과."""

    pages: list[FullPageContent]
    page_dicts: list[dict]
    interrupted: bool = False
    jsonl_path: str = ""  # 스트리밍 모드에서 사용할 JSONL 경로
    source_key: str = ""  # checkpoint 정리용


# ---------------------------------------------------------------------------
# Conversion helper
# ---------------------------------------------------------------------------


def page_to_dict(p: FullPageContent) -> dict:
    """FullPageContent -> dict 변환 (save_results와 동일 형식)"""
    return {
        "page_id": p.page_id,
        "title": p.title,
        "content_text": p.content_text,
        "content_html": p.content_html,
        "content_ir": p.content_ir,
        "content_preview": p.content_preview,
        "tables": [
            {
                "headers": t.headers,
                "rows": t.rows,
                "table_type": t.table_type,
                "row_count": len(t.rows),
            }
            for t in p.tables
        ],
        "code_blocks": p.code_blocks,
        "mentions": [
            {
                "user_id": m.user_id,
                "display_name": m.display_name,
                "email": m.email,
                "context": m.context,
            }
            for m in p.mentions
        ],
        "sections": p.sections,
        "emails": [
            {"email": e.email, "display_name": e.display_name, "context": e.context}
            for e in p.emails
        ],
        "macros": [
            {
                "macro_type": m.macro_type,
                "title": m.title,
                "content": m.content,
                "parameters": m.parameters,
            }
            for m in p.macros
        ],
        "labels": [{"name": lb.name, "prefix": lb.prefix} for lb in p.labels],
        "comments": [
            {
                "comment_id": c.comment_id,
                "author": c.author,
                "author_email": c.author_email,
                "content": c.content,
                "created_at": c.created_at,
                "parent_id": c.parent_id,
            }
            for c in p.comments
        ],
        "creator": p.creator,
        "creator_name": p.creator_name,
        "creator_team": p.creator_team,
        "creator_email": p.creator_email,
        "last_modifier": p.last_modifier,
        "version": p.version,
        "url": p.url,
        "created_at": p.created_at,
        "updated_at": p.updated_at,
        "space_key": p.space_key,
        "ancestors": p.ancestors,
        "internal_links": [
            {
                "target_page_id": lnk.target_page_id,
                "target_url": lnk.target_url,
                "anchor_text": lnk.anchor_text,
                "context": lnk.context,
            }
            for lnk in p.internal_links
        ],
        "external_links": [
            {
                "target_url": lnk.target_url,
                "anchor_text": lnk.anchor_text,
                "context": lnk.context,
            }
            for lnk in p.external_links
        ],
        "restrictions": [
            {
                "operation": r.operation,
                "restriction_type": r.restriction_type,
                "name": r.name,
                "account_id": r.account_id,
            }
            for r in p.restrictions
        ],
        "version_history": p.version_history,
        "attachments": [
            {
                "id": a.id,
                "filename": a.filename,
                "media_type": a.media_type,
                "file_size": a.file_size,
                "download_path": a.download_path,
                "download_url": a.download_url,
                "extracted_text": a.extracted_text,
                "extracted_tables": a.extracted_tables,
                "ocr_confidence": a.ocr_confidence,
                "parse_error": a.parse_error,
                "has_visual_content": a.has_visual_content,
                "visual_analysis_version": a.visual_analysis_version,
                "ocr_mode": a.ocr_mode,
                "ocr_applied": a.ocr_applied,
                "ocr_skip_reason": a.ocr_skip_reason,
                "ocr_units_attempted": a.ocr_units_attempted,
                "ocr_units_extracted": a.ocr_units_extracted,
                "ocr_units_deferred": a.ocr_units_deferred,
                "native_text_chars": a.native_text_chars,
                "ocr_text_chars": a.ocr_text_chars,
            }
            for a in p.attachments
        ],
    }
