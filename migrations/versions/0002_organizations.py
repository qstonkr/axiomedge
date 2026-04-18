"""organizations + org_memberships tables (SAML/Tenant prep)

Revision ID: 0002_organizations
Revises: 0001_baseline
Create Date: 2026-04-18

Org/Tenant 데이터 모델 prep.
- 기존 UserModel.organization_id / KBConfigModel.organization_id 컬럼은 그대로 둠
- 이 마이그레이션은 organizations / org_memberships 테이블만 추가
- FK 추가 + ABAC 통합 + SAML 통합은 별도 PR (실 SAML IdP 결정 후)

Zero-downtime 안전:
- 신규 테이블 추가 — 기존 trafic 영향 없음
- NULLABLE FK 만 (기존 row 영향 없음)
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

# revision identifiers, used by Alembic.
revision = "0002_organizations"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "organizations",
        sa.Column("id", sa.String(100), primary_key=True),
        sa.Column("slug", sa.String(100), nullable=False, unique=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("sso_provider", sa.String(50), nullable=True),
        sa.Column("sso_metadata", JSONB, nullable=False, server_default="{}"),
        sa.Column("max_users", sa.Integer, nullable=True),
        sa.Column("max_kbs", sa.Integer, nullable=True),
        sa.Column("max_storage_gb", sa.Integer, nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("settings", JSONB, nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime,
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime,
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index("idx_organizations_status", "organizations", ["status"])
    op.create_index("idx_organizations_slug", "organizations", ["slug"], unique=True)

    op.create_table(
        "org_memberships",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(100), nullable=False),
        sa.Column("organization_id", sa.String(100), nullable=False),
        sa.Column("role", sa.String(20), nullable=False, server_default="member"),
        sa.Column("invited_by", sa.String(100), nullable=True),
        sa.Column(
            "invited_at",
            sa.DateTime,
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("joined_at", sa.DateTime, nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.UniqueConstraint("user_id", "organization_id", name="uq_org_member_user_org"),
    )
    op.create_index("idx_org_membership_user", "org_memberships", ["user_id"])
    op.create_index("idx_org_membership_org", "org_memberships", ["organization_id"])


def downgrade() -> None:
    op.drop_index("idx_org_membership_org", table_name="org_memberships")
    op.drop_index("idx_org_membership_user", table_name="org_memberships")
    op.drop_table("org_memberships")
    op.drop_index("idx_organizations_slug", table_name="organizations")
    op.drop_index("idx_organizations_status", table_name="organizations")
    op.drop_table("organizations")
