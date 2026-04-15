"""Distill Repository — facade.

실제 구현은 ``repositories/`` 패키지로 분리.
기존 import 호환: ``from src.distill.repository import DistillRepository``
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker

from src.distill.repositories.base_model import DistillBaseModelRepository
from src.distill.repositories.build import DistillBuildRepository
from src.distill.repositories.edge_log import DistillEdgeLogRepository
from src.distill.repositories.edge_server import DistillEdgeServerRepository
from src.distill.repositories.profile import DistillProfileRepository
from src.distill.repositories.training_data import DistillTrainingDataRepository

logger = logging.getLogger(__name__)


class DistillRepository:
    """통합 repository facade — 하위 도메인 repos 조합."""

    def __init__(self, session_maker: async_sessionmaker) -> None:
        self._profiles = DistillProfileRepository(session_maker)
        self._builds = DistillBuildRepository(session_maker)
        self._edge_logs = DistillEdgeLogRepository(session_maker)
        self._training_data = DistillTrainingDataRepository(session_maker)
        self._edge_servers = DistillEdgeServerRepository(session_maker)
        self._base_models = DistillBaseModelRepository(session_maker)

    # --- Base Models (레지스트리) ---
    async def list_base_models(
        self, *, enabled_only: bool = True,
    ) -> list[dict[str, Any]]:
        return await self._base_models.list_all(enabled_only=enabled_only)

    async def get_base_model(self, hf_id: str) -> dict[str, Any] | None:
        return await self._base_models.get(hf_id)

    async def insert_base_model_if_missing(self, data: dict[str, Any]) -> bool:
        """Seed 전용 — 없으면 삽입, 있으면 스킵 (admin 편집 보존)."""
        return await self._base_models.insert_if_missing(data)

    async def upsert_base_model(self, data: dict[str, Any]) -> dict[str, Any]:
        """Admin API 전용 — 덮어쓰기."""
        return await self._base_models.upsert(data)

    async def delete_base_model(self, hf_id: str) -> bool:
        return await self._base_models.delete(hf_id)

    # --- Profiles ---
    async def list_profiles(self) -> list[dict[str, Any]]:
        return await self._profiles.list_all()

    async def get_profile(self, name: str) -> dict[str, Any] | None:
        return await self._profiles.get(name)

    async def create_profile(self, data: dict[str, Any]) -> dict[str, Any]:
        return await self._profiles.create(data)

    async def update_profile(self, name: str, data: dict[str, Any]) -> dict[str, Any] | None:
        return await self._profiles.update(name, data)

    async def delete_profile(self, name: str) -> bool:
        return await self._profiles.delete(name)

    # --- Builds ---
    async def create_build(self, **kwargs: Any) -> dict[str, Any]:
        return await self._builds.create(**kwargs)

    async def update_build(self, build_id: str, **kwargs: Any) -> dict[str, Any] | None:
        return await self._builds.update(build_id, **kwargs)

    async def get_build(self, build_id: str) -> dict[str, Any] | None:
        return await self._builds.get(build_id)

    async def list_builds(
        self, profile_name: str | None = None, limit: int = 50,
    ) -> list[dict[str, Any]]:
        return await self._builds.list_all(profile_name, limit)

    async def get_latest_build(
        self, profile_name: str, status: str = "completed",
    ) -> dict[str, Any] | None:
        return await self._builds.get_latest(profile_name, status)

    async def list_version_history(
        self, profile_name: str,
    ) -> list[dict[str, Any]]:
        return await self._builds.list_version_history(profile_name)

    async def rollback_to(
        self, build_id: str, current_build_id: str,
    ) -> dict[str, Any] | None:
        return await self._builds.rollback_to(build_id, current_build_id)

    # --- Edge Logs ---
    async def save_edge_logs(self, logs: list[dict[str, Any]]) -> int:
        return await self._edge_logs.save_batch(logs)

    async def list_edge_logs(
        self, profile_name: str, store_id: str | None = None,
        success: bool | None = None, limit: int = 50, offset: int = 0,
    ) -> dict[str, Any]:
        return await self._edge_logs.list_logs(
            profile_name, store_id, success, limit, offset,
        )

    async def get_edge_analytics(
        self, profile_name: str, days: int = 7,
    ) -> dict[str, Any]:
        return await self._edge_logs.get_analytics(profile_name, days)

    async def list_failed_queries(
        self, profile_name: str, limit: int = 50,
    ) -> list[dict[str, Any]]:
        return await self._edge_logs.list_failed(profile_name, limit)

    # --- Training Data ---
    async def save_training_data(self, entries: list[dict[str, Any]]) -> int:
        return await self._training_data.save_batch(entries)

    save_training_data_batch = save_training_data  # alias

    async def list_training_data(
        self, profile_name: str, status: str | None = None,
        source_type: str | None = None, batch_id: str | None = None,
        sort_by: str = "created_at", sort_order: str = "desc",
        limit: int = 50, offset: int = 0,
    ) -> dict[str, Any]:
        return await self._training_data.list_data(
            profile_name, status, source_type, batch_id,
            sort_by, sort_order, limit, offset,
        )

    async def get_training_data_stats(self, profile_name: str) -> dict[str, Any]:
        return await self._training_data.get_stats(profile_name)

    async def update_training_data_status(
        self, ids: list[str], status: str,
    ) -> int:
        return await self._training_data.update_status(ids, status)

    async def get_batch_stats(self, batch_id: str) -> dict[str, Any]:
        return await self._training_data.get_batch_stats(batch_id)

    async def delete_training_data_by_source(
        self, profile_name: str, source_type: str,
    ) -> int:
        return await self._training_data.delete_by_source_type(profile_name, source_type)

    async def delete_training_data_by_batch(self, batch_id: str) -> int:
        return await self._training_data.delete_by_batch(batch_id)

    async def delete_build(self, build_id: str) -> bool:
        return await self._builds.delete(build_id)

    async def bulk_update_training_data(
        self, updates: list[dict[str, Any]],
    ) -> int:
        return await self._training_data.bulk_update_with_edit(updates)

    # --- Edge Servers ---
    async def register_edge_server(self, **kwargs) -> dict[str, Any]:
        return await self._edge_servers.register_edge_server(**kwargs)

    async def upsert_heartbeat(
        self, data: dict[str, Any], api_key: str,
    ) -> dict[str, Any]:
        return await self._edge_servers.upsert_heartbeat(data, api_key)

    async def list_edge_servers(
        self, profile_name: str | None = None, status: str | None = None,
    ) -> list[dict[str, Any]]:
        return await self._edge_servers.list_servers(profile_name, status)

    async def get_edge_server(self, store_id: str) -> dict[str, Any] | None:
        return await self._edge_servers.get_server(store_id)

    async def delete_edge_server(self, store_id: str) -> bool:
        return await self._edge_servers.delete_server(store_id)

    async def request_server_update(
        self, store_id: str, update_type: str,
    ) -> dict[str, Any]:
        return await self._edge_servers.request_update(store_id, update_type)

    async def bulk_request_server_update(
        self, profile_name: str, update_type: str,
    ) -> int:
        return await self._edge_servers.bulk_request_update(profile_name, update_type)

    async def get_fleet_stats(self, profile_name: str) -> dict[str, Any]:
        return await self._edge_servers.get_fleet_stats(profile_name)
