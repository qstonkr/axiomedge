"""Per-document ingestion failure tracking.

Revision ID: 0010_ingestion_document_failures
Revises: 0009_bulk_upload_sessions
Create Date: 2026-04-26

배경: ``knowledge_ingestion_runs.errors`` 가 head 10개만 보존하고, 파일별 실패
사유/stage/traceback 이 영속 저장되지 않아 운영팀이 실패 원인을 추적하기 어려운
문제. run row 의 errors 요약은 그대로 유지하면서, 본 테이블에서 모든 문서별
실패를 stage·reason·traceback 와 함께 보존하여 ``--retry-failed RUN_ID`` 등
재시도 흐름의 근거가 된다.

Schema:
- ``id`` (uuid PK), ``run_id`` (FK CASCADE → knowledge_ingestion_runs)
- ``kb_id``, ``doc_id``, ``source_uri`` — 재시도 입력에 필요
- ``stage`` — dedup / quality_check / ingestion_gate / chunk / embed /
  store_qdrant / store_graph / graphrag / pipeline / caller
- ``reason`` (Text), ``traceback`` (Text, last 4KB)
- ``attempt`` — 동일 (run_id, doc_id) 재시도 횟수
- ``failed_at`` — 발생 시각

Index:
- ``idx_kif_kb_failed_at`` — KB별 최근 실패 sweep (Slack alert 용)
- ``idx_kif_run`` — Run별 실패 목록 (--retry-failed)
- ``idx_kif_stage`` — stage 분포 분석
- ``idx_kif_doc`` — 동일 doc 재실패 추적

downgrade: index drop + table drop. FK CASCADE 이므로 run 삭제 시 자동 정리.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "0010_ingestion_document_failures"
down_revision = "0009_bulk_upload_sessions"
branch_labels = None
depends_on = None

TABLE = "knowledge_ingestion_document_failures"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "run_id",
            sa.String(36),
            sa.ForeignKey(
                "knowledge_ingestion_runs.id", ondelete="CASCADE"
            ),
            nullable=False,
        ),
        sa.Column("kb_id", sa.String(255), nullable=False),
        sa.Column("doc_id", sa.String(64), nullable=False),
        sa.Column("source_uri", sa.Text, nullable=True),
        sa.Column("stage", sa.String(40), nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("traceback", sa.Text, nullable=True),
        sa.Column(
            "attempt",
            sa.Integer,
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column(
            "failed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index(
        "idx_kif_kb_failed_at", TABLE, ["kb_id", "failed_at"]
    )
    op.create_index("idx_kif_run", TABLE, ["run_id"])
    op.create_index("idx_kif_stage", TABLE, ["stage"])
    op.create_index("idx_kif_doc", TABLE, ["kb_id", "doc_id"])


def downgrade() -> None:
    op.drop_index("idx_kif_doc", TABLE)
    op.drop_index("idx_kif_stage", TABLE)
    op.drop_index("idx_kif_run", TABLE)
    op.drop_index("idx_kif_kb_failed_at", TABLE)
    op.drop_table(TABLE)
