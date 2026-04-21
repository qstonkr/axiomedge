"""Notion knowledge connector.

Notion API v1 (https://api.notion.com/v1) — page tree BFS crawl. Auth 는
Internal Integration token (``secret_xxx``) 기반 — admin UI 에서 SecretBox
로 입력 후 connector launcher 가 inject.

지원 블록 타입 (markdown 변환):
- paragraph, heading_1/2/3, bulleted_list_item, numbered_list_item
- to_do, code, quote, callout, divider, child_page (link 만, 재귀 BFS 큐)

미지원 (현 시점): toggle, table, embed, file, image, equation, synced_block.
필요해지면 ``_blocks_to_markdown`` 에 case 추가.
"""

from .client import NotionAPIError, NotionClient
from .config import NotionConnectorConfig
from .connector import NotionConnector

__all__ = [
    "NotionAPIError",
    "NotionClient",
    "NotionConnector",
    "NotionConnectorConfig",
]
