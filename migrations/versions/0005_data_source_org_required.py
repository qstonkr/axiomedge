"""data_sources 멀티테넌트 격리 — organization_id NOT NULL + FK.

Revision ID: 0005_data_source_org_required
Revises: 0004_kb_org_required
Create Date: 2026-04-20

DataSourceModel 에 organization_id 가 누락되어 있어 cross-tenant 누설 가능
(`DataSourceRepository.list()` 가 org 필터 없이 전체 row 반환). KBConfigModel
은 0004 에서 동일 패턴으로 격리 완료 — data_sources 만 갭이 남아있던 것.

3 단계 안전 마이그레이션:
1. organization_id NULLABLE 컬럼 추가
2. kb_configs join 으로 backfill — kb_id 가 가리키는 KB 의 org 그대로 상속
3. NOT NULL + FK (RESTRICT) + 인덱스

table 명: ``knowledge_data_sources`` (model name 과 다름).

Safety:
- Defensive backfill 이 NULL 잔여 row 를 default-org 로 흡수 → migration
  fail-on-data 회피.
- FK ondelete=RESTRICT — org 가 data_source 가지고 있으면 삭제 불가.
- Downgrade 는 FK + NOT NULL + 컬럼 모두 제거.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "0005_data_source_org_required"
down_revision = "0004_kb_org_required"
branch_labels = None
depends_on = None

TABLE = "knowledge_data_sources"
COLUMN = "organization_id"
DEFAULT_ORG_ID = "default-org"
FK_NAME = "fk_knowledge_data_sources_organization_id"
INDEX_NAME = "idx_kds_org_id"


def upgrade() -> None:
    # 1) Add nullable column first — backfill 단계에서 NULL OK.
    op.add_column(
        TABLE,
        sa.Column(COLUMN, sa.String(100), nullable=True),
    )

    # 2) Backfill from kb_configs (kb_id → organization_id 1:1).
    #    NULL 잔여 (kb_id 가 stale 한 경우) 는 default-org 로.
    op.execute(
        sa.text(
            f"UPDATE {TABLE} "
            f"SET {COLUMN} = (SELECT organization_id FROM kb_configs "
            f"                WHERE id = {TABLE}.kb_id) "
            f"WHERE {COLUMN} IS NULL"
        )
    )
    op.execute(
        sa.text(
            f"UPDATE {TABLE} SET {COLUMN} = :org WHERE {COLUMN} IS NULL"
        ).bindparams(org=DEFAULT_ORG_ID)
    )

    # 3) Flip to NOT NULL + FK + index.
    op.alter_column(
        TABLE,
        COLUMN,
        existing_type=sa.String(100),
        nullable=False,
    )
    op.create_foreign_key(
        FK_NAME,
        source_table=TABLE,
        referent_table="organizations",
        local_cols=[COLUMN],
        remote_cols=["id"],
        ondelete="RESTRICT",
    )
    op.create_index(INDEX_NAME, TABLE, [COLUMN])


def downgrade() -> None:
    op.drop_index(INDEX_NAME, TABLE)
    op.drop_constraint(FK_NAME, TABLE, type_="foreignkey")
    op.drop_column(TABLE, COLUMN)
