"""NotionConnector — IKnowledgeConnector impl for Notion pages.

BFS from root_page_id. 각 page → blocks 수집 → markdown 변환 → RawDocument.
``child_page`` 블록은 자식 페이지 ID 로 BFS 큐에 push (recursive crawl).

Version fingerprint: ``notion:{root_page_id}:{last_edited_max}`` —
방문한 모든 페이지의 ``last_edited_time`` 중 최댓값. 한 페이지라도 수정되면
fingerprint 가 바뀌어 다음 sync 가 emit. ingestion pipeline 의 content_hash
가 실제 변경분만 적재 (page-level dedup).
"""

from __future__ import annotations

import logging
from collections import deque
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from src.core.models import ConnectorResult, RawDocument

from .client import NotionAPIError, NotionClient
from .config import NotionConnectorConfig

logger = logging.getLogger(__name__)

_FINGERPRINT_PREFIX = "notion:"


class NotionConnector:
    """Notion workspace BFS crawler — ``IKnowledgeConnector`` 구현."""

    def __init__(self) -> None:
        pass

    @property
    def source_type(self) -> str:
        return "notion"

    async def health_check(self) -> bool:
        # 외부 호출 없이 self-test — token 유무는 fetch 시점에 검증.
        return True

    async def fetch(
        self,
        config: dict[str, Any],
        *,
        force: bool = False,  # noqa: ARG002 — fingerprint 비교 안 함 (현 시점)
        last_fingerprint: str | None = None,  # noqa: ARG002
    ) -> ConnectorResult:
        try:
            cfg = NotionConnectorConfig.from_source(
                {"crawl_config": config, **config},
            )
        except ValueError as exc:
            return ConnectorResult(
                success=False, source_type=self.source_type, error=str(exc),
            )

        documents: list[RawDocument] = []
        visited: set[str] = set()
        last_edited_max: datetime | None = None

        async with NotionClient(cfg.auth_token) as client:
            try:
                queue: deque[tuple[str, int]] = deque([(cfg.root_page_id, 0)])
                while queue:
                    page_id, depth = queue.popleft()
                    if page_id in visited:
                        continue
                    visited.add(page_id)
                    if depth > cfg.max_depth:
                        continue
                    try:
                        page = await client.get_page(page_id)
                    except NotionAPIError as e:
                        logger.warning(
                            "notion: failed to fetch page %s: %s", page_id, e,
                        )
                        continue
                    if page.get("archived") and not cfg.include_archived:
                        continue

                    try:
                        blocks = await client.list_all_blocks(
                            page_id, page_size=cfg.page_size,
                        )
                    except NotionAPIError as e:
                        logger.warning(
                            "notion: failed to fetch blocks for %s: %s", page_id, e,
                        )
                        continue

                    title = _extract_page_title(page)
                    body = _blocks_to_markdown(blocks)
                    child_ids = _extract_child_page_ids(blocks)
                    edited = _parse_iso_date(page.get("last_edited_time"))
                    if edited and (last_edited_max is None or edited > last_edited_max):
                        last_edited_max = edited

                    if body.strip():
                        documents.append(_build_document(
                            page=page, page_id=page_id, title=title,
                            body=body, source_name=cfg.name, edited=edited,
                        ))

                    for cid in child_ids:
                        cid_norm = cid.replace("-", "")
                        if cid_norm not in visited:
                            queue.append((cid_norm, depth + 1))

            except NotionAPIError as exc:
                return ConnectorResult(
                    success=False, source_type=self.source_type,
                    error=str(exc), documents=documents,
                )

        fingerprint = (
            f"{_FINGERPRINT_PREFIX}{cfg.root_page_id}:"
            f"{last_edited_max.isoformat() if last_edited_max else 'none'}"
        )

        return ConnectorResult(
            success=True, source_type=self.source_type,
            documents=documents, version_fingerprint=fingerprint,
            metadata={
                "root_page_id": cfg.root_page_id,
                "pages_visited": len(visited),
                "documents_emitted": len(documents),
                "max_depth": cfg.max_depth,
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


# ---------------------------------------------------------------------------
# Helpers — 블록 → markdown / page metadata 추출
# ---------------------------------------------------------------------------


def _extract_page_title(page: dict[str, Any]) -> str:
    """Notion page 의 title property 추출 — DB row 면 'Name' 또는 첫 title prop."""
    props = page.get("properties") or {}
    for prop in props.values():
        if prop.get("type") == "title":
            return _rich_text_to_plain(prop.get("title") or [])
    return "(untitled)"


def _rich_text_to_plain(rich_text: list[dict[str, Any]]) -> str:
    """rich_text array → 순수 텍스트 (annotation 무시)."""
    return "".join(rt.get("plain_text", "") for rt in rich_text)


def _rich_text_to_markdown(rich_text: list[dict[str, Any]]) -> str:
    """rich_text array → markdown (bold/italic/code/link 처리)."""
    parts: list[str] = []
    for rt in rich_text:
        text = rt.get("plain_text", "")
        if not text:
            continue
        anno = rt.get("annotations") or {}
        if anno.get("code"):
            text = f"`{text}`"
        if anno.get("bold"):
            text = f"**{text}**"
        if anno.get("italic"):
            text = f"*{text}*"
        href = rt.get("href")
        if href:
            text = f"[{text}]({href})"
        parts.append(text)
    return "".join(parts)


def _blocks_to_markdown(blocks: list[dict[str, Any]]) -> str:
    """블록 목록 → markdown 본문. 미지원 타입은 placeholder 주석."""
    lines: list[str] = []
    for b in blocks:
        btype = b.get("type", "")
        body = b.get(btype) or {}
        rich = body.get("rich_text") or []
        text = _rich_text_to_markdown(rich) if rich else ""

        if btype == "paragraph":
            lines.append(text)
        elif btype == "heading_1":
            lines.append(f"# {text}")
        elif btype == "heading_2":
            lines.append(f"## {text}")
        elif btype == "heading_3":
            lines.append(f"### {text}")
        elif btype == "bulleted_list_item":
            lines.append(f"- {text}")
        elif btype == "numbered_list_item":
            lines.append(f"1. {text}")
        elif btype == "to_do":
            checked = "x" if body.get("checked") else " "
            lines.append(f"- [{checked}] {text}")
        elif btype == "quote":
            lines.append(f"> {text}")
        elif btype == "callout":
            icon = (body.get("icon") or {}).get("emoji", "💡")
            lines.append(f"> {icon} {text}")
        elif btype == "code":
            language = body.get("language", "")
            code_text = _rich_text_to_plain(rich)
            lines.append(f"```{language}\n{code_text}\n```")
        elif btype == "divider":
            lines.append("---")
        elif btype == "child_page":
            title = body.get("title", "(untitled child page)")
            lines.append(f"_(child page: {title})_")
        # 미지원 (table/embed/file/image/equation/synced_block 등) — skip.
    return "\n\n".join(line for line in lines if line)


def _extract_child_page_ids(blocks: list[dict[str, Any]]) -> list[str]:
    """``child_page`` 블록의 id (= 자식 페이지 ID) 만 추출."""
    out: list[str] = []
    for b in blocks:
        if b.get("type") == "child_page":
            cid = b.get("id")
            if cid:
                out.append(str(cid))
    return out


def _parse_iso_date(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _build_document(
    *,
    page: dict[str, Any],
    page_id: str,
    title: str,
    body: str,
    source_name: str,
    edited: datetime | None,
) -> RawDocument:
    url = page.get("url") or f"https://www.notion.so/{page_id}"
    created_by = (page.get("created_by") or {}).get("id", "")
    metadata: dict[str, Any] = {
        "source_type": "notion",
        "page_id": page_id,
        "url": url,
        "knowledge_type": source_name or "notion",
    }
    if page.get("parent"):
        metadata["parent_type"] = (page["parent"] or {}).get("type", "")
    return RawDocument(
        doc_id=f"notion:{page_id}",
        title=title,
        content=body,
        source_uri=url,
        author=created_by,
        updated_at=edited,
        content_hash=RawDocument.sha256(body),
        metadata=metadata,
    )
