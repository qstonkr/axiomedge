"""data_sources 의 secret 분리 — secret_path + has_secret.

Revision ID: 0006_data_source_secret
Revises: 0005_data_source_org_required
Create Date: 2026-04-20

기존 ``crawl_config`` JSON 평문 안에 떠있던 connector token (Git auth_token,
Confluence PAT — env 에서 흡수된 경우 등) 을 SecretBox 로 분리.

Schema:
- ``secret_path VARCHAR(255) NULL`` — SecretBox path (org/{org_id}/data-source/{id}).
- ``has_secret BOOLEAN DEFAULT FALSE`` — UI 가 빠르게 체크 (실제 token 은
  반환 안 함).

Best-effort 마이그레이션 (별도 migration script 외 부담 없도록):
- ``crawl_config.auth_token`` 평문이 있으면 SecretBox 가 아직 활성화되지
  않았을 수 있으므로 **이 migration 에서는 column 만 추가**. 운영자가
  앱 시작 시점에 ``scripts/migrate_data_source_secrets.py`` (별도 oneshot)
  로 자동 추출 → SecretBox 저장 → crawl_config 평문 token 제거 권장.
- 또는 admin UI 에서 source 별 token 재입력.

downgrade: 두 column 단순 drop.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "0006_data_source_secret"
down_revision = "0005_data_source_org_required"
branch_labels = None
depends_on = None

TABLE = "knowledge_data_sources"


def upgrade() -> None:
    op.add_column(TABLE, sa.Column("secret_path", sa.String(255), nullable=True))
    op.add_column(
        TABLE,
        sa.Column(
            "has_secret",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column(TABLE, "has_secret")
    op.drop_column(TABLE, "secret_path")
