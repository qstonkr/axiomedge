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
    @patch("services.api.search._post")
    def test_basic_query(self, mock_post):
        mock_post.return_value = {"results": []}
        hub_search("서버 장애")
        body = mock_post.call_args[0][1]
        assert body["query"] == "서버 장애"
        assert body["top_k"] == 5

    @patch("services.api.search._post")
    def test_with_kb_ids(self, mock_post):
        mock_post.return_value = {"results": []}
        hub_search("query", kb_ids=["kb1", "kb2"])
        body = mock_post.call_args[0][1]
        assert body["kb_filter"]["kb_ids"] == ["kb1", "kb2"]

    @patch("services.api.search._post")
    def test_with_tier_filter(self, mock_post):
        mock_post.return_value = {"results": []}
        hub_search("query", tier_filter=["GLOBAL"])
        body = mock_post.call_args[0][1]
        assert body["kb_filter"]["tier"] == ["GLOBAL"]

    @patch("services.api.search._post")
    def test_top_k_clamped_min(self, mock_post):
        mock_post.return_value = {"results": []}
        hub_search("query", top_k=0)
        body = mock_post.call_args[0][1]
        assert body["top_k"] == 1

    @patch("services.api.search._post")
    def test_top_k_clamped_max(self, mock_post):
        mock_post.return_value = {"results": []}
        hub_search("query", top_k=100)
        body = mock_post.call_args[0][1]
        assert body["top_k"] == 50

    @patch("services.api.search._post")
    def test_with_mode(self, mock_post):
        mock_post.return_value = {"results": []}
        hub_search("query", mode="hybrid")
        body = mock_post.call_args[0][1]
        assert body["mode"] == "hybrid"

    @patch("services.api.search._post")
    def test_with_group(self, mock_post):
        mock_post.return_value = {"results": []}
        hub_search("query", group_id="g1", group_name="Group 1")
        body = mock_post.call_args[0][1]
        assert body["group_id"] == "g1"
        assert body["group_name"] == "Group 1"

    @patch("services.api.search._post")
    def test_no_kb_filter_when_none(self, mock_post):
        mock_post.return_value = {"results": []}
        hub_search("query")
        body = mock_post.call_args[0][1]
        assert "kb_filter" not in body

    @patch("services.api.search._post")
    def test_endpoint_is_hub(self, mock_post):
        mock_post.return_value = {}
        hub_search("query")
        assert mock_post.call_args[0][0] == "/api/v1/search/hub"

    @patch("services.api.search._post")
    def test_query_sanitized(self, mock_post):
        mock_post.return_value = {}
        hub_search("  hello   world  ")
        body = mock_post.call_args[0][1]
        assert body["query"] == "hello world"


# ===========================================================================
# hub_search_answer
# ===========================================================================

class TestHubSearchAnswer:
    @patch("services.api.search._post")
    def test_includes_answer_flag(self, mock_post):
        mock_post.return_value = {"answer": "response"}
        hub_search_answer("question")
        body = mock_post.call_args[0][1]
        assert body["include_answer"] is True

    @patch("services.api.search._post")
    def test_uses_search_timeout(self, mock_post):
        mock_post.return_value = {}
        hub_search_answer("question")
        call_kwargs = mock_post.call_args[1]
        assert "timeout" in call_kwargs

    @patch("services.api.search._post")
    def test_with_mode_and_group(self, mock_post):
        mock_post.return_value = {}
        hub_search_answer("q", mode="hybrid", group_name="grp")
        body = mock_post.call_args[0][1]
        assert body["mode"] == "hybrid"
        assert body["group_name"] == "grp"


# ===========================================================================
# get_searchable_kbs
# ===========================================================================

class TestSearchableKbs:
    @patch("services.api.search._get")
    def test_calls_correct_endpoint(self, mock_get):
        mock_get.return_value = {"items": []}
        get_searchable_kbs()
        mock_get.assert_called_once_with("/api/v1/search/hub/kbs")


# ===========================================================================
# rag_query
# ===========================================================================

class TestRagQuery:
    @patch("services.api.search._post")
    def test_basic_rag_query(self, mock_post):
        mock_post.return_value = {"answer": "response"}
        rag_query("질문")
        body = mock_post.call_args[0][1]
        assert body["query"] == "질문"
        assert body["mode"] == "classic"

    @patch("services.api.search._post")
    def test_with_kb_ids(self, mock_post):
        mock_post.return_value = {}
        rag_query("q", kb_ids=["kb1"])
        body = mock_post.call_args[0][1]
        assert body["kb_ids"] == ["kb1"]

    @patch("services.api.search._post")
    def test_custom_mode(self, mock_post):
        mock_post.return_value = {}
        rag_query("q", mode="agentic")
        body = mock_post.call_args[0][1]
        assert body["mode"] == "agentic"


