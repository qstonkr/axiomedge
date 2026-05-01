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


async def run_audit_archive(*, repo: Any) -> dict[str, Any]:
    """Pure function — testable. repo 는 AuditLogRepository.

    Modes:
      1. **Default (no env)**: row 보존 — 운영자가 명시적 결정 안 한 상태이므로
         silent skip 으로 안전 fallback.
      2. ``AUDIT_LOG_DELETE_ONLY=1``: dump 없이 delete only (cheap retention).
      3. ``AUDIT_LOG_ARCHIVE_BUCKET=s3://...``: S3 dump 후 delete. **현재 dump
         미구현 — fail-fast** (P0-W3): 운영자가 retention 활성화 의도로 bucket
         을 세팅했는데 silent skip 하면 row 가 영원히 안 지워지는 trap. 따라서
         bucket 만 세팅됐으면 ``status="error"`` 반환 + alert 가능 한 형태.
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
        return {"archived": 0, "deleted": 0, "skipped": 1, "status": "noop"}

    # P0-W3 — bucket 만 설정된 경우 silent skip 대신 명시적 error 보고.
    # 향후 boto3 dump 추가 시 본 분기를 dump 호출로 교체.
    if bucket:
        logger.error(
            "AUDIT_LOG_ARCHIVE_BUCKET=%s 설정됐지만 dump 가 미구현입니다. "
            "row 가 삭제되지 않아 retention 의도가 달성되지 않습니다. "
            "options: (1) ``AUDIT_LOG_DELETE_ONLY=1`` 로 dump 없이 삭제만, "
            "(2) boto3 dump 구현 PR 머지, "
            "(3) 본 cron 비활성화.",
            bucket,
        )
        # Operator 가 모니터링할 수 있는 metric counter 도 함께
        try:
            from src.api.routes.metrics import inc as metrics_inc
            metrics_inc("errors", 1)
        except (ImportError, AttributeError):
            pass
        return {
            "archived": 0, "deleted": 0, "skipped": 1,
            "status": "error",
            "reason": "archive_bucket_set_but_dump_unimplemented",
        }

    # delete_only 모드만 실제 archive_older_than 호출
    deleted = await repo.archive_older_than(days=retention_days)
    logger.info(
        "Audit log archive: deleted %d rows older than %d days",
        deleted, retention_days,
    )
    return {
        "archived": 0, "deleted": deleted, "skipped": 0,
        "status": "ok",
    }


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
