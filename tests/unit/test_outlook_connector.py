"""Outlook connector — config + body/email helper + MSGraph mock fetch."""

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


# ---------------------------------------------------------------------------
# Catalog meta sync — outlook 이 SHARED set 안에 있는지
# ---------------------------------------------------------------------------


class TestCatalogMeta:
    def test_outlook_in_shared_set(self):
        from src.connectors.catalog_meta import (
            SHARED_TOKEN_CONNECTORS,
            is_shared_token_connector,
            is_user_self_service,
        )
        assert "outlook" in SHARED_TOKEN_CONNECTORS
        assert is_shared_token_connector("outlook")
        assert is_user_self_service("outlook")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


class TestOutlookConfig:
    def test_missing_token_raises(self):
        from src.connectors.outlook.config import OutlookConnectorConfig
        with pytest.raises(ValueError, match="auth_token"):
            OutlookConnectorConfig.from_source({"crawl_config": {}})

    def test_defaults(self):
        from src.connectors.outlook.config import OutlookConnectorConfig
        cfg = OutlookConnectorConfig.from_source({
            "crawl_config": {"auth_token": "tk"},
        })
        assert cfg.user_id == "me"
        assert cfg.folder == "inbox"
        assert cfg.days_back == 30
        assert cfg.include_body is True


# ---------------------------------------------------------------------------
# Helpers — body/email 추출
# ---------------------------------------------------------------------------


class TestOutlookHelpers:
    def test_html_body_stripped(self):
        from src.connectors.outlook.connector import _extract_body
        out = _extract_body({"contentType": "html", "content": "<p>hi <b>bold</b></p>"})
        assert "<p>" not in out
        assert "hi" in out and "bold" in out

    def test_text_body_preserved(self):
        from src.connectors.outlook.connector import _extract_body
        assert _extract_body({"contentType": "text", "content": "raw plain"}) == "raw plain"

    def test_empty_body(self):
        from src.connectors.outlook.connector import _extract_body
        assert _extract_body({}) == ""
        assert _extract_body({"content": ""}) == ""

    def test_email_with_name(self):
        from src.connectors.outlook.connector import _extract_email
        out = _extract_email({"emailAddress": {"name": "Alice", "address": "a@x.com"}})
        assert out == "Alice <a@x.com>"

    def test_email_address_only(self):
        from src.connectors.outlook.connector import _extract_email
        assert _extract_email({"emailAddress": {"address": "b@y.com"}}) == "b@y.com"

    def test_email_invalid(self):
        from src.connectors.outlook.connector import _extract_email
        assert _extract_email({}) == ""
        assert _extract_email("not a dict") == ""


# ---------------------------------------------------------------------------
# Fetch — MSGraph mock + filter/days_back 검증
# ---------------------------------------------------------------------------


class TestOutlookFetch:
    @pytest.mark.asyncio
    async def test_fetch_extracts_messages_and_filter_applied(self):
        from src.connectors._msgraph.client import MSGraphClient
        from src.connectors.outlook import OutlookConnector

        captured_params: dict[str, Any] = {}

        async def _iter(path, **kwargs):
            captured_params.update(kwargs.get("params") or {})
            messages = [
                {
                    "id": "m1", "subject": "Quarterly Report",
                    "from": {"emailAddress": {"name": "Boss", "address": "boss@co.com"}},
                    "receivedDateTime": "2026-04-21T10:30:00Z",
                    "body": {"contentType": "html", "content": "<p>Q2 numbers</p>"},
                    "webLink": "https://outlook/m1",
                    "isRead": True,
                    "importance": "normal",
                },
                {
                    "id": "m2", "subject": "(no subject)",
                    "from": {"emailAddress": {"address": "team@co.com"}},
                    "receivedDateTime": "2026-04-20T08:00:00Z",
                    "body": {"contentType": "text", "content": "plain body"},
                    "webLink": "https://outlook/m2",
                    "isRead": False,
                    "importance": "high",
                },
            ]
            for m in messages:
                yield m

        # A1: BaseConnectorClient 상속 → 정상 인스턴스화 + iterate_pages mock.
        client_instance = MSGraphClient(access_token="test-token-stub")
        client_instance.iterate_pages = _iter

        connector = OutlookConnector()
        with patch(
            "src.connectors.outlook.connector.MSGraphClient",
            return_value=client_instance,
        ):
            result = await connector.fetch({
                "auth_token": "tk", "user_id": "alice@co.com",
                "folder": "inbox", "days_back": 7,
            })

        assert result.success
        assert len(result.documents) == 2
        # days_back=7 → $filter 가 receivedDateTime ge ... 형태로 만들어짐
        assert "$filter" in captured_params
        assert "receivedDateTime ge" in captured_params["$filter"]
        # ordering desc
        assert captured_params["$orderby"] == "receivedDateTime desc"

        # html body strip 검증
        assert "<p>" not in result.documents[0].content
        assert "Q2 numbers" in result.documents[0].content
        # author 형식
        assert "Boss" in result.documents[0].author

    @pytest.mark.asyncio
    async def test_days_back_zero_skips_filter(self):
        from src.connectors._msgraph.client import MSGraphClient
        from src.connectors.outlook import OutlookConnector

        captured_params: dict[str, Any] = {}

        async def _iter(path, **kwargs):
            captured_params.update(kwargs.get("params") or {})
            return
            yield  # pragma: no cover (empty generator)

        # A1: BaseConnectorClient 상속 → 정상 인스턴스화 + iterate_pages mock.
        client_instance = MSGraphClient(access_token="test-token-stub")
        client_instance.iterate_pages = _iter

        with patch(
            "src.connectors.outlook.connector.MSGraphClient",
            return_value=client_instance,
        ):
            result = await OutlookConnector().fetch({
                "auth_token": "tk", "days_back": 0,
            })

        assert result.success
        # days_back=0 → $filter 없음 (전체 메일)
        assert "$filter" not in captured_params
