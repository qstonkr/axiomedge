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

    def test_document_libraries_default_on(self):
        from src.connectors.sharepoint.config import SharePointConnectorConfig
        cfg = SharePointConnectorConfig.from_source({
            "crawl_config": {"auth_token": "tk", "site_id": "s1"},
        })
        assert cfg.include_document_libraries is True
        assert cfg.drive_ids == ()
        assert cfg.max_files == 1000
        assert cfg.include_extensions is None

    def test_document_libraries_off(self):
        from src.connectors.sharepoint.config import SharePointConnectorConfig
        cfg = SharePointConnectorConfig.from_source({
            "crawl_config": {
                "auth_token": "tk", "site_id": "s1",
                "include_document_libraries": False,
            },
        })
        assert cfg.include_document_libraries is False

    def test_drive_ids_string_split(self):
        from src.connectors.sharepoint.config import SharePointConnectorConfig
        cfg = SharePointConnectorConfig.from_source({
            "crawl_config": {
                "auth_token": "tk", "site_id": "s1",
                "drive_ids": "D1, D2",
            },
        })
        assert cfg.drive_ids == ("D1", "D2")

    def test_include_extensions_normalized(self):
        from src.connectors.sharepoint.config import SharePointConnectorConfig
        cfg = SharePointConnectorConfig.from_source({
            "crawl_config": {
                "auth_token": "tk", "site_id": "s1",
                "include_extensions": "pdf, DOCX, .md",
            },
        })
        assert set(cfg.include_extensions or ()) == {".pdf", ".docx", ".md"}


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

    @pytest.mark.asyncio
    async def test_document_library_off_skips_drives(self):
        """include_document_libraries=False 일 때 drive fetch 호출되지 않음."""
        from src.connectors.sharepoint import SharePointConnector

        async def _iter(path, **kwargs):
            # drives 경로 호출 감지 — 불려선 안 됨
            assert "/drives" not in path, (
                f"Drives endpoint should not be called, got {path}"
            )
            return
            yield  # pragma: no cover  (generator 문법)

        client = _make_mock_client({})
        client.iterate_pages = _iter

        connector = SharePointConnector()
        with patch(
            "src.connectors.sharepoint.connector.MSGraphClient",
            return_value=client,
        ):
            result = await connector.fetch({
                "auth_token": "tk", "site_id": "s1",
                "list_ids": [],  # 전체 list → drives 경로도 아님
                "include_document_libraries": False,
            })

        assert result.success
        assert result.metadata["drives_total"] == 0

    @pytest.mark.asyncio
    async def test_document_library_fetches_drives(self):
        """Document Library on 일 때 site drives 열거 + driveItem 다운로드 호출."""
        from src.connectors.sharepoint import SharePointConnector

        async def _iter(path, **kwargs):
            if path == "/sites/s1/lists":
                return
            if "/lists/" in path:
                return
            if path == "/sites/s1/drives":
                for d in [{"id": "drv1"}, {"id": "drv2"}]:
                    yield d
                return
            if "/drives/drv1/root/children" in path:
                yield {
                    "id": "f1", "name": "doc.pdf", "file": {},
                    "size": 1024,
                    "parentReference": {"driveId": "drv1"},
                    "webUrl": "https://x/f1",
                    "lastModifiedDateTime": "2026-04-22T00:00:00Z",
                }
                return
            if "/drives/drv2/root/children" in path:
                return
            return
            yield  # pragma: no cover

        client = _make_mock_client({})
        client.iterate_pages = _iter

        download_call_count = [0]

        async def _fake_download(auth_token, item, **kwargs):
            download_call_count[0] += 1
            from src.core.models import RawDocument
            return RawDocument(
                doc_id=f"sharepoint:{item['parentReference']['driveId']}:{item['id']}",
                title=item["name"],
                content="extracted body",
                source_uri=item.get("webUrl", ""),
                author="",
                content_hash=RawDocument.sha256("extracted body"),
                metadata={"source_type": "sharepoint"},
            )

        connector = SharePointConnector()
        with patch(
            "src.connectors.sharepoint.connector.MSGraphClient",
            return_value=client,
        ), patch(
            "src.connectors.sharepoint.connector.download_drive_item",
            _fake_download,
        ):
            result = await connector.fetch({
                "auth_token": "tk", "site_id": "s1",
                "list_ids": [],
                "include_document_libraries": True,
            })

        assert result.success
        assert download_call_count[0] == 1, "drive item download should be called once"
        assert result.metadata["drives_total"] == 2
        assert result.metadata["drive_files_visited"] == 1
        assert any("doc.pdf" == d.title for d in result.documents)


