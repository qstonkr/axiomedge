"""distill_builds 의 GPU async sweeper 메타 컬럼 추가.

Revision ID: 0008_distill_build_gpu_metadata
Revises: 0007_data_source_owner_user
Create Date: 2026-04-21

배경: GPU 학습이 fire-and-forget asyncio.create_task → API 재시작 시 task 소실.
또한 _poll_s3_output timeout 시 EC2 stop 호출이 학습 중 instance 도 죽임.
arq cron sweeper 패턴으로 전환 — DB 가 SSOT.

Schema (모두 nullable, backfill 불필요):
- ``gpu_instance_id VARCHAR(64) NULL`` — start_gpu_training 시 기록.
  NULL = 신구조 미적용 row (기존 진행 중 build 보호 — sweeper 가 무시).
- ``gpu_started_at TIMESTAMPTZ NULL`` — EC2 start 시각. 24h SLA 기준.
- ``s3_result_key VARCHAR(255) NULL`` — sweeper 가 polling 할 S3 path.
- ``last_sweep_at TIMESTAMPTZ NULL`` — 마지막 sweeper 스캔. lock-free idempotency.
- ``gpu_finished_at TIMESTAMPTZ NULL`` — sweeper 가 완료/실패 detect 시각.

Index ``idx_distill_build_status_sweep`` (status, last_sweep_at) — sweeper hot
query (``WHERE status='training' AND (last_sweep_at IS NULL OR < threshold)``).

downgrade: column + index drop.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "0008_distill_build_gpu_metadata"
down_revision = "0007_data_source_owner_user"
branch_labels = None
depends_on = None

TABLE = "distill_builds"
INDEX_NAME = "idx_distill_build_status_sweep"


def upgrade() -> None:
    op.add_column(TABLE, sa.Column("gpu_instance_id", sa.String(64), nullable=True))
    op.add_column(TABLE, sa.Column("gpu_started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(TABLE, sa.Column("s3_result_key", sa.String(255), nullable=True))
    op.add_column(TABLE, sa.Column("last_sweep_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(TABLE, sa.Column("gpu_finished_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(INDEX_NAME, TABLE, ["status", "last_sweep_at"])


def downgrade() -> None:
    op.drop_index(INDEX_NAME, TABLE)
    op.drop_column(TABLE, "gpu_finished_at")
    op.drop_column(TABLE, "last_sweep_at")
    op.drop_column(TABLE, "s3_result_key")
    op.drop_column(TABLE, "gpu_started_at")
    op.drop_column(TABLE, "gpu_instance_id")
