"""Unit tests for dashboard/services/api/kb.py — KB management API client."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Make dashboard modules importable
_DASHBOARD_DIR = str(Path(__file__).resolve().parents[2] / "dashboard")
if _DASHBOARD_DIR not in sys.path:
    sys.path.insert(0, _DASHBOARD_DIR)

# Patch streamlit before importing
_st_mock = MagicMock()
_st_mock.cache_data = lambda **kw: lambda f: f
_st_mock.session_state = {}
sys.modules.setdefault("streamlit", _st_mock)

from services.api.kb import (
    add_kb_member,
    create_kb,
    delete_kb,
    get_kb,
    get_kb_aggregation,
    get_kb_categories,
    get_kb_coverage_gaps,
    get_kb_documents,
    get_kb_lifecycle,
    get_kb_members,
    get_kb_stats,
    list_kbs,
    list_l1_categories,
    remove_kb_member,
    update_kb,
    update_kb_publish_strategy,
)
from services.api._core import api_failed
import services.api.kb as _kb_module


# ===========================================================================
# list_kbs
# ===========================================================================

class TestListKbs:
    def test_list_kbs_no_filter(self, monkeypatch):
        mock_get = MagicMock()
        monkeypatch.setattr(_kb_module, "_get", mock_get)
        mock_get.return_value = {"items": []}
        result = list_kbs()
        mock_get.assert_called_once_with("/api/v1/admin/kb", tier=None, status=None)
        assert result == {"items": []}

    def test_list_kbs_with_tier_filter(self, monkeypatch):
        mock_get = MagicMock()
        monkeypatch.setattr(_kb_module, "_get", mock_get)
        mock_get.return_value = {"items": [{"id": "kb1"}]}
        result = list_kbs(tier="GLOBAL")
        mock_get.assert_called_once_with("/api/v1/admin/kb", tier="GLOBAL", status=None)

    def test_list_kbs_with_status_filter(self, monkeypatch):
        mock_get = MagicMock()
        monkeypatch.setattr(_kb_module, "_get", mock_get)
        mock_get.return_value = {"items": []}
        list_kbs(status="ACTIVE")
        mock_get.assert_called_once_with("/api/v1/admin/kb", tier=None, status="ACTIVE")


# ===========================================================================
# get_kb / create_kb / update_kb / delete_kb
# ===========================================================================

class TestKBCrud:
    def test_get_kb(self, monkeypatch):
        mock_get = MagicMock()
        monkeypatch.setattr(_kb_module, "_get", mock_get)
        mock_get.return_value = {"id": "test-kb", "name": "Test"}
        result = get_kb("test-kb")
        mock_get.assert_called_once_with("/api/v1/admin/kb/test-kb")
        assert result["id"] == "test-kb"

    def test_create_kb(self, monkeypatch):
        mock_post = MagicMock()
        monkeypatch.setattr(_kb_module, "_post", mock_post)
        body = {"id": "new-kb", "name": "New KB", "tier": "TEAM"}
        mock_post.return_value = {"id": "new-kb"}
        result = create_kb(body)
        mock_post.assert_called_once_with("/api/v1/admin/kb", body)
        assert result["id"] == "new-kb"

    def test_update_kb(self, monkeypatch):
        mock_put = MagicMock()
        monkeypatch.setattr(_kb_module, "_put", mock_put)
        mock_put.return_value = {"id": "kb1", "name": "Updated"}
        result = update_kb("kb1", {"name": "Updated"})
        mock_put.assert_called_once_with("/api/v1/admin/kb/kb1", {"name": "Updated"})

    def test_delete_kb(self, monkeypatch):
        mock_delete = MagicMock()
        monkeypatch.setattr(_kb_module, "_delete", mock_delete)
        mock_delete.return_value = {"success": True}
        result = delete_kb("old-kb")
        mock_delete.assert_called_once_with("/api/v1/admin/kb/old-kb")
        assert result["success"] is True


# ===========================================================================
# update_kb_publish_strategy
# ===========================================================================

class TestUpdatePublishStrategy:
    def test_sets_strategy_in_settings(self, monkeypatch):
        mock_get = MagicMock()
        mock_update = MagicMock()
        monkeypatch.setattr(_kb_module, "get_kb", mock_get)
        monkeypatch.setattr(_kb_module, "update_kb", mock_update)
        mock_get.return_value = {"settings": {"other": "val"}}
        mock_update.return_value = {"ok": True}
        update_kb_publish_strategy("kb1", "atomic")
        mock_update.assert_called_once_with(
            "kb1", {"settings": {"other": "val", "publish_strategy": "atomic"}}
        )

    def test_normalizes_strategy_lowercase(self, monkeypatch):
        mock_get = MagicMock()
        mock_update = MagicMock()
        monkeypatch.setattr(_kb_module, "get_kb", mock_get)
        monkeypatch.setattr(_kb_module, "update_kb", mock_update)
        mock_get.return_value = {"settings": {}}
        mock_update.return_value = {}
        update_kb_publish_strategy("kb1", "  ATOMIC  ")
        call_body = mock_update.call_args[0][1]
        assert call_body["settings"]["publish_strategy"] == "atomic"

    def test_defaults_to_legacy_when_none(self, monkeypatch):
        mock_get = MagicMock()
        mock_update = MagicMock()
        monkeypatch.setattr(_kb_module, "get_kb", mock_get)
        monkeypatch.setattr(_kb_module, "update_kb", mock_update)
        mock_get.return_value = {"settings": {}}
        mock_update.return_value = {}
        update_kb_publish_strategy("kb1", None)
        call_body = mock_update.call_args[0][1]
        assert call_body["settings"]["publish_strategy"] == "legacy"

    def test_returns_error_if_get_kb_fails(self, monkeypatch):
        mock_get = MagicMock(return_value={"error": "not found", "_api_failed": True})
        monkeypatch.setattr(_kb_module, "get_kb", mock_get)
        result = update_kb_publish_strategy("kb-bad", "atomic")
        assert api_failed(result) is True

    def test_handles_missing_settings_key(self, monkeypatch):
        mock_get = MagicMock()
        mock_update = MagicMock()
        monkeypatch.setattr(_kb_module, "get_kb", mock_get)
        monkeypatch.setattr(_kb_module, "update_kb", mock_update)
        mock_get.return_value = {}  # no "settings" key
        mock_update.return_value = {}
        update_kb_publish_strategy("kb1", "atomic")
        call_body = mock_update.call_args[0][1]
        assert call_body["settings"]["publish_strategy"] == "atomic"


# ===========================================================================
# KB stats, documents, members
# ===========================================================================

class TestKBDetails:
    def test_get_kb_stats(self, monkeypatch):
        mock_get = MagicMock()
        monkeypatch.setattr(_kb_module, "_get", mock_get)
        mock_get.return_value = {"total_chunks": 100}
        result = get_kb_stats("kb1")
        mock_get.assert_called_once_with("/api/v1/admin/kb/kb1/stats")

    def test_get_kb_documents_pagination(self, monkeypatch):
        mock_get = MagicMock()
        monkeypatch.setattr(_kb_module, "_get", mock_get)
        mock_get.return_value = {"items": [], "total": 0}
        get_kb_documents("kb1", page=2, page_size=10)
        mock_get.assert_called_once_with(
            "/api/v1/admin/kb/kb1/documents", page=2, page_size=10
        )

    def test_get_kb_documents_clamps_page_params(self, monkeypatch):
        mock_get = MagicMock()
        monkeypatch.setattr(_kb_module, "_get", mock_get)
        mock_get.return_value = {"items": []}
        get_kb_documents("kb1", page=-1, page_size=500)
        call_kwargs = mock_get.call_args
        assert call_kwargs[1]["page"] == 1
        assert call_kwargs[1]["page_size"] <= 100

    def test_add_kb_member(self, monkeypatch):
        mock_post = MagicMock()
        monkeypatch.setattr(_kb_module, "_post", mock_post)
        mock_post.return_value = {"ok": True}
        add_kb_member("kb1", {"user_id": "user1", "role": "viewer"})
        mock_post.assert_called_once()

    def test_remove_kb_member(self, monkeypatch):
        mock_delete = MagicMock()
        monkeypatch.setattr(_kb_module, "_delete", mock_delete)
        mock_delete.return_value = {"success": True}
        remove_kb_member("kb1", "member-123")
        mock_delete.assert_called_once_with("/api/v1/admin/kb/kb1/members/member-123")

    def test_get_kb_members(self, monkeypatch):
        mock_get = MagicMock()
        monkeypatch.setattr(_kb_module, "_get", mock_get)
        mock_get.return_value = {"items": []}
        get_kb_members("kb1")
        mock_get.assert_called_once_with("/api/v1/admin/kb/kb1/members")


# ===========================================================================
# Categories & lifecycle
# ===========================================================================

class TestCategoriesLifecycle:
    def test_get_kb_categories(self, monkeypatch):
        mock_get = MagicMock()
        monkeypatch.setattr(_kb_module, "_get", mock_get)
        mock_get.return_value = {"categories": []}
        get_kb_categories("kb1")
        mock_get.assert_called_once_with("/api/v1/admin/kb/kb1/categories")

    def test_list_l1_categories(self, monkeypatch):
        mock_get = MagicMock()
        monkeypatch.setattr(_kb_module, "_get", mock_get)
        mock_get.return_value = {"items": []}
        list_l1_categories()
        mock_get.assert_called_once_with("/api/v1/admin/categories")

    def test_get_kb_lifecycle_with_filter(self, monkeypatch):
        mock_get = MagicMock()
        monkeypatch.setattr(_kb_module, "_get", mock_get)
        mock_get.return_value = {"events": []}
        get_kb_lifecycle("kb1", filter="recent")
        mock_get.assert_called_once_with("/api/v1/admin/kb/kb1/lifecycle", filter="recent")

    def test_get_kb_coverage_gaps(self, monkeypatch):
        mock_get = MagicMock()
        monkeypatch.setattr(_kb_module, "_get", mock_get)
        mock_get.return_value = {"gaps": []}
        get_kb_coverage_gaps("kb1")
        mock_get.assert_called_once_with("/api/v1/admin/kb/kb1/coverage-gaps")
