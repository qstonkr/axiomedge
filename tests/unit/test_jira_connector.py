"""Jira connector — config + ADF text + auth 분기 + JQL fetch (mock)."""

from __future__ import annotations

import asyncio
import base64
from unittest.mock import AsyncMock, patch

import pytest


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# Catalog meta
# ---------------------------------------------------------------------------


class TestJiraCatalog:
    def test_jira_in_per_user_set(self):
        from src.connectors.catalog_meta import (
            PER_USER_TOKEN_CONNECTORS,
            is_per_user_token_connector,
            is_user_self_service,
        )
        assert "jira" in PER_USER_TOKEN_CONNECTORS
        assert is_per_user_token_connector("jira")
        assert is_user_self_service("jira")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


class TestJiraConfig:
    def test_missing_token_raises(self):
        from src.connectors.jira.config import JiraConnectorConfig
        with pytest.raises(ValueError, match="auth_token"):
            JiraConnectorConfig.from_source({
                "crawl_config": {"base_url": "https://x.atlassian.net"},
            })

    def test_missing_base_url_raises(self):
        from src.connectors.jira.config import JiraConnectorConfig
        with pytest.raises(ValueError, match="base_url"):
            JiraConnectorConfig.from_source({
                "crawl_config": {"auth_token": "t"},
            })

    def test_invalid_api_version_raises(self):
        from src.connectors.jira.config import JiraConnectorConfig
        with pytest.raises(ValueError, match="api_version"):
            JiraConnectorConfig.from_source({
                "crawl_config": {
                    "auth_token": "t", "base_url": "https://x.atlassian.net",
                    "api_version": "99",
                },
            })

    def test_base_url_trailing_slash_stripped(self):
        from src.connectors.jira.config import JiraConnectorConfig
        cfg = JiraConnectorConfig.from_source({
            "crawl_config": {
                "auth_token": "t",
                "base_url": "https://x.atlassian.net/",
            },
        })
        assert cfg.base_url == "https://x.atlassian.net"


# ---------------------------------------------------------------------------
# Auth 분기 — Cloud Basic vs Server PAT Bearer
# ---------------------------------------------------------------------------


class TestJiraAuth:
    def test_cloud_email_uses_basic_auth(self):
        from src.connectors.jira.client import JiraClient
        client = JiraClient(
            base_url="https://x.atlassian.net",
            auth_token="api_token_xxx",
            email="alice@co.com",
        )
        auth = client._headers["Authorization"]
        assert auth.startswith("Basic ")
        # base64 decode 후 email:token 형식 검증
        decoded = base64.b64decode(auth[len("Basic "):]).decode()
        assert decoded == "alice@co.com:api_token_xxx"
        _run(client.aclose())

    def test_server_pat_uses_bearer(self):
        from src.connectors.jira.client import JiraClient
        client = JiraClient(
            base_url="https://jira.internal.co",
            auth_token="server_pat_xxx",
            email="",  # Server/DC mode
        )
        assert client._headers["Authorization"] == "Bearer server_pat_xxx"
        _run(client.aclose())

    def test_empty_token_raises(self):
        from src.connectors.jira.client import JiraClient
        with pytest.raises(ValueError, match="auth_token"):
            JiraClient(base_url="https://x", auth_token="")


# ---------------------------------------------------------------------------
# ADF 재귀 text 추출
# ---------------------------------------------------------------------------


class TestADF:
    def test_paragraph_and_heading(self):
        from src.connectors.jira.connector import _adf_to_text
        adf = {"type": "doc", "content": [
            {"type": "heading",
             "content": [{"type": "text", "text": "Title"}]},
            {"type": "paragraph",
             "content": [{"type": "text", "text": "body"}]},
        ]}
        out = _adf_to_text(adf).strip()
        assert "Title" in out
        assert "body" in out

    def test_nested_lists(self):
        from src.connectors.jira.connector import _adf_to_text
        adf = {"type": "doc", "content": [
            {"type": "bulletList", "content": [
                {"type": "listItem", "content": [
                    {"type": "paragraph",
                     "content": [{"type": "text", "text": "item1"}]},
                ]},
                {"type": "listItem", "content": [
                    {"type": "paragraph",
                     "content": [{"type": "text", "text": "item2"}]},
                ]},
            ]},
        ]}
        out = _adf_to_text(adf)
        assert "item1" in out
        assert "item2" in out

    def test_hard_break_inserts_newline(self):
        from src.connectors.jira.connector import _adf_to_text
        adf = {"type": "paragraph", "content": [
            {"type": "text", "text": "line1"},
            {"type": "hardBreak"},
            {"type": "text", "text": "line2"},
        ]}
        out = _adf_to_text(adf)
        assert "line1\nline2" in out

    def test_v2_passthrough(self):
        from src.connectors.jira.connector import _body_to_text
        assert _body_to_text("wiki *bold* text", "2") == "wiki *bold* text"

    def test_v3_string_passthrough(self):
        """v3 라도 body 가 string 이면 그대로 반환 (server REST 가 string 반환 가능)."""
        from src.connectors.jira.connector import _body_to_text
        assert _body_to_text("plain", "3") == "plain"

    def test_none_returns_empty(self):
        from src.connectors.jira.connector import _body_to_text
        assert _body_to_text(None, "3") == ""


