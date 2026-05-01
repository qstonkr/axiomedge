"""distill: force_deploy column + source_chunk_fp column.

Revision ID: 0014_distill_force_deploy_and_chunk_fp
Revises: 0013_failure_doc_id_widen
Create Date: 2026-05-01

배경:
1. ``distill_builds.force_deploy``: 평가 게이트 우회용. 기본 False — 운영자가
   명시적으로 set 해야만 fail-closed 우회. 데이터 부재 / 의도된 회귀 빌드 /
   긴급 배포에 사용.
2. ``distill_training_data.source_chunk_fp``: chunk-level train/test partition.
   train QA 가 만들어진 chunk 와 같은 chunk 에서 test QA 가 만들어지는
   누수 차단. ``hashlib.sha256(content)[:16]``.

기존 row 는 force_deploy=False / source_chunk_fp=NULL — backward-compat 안전.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0014_distill_force_deploy_and_chunk_fp"
down_revision = "0013_failure_doc_id_widen"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "distill_builds",
        sa.Column(
            "force_deploy",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "distill_training_data",
        sa.Column("source_chunk_fp", sa.String(16), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("distill_training_data", "source_chunk_fp")
    op.drop_column("distill_builds", "force_deploy")
