"""Unit tests for dashboard/services/api/search.py — Search & RAG API client."""

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
sys.modules.setdefault("streamlit", _st_mock)

import services.api.search as _search_module

from services.api.search import (
    clear_rag_cache,
    clear_search_cache,
    create_search_group,
    delete_search_group,
    get_intelligent_rag_cache_stats,
    get_intelligent_rag_health,
    get_rag_config,
    get_rag_stats,
    get_search_analytics,
    get_search_history,
    get_searchable_kbs,
    hub_search,
    hub_search_answer,
    intelligent_rag_query,
    invalidate_rag_cache,
    list_search_groups,
    rag_query,
    update_search_group,
)


# ===========================================================================
# hub_search
# ===========================================================================

class TestHubSearch:
    def test_basic_query(self, monkeypatch):
        mock_post = MagicMock()
        monkeypatch.setattr(_search_module, "_post", mock_post)
        mock_post.return_value = {"results": []}
        hub_search("서버 장애")
        body = mock_post.call_args[0][1]
        assert body["query"] == "서버 장애"
        assert body["top_k"] == 5

    def test_with_kb_ids(self, monkeypatch):
        mock_post = MagicMock()
        monkeypatch.setattr(_search_module, "_post", mock_post)
        mock_post.return_value = {"results": []}
        hub_search("query", kb_ids=["kb1", "kb2"])
        body = mock_post.call_args[0][1]
        assert body["kb_filter"]["kb_ids"] == ["kb1", "kb2"]

    def test_with_tier_filter(self, monkeypatch):
        mock_post = MagicMock()
        monkeypatch.setattr(_search_module, "_post", mock_post)
        mock_post.return_value = {"results": []}
        hub_search("query", tier_filter=["GLOBAL"])
        body = mock_post.call_args[0][1]
        assert body["kb_filter"]["tier"] == ["GLOBAL"]

    def test_top_k_clamped_min(self, monkeypatch):
        mock_post = MagicMock()
        monkeypatch.setattr(_search_module, "_post", mock_post)
        mock_post.return_value = {"results": []}
        hub_search("query", top_k=0)
        body = mock_post.call_args[0][1]
        assert body["top_k"] == 1

    def test_top_k_clamped_max(self, monkeypatch):
        mock_post = MagicMock()
        monkeypatch.setattr(_search_module, "_post", mock_post)
        mock_post.return_value = {"results": []}
        hub_search("query", top_k=100)
        body = mock_post.call_args[0][1]
        assert body["top_k"] == 50

    def test_with_mode(self, monkeypatch):
        mock_post = MagicMock()
        monkeypatch.setattr(_search_module, "_post", mock_post)
        mock_post.return_value = {"results": []}
        hub_search("query", mode="hybrid")
        body = mock_post.call_args[0][1]
        assert body["mode"] == "hybrid"

    def test_with_group(self, monkeypatch):
        mock_post = MagicMock()
        monkeypatch.setattr(_search_module, "_post", mock_post)
        mock_post.return_value = {"results": []}
        hub_search("query", group_id="g1", group_name="Group 1")
        body = mock_post.call_args[0][1]
        assert body["group_id"] == "g1"
        assert body["group_name"] == "Group 1"

    def test_no_kb_filter_when_none(self, monkeypatch):
        mock_post = MagicMock()
        monkeypatch.setattr(_search_module, "_post", mock_post)
        mock_post.return_value = {"results": []}
        hub_search("query")
        body = mock_post.call_args[0][1]
        assert "kb_filter" not in body

    def test_endpoint_is_hub(self, monkeypatch):
        mock_post = MagicMock()
        monkeypatch.setattr(_search_module, "_post", mock_post)
        mock_post.return_value = {}
        hub_search("query")
        assert mock_post.call_args[0][0] == "/api/v1/search/hub"

    def test_query_sanitized(self, monkeypatch):
        mock_post = MagicMock()
        monkeypatch.setattr(_search_module, "_post", mock_post)
        mock_post.return_value = {}
        hub_search("  hello   world  ")
        body = mock_post.call_args[0][1]
        assert body["query"] == "hello world"


