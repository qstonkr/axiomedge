"""Bulk upload sessions — presigned URL flow 의 진행 상태 추적.

Revision ID: 0009_bulk_upload_sessions
Revises: 0008_distill_build_gpu_metadata
Create Date: 2026-04-21

배경: ``DocumentUploader.tsx`` 의 sequential POST 패턴이 3500개 파일 같은
대량 케이스에서 1시간+ 점유 + 진행률 X + retry X 문제. 본질 해결로 사용자
브라우저 → MinIO/S3 직접 PUT (백엔드 우회) + arq job 으로 ingest.

Schema:
- ``id`` (uuid PK), ``kb_id``, ``organization_id`` (multi-tenant 격리)
- ``owner_user_id`` (사용자 self-service 격리 — me_data_sources 패턴)
- ``s3_prefix`` — ``user/{uid}/uploads/{sid}/``
- ``total_files`` / ``processed_files`` / ``failed_files`` — 진행률 polling
- ``status`` — pending → processing → completed / failed
- ``files`` JSON — file_idx 별 (filename, s3_key, size)
- ``errors`` JSON — 실패 파일 누적

Index:
- ``idx_bus_owner_status`` — 사용자 화면의 "내 진행 중 업로드" hot query
- ``idx_bus_kb`` — KB 별 진행 중 업로드 조회

downgrade: index drop + table drop.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "0009_bulk_upload_sessions"
down_revision = "0008_distill_build_gpu_metadata"
branch_labels = None
depends_on = None

TABLE = "knowledge_bulk_upload_sessions"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("kb_id", sa.String(100), nullable=False),
        sa.Column("organization_id", sa.String(100), nullable=False),
        sa.Column("owner_user_id", sa.String(100), nullable=False),
        sa.Column("s3_prefix", sa.String(255), nullable=False),
        sa.Column("total_files", sa.Integer, nullable=False),
        sa.Column(
            "processed_files", sa.Integer, nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "failed_files", sa.Integer, nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "status", sa.String(20), nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("errors", sa.Text, server_default=sa.text("'[]'")),
        sa.Column("files", sa.Text, server_default=sa.text("'[]'")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index("idx_bus_owner_status", TABLE, ["owner_user_id", "status"])
    op.create_index("idx_bus_kb", TABLE, ["kb_id"])


def downgrade() -> None:
    op.drop_index("idx_bus_kb", TABLE)
    op.drop_index("idx_bus_owner_status", TABLE)
    op.drop_table(TABLE)