# ---------------------------------------------------------------------------
# driveItem shared helper
# ---------------------------------------------------------------------------


class TestDownloadDriveItem:
    @pytest.mark.asyncio
    async def test_skip_non_file_item(self):
        from src.connectors._msgraph import download_drive_item
        doc = await download_drive_item(
            "tk", {"id": "x", "folder": {}}, source_type="sharepoint",
        )
        assert doc is None

    @pytest.mark.asyncio
    async def test_skip_oversized(self):
        from src.connectors._msgraph import download_drive_item
        doc = await download_drive_item(
            "tk",
            {
                "id": "x", "name": "big.pdf", "file": {},
                "size": 200 * 1024 * 1024,  # 200MB
                "parentReference": {"driveId": "d1"},
            },
            source_type="sharepoint",
        )
        assert doc is None

    @pytest.mark.asyncio
    async def test_extension_filter_rejects(self):
        from src.connectors._msgraph import download_drive_item
        doc = await download_drive_item(
            "tk",
            {
                "id": "x", "name": "img.png", "file": {}, "size": 1024,
                "parentReference": {"driveId": "d1"},
            },
            source_type="sharepoint",
            include_extensions=(".pdf", ".docx"),
        )
        assert doc is None

    @pytest.mark.asyncio
    async def test_download_and_parse_success(self):
        from src.connectors._msgraph import driveitem as di_mod
        from src.connectors._msgraph import download_drive_item

        class _FakeResp:
            status_code = 200
            content = b"%PDF-1.4 fake"
            text = ""

        class _FakeAsyncClient:
            def __init__(self, *args, **kwargs):
                pass
            async def __aenter__(self):
                return self
            async def __aexit__(self, *a):
                return None
            async def get(self, url):
                return _FakeResp()

        # parse_file 을 mock 해서 실제 PDF 파서 호출 회피
        with patch.object(di_mod.httpx, "AsyncClient", _FakeAsyncClient), \
             patch.object(di_mod, "parse_file", return_value="hello content"):
            doc = await download_drive_item(
                "tk",
                {
                    "id": "f1", "name": "doc.pdf", "file": {},
                    "size": 100,
                    "parentReference": {"driveId": "d1"},
                    "webUrl": "https://x/f1",
                    "lastModifiedDateTime": "2026-04-22T00:00:00Z",
                },
                source_type="sharepoint",
                knowledge_type="test-kb",
            )

        assert doc is not None
        assert doc.doc_id == "sharepoint:d1:f1"
        assert doc.title == "doc.pdf"
        assert doc.content == "hello content"
        assert doc.metadata["source_type"] == "sharepoint"
        assert doc.metadata["knowledge_type"] == "test-kb"

    @pytest.mark.asyncio
    async def test_download_http_error_raises(self):
        from src.connectors._msgraph import driveitem as di_mod
        from src.connectors._msgraph import download_drive_item

        class _FakeResp:
            status_code = 500
            text = "server error"
            content = b""

        class _FakeAsyncClient:
            def __init__(self, *args, **kwargs):
                pass
            async def __aenter__(self):
                return self
            async def __aexit__(self, *a):
                return None
            async def get(self, url):
                return _FakeResp()

        with patch.object(di_mod.httpx, "AsyncClient", _FakeAsyncClient):
            with pytest.raises(RuntimeError, match="sharepoint download failed"):
                await download_drive_item(
                    "tk",
                    {
                        "id": "f1", "name": "doc.pdf", "file": {}, "size": 10,
                        "parentReference": {"driveId": "d1"},
                    },
                    source_type="sharepoint",
                )


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
