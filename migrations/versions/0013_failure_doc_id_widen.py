"""knowledge_ingestion_document_failures.doc_id VARCHAR(64) → VARCHAR(128).

Revision ID: 0013_failure_doc_id_widen
Revises: 0012_audit_log_created_at_index
Create Date: 2026-04-26

배경: 0010 에서 ``doc_id VARCHAR(64)`` 는 SHA256 첫 64 문자 hex digest 와
정확히 일치. 그러나 legacy/connector 별 식별자가 prefix 를 포함할 수 있음
(예: ``confluence:373865276``, ``notion:abc123…``). PR-1 의 RawDocument.sha256
경로만 쓰면 안전하지만 직접 doc_id 주입 경로 (CLI ``--retry-failed`` 등) 에서
truncation 위험.

128자 로 확장: 인덱스 재생성 없이 ALTER TYPE 만 — PG 는 VARCHAR 길이 확장이
metadata 만 수정하는 instant 작업이라 다운타임 0.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "0013_failure_doc_id_widen"
down_revision = "0012_audit_log_created_at_index"
branch_labels = None
depends_on = None

TABLE = "knowledge_ingestion_document_failures"


def upgrade() -> None:
    # PG: ALTER COLUMN TYPE VARCHAR(N) — N 증가는 lock-free metadata change.
    op.alter_column(
        TABLE, "doc_id",
        existing_type=sa.String(64),
        type_=sa.String(128),
        existing_nullable=False,
    )


def downgrade() -> None:
    # 주의: 128자 row 가 있으면 truncation 위험. 운영자 수동 정리 필요.
    op.alter_column(
        TABLE, "doc_id",
        existing_type=sa.String(128),
        type_=sa.String(64),
        existing_nullable=False,
    )
