"""Backfill unit tests for src/api/routes/kb.py — KB management routes."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _run(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


def _mock_state(**kwargs):
    from src.api.state import AppState

    state = AppState()
    for k, v in kwargs.items():
        state[k] = v
    return state


# ---------------------------------------------------------------------------
# _enrich_kb_counts
# ---------------------------------------------------------------------------
class TestEnrichKbCounts:
    def test_no_store(self):
        from src.api.routes.kb import _enrich_kb_counts

        kbs = [{"kb_id": "a"}]
        _run(_enrich_kb_counts(kbs, None))
        # Should not add chunk_count when store is None
        assert "chunk_count" not in kbs[0]

    def test_success(self):
        from src.api.routes.kb import _enrich_kb_counts

        store = AsyncMock()
        store.count = AsyncMock(return_value=42)
        kbs = [{"kb_id": "a", "document_count": 5}]
        _run(_enrich_kb_counts(kbs, store))
        assert kbs[0]["chunk_count"] == 42
        assert kbs[0]["doc_count"] == 5

    def test_count_fails_falls_back(self):
        from src.api.routes.kb import _enrich_kb_counts

        store = AsyncMock()
        store.count = AsyncMock(side_effect=RuntimeError("conn refused"))
        kbs = [{"kb_id": "a"}]
        _run(_enrich_kb_counts(kbs, store))
        assert kbs[0]["chunk_count"] == 0

    def test_uses_id_key_fallback(self):
        from src.api.routes.kb import _enrich_kb_counts

        store = AsyncMock()
        store.count = AsyncMock(return_value=10)
        kbs = [{"id": "b"}]
        _run(_enrich_kb_counts(kbs, store))
        assert kbs[0]["chunk_count"] == 10


# ---------------------------------------------------------------------------
# _list_kbs_from_registry
# ---------------------------------------------------------------------------
class TestListKbsFromRegistry:
    def test_list_all(self):
        from src.api.routes.kb import _list_kbs_from_registry

        reg = AsyncMock()
        reg.list_all = AsyncMock(return_value=[{"kb_id": "a"}])
        result = _run(_list_kbs_from_registry(reg, None, tier=None, status=None))
        assert result == {"kbs": [{"kb_id": "a"}]}

    def test_list_by_tier(self):
        from src.api.routes.kb import _list_kbs_from_registry

        reg = AsyncMock()
        reg.list_by_tier = AsyncMock(return_value=[{"kb_id": "a", "tier": "dept"}])
        result = _run(
            _list_kbs_from_registry(reg, None, tier="dept", status=None)
        )
        assert result is not None
        assert len(result["kbs"]) == 1

    def test_list_by_status(self):
        from src.api.routes.kb import _list_kbs_from_registry

        reg = AsyncMock()
        reg.list_by_status = AsyncMock(
            return_value=[{"kb_id": "a", "status": "archived"}]
        )
        result = _run(
            _list_kbs_from_registry(reg, None, tier=None, status="archived")
        )
        assert result is not None

    def test_exception_returns_none(self):
        from src.api.routes.kb import _list_kbs_from_registry

        reg = AsyncMock()
        reg.list_all = AsyncMock(side_effect=RuntimeError("db down"))
        result = _run(
            _list_kbs_from_registry(reg, None, tier=None, status=None)
        )
        assert result is None


# ---------------------------------------------------------------------------
# _list_kbs_from_qdrant
# ---------------------------------------------------------------------------
class TestListKbsFromQdrant:
    def test_success(self):
        from src.api.routes.kb import _list_kbs_from_qdrant

        config = MagicMock()
        config.collection_prefix = "kb"
        provider = MagicMock()
        provider.config = config
        collections = AsyncMock()
        collections._provider = provider
        collections.get_existing_collection_names = AsyncMock(
            return_value=["kb_alpha", "kb_beta"]
        )
        store = AsyncMock()
        store.count = AsyncMock(return_value=10)

        result = _run(_list_kbs_from_qdrant(collections, store))
        assert len(result["kbs"]) == 2
        assert result["kbs"][0]["kb_id"] == "alpha"
        assert result["kbs"][0]["chunk_count"] == 10

    def test_no_store(self):
        from src.api.routes.kb import _list_kbs_from_qdrant

        config = MagicMock()
        config.collection_prefix = "kb"
        provider = MagicMock()
        provider.config = config
        collections = AsyncMock()
        collections._provider = provider
        collections.get_existing_collection_names = AsyncMock(
            return_value=["kb_x"]
        )

        result = _run(_list_kbs_from_qdrant(collections, None))
        assert result["kbs"][0]["chunk_count"] == 0

    def test_count_failure_logged(self):
        from src.api.routes.kb import _list_kbs_from_qdrant

        config = MagicMock()
        config.collection_prefix = "kb"
        provider = MagicMock()
        provider.config = config
        collections = AsyncMock()
        collections._provider = provider
        collections.get_existing_collection_names = AsyncMock(
            return_value=["kb_z"]
        )
        store = AsyncMock()
        store.count = AsyncMock(side_effect=RuntimeError("timeout"))

        result = _run(_list_kbs_from_qdrant(collections, store))
        assert result["kbs"][0]["chunk_count"] == 0

    def test_collection_names_fail(self):
        from src.api.routes.kb import _list_kbs_from_qdrant

        collections = AsyncMock()
        collections.get_existing_collection_names = AsyncMock(
            side_effect=RuntimeError("conn")
        )
        result = _run(_list_kbs_from_qdrant(collections, None))
        assert result["kbs"] == []
        assert "error" in result

    def test_name_without_prefix(self):
        from src.api.routes.kb import _list_kbs_from_qdrant

        config = MagicMock()
        config.collection_prefix = "kb"
        provider = MagicMock()
        provider.config = config
        collections = AsyncMock()
        collections._provider = provider
        collections.get_existing_collection_names = AsyncMock(
            return_value=["custom_collection"]
        )

        result = _run(_list_kbs_from_qdrant(collections, None))
        assert result["kbs"][0]["kb_id"] == "custom_collection"


# ---------------------------------------------------------------------------
# _list_kbs_impl (integration of helpers)
# ---------------------------------------------------------------------------
class TestListKbsImpl:
    def test_no_registry_no_collections(self):
        from src.api.routes.kb import _list_kbs_impl

        state = _mock_state(
            kb_registry=None, qdrant_store=None, qdrant_collections=None
        )
        with patch("src.api.routes.kb._get_state", return_value=state):
            result = _run(_list_kbs_impl())
        assert result == {"kbs": []}

    def test_registry_success(self):
        from src.api.routes.kb import _list_kbs_impl

        reg = AsyncMock()
        reg.list_all = AsyncMock(return_value=[{"kb_id": "a"}])
        state = _mock_state(
            kb_registry=reg, qdrant_store=None, qdrant_collections=None
        )
        with patch("src.api.routes.kb._get_state", return_value=state):
            result = _run(_list_kbs_impl())
        assert len(result["kbs"]) == 1

    def test_registry_fail_fallback_qdrant(self):
        from src.api.routes.kb import _list_kbs_impl

        reg = AsyncMock()
        reg.list_all = AsyncMock(side_effect=RuntimeError("db err"))

        config = MagicMock()
        config.collection_prefix = "kb"
        provider = MagicMock()
        provider.config = config
        collections = AsyncMock()
        collections._provider = provider
        collections.get_existing_collection_names = AsyncMock(
            return_value=["kb_fallback"]
        )
        state = _mock_state(
            kb_registry=reg,
            qdrant_store=None,
            qdrant_collections=collections,
        )
        with patch("src.api.routes.kb._get_state", return_value=state):
            result = _run(_list_kbs_impl())
        assert len(result["kbs"]) == 1
        assert result["kbs"][0]["kb_id"] == "fallback"


# ---------------------------------------------------------------------------
# create_kb (original route)
# ---------------------------------------------------------------------------
class TestCreateKb:
    def test_no_collections_503(self):
        from src.api.routes.kb import create_kb, KBCreateRequest

        state = _mock_state(qdrant_collections=None)
        with patch("src.api.routes.kb._get_state", return_value=state):
            with pytest.raises(Exception) as exc_info:
                _run(create_kb(KBCreateRequest(
                    kb_id="x", name="X"
                )))
            assert exc_info.value.status_code == 503

    def test_success(self):
        from src.api.routes.kb import create_kb, KBCreateRequest

        coll = AsyncMock()
        coll.ensure_collection = AsyncMock()
        state = _mock_state(qdrant_collections=coll)
        with patch("src.api.routes.kb._get_state", return_value=state):
            result = _run(create_kb(KBCreateRequest(
                kb_id="my-kb", name="My KB"
            )))
        assert result["success"] is True
        assert result["kb_id"] == "my-kb"

    def test_ensure_collection_error_500(self):
        from src.api.routes.kb import create_kb, KBCreateRequest

        coll = AsyncMock()
        coll.ensure_collection = AsyncMock(
            side_effect=RuntimeError("vector dim mismatch")
        )
        state = _mock_state(qdrant_collections=coll)
        with patch("src.api.routes.kb._get_state", return_value=state):
            with pytest.raises(Exception) as exc_info:
                _run(create_kb(KBCreateRequest(
                    kb_id="bad", name="Bad"
                )))
            assert exc_info.value.status_code == 500


# ---------------------------------------------------------------------------
# delete_kb (original route)
# ---------------------------------------------------------------------------
class TestDeleteKb:
    def test_no_provider_503(self):
        from src.api.routes.kb import delete_kb

        state = _mock_state(qdrant_provider=None)
        with patch("src.api.routes.kb._get_state", return_value=state):
            with pytest.raises(Exception) as exc_info:
                _run(delete_kb("kb1"))
            assert exc_info.value.status_code == 503

    def test_success(self):
        from src.api.routes.kb import delete_kb

        client = AsyncMock()
        client.delete_collection = AsyncMock()
        provider = AsyncMock()
        provider.ensure_client = AsyncMock(return_value=client)
        coll = MagicMock()
        coll.get_collection_name = MagicMock(return_value="kb_test")
        state = _mock_state(
            qdrant_provider=provider, qdrant_collections=coll
        )
        with patch("src.api.routes.kb._get_state", return_value=state):
            result = _run(delete_kb("test"))
        assert result["success"] is True

    def test_delete_error_500(self):
        from src.api.routes.kb import delete_kb

        provider = AsyncMock()
        provider.ensure_client = AsyncMock(
            side_effect=RuntimeError("connection lost")
        )
        state = _mock_state(
            qdrant_provider=provider, qdrant_collections=None
        )
        with patch("src.api.routes.kb._get_state", return_value=state):
            with pytest.raises(Exception) as exc_info:
                _run(delete_kb("fail"))
            assert exc_info.value.status_code == 500

    def test_delete_no_collections_uses_kb_id(self):
        from src.api.routes.kb import delete_kb

        client = AsyncMock()
        client.delete_collection = AsyncMock()
        provider = AsyncMock()
        provider.ensure_client = AsyncMock(return_value=client)
        state = _mock_state(
            qdrant_provider=provider, qdrant_collections=None
        )
        with patch("src.api.routes.kb._get_state", return_value=state):
            _run(delete_kb("raw-id"))
        client.delete_collection.assert_called_once_with("raw-id")


# ---------------------------------------------------------------------------
# admin_create_kb
# ---------------------------------------------------------------------------
class TestAdminCreateKb:
    def test_no_collections_503(self):
        from src.api.routes.kb import admin_create_kb

        state = _mock_state(qdrant_collections=None)
        with patch("src.api.routes.kb._get_state", return_value=state):
            with pytest.raises(Exception) as exc_info:
                _run(admin_create_kb({"kb_id": "x"}))
            assert exc_info.value.status_code == 503

    def test_success_with_kb_id(self):
        from src.api.routes.kb import admin_create_kb

        coll = AsyncMock()
        coll.ensure_collection = AsyncMock()
        state = _mock_state(qdrant_collections=coll)
        with patch("src.api.routes.kb._get_state", return_value=state):
            result = _run(admin_create_kb({"kb_id": "admin-kb"}))
        assert result["kb_id"] == "admin-kb"

    def test_success_with_name_fallback(self):
        from src.api.routes.kb import admin_create_kb

        coll = AsyncMock()
        coll.ensure_collection = AsyncMock()
        state = _mock_state(qdrant_collections=coll)
        with patch("src.api.routes.kb._get_state", return_value=state):
            result = _run(admin_create_kb({"name": "from-name"}))
        assert result["kb_id"] == "from-name"

    def test_error_500(self):
        from src.api.routes.kb import admin_create_kb

        coll = AsyncMock()
        coll.ensure_collection = AsyncMock(side_effect=RuntimeError("fail"))
        state = _mock_state(qdrant_collections=coll)
        with patch("src.api.routes.kb._get_state", return_value=state):
            with pytest.raises(Exception) as exc_info:
                _run(admin_create_kb({"kb_id": "x"}))
            assert exc_info.value.status_code == 500


# ---------------------------------------------------------------------------
# _get_registry_counts
# ---------------------------------------------------------------------------
class TestGetRegistryCounts:
    def test_no_registry(self):
        from src.api.routes.kb import _get_registry_counts

        result = _run(_get_registry_counts(None))
        assert result == (0, 0)

    def test_success(self):
        from src.api.routes.kb import _get_registry_counts

        reg = AsyncMock()
        reg.list_all = AsyncMock(return_value=[
            {"document_count": 10},
            {"document_count": 20},
        ])
        result = _run(_get_registry_counts(reg))
        assert result == (2, 30)

    def test_exception(self):
        from src.api.routes.kb import _get_registry_counts

        reg = AsyncMock()
        reg.list_all = AsyncMock(side_effect=RuntimeError("fail"))
        result = _run(_get_registry_counts(reg))
        assert result == (0, 0)


# ---------------------------------------------------------------------------
# _get_qdrant_chunk_counts
# ---------------------------------------------------------------------------
class TestGetQdrantChunkCounts:
    def test_no_collections_or_store(self):
        from src.api.routes.kb import _get_qdrant_chunk_counts

        result = _run(_get_qdrant_chunk_counts(None, None, 5))
        assert result == (0, 5)

    def test_success(self):
        from src.api.routes.kb import _get_qdrant_chunk_counts

        config = MagicMock()
        config.collection_prefix = "kb"
        provider = MagicMock()
        provider.config = config
        collections = AsyncMock()
        collections._provider = provider
        collections.get_existing_collection_names = AsyncMock(
            return_value=["kb_a", "kb_b"]
        )
        store = AsyncMock()
        store.count = AsyncMock(side_effect=[100, 200])

        result = _run(_get_qdrant_chunk_counts(collections, store, 0))
        assert result == (300, 2)  # total_chunks, len(raw_names)

    def test_count_error_skipped(self):
        from src.api.routes.kb import _get_qdrant_chunk_counts

        config = MagicMock()
        config.collection_prefix = "kb"
        provider = MagicMock()
        provider.config = config
        collections = AsyncMock()
        collections._provider = provider
        collections.get_existing_collection_names = AsyncMock(
            return_value=["kb_a"]
        )
        store = AsyncMock()
        store.count = AsyncMock(side_effect=RuntimeError("timeout"))

        result = _run(_get_qdrant_chunk_counts(collections, store, 3))
        assert result == (0, 3)

    def test_names_error(self):
        from src.api.routes.kb import _get_qdrant_chunk_counts

        collections = AsyncMock()
        collections.get_existing_collection_names = AsyncMock(
            side_effect=RuntimeError("conn")
        )
        store = AsyncMock()
        result = _run(_get_qdrant_chunk_counts(collections, store, 7))
        assert result == (0, 7)

    def test_fallback_total_kbs_used_when_nonzero(self):
        from src.api.routes.kb import _get_qdrant_chunk_counts

        config = MagicMock()
        config.collection_prefix = "kb"
        provider = MagicMock()
        provider.config = config
        collections = AsyncMock()
        collections._provider = provider
        collections.get_existing_collection_names = AsyncMock(
            return_value=["kb_a"]
        )
        store = AsyncMock()
        store.count = AsyncMock(return_value=50)

        result = _run(_get_qdrant_chunk_counts(collections, store, 10))
        # fallback_total_kbs=10 is nonzero so used as-is
        assert result == (50, 10)


# ---------------------------------------------------------------------------
# _get_avg_quality_score
# ---------------------------------------------------------------------------
class TestGetAvgQualityScore:
    def test_no_collections(self):
        from src.api.routes.kb import _get_avg_quality_score

        result = _run(_get_avg_quality_score(None, None, "http://x"))
        assert result == 0.0

    def test_success(self):
        from src.api.routes.kb import _get_avg_quality_score

        collections = AsyncMock()
        collections.get_existing_collection_names = AsyncMock(
            return_value=["kb_a"]
        )
        store = MagicMock()

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "result": {
                "points": [
                    {"payload": {"quality_score": 8.0}},
                    {"payload": {"quality_score": 6.0}},
                    {"payload": {"quality_score": 0}},  # skipped
                ],
            }
        }

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = _run(
                _get_avg_quality_score(collections, store, "http://q:6333")
            )
        assert result == 7.0  # (8+6)/2

    def test_exception_returns_zero(self):
        from src.api.routes.kb import _get_avg_quality_score

        collections = AsyncMock()
        collections.get_existing_collection_names = AsyncMock(
            side_effect=RuntimeError("fail")
        )
        store = MagicMock()

        result = _run(
            _get_avg_quality_score(collections, store, "http://q:6333")
        )
        assert result == 0.0

    def test_non_200_skipped(self):
        from src.api.routes.kb import _get_avg_quality_score

        collections = AsyncMock()
        collections.get_existing_collection_names = AsyncMock(
            return_value=["kb_a"]
        )
        store = MagicMock()

        mock_resp = MagicMock()
        mock_resp.status_code = 500

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = _run(
                _get_avg_quality_score(collections, store, "http://q:6333")
            )
        assert result == 0.0


# ---------------------------------------------------------------------------
# admin_kb_aggregation
# ---------------------------------------------------------------------------
class TestAdminKbAggregation:
    def test_empty_state(self):
        from src.api.routes.kb import admin_kb_aggregation

        from src.auth.dependencies import OrgContext

        state = _mock_state(
            kb_registry=None,
            qdrant_collections=None,
            qdrant_store=None,
        )
        with (
            patch("src.api.routes.kb._get_state", return_value=state),
            patch(
                "src.api.routes.kb._default_qdrant_url",
                return_value="http://q:6333",
            ),
        ):
            result = _run(admin_kb_aggregation(
                org=OrgContext(id="default-org", user_role_in_org="OWNER"),
            ))
        assert result["total_kbs"] == 0
        assert result["total_chunks"] == 0
        assert result["avg_quality_score"] == 0.0


# ---------------------------------------------------------------------------
# clear_search_cache
# ---------------------------------------------------------------------------
class TestClearSearchCache:
    def test_no_cache(self):
        from src.api.routes.kb import clear_search_cache

        state = _mock_state(search_cache=None)
        with patch("src.api.routes.kb._get_state", return_value=state):
            result = _run(clear_search_cache())
        assert result["success"] is True
        assert result["deleted"] == 0

    def test_cache_cleared(self):
        from src.api.routes.kb import clear_search_cache

        cache = AsyncMock()
        cache.clear = AsyncMock(return_value=15)
        state = _mock_state(search_cache=cache)
        with patch("src.api.routes.kb._get_state", return_value=state):
            result = _run(clear_search_cache())
        assert result["deleted"] == 15

    def test_cache_error_500(self):
        from src.api.routes.kb import clear_search_cache

        cache = AsyncMock()
        cache.clear = AsyncMock(side_effect=RuntimeError("redis down"))
        state = _mock_state(search_cache=cache)
        with patch("src.api.routes.kb._get_state", return_value=state):
            with pytest.raises(Exception) as exc_info:
                _run(clear_search_cache())
            assert exc_info.value.status_code == 500


# ---------------------------------------------------------------------------
# admin_get_kb
# ---------------------------------------------------------------------------
class TestAdminGetKb:
    def _org(self):
        from src.auth.dependencies import OrgContext
        return OrgContext(id="default-org", user_role_in_org="OWNER")

    def test_from_registry(self):
        from src.api.routes.kb import admin_get_kb

        reg = AsyncMock()
        reg.get_kb = AsyncMock(
            return_value={"kb_id": "kb1", "name": "Test"}
        )
        state = _mock_state(kb_registry=reg, qdrant_store=None)
        with patch("src.api.routes.kb._get_state", return_value=state):
            result = _run(admin_get_kb("kb1", org=self._org()))
        assert result["name"] == "Test"

    def test_registry_returns_none_404(self):
        """B-0 Day 3: registry returning None for the caller's org now means
        404 (not Qdrant fallback) — existence of foreign KBs is not leaked.
        """
        from src.api.routes.kb import admin_get_kb
        from fastapi import HTTPException

        reg = AsyncMock()
        reg.get_kb = AsyncMock(return_value=None)
        store = AsyncMock()
        store.count = AsyncMock(return_value=99)
        state = _mock_state(kb_registry=reg, qdrant_store=store)
        with patch("src.api.routes.kb._get_state", return_value=state):
            with pytest.raises(HTTPException) as exc_info:
                _run(admin_get_kb("missing", org=self._org()))
        assert exc_info.value.status_code == 404

    def test_registry_exception_fallback(self):
        from src.api.routes.kb import admin_get_kb

        reg = AsyncMock()
        reg.get_kb = AsyncMock(side_effect=RuntimeError("db err"))
        state = _mock_state(kb_registry=reg, qdrant_store=None)
        with patch("src.api.routes.kb._get_state", return_value=state):
            result = _run(admin_get_kb("broken", org=self._org()))
        assert result["kb_id"] == "broken"
        assert result["chunk_count"] == 0

    def test_no_registry_no_store(self):
        from src.api.routes.kb import admin_get_kb

        state = _mock_state(kb_registry=None, qdrant_store=None)
        with patch("src.api.routes.kb._get_state", return_value=state):
            result = _run(admin_get_kb("kb2", org=self._org()))
        assert result["kb_id"] == "kb2"
        assert result["chunk_count"] == 0
        assert result["status"] == "active"

    def test_store_count_error_fallback(self):
        """When registry is unavailable (raises), Qdrant fallback runs even if
        store.count fails. Registry-returns-None vs raises is the difference
        between 404 (foreign tenant) and graceful fallback (registry down)."""
        from src.api.routes.kb import admin_get_kb

        reg = AsyncMock()
        reg.get_kb = AsyncMock(side_effect=RuntimeError("db err"))
        store = AsyncMock()
        store.count = AsyncMock(side_effect=RuntimeError("timeout"))
        state = _mock_state(kb_registry=reg, qdrant_store=store)
        with patch("src.api.routes.kb._get_state", return_value=state):
            result = _run(admin_get_kb("kb3", org=self._org()))
        assert result["chunk_count"] == 0


# ---------------------------------------------------------------------------
# admin_update_kb
# ---------------------------------------------------------------------------
class TestAdminUpdateKb:
    def test_returns_success(self):
        from src.api.routes.kb import admin_update_kb

        result = _run(admin_update_kb("kb1", {"name": "Updated"}))
        assert result["success"] is True
        assert result["kb_id"] == "kb1"


# ---------------------------------------------------------------------------
# admin_delete_kb
# ---------------------------------------------------------------------------
class TestAdminDeleteKb:
    def test_no_provider_503(self):
        from src.api.routes.kb import admin_delete_kb

        state = _mock_state(qdrant_provider=None)
        with patch("src.api.routes.kb._get_state", return_value=state):
            with pytest.raises(Exception) as exc_info:
                _run(admin_delete_kb("x"))
            assert exc_info.value.status_code == 503

    def test_success(self):
        from src.api.routes.kb import admin_delete_kb

        client = AsyncMock()
        client.delete_collection = AsyncMock()
        provider = AsyncMock()
        provider.ensure_client = AsyncMock(return_value=client)
        coll = MagicMock()
        coll.get_collection_name = MagicMock(return_value="kb_del")
        state = _mock_state(
            qdrant_provider=provider, qdrant_collections=coll
        )
        with patch("src.api.routes.kb._get_state", return_value=state):
            result = _run(admin_delete_kb("del"))
        assert result["success"] is True

    def test_error_500(self):
        from src.api.routes.kb import admin_delete_kb

        provider = AsyncMock()
        provider.ensure_client = AsyncMock(
            side_effect=RuntimeError("err")
        )
        state = _mock_state(
            qdrant_provider=provider, qdrant_collections=None
        )
        with patch("src.api.routes.kb._get_state", return_value=state):
            with pytest.raises(Exception) as exc_info:
                _run(admin_delete_kb("fail"))
            assert exc_info.value.status_code == 500


# ---------------------------------------------------------------------------
# admin_kb_stats
# ---------------------------------------------------------------------------
class TestAdminKbStats:
    def test_no_store(self):
        from src.api.routes.kb import admin_kb_stats

        state = _mock_state(qdrant_store=None)
        with patch("src.api.routes.kb._get_state", return_value=state):
            result = _run(admin_kb_stats("kb1"))
        assert result["total_chunks"] == 0
        assert result["kb_id"] == "kb1"

    def test_with_store(self):
        from src.api.routes.kb import admin_kb_stats

        store = AsyncMock()
        store.count = AsyncMock(return_value=150)
        state = _mock_state(qdrant_store=store)
        with patch("src.api.routes.kb._get_state", return_value=state):
            result = _run(admin_kb_stats("kb2"))
        assert result["total_chunks"] == 150

    def test_store_error(self):
        from src.api.routes.kb import admin_kb_stats

        store = AsyncMock()
        store.count = AsyncMock(side_effect=RuntimeError("fail"))
        state = _mock_state(qdrant_store=store)
        with patch("src.api.routes.kb._get_state", return_value=state):
            result = _run(admin_kb_stats("kb3"))
        assert result["total_chunks"] == 0


# ---------------------------------------------------------------------------
# _scroll_kb_documents
# ---------------------------------------------------------------------------
class TestScrollKbDocuments:
    def test_empty_response(self):
        from src.api.routes.kb import _scroll_kb_documents

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"result": {"points": []}}

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = _run(
                _scroll_kb_documents("http://q:6333", "kb_test", 100)
            )
        assert result == {}

    def test_collects_unique_docs(self):
        from src.api.routes.kb import _scroll_kb_documents

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "result": {
                "points": [
                    {"payload": {
                        "doc_id": "d1",
                        "document_name": "Doc 1",
                        "source_type": "file",
                    }},
                    {"payload": {
                        "doc_id": "d1",  # duplicate
                        "document_name": "Doc 1",
                    }},
                    {"payload": {
                        "doc_id": "d2",
                        "document_name": "Doc 2",
                    }},
                ],
                "next_page_offset": None,
            }
        }

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = _run(
                _scroll_kb_documents("http://q:6333", "kb_test", 100)
            )
        assert len(result) == 2
        assert "d1" in result
        assert "d2" in result

    def test_non_200_stops(self):
        from src.api.routes.kb import _scroll_kb_documents

        mock_resp = MagicMock()
        mock_resp.status_code = 404

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = _run(
                _scroll_kb_documents("http://q:6333", "kb_test", 100)
            )
        assert result == {}

    def test_empty_doc_id_skipped(self):
        from src.api.routes.kb import _scroll_kb_documents

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "result": {
                "points": [
                    {"payload": {"doc_id": "", "document_name": "X"}},
                    {"payload": {"document_name": "Y"}},  # no doc_id
                ],
                "next_page_offset": None,
            }
        }

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = _run(
                _scroll_kb_documents("http://q:6333", "kb_test", 100)
            )
        assert result == {}


# ---------------------------------------------------------------------------
# admin_kb_documents
# ---------------------------------------------------------------------------
class TestAdminKbDocuments:
    def test_no_collections(self):
        from src.api.routes.kb import admin_kb_documents

        state = _mock_state(qdrant_collections=None)
        with (
            patch("src.api.routes.kb._get_state", return_value=state),
            patch(
                "src.api.routes.kb._default_qdrant_url",
                return_value="http://q:6333",
            ),
        ):
            result = _run(admin_kb_documents("kb1"))
        assert result["documents"] == []
        assert result["total"] == 0

    def test_pagination(self):
        from src.api.routes.kb import admin_kb_documents

        coll = MagicMock()
        coll.get_collection_name = MagicMock(return_value="kb_test")
        state = _mock_state(qdrant_collections=coll)

        with (
            patch("src.api.routes.kb._get_state", return_value=state),
            patch(
                "src.api.routes.kb._default_qdrant_url",
                return_value="http://q:6333",
            ),
            patch(
                "src.api.routes.kb._scroll_kb_documents",
                new_callable=AsyncMock,
                return_value={
                    f"d{i}": {"doc_id": f"d{i}", "title": f"Doc {i}"}
                    for i in range(5)
                },
            ),
        ):
            result = _run(admin_kb_documents("kb1", page=1, page_size=2))
        assert len(result["documents"]) == 2
        assert result["total"] == 5
        assert result["page"] == 1

    def test_exception_returns_empty(self):
        from src.api.routes.kb import admin_kb_documents

        coll = MagicMock()
        coll.get_collection_name = MagicMock(
            side_effect=RuntimeError("oops")
        )
        state = _mock_state(qdrant_collections=coll)
        with (
            patch("src.api.routes.kb._get_state", return_value=state),
            patch(
                "src.api.routes.kb._default_qdrant_url",
                return_value="http://q:6333",
            ),
        ):
            result = _run(admin_kb_documents("kb1"))
        assert result["documents"] == []


# ---------------------------------------------------------------------------
# admin_kb_categories
# ---------------------------------------------------------------------------
class TestAdminKbCategories:
    def test_no_collections(self):
        from src.api.routes.kb import admin_kb_categories

        state = _mock_state(qdrant_collections=None)
        with (
            patch("src.api.routes.kb._get_state", return_value=state),
            patch(
                "src.api.routes.kb._default_qdrant_url",
                return_value="http://q:6333",
            ),
        ):
            result = _run(admin_kb_categories("kb1"))
        assert result["categories"] == []

    def test_success(self):
        from src.api.routes.kb import admin_kb_categories

        coll = MagicMock()
        coll.get_collection_name = MagicMock(return_value="kb_test")
        state = _mock_state(qdrant_collections=coll)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "result": {
                "points": [
                    {"payload": {"l1_category": "FAQ"}},
                    {"payload": {"l1_category": "FAQ"}},
                    {"payload": {"l1_category": "매뉴얼"}},
                    {"payload": {}},  # defaults to 기타
                ],
                "next_page_offset": None,
            }
        }

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with (
            patch("src.api.routes.kb._get_state", return_value=state),
            patch(
                "src.api.routes.kb._default_qdrant_url",
                return_value="http://q:6333",
            ),
            patch("httpx.AsyncClient", return_value=mock_client),
        ):
            result = _run(admin_kb_categories("kb1"))
        assert result["total"] == 3
        assert result["categories"][0]["name"] == "FAQ"
        assert result["categories"][0]["document_count"] == 2

    def test_exception_returns_empty(self):
        from src.api.routes.kb import admin_kb_categories

        coll = MagicMock()
        coll.get_collection_name = MagicMock(
            side_effect=RuntimeError("fail")
        )
        state = _mock_state(qdrant_collections=coll)
        with (
            patch("src.api.routes.kb._get_state", return_value=state),
            patch(
                "src.api.routes.kb._default_qdrant_url",
                return_value="http://q:6333",
            ),
        ):
            result = _run(admin_kb_categories("kb1"))
        assert result["categories"] == []


# ---------------------------------------------------------------------------
# admin_kb_trust_scores
# ---------------------------------------------------------------------------
class TestAdminKbTrustScores:
    def test_no_repo(self):
        from src.api.routes.kb import admin_kb_trust_scores

        state = _mock_state(trust_score_repo=None)
        with patch("src.api.routes.kb._get_state", return_value=state):
            result = _run(admin_kb_trust_scores("kb1"))
        assert result["items"] == []

    def test_success(self):
        from src.api.routes.kb import admin_kb_trust_scores

        repo = AsyncMock()
        repo.get_by_kb = AsyncMock(
            return_value=[{"doc_id": "d1", "kts_score": 0.8}]
        )
        state = _mock_state(trust_score_repo=repo)
        with patch("src.api.routes.kb._get_state", return_value=state):
            result = _run(admin_kb_trust_scores("kb1"))
        assert len(result["items"]) == 1

    def test_error(self):
        from src.api.routes.kb import admin_kb_trust_scores

        repo = AsyncMock()
        repo.get_by_kb = AsyncMock(side_effect=RuntimeError("db"))
        state = _mock_state(trust_score_repo=repo)
        with patch("src.api.routes.kb._get_state", return_value=state):
            result = _run(admin_kb_trust_scores("kb1"))
        assert result["items"] == []


# ---------------------------------------------------------------------------
# admin_kb_trust_score_distribution
# ---------------------------------------------------------------------------
class TestAdminKbTrustScoreDistribution:
    def test_no_repo(self):
        from src.api.routes.kb import admin_kb_trust_score_distribution

        state = _mock_state(trust_score_repo=None)
        with patch("src.api.routes.kb._get_state", return_value=state):
            result = _run(admin_kb_trust_score_distribution("kb1"))
        assert result["distribution"] == {}

    def test_success(self):
        from src.api.routes.kb import admin_kb_trust_score_distribution

        repo = AsyncMock()
        repo.get_by_kb = AsyncMock(return_value=[
            {"confidence_tier": "HIGH", "kts_score": 0.9},
            {"confidence_tier": "HIGH", "kts_score": 0.8},
            {"confidence_tier": "LOW", "kts_score": 0.3},
            {"confidence_tier": None, "kts_score": 0.5},
            {"confidence_tier": "UNKNOWN_TIER", "kts_score": 0.4},
        ])
        state = _mock_state(trust_score_repo=repo)
        with patch("src.api.routes.kb._get_state", return_value=state):
            result = _run(admin_kb_trust_score_distribution("kb1"))
        assert result["distribution"]["HIGH"] == 2
        assert result["distribution"]["LOW"] == 1
        assert result["distribution"]["UNCERTAIN"] == 2
        assert result["total"] == 5

    def test_error(self):
        from src.api.routes.kb import admin_kb_trust_score_distribution

        repo = AsyncMock()
        repo.get_by_kb = AsyncMock(side_effect=RuntimeError("err"))
        state = _mock_state(trust_score_repo=repo)
        with patch("src.api.routes.kb._get_state", return_value=state):
            result = _run(admin_kb_trust_score_distribution("kb1"))
        assert result["avg_score"] == 0


# ---------------------------------------------------------------------------
# Static return endpoints
# ---------------------------------------------------------------------------
class TestStaticEndpoints:
    def test_lifecycle(self):
        from src.api.routes.kb import admin_kb_lifecycle

        result = _run(admin_kb_lifecycle("kb1"))
        assert result["kb_id"] == "kb1"
        assert result["stage"] == "active"

    def test_coverage_gaps(self):
        from src.api.routes.kb import admin_kb_coverage_gaps

        result = _run(admin_kb_coverage_gaps("kb1"))
        assert result["coverage_score"] == 1.0
        assert result["gaps"] == []

    def test_impact(self):
        from src.api.routes.kb import admin_kb_impact

        result = _run(admin_kb_impact("kb1"))
        assert result["total_queries_served"] == 0

    def test_impact_rankings(self):
        from src.api.routes.kb import admin_kb_impact_rankings

        result = _run(admin_kb_impact_rankings("kb1"))
        assert result["rankings"] == []

    def test_freshness(self):
        from src.api.routes.kb import admin_kb_freshness

        result = _run(admin_kb_freshness("kb1"))
        assert result["freshness_score"] == 0.0

    def test_value_tiers(self):
        from src.api.routes.kb import admin_kb_value_tiers

        result = _run(admin_kb_value_tiers("kb1"))
        assert result["tiers"] == []

    def test_members(self):
        from src.api.routes.kb import admin_kb_members

        result = _run(admin_kb_members("kb1"))
        assert result["members"] == []

    def test_add_member(self):
        from src.api.routes.kb import admin_add_kb_member

        result = _run(admin_add_kb_member("kb1", {"user_id": "u1"}))
        assert result["success"] is True

    def test_remove_member(self):
        from src.api.routes.kb import admin_remove_kb_member

        result = _run(admin_remove_kb_member("kb1", "m1"))
        assert result["member_id"] == "m1"


# ---------------------------------------------------------------------------
# admin_list_kbs (admin route delegates to _list_kbs_impl)
# ---------------------------------------------------------------------------
# Direct route-handler tests bypass FastAPI's dependency injection, so an
# explicit OrgContext stand-in is supplied wherever Depends(get_current_org)
# would otherwise arrive at runtime.
def _org(org_id: str = "default-org") -> "OrgContext":
    from src.auth.dependencies import OrgContext
    return OrgContext(id=org_id, user_role_in_org="OWNER")


class TestAdminListKbs:
    def test_delegates_with_params(self):
        from src.api.routes.kb import admin_list_kbs

        state = _mock_state(
            kb_registry=None, qdrant_store=None, qdrant_collections=None
        )
        with patch("src.api.routes.kb._get_state", return_value=state):
            result = _run(admin_list_kbs(tier="dept", status=None, org=_org()))
        assert result == {"kbs": []}

    def test_delegates_with_status(self):
        from src.api.routes.kb import admin_list_kbs

        reg = AsyncMock()
        reg.list_by_status = AsyncMock(
            return_value=[{"kb_id": "a"}]
        )
        state = _mock_state(
            kb_registry=reg, qdrant_store=None, qdrant_collections=None
        )
        with patch("src.api.routes.kb._get_state", return_value=state):
            result = _run(admin_list_kbs(tier=None, status="active", org=_org()))
        assert len(result["kbs"]) == 1


# ---------------------------------------------------------------------------
# list_kbs (original route)
# ---------------------------------------------------------------------------
class TestListKbs:
    def test_delegates(self):
        from src.api.routes.kb import list_kbs

        state = _mock_state(
            kb_registry=None, qdrant_store=None, qdrant_collections=None
        )
        with patch("src.api.routes.kb._get_state", return_value=state):
            result = _run(list_kbs(org=_org()))
        assert result == {"kbs": []}
