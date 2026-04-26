"""knowledge_audit_logs.created_at 인덱스 추가 — archive cron range scan 가속.

Revision ID: 0012_audit_log_created_at_index
Revises: 0011_feature_flags
Create Date: 2026-04-26

배경: PR-12 (J) audit_log archive cron 이 ``created_at < cutoff`` range
조회를 daily 로 수행. 인덱스 없이는 N 일 후 row 가 1M+ 누적 시 full scan.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "0012_audit_log_created_at_index"
down_revision = "0011_feature_flags"
branch_labels = None
depends_on = None

TABLE = "knowledge_audit_logs"
INDEX = "idx_audit_created_at"


def upgrade() -> None:
    op.create_index(INDEX, TABLE, ["created_at"])


def downgrade() -> None:
    op.drop_index(INDEX, TABLE)
