"""arq cron: audit log archive (PR-12 J + P1-2).

매일 03:10 UTC 에 ``AUDIT_LOG_RETENTION_DAYS`` 보다 오래된 audit row 삭제.
``AUDIT_LOG_ARCHIVE_BUCKET`` env 가 설정돼 있으면 dump → S3/MinIO 후 삭제,
미설정이면 archive 만 skip 하고 row 는 보존 (안전 default).
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


async def run_audit_archive(*, repo: Any) -> dict[str, int]:
    """Pure function — testable. repo 는 AuditLogRepository.

    archive bucket 미설정 시:
      - 운영자가 ``AUDIT_LOG_ARCHIVE_BUCKET`` 를 명시할 때까지 row 보존.
      - 단순 delete-only 모드를 원하면 ``AUDIT_LOG_DELETE_ONLY=1``.
    """
    retention_days = int(os.getenv("AUDIT_LOG_RETENTION_DAYS", "180"))
    bucket = os.getenv("AUDIT_LOG_ARCHIVE_BUCKET", "").strip()
    delete_only = os.getenv(
        "AUDIT_LOG_DELETE_ONLY", "",
    ).strip().lower() in ("1", "true", "yes")

    if not bucket and not delete_only:
        logger.info(
            "AUDIT_LOG_ARCHIVE_BUCKET 미설정 + DELETE_ONLY 비활성 → "
            "archive skip (행 보존). retention=%d days", retention_days,
        )
        return {"archived": 0, "deleted": 0, "skipped": 1}

    # bucket 이 설정된 경우 실제 S3 dump 는 별도 PR (현재 P1 범위) — 여기서는
    # delete-only 또는 dummy ack. 추후 boto3 dump 추가 가능.
    if bucket:
        logger.warning(
            "AUDIT_LOG_ARCHIVE_BUCKET=%s 설정됐지만 dump 미구현 — 행 보존",
            bucket,
        )
        return {"archived": 0, "deleted": 0, "skipped": 1}

    # delete_only 모드만 실제 archive_older_than 호출
    deleted = await repo.archive_older_than(days=retention_days)
    logger.info(
        "Audit log archive: deleted %d rows older than %d days",
        deleted, retention_days,
    )
    return {"archived": 0, "deleted": deleted, "skipped": 0}


async def audit_log_archive_sweep(ctx: dict[str, Any]) -> dict[str, Any]:
    """arq entrypoint."""
    try:
        from src.stores.postgres.session import get_knowledge_session_maker
        from src.stores.postgres.repositories.audit_log import (
            AuditLogRepository,
        )
    except ImportError as e:
        return {"status": "skipped", "reason": str(e)}

    session_maker = get_knowledge_session_maker()
    if session_maker is None:
        return {"status": "skipped", "reason": "no_database_url"}

    repo = AuditLogRepository(session_maker)
    return await run_audit_archive(repo=repo)


__all__ = ["audit_log_archive_sweep", "run_audit_archive"]
