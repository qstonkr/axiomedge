# pyright: reportAttributeAccessIssue=false, reportGeneralTypeIssues=false
"""Data Source Repository - PostgreSQL backed.

모든 메서드가 ``organization_id`` 를 필수로 받아 cross-tenant 누설 차단.
KBConfigModel 의 0004 패턴과 동일 — 라우트 핸들러는 ``OrgContext`` 에서
``org.id`` 를 받아 그대로 전달한다.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.exc import SQLAlchemyError

from src.stores.postgres.models import DataSourceModel
from src.stores.postgres.repositories.base import BaseRepository

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _safe_json_loads(text: str | None, default: Any = None) -> Any:
    if not text:
        return default if default is not None else {}
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return default if default is not None else {}


class DataSourceRepository(BaseRepository):
    """Async PostgreSQL repository for data source registry.

    모든 mutation/query 가 ``organization_id`` 를 강제. cross-org 접근은
    ``None`` / ``False`` 를 반환해 라우트 핸들러가 404 로 매핑 — 존재 누설 X.
    """

    async def register(
        self, data: dict[str, Any], organization_id: str
    ) -> dict[str, Any]:
        """신규 data_source 등록. body 의 organization_id 는 항상 인자로 덮어씀."""
        async with await self._get_session() as session:
            try:
                model_data = dict(data)
                model_data["organization_id"] = organization_id
                for field in ("crawl_config", "pipeline_config", "last_sync_result"):
                    if field in model_data and isinstance(model_data[field], dict):
                        model_data[field] = json.dumps(model_data[field])
                if "metadata" in model_data:
                    model_data["metadata_"] = json.dumps(model_data.pop("metadata"))
                model = DataSourceModel(**model_data)
                session.add(model)
                await session.commit()
                # 응답 dict 에도 org_id 보존.
                data_with_org = dict(data)
                data_with_org["organization_id"] = organization_id
                return data_with_org
            except SQLAlchemyError:
                await session.rollback()
                raise

    async def get(
        self, source_id: str, organization_id: str
    ) -> dict[str, Any] | None:
        """org 미스매치 시 None — 라우트가 404 로 매핑 (존재 누설 방지)."""
        async with await self._get_session() as session:
            stmt = select(DataSourceModel).where(
                DataSourceModel.id == source_id,
                DataSourceModel.organization_id == organization_id,
            )
            result = await session.execute(stmt)
            model = result.scalar_one_or_none()
            return self._to_dict(model) if model else None

    async def get_by_name(
        self, name: str, organization_id: str
    ) -> dict[str, Any] | None:
        """name 은 unique constraint 가 있지만 org scope 안에서 의미 있게 사용."""
        async with await self._get_session() as session:
            stmt = select(DataSourceModel).where(
                DataSourceModel.name == name,
                DataSourceModel.organization_id == organization_id,
            )
            result = await session.execute(stmt)
            model = result.scalar_one_or_none()
            return self._to_dict(model) if model else None

    async def list(
        self,
        organization_id: str,
        source_type: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        """해당 org 의 data_source 만 반환."""
        async with await self._get_session() as session:
            stmt = select(DataSourceModel).where(
                DataSourceModel.organization_id == organization_id,
            )
            if source_type:
                stmt = stmt.where(DataSourceModel.source_type == source_type)
            if status:
                stmt = stmt.where(DataSourceModel.status == status)
            stmt = stmt.order_by(DataSourceModel.created_at.desc()).limit(1000)
            result = await session.execute(stmt)
            return [self._to_dict(m) for m in result.scalars().all()]

    async def update_status(
        self,
        source_id: str,
        status: str,
        organization_id: str,
        error_message: str | None = None,
    ) -> bool:
        """org 미스매치 시 False — 0 row 업데이트."""
        async with await self._get_session() as session:
            try:
                stmt = (
                    update(DataSourceModel)
                    .where(
                        DataSourceModel.id == source_id,
                        DataSourceModel.organization_id == organization_id,
                    )
                    .values(status=status, error_message=error_message, updated_at=_utc_now())
                )
                result = await session.execute(stmt)
                await session.commit()
                return result.rowcount > 0
            except SQLAlchemyError:
                await session.rollback()
                raise

    async def complete_sync(
        self,
        source_id: str,
        status: str,
        organization_id: str,
        sync_result: dict[str, Any] | None = None,
        error_message: str | None = None,
    ) -> bool:
        """동기화 완료 후 상태 업데이트. org 미스매치 시 False."""
        async with await self._get_session() as session:
            try:
                now = _utc_now()
                values: dict[str, Any] = {
                    "status": status,
                    "error_message": error_message,
                    "updated_at": now,
                    "last_sync_at": now,
                }
                if sync_result is not None:
                    values["last_sync_result"] = json.dumps(sync_result)
                stmt = (
                    update(DataSourceModel)
                    .where(
                        DataSourceModel.id == source_id,
                        DataSourceModel.organization_id == organization_id,
                    )
                    .values(**values)
                )
                result = await session.execute(stmt)
                await session.commit()
                return result.rowcount > 0
            except SQLAlchemyError:
                await session.rollback()
                raise

    async def delete(self, source_id: str, organization_id: str) -> bool:
        """org 미스매치 시 False (rowcount=0). 라우트는 404 응답."""
        async with await self._get_session() as session:
            try:
                stmt = delete(DataSourceModel).where(
                    DataSourceModel.id == source_id,
                    DataSourceModel.organization_id == organization_id,
                )
                result = await session.execute(stmt)
                await session.commit()
                return result.rowcount > 0
            except SQLAlchemyError:
                await session.rollback()
                raise

    async def set_secret_path(
        self,
        source_id: str,
        organization_id: str,
        secret_path: str | None,
    ) -> bool:
        """SecretBox put/delete 후 호출 — DB 의 secret_path / has_secret 동기화.

        secret_path 가 None 이면 토큰 삭제 (has_secret=False), 아니면 등록
        (has_secret=True). org 미스매치 시 False.
        """
        async with await self._get_session() as session:
            try:
                stmt = (
                    update(DataSourceModel)
                    .where(
                        DataSourceModel.id == source_id,
                        DataSourceModel.organization_id == organization_id,
                    )
                    .values(
                        secret_path=secret_path,
                        has_secret=secret_path is not None,
                        updated_at=_utc_now(),
                    )
                )
                result = await session.execute(stmt)
                await session.commit()
                return result.rowcount > 0
            except SQLAlchemyError:
                await session.rollback()
                raise

    @staticmethod
    def _to_dict(model: DataSourceModel) -> dict[str, Any]:
        return {
            "id": model.id,
            "name": model.name,
            "source_type": model.source_type,
            "kb_id": model.kb_id,
            "organization_id": model.organization_id,
            "crawl_config": _safe_json_loads(model.crawl_config),
            "pipeline_config": _safe_json_loads(model.pipeline_config),
            "schedule": model.schedule,
            "status": model.status,
            "created_at": model.created_at,
            "updated_at": model.updated_at,
            "last_sync_at": model.last_sync_at,
            "last_sync_result": _safe_json_loads(model.last_sync_result),
            "error_message": model.error_message,
            "metadata": _safe_json_loads(model.metadata_),
            # secret_path 는 connector launcher 가 SecretBox.get 호출에 사용.
            # plain token 은 절대 응답에 포함 X — 라우트가 응답 직전 mask.
            "secret_path": model.secret_path,
            "has_secret": bool(model.has_secret),
        }