# ---------------------------------------------------------------------------
# Fetch — search_issues mock
# ---------------------------------------------------------------------------


class TestJiraFetch:
    @pytest.mark.asyncio
    async def test_fetch_builds_documents_with_comments(self):
        from src.connectors.jira import JiraConnector
        from src.connectors.jira.client import JiraClient

        async def _search(jql, **kwargs):
            issues = [{
                "id": "10001", "key": "ENG-1",
                "fields": {
                    "summary": "Login bug",
                    "description": {"type": "doc", "content": [
                        {"type": "paragraph",
                         "content": [{"type": "text", "text": "401 on Safari"}]},
                    ]},
                    "status": {"name": "In Progress"},
                    "reporter": {"displayName": "alice"},
                    "updated": "2026-04-21T09:00:00Z",
                    "comment": {"comments": [
                        {"author": {"displayName": "bob"},
                         "created": "2026-04-21T10:00:00Z",
                         "body": {"type": "doc", "content": [
                             {"type": "paragraph",
                              "content": [{"type": "text", "text": "checking session token"}]},
                         ]}},
                    ]},
                },
            }]
            for issue in issues:
                yield issue

        client_instance = JiraClient.__new__(JiraClient)
        client_instance.search_issues = _search
        client_instance.aclose = AsyncMock()
        client_instance.__aenter__ = AsyncMock(return_value=client_instance)
        client_instance.__aexit__ = AsyncMock(return_value=None)

        connector = JiraConnector()
        with patch(
            "src.connectors.jira.connector.JiraClient",
            return_value=client_instance,
        ):
            result = await connector.fetch({
                "auth_token": "t", "base_url": "https://x.atlassian.net",
                "email": "a@x.com",
                "jql": "project = ENG", "api_version": "3",
            })

        assert result.success
        assert len(result.documents) == 1
        doc = result.documents[0]
        assert "ENG-1" in doc.title
        assert "Login bug" in doc.title
        assert "401 on Safari" in doc.content
        assert "checking session token" in doc.content  # comment 포함
        assert "Status: In Progress" in doc.content
        assert doc.author == "alice"
        assert doc.metadata["issue_key"] == "ENG-1"
        assert doc.metadata["status"] == "In Progress"
        assert doc.source_uri == "https://x.atlassian.net/browse/ENG-1"

    @pytest.mark.asyncio
    async def test_include_comments_false_skips_comments(self):
        from src.connectors.jira import JiraConnector
        from src.connectors.jira.client import JiraClient

        async def _search(jql, **kwargs):
            yield {
                "id": "10002", "key": "ENG-2",
                "fields": {
                    "summary": "S",
                    "description": "plain string description",
                    "status": {"name": "Open"},
                    "reporter": {"displayName": "carol"},
                    "updated": "2026-04-20T00:00:00Z",
                    "comment": {"comments": [
                        {"author": {"displayName": "dan"},
                         "created": "2026-04-20T01:00:00Z",
                         "body": "should not appear"},
                    ]},
                },
            }

        client_instance = JiraClient.__new__(JiraClient)
        client_instance.search_issues = _search
        client_instance.aclose = AsyncMock()
        client_instance.__aenter__ = AsyncMock(return_value=client_instance)
        client_instance.__aexit__ = AsyncMock(return_value=None)

        with patch(
            "src.connectors.jira.connector.JiraClient",
            return_value=client_instance,
        ):
            result = await JiraConnector().fetch({
                "auth_token": "t", "base_url": "https://x.atlassian.net",
                "jql": "key=ENG-2", "include_comments": False,
                "api_version": "2",  # v2: description 이 string
            })

        assert result.success
        assert "should not appear" not in result.documents[0].content
        assert "plain string description" in result.documents[0].content
