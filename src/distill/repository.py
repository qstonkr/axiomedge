"""Distill Repository — Async PostgreSQL CRUD.

DistillBase 전용 세션으로 RAG 코어와 독립.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.distill.models import (
    DistillBuildModel,
    DistillEdgeLogModel,
    DistillProfileModel,
    DistillTrainingDataModel,
)

logger = logging.getLogger(__name__)


class DistillRepository:
    """Async repository for distill plugin tables."""

    def __init__(self, session_maker: async_sessionmaker) -> None:
        self._session_maker = session_maker

    # =====================================================================
    # Profiles
    # =====================================================================

    async def list_profiles(self) -> list[dict[str, Any]]:
        async with self._session_maker() as session:
            stmt = select(DistillProfileModel).order_by(DistillProfileModel.name)
            result = await session.execute(stmt)
            return [self._profile_to_dict(r) for r in result.scalars().all()]

    async def get_profile(self, name: str) -> dict[str, Any] | None:
        async with self._session_maker() as session:
            stmt = select(DistillProfileModel).where(DistillProfileModel.name == name)
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            return self._profile_to_dict(row) if row else None

    async def create_profile(self, data: dict[str, Any]) -> dict[str, Any]:
        async with self._session_maker() as session:
            try:
                config_fields = {}
                for key in ("lora", "training", "qa_style", "data_quality", "deploy"):
                    if key in data:
                        config_fields[key] = data.pop(key)

                model = DistillProfileModel(
                    name=data["name"],
                    enabled=data.get("enabled", False),
                    description=data.get("description", ""),
                    search_group=data["search_group"],
                    base_model=data.get("base_model", "Qwen/Qwen2.5-0.5B-Instruct"),
                    config=json.dumps(config_fields, ensure_ascii=False),
                )
                session.add(model)
                await session.commit()
                await session.refresh(model)
                return self._profile_to_dict(model)
            except SQLAlchemyError as e:
                await session.rollback()
                logger.error("Failed to create profile: %s", e)
                raise

    async def update_profile(self, name: str, data: dict[str, Any]) -> dict[str, Any] | None:
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
            return self._profile_to_dict(model)

    async def delete_profile(self, name: str) -> bool:
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
    def _profile_to_dict(model: DistillProfileModel) -> dict[str, Any]:
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

    # =====================================================================
    # Builds
    # =====================================================================

    async def create_build(self, **kwargs: Any) -> dict[str, Any]:
        async with self._session_maker() as session:
            model = DistillBuildModel(**kwargs)
            session.add(model)
            await session.commit()
            await session.refresh(model)
            return self._build_to_dict(model)

    async def update_build(self, build_id: str, **kwargs: Any) -> dict[str, Any] | None:
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
            return self._build_to_dict(model) if model else None

    async def get_build(self, build_id: str) -> dict[str, Any] | None:
        async with self._session_maker() as session:
            stmt = select(DistillBuildModel).where(DistillBuildModel.id == build_id)
            result = await session.execute(stmt)
            model = result.scalar_one_or_none()
            return self._build_to_dict(model) if model else None

    async def list_builds(
        self,
        profile_name: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        async with self._session_maker() as session:
            stmt = select(DistillBuildModel).order_by(DistillBuildModel.created_at.desc())
            if profile_name:
                stmt = stmt.where(DistillBuildModel.profile_name == profile_name)
            stmt = stmt.limit(limit)
            result = await session.execute(stmt)
            return [self._build_to_dict(r) for r in result.scalars().all()]

    async def get_latest_build(
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
            return self._build_to_dict(model) if model else None

    @staticmethod
    def _build_to_dict(model: DistillBuildModel) -> dict[str, Any]:
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

    # =====================================================================
    # Edge Logs
    # =====================================================================

    async def save_edge_logs(self, logs: list[dict[str, Any]]) -> int:
        async with self._session_maker() as session:
            count = 0
            for log in logs:
                model = DistillEdgeLogModel(
                    id=log.get("id", str(uuid.uuid4())),
                    profile_name=log["profile_name"],
                    store_id=log["store_id"],
                    query=log["query"],
                    answer=log.get("answer"),
                    confidence=log.get("confidence"),
                    latency_ms=log.get("latency_ms"),
                    success=log.get("success", True),
                    model_version=log.get("model_version"),
                    edge_timestamp=log.get("edge_timestamp", datetime.now(timezone.utc)),
                )
                session.add(model)
                count += 1
            try:
                await session.commit()
            except SQLAlchemyError as e:
                await session.rollback()
                logger.error("Failed to save edge logs: %s", e)
                return 0
            return count

    async def list_edge_logs(
        self,
        profile_name: str,
        store_id: str | None = None,
        success: bool | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        async with self._session_maker() as session:
            stmt = (
                select(DistillEdgeLogModel)
                .where(DistillEdgeLogModel.profile_name == profile_name)
            )
            if store_id:
                stmt = stmt.where(DistillEdgeLogModel.store_id == store_id)
            if success is not None:
                stmt = stmt.where(DistillEdgeLogModel.success == success)

            count_stmt = select(func.count()).select_from(stmt.subquery())
            total = (await session.execute(count_stmt)).scalar() or 0

            stmt = stmt.order_by(DistillEdgeLogModel.edge_timestamp.desc())
            stmt = stmt.offset(offset).limit(limit)
            result = await session.execute(stmt)
            items = [self._edge_log_to_dict(r) for r in result.scalars().all()]

            return {"items": items, "total": total}

    async def get_edge_analytics(
        self, profile_name: str, days: int = 7,
    ) -> dict[str, Any]:
        async with self._session_maker() as session:
            from datetime import timedelta
            cutoff = datetime.now(timezone.utc) - timedelta(days=days)
            base = DistillEdgeLogModel.profile_name == profile_name
            time_filter = DistillEdgeLogModel.edge_timestamp >= cutoff

            total_stmt = select(func.count()).select_from(DistillEdgeLogModel).where(base, time_filter)
            total = (await session.execute(total_stmt)).scalar() or 0

            success_stmt = (
                select(func.count()).select_from(DistillEdgeLogModel)
                .where(base, time_filter, DistillEdgeLogModel.success.is_(True))
            )
            success_count = (await session.execute(success_stmt)).scalar() or 0

            avg_latency_stmt = (
                select(func.avg(DistillEdgeLogModel.latency_ms))
                .where(base, time_filter)
            )
            avg_latency = (await session.execute(avg_latency_stmt)).scalar() or 0

            store_stmt = (
                select(func.count(func.distinct(DistillEdgeLogModel.store_id)))
                .where(base, time_filter)
            )
            store_count = (await session.execute(store_stmt)).scalar() or 0

            return {
                "total_queries": total,
                "success_count": success_count,
                "success_rate": success_count / total if total else 0,
                "avg_latency_ms": round(float(avg_latency), 1),
                "store_count": store_count,
                "period_days": days,
            }

    async def list_failed_queries(
        self, profile_name: str, limit: int = 50,
    ) -> list[dict[str, Any]]:
        async with self._session_maker() as session:
            stmt = (
                select(DistillEdgeLogModel)
                .where(
                    DistillEdgeLogModel.profile_name == profile_name,
                    DistillEdgeLogModel.success.is_(False),
                )
                .order_by(DistillEdgeLogModel.edge_timestamp.desc())
                .limit(limit)
            )
            result = await session.execute(stmt)
            return [self._edge_log_to_dict(r) for r in result.scalars().all()]

    @staticmethod
    def _edge_log_to_dict(model: DistillEdgeLogModel) -> dict[str, Any]:
        return {
            "id": model.id,
            "profile_name": model.profile_name,
            "store_id": model.store_id,
            "query": model.query,
            "answer": model.answer,
            "confidence": model.confidence,
            "latency_ms": model.latency_ms,
            "success": model.success,
            "model_version": model.model_version,
            "edge_timestamp": model.edge_timestamp.isoformat() if model.edge_timestamp else None,
            "collected_at": model.collected_at.isoformat() if model.collected_at else None,
        }

    # =====================================================================
    # Training Data
    # =====================================================================

    async def save_training_data(self, entries: list[dict[str, Any]]) -> int:
        async with self._session_maker() as session:
            count = 0
            for entry in entries:
                model = DistillTrainingDataModel(
                    id=entry.get("id", str(uuid.uuid4())),
                    profile_name=entry["profile_name"],
                    question=entry["question"],
                    answer=entry["answer"],
                    source_type=entry.get("source_type", "manual"),
                    source_id=entry.get("source_id"),
                    kb_id=entry.get("kb_id"),
                    status=entry.get("status", "approved"),
                )
                session.add(model)
                count += 1
            try:
                await session.commit()
            except SQLAlchemyError as e:
                await session.rollback()
                logger.error("Failed to save training data: %s", e)
                return 0
            return count

    async def list_training_data(
        self,
        profile_name: str,
        status: str | None = None,
        source_type: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        async with self._session_maker() as session:
            stmt = (
                select(DistillTrainingDataModel)
                .where(DistillTrainingDataModel.profile_name == profile_name)
            )
            if status:
                stmt = stmt.where(DistillTrainingDataModel.status == status)
            if source_type:
                stmt = stmt.where(DistillTrainingDataModel.source_type == source_type)

            count_stmt = select(func.count()).select_from(stmt.subquery())
            total = (await session.execute(count_stmt)).scalar() or 0

            stmt = stmt.order_by(DistillTrainingDataModel.created_at.desc())
            stmt = stmt.offset(offset).limit(limit)
            result = await session.execute(stmt)
            items = [self._training_data_to_dict(r) for r in result.scalars().all()]

            return {"items": items, "total": total}

    async def get_training_data_stats(self, profile_name: str) -> dict[str, Any]:
        async with self._session_maker() as session:
            base = DistillTrainingDataModel.profile_name == profile_name
            approved = DistillTrainingDataModel.status == "approved"

            total_stmt = select(func.count()).select_from(DistillTrainingDataModel).where(base, approved)
            total = (await session.execute(total_stmt)).scalar() or 0

            stats = {"total": total}
            for src_type in ("chunk_qa", "usage_log", "retrain", "manual"):
                type_stmt = (
                    select(func.count()).select_from(DistillTrainingDataModel)
                    .where(base, approved, DistillTrainingDataModel.source_type == src_type)
                )
                stats[src_type] = (await session.execute(type_stmt)).scalar() or 0

            return stats

    async def update_training_data_status(
        self, ids: list[str], status: str,
    ) -> int:
        async with self._session_maker() as session:
            stmt = (
                update(DistillTrainingDataModel)
                .where(DistillTrainingDataModel.id.in_(ids))
                .values(status=status)
            )
            result = await session.execute(stmt)
            await session.commit()
            return result.rowcount

    @staticmethod
    def _training_data_to_dict(model: DistillTrainingDataModel) -> dict[str, Any]:
        return {
            "id": model.id,
            "profile_name": model.profile_name,
            "question": model.question,
            "answer": model.answer,
            "source_type": model.source_type,
            "source_id": model.source_id,
            "kb_id": model.kb_id,
            "status": model.status,
            "used_in_build": model.used_in_build,
            "created_at": model.created_at.isoformat() if model.created_at else None,
        }
