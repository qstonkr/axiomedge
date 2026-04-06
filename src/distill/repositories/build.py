"""Build Repository — 빌드/학습 이력 CRUD."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.distill.models import DistillBuildModel

logger = logging.getLogger(__name__)


class DistillBuildRepository:

    def __init__(self, session_maker: async_sessionmaker) -> None:
        self._session_maker = session_maker

    async def create(self, **kwargs: Any) -> dict[str, Any]:
        async with self._session_maker() as session:
            model = DistillBuildModel(**kwargs)
            session.add(model)
            await session.commit()
            await session.refresh(model)
            return self._to_dict(model)

    async def update(self, build_id: str, **kwargs: Any) -> dict[str, Any] | None:
        async with self._session_maker() as session:
            kwargs["updated_at"] = datetime.now(timezone.utc)
            stmt = (
                update(DistillBuildModel)
                .where(DistillBuildModel.id == build_id)
                .values(**kwargs)
            )
            await session.execute(stmt)
            await session.commit()

            result = await session.execute(
                select(DistillBuildModel).where(DistillBuildModel.id == build_id)
            )
            model = result.scalar_one_or_none()
            return self._to_dict(model) if model else None

    async def get(self, build_id: str) -> dict[str, Any] | None:
        async with self._session_maker() as session:
            stmt = select(DistillBuildModel).where(DistillBuildModel.id == build_id)
            result = await session.execute(stmt)
            model = result.scalar_one_or_none()
            return self._to_dict(model) if model else None

    async def list_all(
        self, profile_name: str | None = None, limit: int = 50,
    ) -> list[dict[str, Any]]:
        async with self._session_maker() as session:
            stmt = select(DistillBuildModel).order_by(DistillBuildModel.created_at.desc())
            if profile_name:
                stmt = stmt.where(DistillBuildModel.profile_name == profile_name)
            stmt = stmt.limit(limit)
            result = await session.execute(stmt)
            return [self._to_dict(r) for r in result.scalars().all()]

    async def get_latest(
        self, profile_name: str, status: str = "completed",
    ) -> dict[str, Any] | None:
        async with self._session_maker() as session:
            stmt = (
                select(DistillBuildModel)
                .where(
                    DistillBuildModel.profile_name == profile_name,
                    DistillBuildModel.status == status,
                )
                .order_by(DistillBuildModel.created_at.desc())
                .limit(1)
            )
            result = await session.execute(stmt)
            model = result.scalar_one_or_none()
            return self._to_dict(model) if model else None

    @staticmethod
    def _to_dict(model: DistillBuildModel) -> dict[str, Any]:
        return {
            "id": model.id,
            "profile_name": model.profile_name,
            "status": model.status,
            "version": model.version,
            "search_group": model.search_group,
            "base_model": model.base_model,
            "training_samples": model.training_samples,
            "train_loss": model.train_loss,
            "eval_loss": model.eval_loss,
            "training_duration_sec": model.training_duration_sec,
            "eval_faithfulness": model.eval_faithfulness,
            "eval_relevancy": model.eval_relevancy,
            "eval_passed": model.eval_passed,
            "gguf_size_mb": model.gguf_size_mb,
            "quantize_method": model.quantize_method,
            "s3_uri": model.s3_uri,
            "deployed_at": model.deployed_at.isoformat() if model.deployed_at else None,
            "error_message": model.error_message,
            "error_step": model.error_step,
            "created_at": model.created_at.isoformat() if model.created_at else None,
            "updated_at": model.updated_at.isoformat() if model.updated_at else None,
        }
