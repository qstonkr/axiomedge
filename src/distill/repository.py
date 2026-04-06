"""Distill Repository — facade.

실제 구현은 ``repositories/`` 패키지로 분리.
기존 import 호환: ``from src.distill.repository import DistillRepository``
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker

from src.distill.repositories.build import DistillBuildRepository
from src.distill.repositories.edge_log import DistillEdgeLogRepository
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

    async def list_training_data(
        self, profile_name: str, status: str | None = None,
        source_type: str | None = None, limit: int = 50, offset: int = 0,
    ) -> dict[str, Any]:
        return await self._training_data.list_data(
            profile_name, status, source_type, limit, offset,
        )

    async def get_training_data_stats(self, profile_name: str) -> dict[str, Any]:
        return await self._training_data.get_stats(profile_name)

    async def update_training_data_status(
        self, ids: list[str], status: str,
    ) -> int:
        return await self._training_data.update_status(ids, status)
