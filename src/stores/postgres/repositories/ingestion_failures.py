# pyright: reportAttributeAccessIssue=false, reportGeneralTypeIssues=false
"""Per-document ingestion failure repository.

문서 단위 실패를 ``knowledge_ingestion_document_failures`` 에 영속화하고
재시도/알림 흐름의 입력을 제공한다. ``IngestionRunRepository.errors[:10]``
요약과 보완 관계 — run row 는 빠른 카운터, 본 repo 는 상세 트레이스.
"""

from __future__ import annotations

import logging
import uuid as _uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.exc import SQLAlchemyError

from src.stores.postgres.models import IngestionDocumentFailureModel
from src.stores.postgres.repositories.base import BaseRepository

logger = logging.getLogger(__name__)

_TRACEBACK_MAX_BYTES = 4096
# P1-4 hybrid truncation: head 1KB + tail 3KB. 다중-라인 traceback 의
# 첫 frame (어느 함수가 raise 됐는지) + 마지막 frame (실제 원인) 양쪽 보존.
_TRACEBACK_HEAD_BYTES = 1024
_TRACEBACK_TAIL_BYTES = 3072
_TRACEBACK_MARKER = "\n…[truncated middle frames]…\n"


def _truncate_traceback(tb: str | None) -> str | None:
    """Hybrid head+tail truncation for ``traceback.format_exc()``.

    Total output size is bounded by
    ``_TRACEBACK_HEAD_BYTES + len(marker) + _TRACEBACK_TAIL_BYTES`` ≤ 4128B.
    PG Text 컬럼이라 4KB 근처 cap 은 row-size 안전.
    """
    if tb is None:
        return None
    if len(tb) <= _TRACEBACK_MAX_BYTES:
        return tb
    head = tb[:_TRACEBACK_HEAD_BYTES]
    tail = tb[-_TRACEBACK_TAIL_BYTES:]
    return f"{head}{_TRACEBACK_MARKER}{tail}"


class IngestionFailureRepository(BaseRepository):
    """문서 단위 실패 영속화 repository."""

    async def record(
        self,
        *,
        run_id: str,
        kb_id: str,
        doc_id: str,
        stage: str,
        reason: str,
        source_uri: str | None = None,
        traceback: str | None = None,
        attempt: int = 1,
    ) -> str | None:
        """실패 1건 기록. 성공 시 row id 반환, 실패 시 None (best-effort).

        호출자는 실패 영속화 자체가 깨지더라도 인제스트 흐름이 멈추면 안 되므로
        예외를 swallow 한다 (logger.warning 만).
        """
        row_id = str(_uuid.uuid4())
        tb = _truncate_traceback(traceback)

        async with await self._get_session() as session:
            try:
                model = IngestionDocumentFailureModel(
                    id=row_id,
                    run_id=run_id,
                    kb_id=kb_id,
                    doc_id=doc_id,
                    source_uri=source_uri,
                    stage=stage,
                    reason=reason or "(no reason)",
                    traceback=tb,
                    attempt=max(1, int(attempt)),
                    failed_at=datetime.now(timezone.utc),
                )
                session.add(model)
                await session.commit()
                return row_id
            except SQLAlchemyError as e:
                await session.rollback()
                logger.warning(
                    "Failed to persist ingestion failure (run=%s doc=%s): %s",
                    run_id, doc_id, e,
                )
                return None

    async def list_by_run(
        self, run_id: str, *, limit: int = 1000
    ) -> list[dict[str, Any]]:
        """Run id 의 모든 실패 row (failed_at desc)."""
        async with await self._get_session() as session:
            try:
                result = await session.execute(
                    select(IngestionDocumentFailureModel)
                    .where(IngestionDocumentFailureModel.run_id == run_id)
                    .order_by(IngestionDocumentFailureModel.failed_at.desc())
                    .limit(limit)
                )
                return [self._to_dict(m) for m in result.scalars().all()]
            except SQLAlchemyError as e:
                logger.warning(
                    "list_by_run failed (run=%s): %s", run_id, e
                )
                return []

    async def list_by_kb(
        self,
        kb_id: str,
        *,
        since_hours: int = 24,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        """KB 의 최근 N 시간 실패 row (failed_at desc)."""
        cutoff = datetime.now(timezone.utc) - timedelta(
            hours=max(1, int(since_hours))
        )
        async with await self._get_session() as session:
            try:
                result = await session.execute(
                    select(IngestionDocumentFailureModel)
                    .where(
                        IngestionDocumentFailureModel.kb_id == kb_id,
                        IngestionDocumentFailureModel.failed_at >= cutoff,
                    )
                    .order_by(IngestionDocumentFailureModel.failed_at.desc())
                    .limit(limit)
                )
                return [self._to_dict(m) for m in result.scalars().all()]
            except SQLAlchemyError as e:
                logger.warning(
                    "list_by_kb failed (kb=%s): %s", kb_id, e
                )
                return []

    async def doc_ids_for_run(
        self, run_id: str, *, stage: str | None = None
    ) -> list[str]:
        """Run id 의 distinct doc_id 목록 (재시도 입력)."""
        async with await self._get_session() as session:
            try:
                stmt = (
                    select(IngestionDocumentFailureModel.doc_id)
                    .where(IngestionDocumentFailureModel.run_id == run_id)
                    .distinct()
                )
                if stage:
                    stmt = stmt.where(
                        IngestionDocumentFailureModel.stage == stage
                    )
                result = await session.execute(stmt)
                return [row[0] for row in result.all()]
            except SQLAlchemyError as e:
                logger.warning(
                    "doc_ids_for_run failed (run=%s): %s", run_id, e
                )
                return []

    async def delete_by_run_and_docs(
        self, run_id: str, doc_ids: list[str]
    ) -> int:
        """재시도 성공 후 해당 doc 의 실패 row 정리."""
        if not doc_ids:
            return 0
        async with await self._get_session() as session:
            try:
                result = await session.execute(
                    delete(IngestionDocumentFailureModel)
                    .where(
                        IngestionDocumentFailureModel.run_id == run_id,
                        IngestionDocumentFailureModel.doc_id.in_(doc_ids),
                    )
                )
                await session.commit()
                return int(result.rowcount or 0)
            except SQLAlchemyError as e:
                await session.rollback()
                logger.warning(
                    "delete_by_run_and_docs failed (run=%s): %s",
                    run_id, e,
                )
                return 0

    @staticmethod
    def _to_dict(model: IngestionDocumentFailureModel) -> dict[str, Any]:
        return {
            "id": model.id,
            "run_id": model.run_id,
            "kb_id": model.kb_id,
            "doc_id": model.doc_id,
            "source_uri": model.source_uri,
            "stage": model.stage,
            "reason": model.reason,
            "traceback": model.traceback,
            "attempt": model.attempt or 1,
            "failed_at": model.failed_at,
        }