# ===========================================================================
# hub_search_answer
# ===========================================================================

class TestHubSearchAnswer:
    def test_includes_answer_flag(self, monkeypatch):
        mock_post = MagicMock()
        monkeypatch.setattr(_search_module, "_post", mock_post)
        mock_post.return_value = {"answer": "response"}
        hub_search_answer("question")
        body = mock_post.call_args[0][1]
        assert body["include_answer"] is True

    def test_uses_search_timeout(self, monkeypatch):
        mock_post = MagicMock()
        monkeypatch.setattr(_search_module, "_post", mock_post)
        mock_post.return_value = {}
        hub_search_answer("question")
        call_kwargs = mock_post.call_args[1]
        assert "timeout" in call_kwargs

    def test_with_mode_and_group(self, monkeypatch):
        mock_post = MagicMock()
        monkeypatch.setattr(_search_module, "_post", mock_post)
        mock_post.return_value = {}
        hub_search_answer("q", mode="hybrid", group_name="grp")
        body = mock_post.call_args[0][1]
        assert body["mode"] == "hybrid"
        assert body["group_name"] == "grp"


# ===========================================================================
# get_searchable_kbs
# ===========================================================================

class TestSearchableKbs:
    def test_calls_correct_endpoint(self, monkeypatch):
        mock_get = MagicMock()
        monkeypatch.setattr(_search_module, "_get", mock_get)
        mock_get.return_value = {"items": []}
        get_searchable_kbs()
        mock_get.assert_called_once_with("/api/v1/search/hub/kbs")


# ===========================================================================
# rag_query
# ===========================================================================

class TestRagQuery:
    def test_basic_rag_query(self, monkeypatch):
        mock_post = MagicMock()
        monkeypatch.setattr(_search_module, "_post", mock_post)
        mock_post.return_value = {"answer": "response"}
        rag_query("질문")
        body = mock_post.call_args[0][1]
        assert body["query"] == "질문"
        assert body["mode"] == "classic"

    def test_with_kb_ids(self, monkeypatch):
        mock_post = MagicMock()
        monkeypatch.setattr(_search_module, "_post", mock_post)
        mock_post.return_value = {}
        rag_query("q", kb_ids=["kb1"])
        body = mock_post.call_args[0][1]
        assert body["kb_ids"] == ["kb1"]

    def test_custom_mode(self, monkeypatch):
        mock_post = MagicMock()
        monkeypatch.setattr(_search_module, "_post", mock_post)
        mock_post.return_value = {}
        rag_query("q", mode="agentic")
        body = mock_post.call_args[0][1]
        assert body["mode"] == "agentic"


# ===========================================================================
# Search Groups CRUD
# ===========================================================================

class TestSearchGroups:
    def test_list_search_groups(self, monkeypatch):
        mock_get = MagicMock()
        monkeypatch.setattr(_search_module, "_get", mock_get)
        mock_get.return_value = {"items": []}
        list_search_groups()
        mock_get.assert_called_once_with("/api/v1/search-groups")

    def test_create_search_group(self, monkeypatch):
        mock_post = MagicMock()
        monkeypatch.setattr(_search_module, "_post", mock_post)
        mock_post.return_value = {"id": "sg1"}
        body = {"name": "My Group", "kb_ids": ["kb1"]}
        create_search_group(body)
        mock_post.assert_called_once_with("/api/v1/search-groups", body)

    def test_update_search_group(self, monkeypatch):
        mock_put = MagicMock()
        monkeypatch.setattr(_search_module, "_put", mock_put)
        mock_put.return_value = {}
        update_search_group("sg1", {"name": "Renamed"})
        mock_put.assert_called_once_with("/api/v1/search-groups/sg1", {"name": "Renamed"})

    def test_delete_search_group(self, monkeypatch):
        mock_delete = MagicMock()
        monkeypatch.setattr(_search_module, "_delete", mock_delete)
        mock_delete.return_value = {"success": True}
        delete_search_group("sg1")
        mock_delete.assert_called_once_with("/api/v1/search-groups/sg1")


# ===========================================================================
# Cache operations
# ===========================================================================

