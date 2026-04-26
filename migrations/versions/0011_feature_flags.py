"""Feature flags table — runtime kill-switch (PR-11 N).

Revision ID: 0011_feature_flags
Revises: 0010_ingestion_document_failures
Create Date: 2026-04-26

배경: 위험한 변경(파일 단위 병렬 인제스트, GraphRAG batch Neo4j 등)을 코드
재배포 없이 즉시 enable/disable 하기 위한 경량 토글 테이블.

Schema:
- ``name`` — flag identifier (예: ENABLE_INGESTION_FILE_PARALLEL)
- ``scope`` — ``_global`` | ``org:<id>`` | ``kb:<id>`` (precedence: kb > org > global)
- ``enabled`` — bool
- ``payload`` — JSON 추가 설정 (예: 임계치 조정)
- ``updated_at``, ``updated_by``

PK = (name, scope) 으로 동일 flag 의 여러 scope 공존 가능.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "0011_feature_flags"
down_revision = "0010_ingestion_document_failures"
branch_labels = None
depends_on = None

TABLE = "knowledge_feature_flags"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column(
            "scope", sa.String(64), nullable=False,
            server_default=sa.text("'_global'"),
        ),
        sa.Column(
            "enabled", sa.Boolean, nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "payload", sa.Text, nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column("updated_by", sa.String(100), nullable=True),
        sa.PrimaryKeyConstraint("name", "scope"),
    )
    op.create_index("idx_ff_name", TABLE, ["name"])


def downgrade() -> None:
    op.drop_index("idx_ff_name", TABLE)
    op.drop_table(TABLE)
