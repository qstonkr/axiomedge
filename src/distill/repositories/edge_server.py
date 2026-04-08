"""Edge Server Repository — 엣지 서버 CRUD + heartbeat + 업데이트 요청."""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.distill.models import DistillEdgeServerModel

logger = logging.getLogger(__name__)


def _hash_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode()).hexdigest()


class DistillEdgeServerRepository:

    def __init__(self, session_maker: async_sessionmaker) -> None:
        self._session_maker = session_maker

    async def upsert_heartbeat(
        self, data: dict[str, Any], api_key: str,
    ) -> dict[str, Any]:
        """heartbeat 수신: 없으면 등록 + API 키 해시 저장, 있으면 갱신 + 키 검증.

        Returns: {status, pending_model_update, pending_app_update, latest_*}
        """
        store_id = data.get("store_id", "")
        if not store_id:
            raise ValueError("store_id is required")

        key_hash = _hash_key(api_key)

        async with self._session_maker() as session:
            stmt = select(DistillEdgeServerModel).where(
                DistillEdgeServerModel.store_id == store_id
            )
            result = await session.execute(stmt)
            existing = result.scalar_one_or_none()

            now = datetime.now(timezone.utc)

            if existing:
                # 키 검증
                if existing.api_key_hash and existing.api_key_hash != key_hash:
                    raise PermissionError("Invalid API key for this server")

                # pending 플래그 읽기
                pending_model = existing.pending_model_update
                pending_app = existing.pending_app_update

                # 단일 UPDATE로 갱신 + pending 플래그 리셋
                values = {
                    "status": data.get("status", "online"),
                    "last_heartbeat": now,
                    "server_ip": data.get("server_ip"),
                    "os_type": data.get("os_type"),
                    "app_version": data.get("app_version"),
                    "model_version": data.get("model_version"),
                    "model_sha256": data.get("model_sha256"),
                    "cpu_info": data.get("cpu_info"),
                    "ram_total_mb": data.get("ram_total_mb"),
                    "ram_used_mb": data.get("ram_used_mb"),
                    "disk_free_mb": data.get("disk_free_mb"),
                    "avg_latency_ms": data.get("avg_latency_ms"),
                    "total_queries": data.get("total_queries", 0),
                    "success_rate": data.get("success_rate"),
                    "pending_model_update": False,
                    "pending_app_update": False,
                    "updated_at": now,
                }
                await session.execute(
                    update(DistillEdgeServerModel)
                    .where(DistillEdgeServerModel.store_id == store_id)
                    .values(**values)
                )

                await session.commit()
            else:
                # 신규 등록
                model = DistillEdgeServerModel(
                    id=str(uuid.uuid4()),
                    store_id=store_id,
                    profile_name=data.get("profile_name", "default"),
                    display_name=data.get("display_name", store_id),
                    status=data.get("status", "online"),
                    last_heartbeat=now,
                    server_ip=data.get("server_ip"),
                    os_type=data.get("os_type"),
                    app_version=data.get("app_version"),
                    model_version=data.get("model_version"),
                    model_sha256=data.get("model_sha256"),
                    cpu_info=data.get("cpu_info"),
                    ram_total_mb=data.get("ram_total_mb"),
                    ram_used_mb=data.get("ram_used_mb"),
                    disk_free_mb=data.get("disk_free_mb"),
                    avg_latency_ms=data.get("avg_latency_ms"),
                    total_queries=data.get("total_queries", 0),
                    success_rate=data.get("success_rate"),
                    api_key_hash=key_hash,
                )
                session.add(model)
                pending_model = False
                pending_app = False
                try:
                    await session.commit()
                except SQLAlchemyError as e:
                    await session.rollback()
                    raise ValueError(f"Failed to register server: {e}")

        return {
            "status": "ok",
            "pending_model_update": pending_model,
            "pending_app_update": pending_app,
        }

    async def list_servers(
        self, profile_name: str | None = None, status: str | None = None,
    ) -> list[dict[str, Any]]:
        await self.mark_stale_servers_offline()
        async with self._session_maker() as session:
            stmt = select(DistillEdgeServerModel).order_by(
                DistillEdgeServerModel.last_heartbeat.desc()
            )
            if profile_name:
                stmt = stmt.where(DistillEdgeServerModel.profile_name == profile_name)
            if status:
                stmt = stmt.where(DistillEdgeServerModel.status == status)
            result = await session.execute(stmt)
            return [self._to_dict(r) for r in result.scalars().all()]

    async def get_server(self, store_id: str) -> dict[str, Any] | None:
        async with self._session_maker() as session:
            stmt = select(DistillEdgeServerModel).where(
                DistillEdgeServerModel.store_id == store_id
            )
            result = await session.execute(stmt)
            model = result.scalar_one_or_none()
            return self._to_dict(model) if model else None

    async def delete_server(self, store_id: str) -> bool:
        async with self._session_maker() as session:
            stmt = select(DistillEdgeServerModel).where(
                DistillEdgeServerModel.store_id == store_id
            )
            result = await session.execute(stmt)
            model = result.scalar_one_or_none()
            if not model:
                return False
            await session.delete(model)
            await session.commit()
            return True

    async def request_update(
        self, store_id: str, update_type: str,
    ) -> dict[str, Any]:
        """update_type: 'model' | 'app' | 'both' → pending 플래그 설정."""
        values: dict[str, Any] = {}
        if update_type in ("model", "both"):
            values["pending_model_update"] = True
        if update_type in ("app", "both"):
            values["pending_app_update"] = True
        if not values:
            raise ValueError(f"Invalid update_type: {update_type}")

        async with self._session_maker() as session:
            await session.execute(
                update(DistillEdgeServerModel)
                .where(DistillEdgeServerModel.store_id == store_id)
                .values(**values)
            )
            await session.commit()

        return {"store_id": store_id, "update_type": update_type, "requested": True}

    async def bulk_request_update(
        self, profile_name: str, update_type: str,
    ) -> int:
        """프로필 내 온라인 서버 전체에 업데이트 요청."""
        values: dict[str, Any] = {}
        if update_type in ("model", "both"):
            values["pending_model_update"] = True
        if update_type in ("app", "both"):
            values["pending_app_update"] = True

        async with self._session_maker() as session:
            stmt = (
                update(DistillEdgeServerModel)
                .where(
                    DistillEdgeServerModel.profile_name == profile_name,
                    DistillEdgeServerModel.status == "online",
                )
                .values(**values)
            )
            result = await session.execute(stmt)
            await session.commit()
            return result.rowcount

    async def get_fleet_stats(self, profile_name: str) -> dict[str, Any]:
        """fleet 통계 (단일 GROUP BY 쿼리)."""
        await self.mark_stale_servers_offline()
        async with self._session_maker() as session:
            base = DistillEdgeServerModel.profile_name == profile_name

            result = await session.execute(
                select(
                    DistillEdgeServerModel.status,
                    func.count(),
                ).where(base).group_by(DistillEdgeServerModel.status)
            )
            rows = result.all()

            stats: dict[str, Any] = {
                "total": 0, "online": 0, "offline": 0,
                "error": 0, "updating": 0, "unknown": 0,
            }
            for status, count in rows:
                stats[status] = count
                stats["total"] += count

            return stats

    async def mark_stale_servers_offline(self, timeout_minutes: int = 10) -> int:
        """last_heartbeat가 timeout_minutes 이상 전인 서버를 offline으로 갱신."""
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=timeout_minutes)
        async with self._session_maker() as session:
            stmt = (
                update(DistillEdgeServerModel)
                .where(
                    DistillEdgeServerModel.status == "online",
                    DistillEdgeServerModel.last_heartbeat < cutoff,
                )
                .values(status="offline")
            )
            result = await session.execute(stmt)
            await session.commit()
            if result.rowcount > 0:
                logger.info("Marked %d stale servers as offline", result.rowcount)
            return result.rowcount

    @staticmethod
    def _to_dict(model: DistillEdgeServerModel) -> dict[str, Any]:
        return {
            "id": model.id,
            "store_id": model.store_id,
            "profile_name": model.profile_name,
            "display_name": model.display_name,
            "status": model.status,
            "last_heartbeat": model.last_heartbeat.isoformat() if model.last_heartbeat else None,
            "server_ip": model.server_ip,
            "os_type": model.os_type,
            "app_version": model.app_version,
            "model_version": model.model_version,
            "model_sha256": model.model_sha256,
            "cpu_info": model.cpu_info,
            "ram_total_mb": model.ram_total_mb,
            "ram_used_mb": model.ram_used_mb,
            "disk_free_mb": model.disk_free_mb,
            "avg_latency_ms": model.avg_latency_ms,
            "total_queries": model.total_queries,
            "success_rate": model.success_rate,
            "pending_model_update": model.pending_model_update,
            "pending_app_update": model.pending_app_update,
            "created_at": model.created_at.isoformat() if model.created_at else None,
            "updated_at": model.updated_at.isoformat() if model.updated_at else None,
        }
