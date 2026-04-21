"""Microsoft Graph connectors — SharePoint / OneDrive / Teams.

각 connector 의 config validation + MSGraph API mock + paging/skip 분기 검증.
실제 Graph API 호출 X — MSGraphClient 메서드 mock.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _make_async_iter(items: list[dict[str, Any]]):
    """list → async iterator (MSGraphClient.iterate_pages mock)."""

    async def _gen():
        for it in items:
            yield it

    return _gen


def _make_mock_client(responses: dict[str, list[dict[str, Any]]]):
    """MSGraphClient mock — iterate_pages(path) lookup."""
    from src.connectors._msgraph.client import MSGraphClient

    client = MSGraphClient.__new__(MSGraphClient)

    def _iter(path: str, **kwargs):
        for prefix, items in responses.items():
            if prefix in path:
                return _make_async_iter(items)()
        return _make_async_iter([])()

    client.iterate_pages = _iter
    client.get = AsyncMock()
    client.aclose = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    return client


# ---------------------------------------------------------------------------
# Catalog meta sync
# ---------------------------------------------------------------------------


class TestCatalogMetaSync:
    def test_msgraph_connectors_in_shared_set(self):
        from src.connectors.catalog_meta import (
            SHARED_TOKEN_CONNECTORS,
            is_shared_token_connector,
            is_user_self_service,
        )
        for ct in ("sharepoint", "onedrive", "teams"):
            assert ct in SHARED_TOKEN_CONNECTORS, f"{ct} not in SHARED set"
            assert is_shared_token_connector(ct)
            assert is_user_self_service(ct)


# ---------------------------------------------------------------------------
# SharePoint
# ---------------------------------------------------------------------------


class TestSharePointConfig:
    def test_missing_token_raises(self):
        from src.connectors.sharepoint.config import SharePointConnectorConfig
        with pytest.raises(ValueError, match="auth_token"):
            SharePointConnectorConfig.from_source({"crawl_config": {"site_id": "s1"}})

    def test_missing_site_id_raises(self):
        from src.connectors.sharepoint.config import SharePointConnectorConfig
        with pytest.raises(ValueError, match="site_id"):
            SharePointConnectorConfig.from_source({"crawl_config": {"auth_token": "tk"}})

    def test_string_list_ids_split_on_comma(self):
        from src.connectors.sharepoint.config import SharePointConnectorConfig
        cfg = SharePointConnectorConfig.from_source({
            "crawl_config": {
                "auth_token": "tk", "site_id": "s1",
                "list_ids": "L1, L2 , L3",
            },
        })
        assert cfg.list_ids == ("L1", "L2", "L3")


class TestSharePointFetch:
    @pytest.mark.asyncio
    async def test_skip_forbidden_list_continues_others(self):
        from src.connectors._msgraph.client import MSGraphAPIError
        from src.connectors.sharepoint import SharePointConnector

        client = _make_mock_client({
            # /sites/{id}/lists/L_OK/items 만 hit
            "/lists/L_OK/items": [
                {
                    "id": "i1", "webUrl": "https://x/i1",
                    "fields": {"Title": "T1", "Description": "body 1"},
                    "lastModifiedDateTime": "2026-04-21T00:00:00Z",
                    "createdBy": {"user": {"displayName": "alice"}},
                },
            ],
        })

        # iterate_pages 가 path 별 분기 — L_BAD 호출 시 raise 시뮬레이션
        async def _iter(path, **kwargs):
            if "L_BAD" in path:
                raise MSGraphAPIError("forbidden", status=403, code="accessDenied")
            for it in [
                {
                    "id": "i1", "webUrl": "https://x/i1",
                    "fields": {"Title": "T1", "Description": "body 1"},
                    "lastModifiedDateTime": "2026-04-21T00:00:00Z",
                    "createdBy": {"user": {"displayName": "alice"}},
                },
            ] if "L_OK" in path else []:
                yield it

        # Re-patch with branching iterator
        client.iterate_pages = _iter

        connector = SharePointConnector()
        with patch(
            "src.connectors.sharepoint.connector.MSGraphClient",
            return_value=client,
        ):
            result = await connector.fetch({
                "auth_token": "tk", "site_id": "s1",
                "list_ids": ["L_BAD", "L_OK"],
            })

        assert result.success
        assert "L_BAD" in result.metadata["lists_skipped"]
        assert len(result.documents) == 1
        assert "body 1" in result.documents[0].content


# ---------------------------------------------------------------------------
# OneDrive
# ---------------------------------------------------------------------------


class TestOneDriveConfig:
    def test_missing_drive_path_raises(self):
        from src.connectors.onedrive.config import OneDriveConnectorConfig
        with pytest.raises(ValueError, match="drive_path"):
            OneDriveConnectorConfig.from_source({"crawl_config": {"auth_token": "tk"}})

    def test_default_extensions(self):
        from src.connectors.onedrive.config import OneDriveConnectorConfig
        cfg = OneDriveConnectorConfig.from_source({
            "crawl_config": {"auth_token": "tk", "drive_path": "drives/d1"},
        })
        assert ".pdf" in cfg.include_extensions
        assert ".md" in cfg.include_extensions

    def test_custom_extensions_normalized(self):
        from src.connectors.onedrive.config import OneDriveConnectorConfig
        cfg = OneDriveConnectorConfig.from_source({
            "crawl_config": {
                "auth_token": "tk", "drive_path": "drives/d1",
                "include_extensions": "pdf, MD, .txt",
            },
        })
        assert set(cfg.include_extensions) == {".pdf", ".md", ".txt"}


# ---------------------------------------------------------------------------
# Teams
# ---------------------------------------------------------------------------


class TestTeamsConfig:
    def test_missing_team_id_raises(self):
        from src.connectors.teams.config import TeamsConnectorConfig
        with pytest.raises(ValueError, match="team_id"):
            TeamsConnectorConfig.from_source({"crawl_config": {"auth_token": "tk"}})


class TestTeamsFormat:
    def test_html_body_stripped(self):
        from src.connectors.teams.connector import _format_message
        msg = {
            "body": {"contentType": "html", "content": "<p>Hello <b>world</b></p>"},
            "from": {"user": {"displayName": "alice"}},
            "createdDateTime": "2026-04-21T10:30:00Z",
        }
        out = _format_message(msg)
        assert "<p>" not in out
        assert "Hello" in out and "world" in out
        assert "alice" in out

    def test_empty_body_returns_empty(self):
        from src.connectors.teams.connector import _format_message
        assert _format_message({"body": {"content": ""}}) == ""

    @pytest.mark.asyncio
    async def test_skip_forbidden_channel(self):
        from src.connectors._msgraph.client import MSGraphAPIError
        from src.connectors.teams import TeamsConnector
        from src.connectors.teams.connector import TeamsConnectorConfig  # noqa: F401

        async def _iter(path, **kwargs):
            if "C_BAD" in path:
                raise MSGraphAPIError("forbidden", status=403, code="forbidden")
            if "C_OK/messages" in path and "/replies" not in path:
                for m in [{
                    "id": "m1",
                    "body": {"contentType": "text", "content": "hello team"},
                    "from": {"user": {"displayName": "bob"}},
                    "createdDateTime": "2026-04-21T01:00:00Z",
                    "webUrl": "https://teams/m1",
                }]:
                    yield m

        client = _make_mock_client({})
        client.iterate_pages = _iter

        connector = TeamsConnector()
        with patch(
            "src.connectors.teams.connector.MSGraphClient",
            return_value=client,
        ):
            result = await connector.fetch({
                "auth_token": "tk", "team_id": "T1",
                "channel_ids": ["C_BAD", "C_OK"],
                "days_back": 0, "include_replies": False,
            })

        assert result.success
        assert "C_BAD" in result.metadata["channels_skipped"]
        assert len(result.documents) == 1
        assert "hello team" in result.documents[0].content


# ---------------------------------------------------------------------------
# MSGraphClient — paging
# ---------------------------------------------------------------------------


class TestMSGraphClientPaging:
    def test_invalid_token_raises(self):
        from src.connectors._msgraph.client import MSGraphClient
        with pytest.raises(ValueError, match="access_token"):
            MSGraphClient("")
