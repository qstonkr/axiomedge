"""baseline — captures schema as of Alembic adoption

Revision ID: 0001_baseline
Revises:
Create Date: 2026-04-18

기존 DB 는 ``alembic stamp head`` 로 baseline 에 도달한 것으로 표시한다.
신규 DB 는 ``init_db.init_database()`` 가 ``metadata.create_all()`` 로 테이블을
만든 뒤 자동으로 stamp head 한다 (init_db.py 참고).

이후 스키마 변경은 ``alembic revision --autogenerate -m "..."`` 로
새 migration 을 생성하고 ``alembic upgrade head`` 로 적용한다.
"""

from __future__ import annotations

# revision identifiers, used by Alembic.
revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # No-op: baseline 은 metadata.create_all() 결과와 동등하다.
    # 신규 DB 는 init_db 가 create_all 후 stamp 하므로 이 upgrade 는 실행되지 않는다.
    pass


def downgrade() -> None:
    # baseline 이전으로 내릴 수 없음 (전체 drop 은 init_db.drop_all_tables 사용).
    pass
