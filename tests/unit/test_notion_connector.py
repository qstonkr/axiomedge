"""NotionConnector — config / block-to-markdown / BFS 검증.

실제 Notion API 호출 X — NotionClient 메서드를 mock 으로 교체하고
BFS / blocks_to_markdown / page metadata 추출 로직만 검증.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# Config — required fields + parsing
# ---------------------------------------------------------------------------


class TestNotionConfig:
    def test_missing_token_raises(self):
        from src.connectors.notion.config import NotionConnectorConfig
        with pytest.raises(ValueError, match="auth_token"):
            NotionConnectorConfig.from_source({
                "crawl_config": {"root_page_id": "abc"},
            })

    def test_missing_root_page_id_raises(self):
        from src.connectors.notion.config import NotionConnectorConfig
        with pytest.raises(ValueError, match="root_page_id"):
            NotionConnectorConfig.from_source({
                "crawl_config": {"auth_token": "secret_xxx"},
            })

    def test_root_page_id_strips_hyphens(self):
        from src.connectors.notion.config import NotionConnectorConfig
        cfg = NotionConnectorConfig.from_source({
            "crawl_config": {
                "auth_token": "secret_xxx",
                "root_page_id": "abcd-1234-abcd-1234-abcd-1234-abcd-1234",
            },
        })
        assert "-" not in cfg.root_page_id

    def test_page_size_clamped_to_max(self):
        from src.connectors.notion.config import NotionConnectorConfig
        cfg = NotionConnectorConfig.from_source({
            "crawl_config": {
                "auth_token": "tk", "root_page_id": "abc", "page_size": 999,
            },
        })
        assert cfg.page_size == 100  # clamped


# ---------------------------------------------------------------------------
# Block → markdown 변환
# ---------------------------------------------------------------------------


class TestBlocksToMarkdown:
    def _make_text_block(self, btype: str, text: str, **extra) -> dict[str, Any]:
        body = {"rich_text": [{"plain_text": text, "annotations": {}}]}
        body.update(extra)
        return {"type": btype, btype: body}

    def test_paragraph_heading_list(self):
        from src.connectors.notion.connector import _blocks_to_markdown
        blocks = [
            self._make_text_block("heading_1", "Title"),
            self._make_text_block("paragraph", "Body."),
            self._make_text_block("bulleted_list_item", "item 1"),
            self._make_text_block("bulleted_list_item", "item 2"),
        ]
        out = _blocks_to_markdown(blocks)
        assert "# Title" in out
        assert "Body." in out
        assert "- item 1" in out
        assert "- item 2" in out

    def test_to_do_checked(self):
        from src.connectors.notion.connector import _blocks_to_markdown
        blocks = [{
            "type": "to_do",
            "to_do": {
                "rich_text": [{"plain_text": "task A", "annotations": {}}],
                "checked": True,
            },
        }, {
            "type": "to_do",
            "to_do": {
                "rich_text": [{"plain_text": "task B", "annotations": {}}],
                "checked": False,
            },
        }]
        out = _blocks_to_markdown(blocks)
        assert "- [x] task A" in out
        assert "- [ ] task B" in out

    def test_code_block_preserves_language(self):
        from src.connectors.notion.connector import _blocks_to_markdown
        blocks = [{
            "type": "code",
            "code": {
                "rich_text": [{"plain_text": "print('hi')", "annotations": {}}],
                "language": "python",
            },
        }]
        out = _blocks_to_markdown(blocks)
        assert "```python\nprint('hi')\n```" in out

    def test_annotations_bold_italic_code_link(self):
        from src.connectors.notion.connector import _rich_text_to_markdown
        rt = [
            {"plain_text": "bold", "annotations": {"bold": True}, "href": None},
            {"plain_text": "italic", "annotations": {"italic": True}, "href": None},
            {"plain_text": "code", "annotations": {"code": True}, "href": None},
            {"plain_text": "link", "annotations": {}, "href": "https://x.com"},
        ]
        out = _rich_text_to_markdown(rt)
        assert "**bold**" in out
        assert "*italic*" in out
        assert "`code`" in out
        assert "[link](https://x.com)" in out

    def test_unsupported_block_skipped_silently(self):
        from src.connectors.notion.connector import _blocks_to_markdown
        out = _blocks_to_markdown([
            {"type": "table", "table": {}},
            {"type": "embed", "embed": {"url": "x"}},
        ])
        assert out == ""


# ---------------------------------------------------------------------------
# BFS — child_page 발견 시 큐에 추가
# ---------------------------------------------------------------------------


class TestNotionBFS:
    @pytest.mark.asyncio
    async def test_bfs_visits_children_within_depth(self):
        from src.connectors.notion import NotionConnector
        from src.connectors.notion.client import NotionClient

        # Mock — root → child → grandchild
        async def fake_get_page(page_id):
            return {
                "id": page_id,
                "properties": {"Name": {
                    "type": "title",
                    "title": [{"plain_text": f"Page-{page_id[:4]}"}],
                }},
                "last_edited_time": "2026-04-21T00:00:00Z",
                "url": f"https://www.notion.so/{page_id}",
                "created_by": {"id": "user-1"},
                "parent": {"type": "workspace"},
                "archived": False,
            }

        async def fake_list_all_blocks(page_id, page_size=100):
            if page_id == "root":
                return [
                    {"type": "paragraph",
                     "paragraph": {"rich_text": [{"plain_text": "root body", "annotations": {}}]}},
                    {"type": "child_page", "id": "child", "child_page": {"title": "Child"}},
                ]
            if page_id == "child":
                return [
                    {"type": "paragraph",
                     "paragraph": {"rich_text": [{"plain_text": "child body", "annotations": {}}]}},
                ]
            return []

        # P1-9: NotionClient 가 BaseConnectorClient 상속 — 정상 인스턴스화 후
        # 메서드만 mock 으로 교체. __aenter__/__aexit__ 는 magic method 라
        # 인스턴스 attribute 패치로는 안 잡혀, base 의 라이프사이클을 그대로
        # 사용한다 (httpx.AsyncClient 가 만들어지지만 실제 외부 호출 X).
        client_instance = NotionClient(auth_token="secret_xxx_test")
        client_instance.get_page = AsyncMock(side_effect=fake_get_page)
        client_instance.list_all_blocks = AsyncMock(
            side_effect=fake_list_all_blocks,
        )

        # NotionConnector 안에서 NotionClient(...) 인스턴스화 — patch.
        from unittest.mock import patch

        connector = NotionConnector()
        with patch(
            "src.connectors.notion.connector.NotionClient",
            return_value=client_instance,
        ):
            result = await connector.fetch({
                "auth_token": "secret_xxx", "root_page_id": "root", "max_depth": 2,
            })

        assert result.success
        assert len(result.documents) == 2  # root + child
        assert any("root body" in d.content for d in result.documents)
        assert any("child body" in d.content for d in result.documents)
        assert "notion:root:" in result.version_fingerprint
