"""B-0: RBAC enforcement — auth_roles.is_legacy + default-org seed

Revision ID: 0003_rbac_b0
Revises: 0002_organizations
Create Date: 2026-04-18

B-0 (Backend Multi-Tenant + RBAC Enforcement) Day 1.

Changes:
- Add `is_legacy` boolean column to `auth_roles` (default False).
- Seed a single `default-org` organization row so existing users + KBs can
  be backfilled to it on app startup (see scripts/backfill_org_id.py).

Zero-downtime safe:
- New column has server_default=False — existing rows updated implicitly.
- Organization row insert is idempotent (ON CONFLICT DO NOTHING).
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "0003_rbac_b0"
down_revision = "0002_organizations"
branch_labels = None
depends_on = None

DEFAULT_ORG_ID = "default-org"
DEFAULT_ORG_SLUG = "default"
DEFAULT_ORG_NAME = "Default Organization"


def upgrade() -> None:
    op.add_column(
        "auth_roles",
        sa.Column(
            "is_legacy",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.create_index(
        "idx_auth_role_legacy",
        "auth_roles",
        ["is_legacy"],
    )

    # Seed default-org. Existing 5 legacy roles will be flagged on startup
    # by AuthService.seed_defaults() (cannot do that here without app context).
    # created_at/updated_at are written explicitly because the live DB was
    # bootstrapped via create_all() (no server_default was applied to those
    # columns, so the model-level Python default fires only via the ORM).
    op.execute(
        sa.text(
            """
            INSERT INTO organizations
                (id, slug, name, status, sso_metadata, settings, created_at, updated_at)
            VALUES
                (:id, :slug, :name, 'active', '{}', '{}',
                 CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT (id) DO NOTHING
            """
        ).bindparams(
            id=DEFAULT_ORG_ID,
            slug=DEFAULT_ORG_SLUG,
            name=DEFAULT_ORG_NAME,
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM organizations WHERE id = :id").bindparams(id=DEFAULT_ORG_ID))
    op.drop_index("idx_auth_role_legacy", table_name="auth_roles")
    op.drop_column("auth_roles", "is_legacy")
