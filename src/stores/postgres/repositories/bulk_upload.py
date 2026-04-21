# pyright: reportAttributeAccessIssue=false, reportGeneralTypeIssues=false
"""Bulk upload session repository — presigned URL flow 의 진행 상태 SSOT.

cross-user 격리: 모든 메서드가 ``organization_id`` + ``owner_user_id`` 강제 —
data_source repo (0007 패턴) 와 동일.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError

from src.stores.postgres.models import BulkUploadSessionModel
from src.stores.postgres.repositories.base import BaseRepository

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _safe_json_loads(text: str | None, default: Any) -> Any:
    if not text:
        return default
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return default


class BulkUploadRepository(BaseRepository):
    """Bulk upload session CRUD + 진행 상태 update."""

    async def create(
        self,
        *,
        session_id: str,
        kb_id: str,
        organization_id: str,
        owner_user_id: str,
        s3_prefix: str,
        files: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """init endpoint 가 호출. files = [{file_idx, filename, s3_key, size}]."""
        async with await self._get_session() as session:
            try:
                model = BulkUploadSessionModel(
                    id=session_id,
                    kb_id=kb_id,
                    organization_id=organization_id,
                    owner_user_id=owner_user_id,
                    s3_prefix=s3_prefix,
                    total_files=len(files),
                    processed_files=0,
                    failed_files=0,
                    status="pending",
                    files=json.dumps(files, ensure_ascii=False),
                    errors="[]",
                )
                session.add(model)
                await session.commit()
                return self._to_dict(model)
            except SQLAlchemyError:
                await session.rollback()
                raise

    async def get(
        self, session_id: str, *, organization_id: str, owner_user_id: str,
    ) -> dict[str, Any] | None:
        """cross-user/cross-org 시 None — 라우트가 404 매핑 (존재 누설 X)."""
        async with await self._get_session() as session:
            stmt = select(BulkUploadSessionModel).where(
                BulkUploadSessionModel.id == session_id,
                BulkUploadSessionModel.organization_id == organization_id,
                BulkUploadSessionModel.owner_user_id == owner_user_id,
            )
            result = await session.execute(stmt)
            model = result.scalar_one_or_none()
            return self._to_dict(model) if model else None

    async def get_for_worker(self, session_id: str) -> dict[str, Any] | None:
        """arq worker 가 호출 — owner check 없이 raw fetch (worker 는 system context)."""
        async with await self._get_session() as session:
            stmt = select(BulkUploadSessionModel).where(
                BulkUploadSessionModel.id == session_id,
            )
            result = await session.execute(stmt)
            model = result.scalar_one_or_none()
            return self._to_dict(model) if model else None

    async def set_status(self, session_id: str, status: str) -> bool:
        async with await self._get_session() as session:
            try:
                stmt = (
                    update(BulkUploadSessionModel)
                    .where(BulkUploadSessionModel.id == session_id)
                    .values(status=status, updated_at=_utc_now())
                )
                result = await session.execute(stmt)
                await session.commit()
                return result.rowcount > 0
            except SQLAlchemyError:
                await session.rollback()
                raise

    async def increment_processed(
        self,
        session_id: str,
        *,
        success: bool,
        filename: str | None = None,
        error: str | None = None,
    ) -> None:
        """1 파일 처리 완료 시 호출. partial failure 허용 — 실패 카운트 누적."""
        async with await self._get_session() as session:
            try:
                stmt = select(BulkUploadSessionModel).where(
                    BulkUploadSessionModel.id == session_id,
                )
                result = await session.execute(stmt)
                model = result.scalar_one_or_none()
                if model is None:
                    return

                if success:
                    model.processed_files = (model.processed_files or 0) + 1
                else:
                    model.failed_files = (model.failed_files or 0) + 1
                    errs = _safe_json_loads(model.errors, [])
                    errs.append({
                        "filename": filename or "",
                        "error_message": (error or "")[:500],
                    })
                    model.errors = json.dumps(errs, ensure_ascii=False)

                # status 자동 전이 — 모든 파일 처리 완료 시.
                total = model.total_files or 0
                done = (model.processed_files or 0) + (model.failed_files or 0)
                if done >= total:
                    model.status = (
                        "completed" if (model.failed_files or 0) == 0 else "failed"
                    )
                model.updated_at = _utc_now()
                await session.commit()
            except SQLAlchemyError:
                await session.rollback()
                raise

    async def list_orphan_pending(
        self, *, cutoff: datetime,
    ) -> list[dict[str, Any]]:
        """Cleanup cron 용 — status='pending' AND created_at < cutoff.

        finalize 못 한 채 abort 된 session 을 찾아 S3 + DB 정리 대상으로 반환.
        idempotent — status 가 'failed' 로 바뀌면 다음 호출에서 자연 제외.
        """
        async with await self._get_session() as session:
            stmt = (
                select(BulkUploadSessionModel)
                .where(
                    BulkUploadSessionModel.status == "pending",
                    BulkUploadSessionModel.created_at < cutoff,
                )
                .order_by(BulkUploadSessionModel.created_at.asc())
                .limit(100)  # 한 tick 당 cap
            )
            result = await session.execute(stmt)
            return [self._to_dict(m) for m in result.scalars().all()]

    async def update_error(
        self, session_id: str, *, error_message: str,
    ) -> None:
        """Cleanup cron / finalize 실패 시 호출. errors JSON 갱신."""
        async with await self._get_session() as session:
            try:
                stmt = (
                    update(BulkUploadSessionModel)
                    .where(BulkUploadSessionModel.id == session_id)
                    .values(
                        errors=json.dumps(
                            [{"filename": "", "error_message": error_message[:500]}],
                            ensure_ascii=False,
                        ),
                        updated_at=_utc_now(),
                    )
                )
                await session.execute(stmt)
                await session.commit()
            except SQLAlchemyError:
                await session.rollback()
                raise

    async def list_recent_for_user(
        self, *, organization_id: str, owner_user_id: str, limit: int = 20,
    ) -> list[dict[str, Any]]:
        """사용자 화면의 "내 최근 업로드" 표시용."""
        async with await self._get_session() as session:
            stmt = (
                select(BulkUploadSessionModel)
                .where(
                    BulkUploadSessionModel.organization_id == organization_id,
                    BulkUploadSessionModel.owner_user_id == owner_user_id,
                )
                .order_by(BulkUploadSessionModel.created_at.desc())
                .limit(limit)
            )
            result = await session.execute(stmt)
            return [self._to_dict(m) for m in result.scalars().all()]

    @staticmethod
    def _to_dict(model: BulkUploadSessionModel) -> dict[str, Any]:
        return {
            "id": model.id,
            "kb_id": model.kb_id,
            "organization_id": model.organization_id,
            "owner_user_id": model.owner_user_id,
            "s3_prefix": model.s3_prefix,
            "total_files": model.total_files,
            "processed_files": model.processed_files,
            "failed_files": model.failed_files,
            "status": model.status,
            "errors": _safe_json_loads(model.errors, []),
            "files": _safe_json_loads(model.files, []),
            "created_at": model.created_at,
            "updated_at": model.updated_at,
        }
