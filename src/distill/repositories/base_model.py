"""Base Model Registry Repository — 대시보드 드롭다운 SSOT."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.distill.models import DistillBaseModelEntry

logger = logging.getLogger(__name__)


class DistillBaseModelRepository:
    """베이스 모델 레지스트리 CRUD."""

    def __init__(self, session_maker: async_sessionmaker) -> None:
        self._session_maker = session_maker

    async def list_all(self, *, enabled_only: bool = True) -> list[dict[str, Any]]:
        async with self._session_maker() as session:
            stmt = select(DistillBaseModelEntry).order_by(
                DistillBaseModelEntry.sort_order,
                DistillBaseModelEntry.hf_id,
            )
            if enabled_only:
                stmt = stmt.where(DistillBaseModelEntry.enabled.is_(True))
            result = await session.execute(stmt)
            return [self._to_dict(r) for r in result.scalars().all()]

    async def get(self, hf_id: str) -> dict[str, Any] | None:
        async with self._session_maker() as session:
            stmt = select(DistillBaseModelEntry).where(
                DistillBaseModelEntry.hf_id == hf_id,
            )
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            return self._to_dict(row) if row else None

    async def insert_if_missing(self, data: dict[str, Any]) -> bool:
        """행이 없을 때만 삽입. 이미 있으면 **아무것도 하지 않음**.

        Seed 스크립트 전용 — admin 이 Admin UI 에서 편집한 값 (e.g. verified 토글,
        notes 수정) 이 앱 재시작 시 seed 로 덮어써지는 걸 막는다. 신규 hf_id 는
        defaults 에 추가 시 다음 재시작에 자동 반영되지만, 기존 행은 건드리지
        않음 = migration 성격.

        Admin 이 명시적으로 update 하고 싶으면 ``upsert()`` 호출 (admin API 경로).

        Returns:
            True — 새 행을 삽입함
            False — 이미 존재해서 스킵됨
        """
        if "hf_id" not in data:
            raise ValueError("base model insert_if_missing requires 'hf_id'")

        async with self._session_maker() as session:
            try:
                stmt = pg_insert(DistillBaseModelEntry).values(**data)
                stmt = stmt.on_conflict_do_nothing(index_elements=["hf_id"])
                result = await session.execute(stmt)
                await session.commit()
                # rowcount 가 1 이면 삽입, 0 이면 스킵
                return (result.rowcount or 0) > 0
            except SQLAlchemyError as e:
                await session.rollback()
                logger.error(
                    "Failed to insert_if_missing base model %s: %s",
                    data.get("hf_id"), e,
                )
                raise

    async def upsert(self, data: dict[str, Any]) -> dict[str, Any]:
        """하나의 베이스 모델 추가/갱신 (덮어쓰기). Admin API 전용.

        PostgreSQL ``INSERT ... ON CONFLICT DO UPDATE`` 로 atomic 하게 처리.
        SELECT → INSERT/UPDATE 2단계 race 조건을 피한다. DB 는 PG 전용.

        Seed 경로에서는 ``insert_if_missing()`` 를 써야 한다 — upsert 는 admin
        편집을 덮어쓴다.
        """
        if "hf_id" not in data:
            raise ValueError("base model upsert requires 'hf_id'")

        async with self._session_maker() as session:
            try:
                stmt = pg_insert(DistillBaseModelEntry).values(**data)
                update_cols = {
                    col: getattr(stmt.excluded, col)
                    for col in data.keys()
                    if col != "hf_id"
                }
                # updated_at 은 호출자가 안 넣어도 항상 DB NOW() 로 갱신
                update_cols["updated_at"] = func.now()
                stmt = stmt.on_conflict_do_update(
                    index_elements=["hf_id"],
                    set_=update_cols,
                )
                await session.execute(stmt)
                await session.commit()
                # 반환값은 현재 저장된 row — 다시 읽어서 직렬화
                result = await session.execute(
                    select(DistillBaseModelEntry).where(
                        DistillBaseModelEntry.hf_id == data["hf_id"],
                    ),
                )
                row = result.scalar_one()
                return self._to_dict(row)
            except SQLAlchemyError as e:
                await session.rollback()
                logger.error("Failed to upsert base model %s: %s", data.get("hf_id"), e)
                raise

    async def delete(self, hf_id: str) -> bool:
        async with self._session_maker() as session:
            try:
                stmt = select(DistillBaseModelEntry).where(
                    DistillBaseModelEntry.hf_id == hf_id,
                )
                result = await session.execute(stmt)
                model = result.scalar_one_or_none()
                if not model:
                    return False
                await session.delete(model)
                await session.commit()
                return True
            except SQLAlchemyError as e:
                await session.rollback()
                logger.error("Failed to delete base model %s: %s", hf_id, e)
                return False

    @staticmethod
    def _to_dict(model: DistillBaseModelEntry) -> dict[str, Any]:
        return {
            "hf_id": model.hf_id,
            "display_name": model.display_name,
            "params": model.params,
            "license": model.license,
            "commercial_use": model.commercial_use,
            "verified": model.verified,
            "notes": model.notes or "",
            "enabled": model.enabled,
            "sort_order": model.sort_order,
            "created_at": model.created_at.isoformat() if model.created_at else None,
            "updated_at": model.updated_at.isoformat() if model.updated_at else None,
        }