class TestCacheOps:
    def test_get_cache_stats(self, monkeypatch):
        mock_get = MagicMock()
        monkeypatch.setattr(_search_module, "_get", mock_get)
        mock_get.return_value = {"hit_rate": 0.5}
        get_intelligent_rag_cache_stats()
        mock_get.assert_called_once_with("/api/v1/intelligent-rag/cache/stats")

    def test_invalidate_cache_with_pattern(self, monkeypatch):
        mock_post = MagicMock()
        monkeypatch.setattr(_search_module, "_post", mock_post)
        mock_post.return_value = {}
        invalidate_rag_cache("kb1:*")
        body = mock_post.call_args[0][1]
        assert body["pattern"] == "kb1:*"

    def test_invalidate_cache_no_pattern(self, monkeypatch):
        mock_post = MagicMock()
        monkeypatch.setattr(_search_module, "_post", mock_post)
        mock_post.return_value = {}
        invalidate_rag_cache()
        body = mock_post.call_args[0][1]
        assert body == {}

    def test_clear_rag_cache(self, monkeypatch):
        mock_post = MagicMock()
        monkeypatch.setattr(_search_module, "_post", mock_post)
        mock_post.return_value = {}
        clear_rag_cache()
        mock_post.assert_called_once_with("/api/v1/intelligent-rag/cache/clear")

    def test_clear_search_cache(self, monkeypatch):
        mock_post = MagicMock()
        monkeypatch.setattr(_search_module, "_post", mock_post)
        mock_post.return_value = {}
        clear_search_cache()
        mock_post.assert_called_once_with("/api/v1/admin/kb/search-cache/clear")


# ===========================================================================
# Analytics & stats
# ===========================================================================

class TestAnalytics:
    def test_get_search_history_pagination(self, monkeypatch):
        mock_get = MagicMock()
        monkeypatch.setattr(_search_module, "_get", mock_get)
        mock_get.return_value = {"items": []}
        get_search_history(page=2, page_size=25)
        mock_get.assert_called_once_with(
            "/api/v1/admin/search/history", page=2, page_size=25
        )

    def test_get_search_analytics(self, monkeypatch):
        mock_get = MagicMock()
        monkeypatch.setattr(_search_module, "_get", mock_get)
        mock_get.return_value = {"total": 100}
        get_search_analytics()
        mock_get.assert_called_once_with("/api/v1/admin/search/analytics")

    def test_get_rag_config(self, monkeypatch):
        mock_get = MagicMock()
        monkeypatch.setattr(_search_module, "_get", mock_get)
        mock_get.return_value = {"model": "exaone"}
        get_rag_config()
        mock_get.assert_called_once_with("/api/v1/knowledge/rag/config")

    def test_get_rag_stats(self, monkeypatch):
        mock_get = MagicMock()
        monkeypatch.setattr(_search_module, "_get", mock_get)
        mock_get.return_value = {}
        get_rag_stats()
        mock_get.assert_called_once_with("/api/v1/knowledge/rag/stats")

    def test_get_intelligent_rag_health(self, monkeypatch):
        mock_get = MagicMock()
        monkeypatch.setattr(_search_module, "_get", mock_get)
        mock_get.return_value = {"healthy": True}
        get_intelligent_rag_health()
        mock_get.assert_called_once_with("/api/v1/intelligent-rag/health")


# ===========================================================================
# intelligent_rag_query
# ===========================================================================

class TestIntelligentRagQuery:
    def test_basic_query(self, monkeypatch):
        mock_post = MagicMock()
        monkeypatch.setattr(_search_module, "_post", mock_post)
        mock_post.return_value = {"answer": "test"}
        intelligent_rag_query("hello")
        body = mock_post.call_args[0][1]
        assert body["query"] == "hello"
        assert "domain" not in body

    def test_with_domain(self, monkeypatch):
        mock_post = MagicMock()
        monkeypatch.setattr(_search_module, "_post", mock_post)
        mock_post.return_value = {}
        intelligent_rag_query("q", domain="itops")
        body = mock_post.call_args[0][1]
        assert body["domain"] == "itops"
