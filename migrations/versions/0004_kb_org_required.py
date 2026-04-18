"""B-0 Day 5: KBConfigModel.organization_id NOT NULL + FK to organizations.

Revision ID: 0004_kb_org_required
Revises: 0003_rbac_b0
Create Date: 2026-04-19

Hardens the multi-tenant boundary that B-0 Day 3 introduced. Up to now the
column was nullable so ``scripts/backfill_org_id.py`` could populate it
incrementally; from this migration onwards every KB row must point at a real
organization.

**Point of no return** — the column flips to NOT NULL + foreign key. Run the
backfill script first (it is idempotent) and verify ``SELECT COUNT(*) FROM
kb_configs WHERE organization_id IS NULL`` returns 0 before applying.

Safety:
- Defensive backfill at the start of upgrade() — fills any straggler row with
  the seeded ``default-org`` so the migration cannot fail on stale data.
- FK uses ON DELETE RESTRICT — orgs can't be dropped while they own KBs.
- Downgrade reverts to the previous nullable state (no FK).
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "0004_kb_org_required"
down_revision = "0003_rbac_b0"
branch_labels = None
depends_on = None

DEFAULT_ORG_ID = "default-org"
FK_NAME = "fk_kb_configs_organization_id"


def upgrade() -> None:
    # Defensive backfill — should be a no-op if scripts/backfill_org_id.py
    # was already run. Cheap insurance against partially-migrated DBs.
    op.execute(
        sa.text(
            "UPDATE kb_configs SET organization_id = :org "
            "WHERE organization_id IS NULL"
        ).bindparams(org=DEFAULT_ORG_ID)
    )

    # Flip the column to NOT NULL.
    op.alter_column(
        "kb_configs",
        "organization_id",
        existing_type=sa.String(100),
        nullable=False,
    )

    # Foreign-key gate — an org can't be deleted while it still owns KBs.
    op.create_foreign_key(
        FK_NAME,
        source_table="kb_configs",
        referent_table="organizations",
        local_cols=["organization_id"],
        remote_cols=["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(FK_NAME, "kb_configs", type_="foreignkey")
    op.alter_column(
        "kb_configs",
        "organization_id",
        existing_type=sa.String(100),
        nullable=True,
    )
