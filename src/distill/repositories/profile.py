# pyright: reportGeneralTypeIssues=false
"""Profile Repository — 빌드 프로필 CRUD."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.distill.models import DistillProfileModel

logger = logging.getLogger(__name__)


class DistillProfileRepository:

    def __init__(self, session_maker: async_sessionmaker) -> None:
        self._session_maker = session_maker

    async def list_all(self) -> list[dict[str, Any]]:
        async with self._session_maker() as session:
            stmt = select(DistillProfileModel).order_by(DistillProfileModel.name)
            result = await session.execute(stmt)
            return [self._to_dict(r) for r in result.scalars().all()]

    async def get(self, name: str) -> dict[str, Any] | None:
        async with self._session_maker() as session:
            stmt = select(DistillProfileModel).where(DistillProfileModel.name == name)
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            return self._to_dict(row) if row else None

    async def create(self, data: dict[str, Any]) -> dict[str, Any]:
        async with self._session_maker() as session:
            try:
                config_fields = {}
                for key in ("lora", "training", "qa_style", "data_quality", "deploy"):
                    if key in data:
                        config_fields[key] = data.pop(key)

                if not data.get("base_model"):
                    raise ValueError(
                        "base_model is required — pick one from "
                        "distill_base_models registry",
                    )
                model = DistillProfileModel(
                    name=data["name"],
                    enabled=data.get("enabled", False),
                    description=data.get("description", ""),
                    search_group=data["search_group"],
                    base_model=data["base_model"],
                    config=json.dumps(config_fields, ensure_ascii=False),
                )
                session.add(model)
                await session.commit()
                await session.refresh(model)
                return self._to_dict(model)
            except SQLAlchemyError as e:
                await session.rollback()
                logger.error("Failed to create profile: %s", e)
                raise

    async def update(self, name: str, data: dict[str, Any]) -> dict[str, Any] | None:
        async with self._session_maker() as session:
            stmt = select(DistillProfileModel).where(DistillProfileModel.name == name)
            result = await session.execute(stmt)
            model = result.scalar_one_or_none()
            if not model:
                return None

            config = json.loads(model.config) if model.config else {}
            for key in ("lora", "training", "qa_style", "data_quality", "deploy"):
                if key in data:
                    config[key] = data.pop(key)
            model.config = json.dumps(config, ensure_ascii=False)

            for field in ("enabled", "description", "search_group", "base_model"):
                if field in data:
                    setattr(model, field, data[field])

            model.updated_at = datetime.now(timezone.utc)
            await session.commit()
            await session.refresh(model)
            return self._to_dict(model)

    async def delete(self, name: str) -> bool:
        async with self._session_maker() as session:
            try:
                stmt = select(DistillProfileModel).where(DistillProfileModel.name == name)
                result = await session.execute(stmt)
                model = result.scalar_one_or_none()
                if not model:
                    return False
                await session.delete(model)
                await session.commit()
                return True
            except SQLAlchemyError as e:
                await session.rollback()
                logger.error("Failed to delete profile %s: %s", name, e)
                return False

    @staticmethod
    def _to_dict(model: DistillProfileModel) -> dict[str, Any]:
        config = {}
        if model.config:
            try:
                config = json.loads(model.config) if isinstance(model.config, str) else {}
            except (json.JSONDecodeError, TypeError):
                pass
        return {
            "name": model.name,
            "enabled": model.enabled,
            "description": model.description,
            "search_group": model.search_group,
            "base_model": model.base_model,
            **config,
            "created_at": model.created_at.isoformat() if model.created_at else None,
            "updated_at": model.updated_at.isoformat() if model.updated_at else None,
        }