# ===========================================================================
# Search Groups CRUD
# ===========================================================================

class TestSearchGroups:
    @patch("services.api.search._get")
    def test_list_search_groups(self, mock_get):
        mock_get.return_value = {"items": []}
        list_search_groups()
        mock_get.assert_called_once_with("/api/v1/search-groups")

    @patch("services.api.search._post")
    def test_create_search_group(self, mock_post):
        mock_post.return_value = {"id": "sg1"}
        body = {"name": "My Group", "kb_ids": ["kb1"]}
        create_search_group(body)
        mock_post.assert_called_once_with("/api/v1/search-groups", body)

    @patch("services.api.search._put")
    def test_update_search_group(self, mock_put):
        mock_put.return_value = {}
        update_search_group("sg1", {"name": "Renamed"})
        mock_put.assert_called_once_with("/api/v1/search-groups/sg1", {"name": "Renamed"})

    @patch("services.api.search._delete")
    def test_delete_search_group(self, mock_delete):
        mock_delete.return_value = {"success": True}
        delete_search_group("sg1")
        mock_delete.assert_called_once_with("/api/v1/search-groups/sg1")


# ===========================================================================
# Cache operations
# ===========================================================================

class TestCacheOps:
    @patch("services.api.search._get")
    def test_get_cache_stats(self, mock_get):
        mock_get.return_value = {"hit_rate": 0.5}
        get_intelligent_rag_cache_stats()
        mock_get.assert_called_once_with("/api/v1/intelligent-rag/cache/stats")

    @patch("services.api.search._post")
    def test_invalidate_cache_with_pattern(self, mock_post):
        mock_post.return_value = {}
        invalidate_rag_cache("kb1:*")
        body = mock_post.call_args[0][1]
        assert body["pattern"] == "kb1:*"

    @patch("services.api.search._post")
    def test_invalidate_cache_no_pattern(self, mock_post):
        mock_post.return_value = {}
        invalidate_rag_cache()
        body = mock_post.call_args[0][1]
        assert body == {}

    @patch("services.api.search._post")
    def test_clear_rag_cache(self, mock_post):
        mock_post.return_value = {}
        clear_rag_cache()
        mock_post.assert_called_once_with("/api/v1/intelligent-rag/cache/clear")

    @patch("services.api.search._post")
    def test_clear_search_cache(self, mock_post):
        mock_post.return_value = {}
        clear_search_cache()
        mock_post.assert_called_once_with("/api/v1/admin/kb/search-cache/clear")


# ===========================================================================
# Analytics & stats
# ===========================================================================

class TestAnalytics:
    @patch("services.api.search._get")
    def test_get_search_history_pagination(self, mock_get):
        mock_get.return_value = {"items": []}
        get_search_history(page=2, page_size=25)
        mock_get.assert_called_once_with(
            "/api/v1/admin/search/history", page=2, page_size=25
        )

    @patch("services.api.search._get")
    def test_get_search_analytics(self, mock_get):
        mock_get.return_value = {"total": 100}
        get_search_analytics()
        mock_get.assert_called_once_with("/api/v1/admin/search/analytics")

    @patch("services.api.search._get")
    def test_get_rag_config(self, mock_get):
        mock_get.return_value = {"model": "exaone"}
        get_rag_config()
        mock_get.assert_called_once_with("/api/v1/knowledge/rag/config")

    @patch("services.api.search._get")
    def test_get_rag_stats(self, mock_get):
        mock_get.return_value = {}
        get_rag_stats()
        mock_get.assert_called_once_with("/api/v1/knowledge/rag/stats")

    @patch("services.api.search._get")
    def test_get_intelligent_rag_health(self, mock_get):
        mock_get.return_value = {"healthy": True}
        get_intelligent_rag_health()
        mock_get.assert_called_once_with("/api/v1/intelligent-rag/health")


# ===========================================================================
# intelligent_rag_query
# ===========================================================================

class TestIntelligentRagQuery:
    @patch("services.api.search._post")
    def test_basic_query(self, mock_post):
        mock_post.return_value = {"answer": "test"}
        intelligent_rag_query("hello")
        body = mock_post.call_args[0][1]
        assert body["query"] == "hello"
        assert "domain" not in body

    @patch("services.api.search._post")
    def test_with_domain(self, mock_post):
        mock_post.return_value = {}
        intelligent_rag_query("q", domain="itops")
        body = mock_post.call_args[0][1]
        assert body["domain"] == "itops"
