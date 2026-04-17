"""Tests for src/distill/repository.py — facade delegation."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.distill.repository import DistillRepository


@pytest.fixture
def repo() -> DistillRepository:
    r = DistillRepository.__new__(DistillRepository)
    r._profiles = MagicMock()
    r._builds = MagicMock()
    r._edge_logs = MagicMock()
    r._training_data = MagicMock()
    r._edge_servers = MagicMock()
    r._base_models = MagicMock()
    return r


class TestBaseModelDelegation:
    @pytest.mark.asyncio
    async def test_list_base_models(self, repo: DistillRepository) -> None:
        repo._base_models.list_all = AsyncMock(return_value=[{"id": "1"}])
        result = await repo.list_base_models()
        repo._base_models.list_all.assert_awaited_once_with(
            enabled_only=True,
        )
        assert result == [{"id": "1"}]

    @pytest.mark.asyncio
    async def test_get_base_model(self, repo: DistillRepository) -> None:
        repo._base_models.get = AsyncMock(return_value={"hf_id": "x"})
        result = await repo.get_base_model("x")
        assert result == {"hf_id": "x"}

    @pytest.mark.asyncio
    async def test_insert_base_model_if_missing(
        self, repo: DistillRepository,
    ) -> None:
        repo._base_models.insert_if_missing = AsyncMock(return_value=True)
        result = await repo.insert_base_model_if_missing({"hf_id": "y"})
        assert result is True

    @pytest.mark.asyncio
    async def test_upsert_base_model(
        self, repo: DistillRepository,
    ) -> None:
        repo._base_models.upsert = AsyncMock(return_value={"hf_id": "z"})
        result = await repo.upsert_base_model({"hf_id": "z"})
        assert result == {"hf_id": "z"}

    @pytest.mark.asyncio
    async def test_delete_base_model(
        self, repo: DistillRepository,
    ) -> None:
        repo._base_models.delete = AsyncMock(return_value=True)
        assert await repo.delete_base_model("x") is True


class TestProfileDelegation:
    @pytest.mark.asyncio
    async def test_list(self, repo: DistillRepository) -> None:
        repo._profiles.list_all = AsyncMock(return_value=[])
        assert await repo.list_profiles() == []

    @pytest.mark.asyncio
    async def test_get(self, repo: DistillRepository) -> None:
        repo._profiles.get = AsyncMock(return_value=None)
        assert await repo.get_profile("p") is None

    @pytest.mark.asyncio
    async def test_create(self, repo: DistillRepository) -> None:
        repo._profiles.create = AsyncMock(return_value={"name": "p"})
        assert await repo.create_profile({"name": "p"}) == {"name": "p"}

    @pytest.mark.asyncio
    async def test_update(self, repo: DistillRepository) -> None:
        repo._profiles.update = AsyncMock(return_value={"name": "p"})
        assert await repo.update_profile("p", {}) == {"name": "p"}

    @pytest.mark.asyncio
    async def test_delete(self, repo: DistillRepository) -> None:
        repo._profiles.delete = AsyncMock(return_value=True)
        assert await repo.delete_profile("p") is True


class TestBuildDelegation:
    @pytest.mark.asyncio
    async def test_create(self, repo: DistillRepository) -> None:
        repo._builds.create = AsyncMock(return_value={"id": "b1"})
        assert await repo.create_build(profile="p") == {"id": "b1"}

    @pytest.mark.asyncio
    async def test_update(self, repo: DistillRepository) -> None:
        repo._builds.update = AsyncMock(return_value={"id": "b1"})
        assert await repo.update_build("b1", status="done") == {"id": "b1"}

    @pytest.mark.asyncio
    async def test_get(self, repo: DistillRepository) -> None:
        repo._builds.get = AsyncMock(return_value=None)
        assert await repo.get_build("b1") is None

    @pytest.mark.asyncio
    async def test_list(self, repo: DistillRepository) -> None:
        repo._builds.list_all = AsyncMock(return_value=[])
        assert await repo.list_builds(profile_name="p") == []

    @pytest.mark.asyncio
    async def test_get_latest(self, repo: DistillRepository) -> None:
        repo._builds.get_latest = AsyncMock(return_value=None)
        assert await repo.get_latest_build("p") is None

    @pytest.mark.asyncio
    async def test_list_version_history(
        self, repo: DistillRepository,
    ) -> None:
        repo._builds.list_version_history = AsyncMock(return_value=[])
        assert await repo.list_version_history("p") == []

    @pytest.mark.asyncio
    async def test_rollback(self, repo: DistillRepository) -> None:
        repo._builds.rollback_to = AsyncMock(return_value=None)
        assert await repo.rollback_to("b1", "b2") is None

    @pytest.mark.asyncio
    async def test_delete(self, repo: DistillRepository) -> None:
        repo._builds.delete = AsyncMock(return_value=True)
        assert await repo.delete_build("b1") is True


class TestEdgeLogDelegation:
    @pytest.mark.asyncio
    async def test_save(self, repo: DistillRepository) -> None:
        repo._edge_logs.save_batch = AsyncMock(return_value=5)
        assert await repo.save_edge_logs([{}] * 5) == 5

    @pytest.mark.asyncio
    async def test_list(self, repo: DistillRepository) -> None:
        repo._edge_logs.list_logs = AsyncMock(return_value={"items": []})
        result = await repo.list_edge_logs("p", store_id="s1")
        assert result == {"items": []}

    @pytest.mark.asyncio
    async def test_analytics(self, repo: DistillRepository) -> None:
        repo._edge_logs.get_analytics = AsyncMock(return_value={})
        assert await repo.get_edge_analytics("p") == {}

    @pytest.mark.asyncio
    async def test_failed(self, repo: DistillRepository) -> None:
        repo._edge_logs.list_failed = AsyncMock(return_value=[])
        assert await repo.list_failed_queries("p") == []


class TestTrainingDataDelegation:
    @pytest.mark.asyncio
    async def test_save(self, repo: DistillRepository) -> None:
        repo._training_data.save_batch = AsyncMock(return_value=3)
        assert await repo.save_training_data([{}] * 3) == 3

    def test_save_alias(self, repo: DistillRepository) -> None:
        assert repo.save_training_data_batch == repo.save_training_data

    @pytest.mark.asyncio
    async def test_list(self, repo: DistillRepository) -> None:
        repo._training_data.list_data = AsyncMock(
            return_value={"items": []},
        )
        result = await repo.list_training_data("p", status="approved")
        assert result == {"items": []}

    @pytest.mark.asyncio
    async def test_stats(self, repo: DistillRepository) -> None:
        repo._training_data.get_stats = AsyncMock(return_value={})
        assert await repo.get_training_data_stats("p") == {}

    @pytest.mark.asyncio
    async def test_update_status(self, repo: DistillRepository) -> None:
        repo._training_data.update_status = AsyncMock(return_value=2)
        assert await repo.update_training_data_status(["a"], "ok") == 2

    @pytest.mark.asyncio
    async def test_batch_stats(self, repo: DistillRepository) -> None:
        repo._training_data.get_batch_stats = AsyncMock(return_value={})
        assert await repo.get_batch_stats("b1") == {}

    @pytest.mark.asyncio
    async def test_delete_by_source(self, repo: DistillRepository) -> None:
        repo._training_data.delete_by_source_type = AsyncMock(
            return_value=5,
        )
        assert await repo.delete_training_data_by_source("p", "qa") == 5

    @pytest.mark.asyncio
    async def test_delete_by_batch(self, repo: DistillRepository) -> None:
        repo._training_data.delete_by_batch = AsyncMock(return_value=3)
        assert await repo.delete_training_data_by_batch("b1") == 3

    @pytest.mark.asyncio
    async def test_bulk_update(self, repo: DistillRepository) -> None:
        repo._training_data.bulk_update_with_edit = AsyncMock(
            return_value=2,
        )
        assert await repo.bulk_update_training_data([{}]) == 2


class TestEdgeServerDelegation:
    @pytest.mark.asyncio
    async def test_register(self, repo: DistillRepository) -> None:
        repo._edge_servers.register_edge_server = AsyncMock(
            return_value={"store_id": "s1"},
        )
        r = await repo.register_edge_server(store_id="s1")
        assert r == {"store_id": "s1"}

    @pytest.mark.asyncio
    async def test_heartbeat(self, repo: DistillRepository) -> None:
        repo._edge_servers.upsert_heartbeat = AsyncMock(
            return_value={"ok": True},
        )
        r = await repo.upsert_heartbeat({"store_id": "s1"}, "key")
        assert r == {"ok": True}

    @pytest.mark.asyncio
    async def test_list(self, repo: DistillRepository) -> None:
        repo._edge_servers.list_servers = AsyncMock(return_value=[])
        assert await repo.list_edge_servers("p") == []

    @pytest.mark.asyncio
    async def test_get(self, repo: DistillRepository) -> None:
        repo._edge_servers.get_server = AsyncMock(return_value=None)
        assert await repo.get_edge_server("s1") is None

    @pytest.mark.asyncio
    async def test_delete(self, repo: DistillRepository) -> None:
        repo._edge_servers.delete_server = AsyncMock(return_value=True)
        assert await repo.delete_edge_server("s1") is True

    @pytest.mark.asyncio
    async def test_request_update(self, repo: DistillRepository) -> None:
        repo._edge_servers.request_update = AsyncMock(
            return_value={"ok": True},
        )
        r = await repo.request_server_update("s1", "model")
        assert r == {"ok": True}

    @pytest.mark.asyncio
    async def test_bulk_update(self, repo: DistillRepository) -> None:
        repo._edge_servers.bulk_request_update = AsyncMock(return_value=3)
        assert await repo.bulk_request_server_update("p", "model") == 3

    @pytest.mark.asyncio
    async def test_fleet_stats(self, repo: DistillRepository) -> None:
        repo._edge_servers.get_fleet_stats = AsyncMock(return_value={})
        assert await repo.get_fleet_stats("p") == {}
