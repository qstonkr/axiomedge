"""Linear (GraphQL) + Salesforce (REST + SOQL + OAuth refresh) connectors."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# Catalog meta sync
# ---------------------------------------------------------------------------


class TestCatalogMeta:
    def test_linear_per_user(self):
        from src.connectors.catalog_meta import (
            PER_USER_TOKEN_CONNECTORS,
            is_per_user_token_connector,
        )
        assert "linear" in PER_USER_TOKEN_CONNECTORS
        assert is_per_user_token_connector("linear")

    def test_salesforce_shared(self):
        from src.connectors.catalog_meta import (
            SHARED_TOKEN_CONNECTORS,
            is_shared_token_connector,
        )
        assert "salesforce" in SHARED_TOKEN_CONNECTORS
        assert is_shared_token_connector("salesforce")


# ---------------------------------------------------------------------------
# Linear config + GraphQL fetch (mocked)
# ---------------------------------------------------------------------------


class TestLinearConfig:
    def test_missing_token(self):
        from src.connectors.linear.config import LinearConnectorConfig
        with pytest.raises(ValueError, match="auth_token"):
            LinearConnectorConfig.from_source({"crawl_config": {}})

    def test_team_keys_uppercased(self):
        from src.connectors.linear.config import LinearConnectorConfig
        cfg = LinearConnectorConfig.from_source({
            "crawl_config": {"auth_token": "lin_x", "team_keys": "eng,design"},
        })
        assert cfg.team_keys == ("ENG", "DESIGN")

    def test_days_back_zero_preserved(self):
        from src.connectors.linear.config import LinearConnectorConfig
        cfg = LinearConnectorConfig.from_source({
            "crawl_config": {"auth_token": "lin_x", "days_back": 0},
        })
        assert cfg.days_back == 0


class TestLinearAuth:
    def test_raw_key_no_bearer_prefix(self):
        """Linear 는 ``Authorization: {key}`` (Bearer 없음)."""
        from src.connectors.linear.client import LinearClient
        client = LinearClient("lin_api_xyz")
        assert client._headers["Authorization"] == "lin_api_xyz"
        _run(client.aclose())

    def test_empty_token_raises(self):
        from src.connectors.linear.client import LinearClient
        with pytest.raises(ValueError, match="auth_token"):
            LinearClient("")


class TestLinearFetch:
    @pytest.mark.asyncio
    async def test_fetch_builds_documents_with_comments(self):
        from src.connectors.linear import LinearConnector
        from src.connectors.linear.client import LinearClient

        async def _query(query, variables=None):
            return {"issues": {
                "pageInfo": {"hasNextPage": False, "endCursor": None},
                "nodes": [{
                    "id": "iss-1", "identifier": "ENG-1",
                    "title": "Login bug",
                    "description": "401 on Safari",
                    "url": "https://linear.app/x/issue/ENG-1",
                    "updatedAt": "2026-04-21T00:00:00Z",
                    "state": {"name": "In Progress"},
                    "assignee": {"name": "alice"},
                    "creator": {"name": "alice"},
                    "team": {"key": "ENG", "name": "Eng"},
                    "labels": {"nodes": [{"name": "bug"}]},
                    "comments": {"nodes": [
                        {"body": "checking", "createdAt": "2026-04-21T01:00:00Z",
                         "user": {"name": "bob"}},
                    ]},
                }],
            }}

        client_instance = LinearClient.__new__(LinearClient)
        client_instance.query = _query
        client_instance.aclose = AsyncMock()
        client_instance.__aenter__ = AsyncMock(return_value=client_instance)
        client_instance.__aexit__ = AsyncMock(return_value=None)

        with patch(
            "src.connectors.linear.connector.LinearClient",
            return_value=client_instance,
        ):
            result = await LinearConnector().fetch({
                "auth_token": "lin_x", "team_keys": ["ENG"], "days_back": 0,
            })

        assert result.success
        assert len(result.documents) == 1
        doc = result.documents[0]
        assert "ENG-1" in doc.title
        assert "Login bug" in doc.title
        assert "401 on Safari" in doc.content
        assert "checking" in doc.content
        assert "Labels: bug" in doc.content
        assert doc.metadata["identifier"] == "ENG-1"
        assert doc.metadata["team_key"] == "ENG"


# ---------------------------------------------------------------------------
# Salesforce auth + SOQL paging
# ---------------------------------------------------------------------------


class TestSalesforceAuth:
    def test_invalid_json_raises(self):
        from src.connectors.salesforce.auth import (
            SalesforceAuthError, _parse_credentials,
        )
        with pytest.raises(SalesforceAuthError, match="JSON"):
            _parse_credentials("not json")

    def test_missing_required_field_raises(self):
        from src.connectors.salesforce.auth import (
            SalesforceAuthError, _parse_credentials,
        )
        # missing refresh_token
        creds = json.dumps({
            "instance_url": "https://x", "client_id": "a", "client_secret": "b",
        })
        with pytest.raises(SalesforceAuthError, match="refresh_token"):
            _parse_credentials(creds)

    def test_valid_credentials_parsed(self):
        from src.connectors.salesforce.auth import _parse_credentials
        creds = _parse_credentials(json.dumps({
            "instance_url": "https://x.my.salesforce.com",
            "client_id": "abc", "client_secret": "def",
            "refresh_token": "5Ae",
        }))
        assert creds["client_id"] == "abc"

    @pytest.mark.asyncio
    async def test_refresh_calls_oauth_endpoint(self, monkeypatch):
        """token endpoint 호출 + access_token 추출."""
        from src.connectors.salesforce import auth as auth_mod

        captured: dict = {}

        class _FakeResp:
            status_code = 200
            def json(self):
                return {
                    "access_token": "fake-bearer-xyz",
                    "instance_url": "https://x.my.salesforce.com",
                }

        class _FakeClient:
            def __init__(self, **kwargs):
                pass
            async def __aenter__(self):
                return self
            async def __aexit__(self, *_):
                return None
            async def post(self, url, data=None):
                captured["url"] = url
                captured["data"] = data
                return _FakeResp()

        monkeypatch.setattr(auth_mod.httpx, "AsyncClient", _FakeClient)

        creds = json.dumps({
            "instance_url": "https://x.my.salesforce.com",
            "client_id": "abc", "client_secret": "def",
            "refresh_token": "5Ae",
        })
        token, instance = await auth_mod.refresh_access_token(creds)
        assert token == "fake-bearer-xyz"
        assert instance == "https://x.my.salesforce.com"
        assert captured["url"].endswith("/services/oauth2/token")
        assert captured["data"]["grant_type"] == "refresh_token"
        assert captured["data"]["refresh_token"] == "5Ae"


class TestSalesforceConfig:
    def test_missing_token(self):
        from src.connectors.salesforce.config import SalesforceConnectorConfig
        with pytest.raises(ValueError, match="auth_token"):
            SalesforceConnectorConfig.from_source({"crawl_config": {}})

    def test_missing_soql(self):
        from src.connectors.salesforce.config import SalesforceConnectorConfig
        with pytest.raises(ValueError, match="soql"):
            SalesforceConnectorConfig.from_source({
                "crawl_config": {"auth_token": "tk", "object_name": "Account"},
            })

    def test_missing_object_name(self):
        from src.connectors.salesforce.config import SalesforceConnectorConfig
        with pytest.raises(ValueError, match="object_name"):
            SalesforceConnectorConfig.from_source({
                "crawl_config": {"auth_token": "tk", "soql": "SELECT Id FROM Account"},
            })

    def test_body_fields_string_split(self):
        from src.connectors.salesforce.config import SalesforceConnectorConfig
        cfg = SalesforceConnectorConfig.from_source({
            "crawl_config": {
                "auth_token": "tk",
                "soql": "SELECT Id FROM Account",
                "object_name": "Account",
                "body_fields": "Description, Industry, Phone",
            },
        })
        assert cfg.body_fields == ("Description", "Industry", "Phone")


class TestSalesforceFetch:
    @pytest.mark.asyncio
    async def test_query_builds_record_documents(self, monkeypatch):
        from src.connectors.salesforce import SalesforceConnector
        from src.connectors.salesforce.client import SalesforceClient

        # auth.refresh_access_token mock
        async def _fake_refresh(token_str):
            return ("access-xyz", "https://x.my.salesforce.com")

        monkeypatch.setattr(
            "src.connectors.salesforce.connector.refresh_access_token",
            _fake_refresh,
        )

        async def _query(soql):
            for rec in [
                {"Id": "001AbC", "Name": "Acme Corp", "Description": "Big customer",
                 "Industry": "Tech",
                 "LastModifiedDate": "2026-04-21T12:00:00.000+0000"},
                {"Id": "001Def", "Name": "Beta Inc", "Description": "",
                 "Industry": "Finance",
                 "LastModifiedDate": "2026-04-20T08:00:00.000+0000"},
            ]:
                yield rec

        client_instance = SalesforceClient.__new__(SalesforceClient)
        client_instance.query = _query
        client_instance.aclose = AsyncMock()
        client_instance.__aenter__ = AsyncMock(return_value=client_instance)
        client_instance.__aexit__ = AsyncMock(return_value=None)

        with patch(
            "src.connectors.salesforce.connector.SalesforceClient",
            return_value=client_instance,
        ):
            result = await SalesforceConnector().fetch({
                "auth_token": "any",
                "soql": "SELECT Id, Name, Description, Industry FROM Account",
                "object_name": "Account",
                "body_fields": ["Description", "Industry"],
            })

        assert result.success
        assert len(result.documents) == 2
        # Description 비어도 Industry 만 있어도 doc 생성
        assert "Big customer" in result.documents[0].content
        assert "Tech" in result.documents[0].content
        assert "Finance" in result.documents[1].content
        assert result.documents[0].metadata["object_name"] == "Account"
        assert result.documents[0].source_uri.endswith("/001AbC")
