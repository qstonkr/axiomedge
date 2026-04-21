"""data_sources.owner_user_id — 사용자 self-service source 추적용.

Revision ID: 0007_data_source_owner_user
Revises: 0006_data_source_secret
Create Date: 2026-04-21

배경: 카탈로그 카드 UI 가 admin/사용자 양쪽에서 source 등록 지원하도록 확장
하면서, 누가 등록했는지 추적이 필요해짐.

- ``owner_user_id IS NULL`` — admin 등록 (organization-wide source). 기존
  source 모두 이 모드 (마이그레이션 backfill 없음).
- ``owner_user_id = '<uid>'`` — 사용자 self-service 등록. 본인의 personal
  KB 에만 attach 가능 (라우트 권한 체크: kb.owner_id == owner_user_id).

권한 체크는 라우트 layer 가 담당 — 여기서는 컬럼만 추가. FK 안 걸음 (users
테이블 schema 가 외부 IdP 와 결합되어 깨끗한 cascade 정의 어려움).

downgrade: index + 컬럼 drop.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "0007_data_source_owner_user"
down_revision = "0006_data_source_secret"
branch_labels = None
depends_on = None

TABLE = "knowledge_data_sources"
INDEX_NAME = "idx_kds_owner_user"


def upgrade() -> None:
    op.add_column(TABLE, sa.Column("owner_user_id", sa.String(100), nullable=True))
    op.create_index(INDEX_NAME, TABLE, ["owner_user_id"])


def downgrade() -> None:
    op.drop_index(INDEX_NAME, TABLE)
    op.drop_column(TABLE, "owner_user_id")
