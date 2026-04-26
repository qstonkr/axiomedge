"""Asana / Dropbox / Box connectors — config + basic fetch behavior."""

from __future__ import annotations

import asyncio

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
    def test_asana_in_per_user_set(self):
        from src.connectors.catalog_meta import (
            PER_USER_TOKEN_CONNECTORS,
            is_per_user_token_connector,
        )
        assert "asana" in PER_USER_TOKEN_CONNECTORS
        assert is_per_user_token_connector("asana")

    def test_dropbox_box_in_shared_set(self):
        from src.connectors.catalog_meta import (
            SHARED_TOKEN_CONNECTORS,
            is_shared_token_connector,
            is_user_self_service,
        )
        for ct in ("dropbox", "box"):
            assert ct in SHARED_TOKEN_CONNECTORS
            assert is_shared_token_connector(ct)
            assert is_user_self_service(ct)


# ---------------------------------------------------------------------------
# Asana config
# ---------------------------------------------------------------------------


class TestAsanaConfig:
    def test_missing_token_raises(self):
        from src.connectors.asana.config import AsanaConnectorConfig
        with pytest.raises(ValueError, match="auth_token"):
            AsanaConnectorConfig.from_source({"crawl_config": {"workspace_gid": "1"}})

    def test_missing_scope_raises(self):
        """workspace_gid + project_gids 둘 다 비어있으면 raise."""
        from src.connectors.asana.config import AsanaConnectorConfig
        with pytest.raises(ValueError, match="workspace_gid or project_gids"):
            AsanaConnectorConfig.from_source({"crawl_config": {"auth_token": "t"}})

    def test_string_project_gids_split(self):
        from src.connectors.asana.config import AsanaConnectorConfig
        cfg = AsanaConnectorConfig.from_source({
            "crawl_config": {"auth_token": "t", "project_gids": "p1, p2 , p3"},
        })
        assert cfg.project_gids == ("p1", "p2", "p3")

    def test_days_back_zero_preserved(self):
        from src.connectors.asana.config import AsanaConnectorConfig
        cfg = AsanaConnectorConfig.from_source({
            "crawl_config": {
                "auth_token": "t", "workspace_gid": "1", "days_back": 0,
            },
        })
        assert cfg.days_back == 0


# ---------------------------------------------------------------------------
# Dropbox config
# ---------------------------------------------------------------------------


class TestDropboxConfig:
    def test_missing_token_raises(self):
        from src.connectors.dropbox.config import DropboxConnectorConfig
        with pytest.raises(ValueError, match="auth_token"):
            DropboxConnectorConfig.from_source({"crawl_config": {}})

    def test_root_folder_normalized_to_empty(self):
        from src.connectors.dropbox.config import DropboxConnectorConfig
        cfg = DropboxConnectorConfig.from_source({
            "crawl_config": {"auth_token": "t", "folder_path": "/"},
        })
        assert cfg.folder_path == ""

    def test_folder_path_gets_leading_slash(self):
        from src.connectors.dropbox.config import DropboxConnectorConfig
        cfg = DropboxConnectorConfig.from_source({
            "crawl_config": {"auth_token": "t", "folder_path": "Documents/2026"},
        })
        assert cfg.folder_path == "/Documents/2026"

    def test_extensions_normalized(self):
        from src.connectors.dropbox.config import DropboxConnectorConfig
        cfg = DropboxConnectorConfig.from_source({
            "crawl_config": {
                "auth_token": "t",
                "include_extensions": "PDF, .DOCX, md",
            },
        })
        assert set(cfg.include_extensions) == {".pdf", ".docx", ".md"}


# ---------------------------------------------------------------------------
# Box config
# ---------------------------------------------------------------------------


class TestBoxConfig:
    def test_missing_token_raises(self):
        from src.connectors.box.config import BoxConnectorConfig
        with pytest.raises(ValueError, match="auth_token"):
            BoxConnectorConfig.from_source({"crawl_config": {}})

    def test_default_folder_root(self):
        from src.connectors.box.config import BoxConnectorConfig
        cfg = BoxConnectorConfig.from_source({
            "crawl_config": {"auth_token": "t"},
        })
        assert cfg.folder_id == "0"
        assert cfg.recursive is True


# ---------------------------------------------------------------------------
# Auth header sanity
# ---------------------------------------------------------------------------


class TestAuthHeaders:
    def test_asana_bearer(self):
        from src.connectors.asana.client import AsanaClient
        client = AsanaClient("pat_xyz")
        assert client._headers["Authorization"] == "Bearer pat_xyz"
        _run(client.aclose())

    def test_dropbox_bearer(self):
        from src.connectors.dropbox.client import DropboxClient
        client = DropboxClient("dbx_xyz")
        assert client._token == "dbx_xyz"
        _run(client.aclose())

    def test_box_bearer(self):
        # F3: BoxClient 가 BaseConnectorClient 패턴으로 — _default_headers
        # 에 Authorization Bearer 가 들어가도록 base 가 자동 처리.
        from src.connectors.box.client import BoxClient
        client = BoxClient("box_xyz")
        assert client._default_headers["Authorization"] == "Bearer box_xyz"
        _run(client.aclose())

    def test_empty_tokens_raise(self):
        from src.connectors.asana.client import AsanaClient
        from src.connectors.box.client import BoxClient
        from src.connectors.dropbox.client import DropboxClient

        for cls in (AsanaClient, DropboxClient, BoxClient):
            with pytest.raises(ValueError, match="auth_token"):
                cls("")
