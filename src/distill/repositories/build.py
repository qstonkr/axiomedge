# pyright: reportGeneralTypeIssues=false
"""Build Repository — 빌드/학습 이력 CRUD."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import or_, select, text, update
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

    async def delete(self, build_id: str) -> bool:
        """빌드 삭제."""
        async with self._session_maker() as session:
            stmt = select(DistillBuildModel).where(DistillBuildModel.id == build_id)
            result = await session.execute(stmt)
            model = result.scalar_one_or_none()
            if not model:
                return False
            await session.delete(model)
            await session.commit()
            return True

    async def list_version_history(
        self, profile_name: str,
    ) -> list[dict[str, Any]]:
        """배포된 빌드 버전 히스토리 (비교용)."""
        async with self._session_maker() as session:
            stmt = (
                select(DistillBuildModel)
                .where(
                    DistillBuildModel.profile_name == profile_name,
                    DistillBuildModel.status.in_(["completed", "deployed"]),
                )
                .order_by(DistillBuildModel.created_at.desc())
                .limit(20)
            )
            result = await session.execute(stmt)
            return [self._to_dict(r) for r in result.scalars().all()]

    # ---------------------------------------------------------------------
    # Async sweeper 패턴 — gpu_instance_id NOT NULL 인 row 만 대상.
    # 기존 fire-and-forget build 는 NULL 이라 sweeper 가 무시.
    # ---------------------------------------------------------------------

    async def list_in_progress_training(
        self, *, sweep_threshold_seconds: int = 30,
    ) -> list[dict[str, Any]]:
        """sweeper 가 호출. status='training' AND gpu_instance_id IS NOT NULL
        AND (last_sweep_at IS NULL OR < now() - threshold).

        threshold 는 같은 build 가 동시에 다수 sweep tick 에 잡히지 않도록 —
        ``claim_for_sweep`` 의 atomic update 와 함께 idempotency 보장.
        """
        async with self._session_maker() as session:
            stmt = (
                select(DistillBuildModel)
                .where(
                    DistillBuildModel.status == "training",
                    DistillBuildModel.gpu_instance_id.isnot(None),
                    or_(
                        DistillBuildModel.last_sweep_at.is_(None),
                        DistillBuildModel.last_sweep_at
                        < text(f"NOW() - INTERVAL '{int(sweep_threshold_seconds)} seconds'"),
                    ),
                )
                .order_by(DistillBuildModel.gpu_started_at.asc())
                .limit(50)  # 한 tick 당 cap — 큰 backlog 도 안전
            )
            result = await session.execute(stmt)
            return [self._to_dict(r) for r in result.scalars().all()]

    async def claim_for_sweep(
        self, build_id: str, *, threshold_seconds: int = 30,
    ) -> bool:
        """Atomic claim — 다른 worker 가 이미 처리 중이면 False.

        UPDATE ... SET last_sweep_at=NOW() WHERE id=:id AND
        (last_sweep_at IS NULL OR last_sweep_at < NOW() - threshold)
        RETURNING id;
        """
        async with self._session_maker() as session:
            stmt = (
                update(DistillBuildModel)
                .where(
                    DistillBuildModel.id == build_id,
                    or_(
                        DistillBuildModel.last_sweep_at.is_(None),
                        DistillBuildModel.last_sweep_at
                        < text(f"NOW() - INTERVAL '{int(threshold_seconds)} seconds'"),
                    ),
                )
                .values(last_sweep_at=datetime.now(timezone.utc))
                .returning(DistillBuildModel.id)
            )
            result = await session.execute(stmt)
            row = result.first()
            await session.commit()
            return row is not None

    async def set_gpu_metadata(
        self,
        build_id: str,
        *,
        gpu_instance_id: str,
        s3_result_key: str,
        gpu_started_at: datetime,
    ) -> dict[str, Any] | None:
        """``start_gpu_training`` 직후 호출. 신구조 marker (gpu_instance_id) 등록."""
        return await self.update(
            build_id,
            gpu_instance_id=gpu_instance_id,
            s3_result_key=s3_result_key,
            gpu_started_at=gpu_started_at,
        )

    async def rollback_to(
        self, build_id: str, current_build_id: str,
    ) -> dict[str, Any] | None:
        """특정 빌드로 롤백 (rollback_from 기록)."""
        async with self._session_maker() as session:
            # 현재 배포 해제
            await session.execute(
                update(DistillBuildModel)
                .where(DistillBuildModel.id == current_build_id)
                .values(deployed_at=None)
            )
            # 대상 빌드 재배포
            now = datetime.now(timezone.utc)
            await session.execute(
                update(DistillBuildModel)
                .where(DistillBuildModel.id == build_id)
                .values(deployed_at=now, rollback_from=current_build_id)
            )
            await session.commit()

            result = await session.execute(
                select(DistillBuildModel).where(DistillBuildModel.id == build_id)
            )
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
            "data_sources": model.data_sources,
            "train_loss": model.train_loss,
            "eval_loss": model.eval_loss,
            "training_duration_sec": model.training_duration_sec,
            "eval_faithfulness": model.eval_faithfulness,
            "eval_relevancy": model.eval_relevancy,
            "eval_passed": model.eval_passed,
            "gguf_size_mb": model.gguf_size_mb,
            "gguf_sha256": model.gguf_sha256,
            "model_name": model.model_name,
            "quantize_method": model.quantize_method,
            "s3_uri": model.s3_uri,
            "deployed_at": model.deployed_at.isoformat() if model.deployed_at else None,
            "rollback_from": model.rollback_from,
            "error_message": model.error_message,
            "error_step": model.error_step,
            # 0008 — async sweeper 메타. NULL = 신구조 미적용 (기존 fire-and-forget build).
            "gpu_instance_id": model.gpu_instance_id,
            "gpu_started_at": (
                model.gpu_started_at.isoformat() if model.gpu_started_at else None
            ),
            "s3_result_key": model.s3_result_key,
            "last_sweep_at": (
                model.last_sweep_at.isoformat() if model.last_sweep_at else None
            ),
            "gpu_finished_at": (
                model.gpu_finished_at.isoformat() if model.gpu_finished_at else None
            ),
            "created_at": model.created_at.isoformat() if model.created_at else None,
            "updated_at": model.updated_at.isoformat() if model.updated_at else None,
        }
