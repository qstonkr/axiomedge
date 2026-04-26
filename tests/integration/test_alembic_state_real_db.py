"""Alembic migration state 회귀 가드 — C1 / P1-3.

Local PG 가 head (0013) 까지 적용된 상태인지 검증. CI 에서 db-upgrade 실행
후 본 테스트가 통과해야 deploy 진행 — 잠재적 schema drift 감지.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.requires_postgres


@pytest.mark.asyncio
async def test_alembic_version_at_head(pg_session_maker):
    """alembic_version 이 0013 이상 (또는 head) 인지 확인."""
    async with pg_session_maker() as session:
        result = await session.execute(text(
            "SELECT version_num FROM alembic_version"
        ))
        rows = result.all()
        assert len(rows) == 1
        version = rows[0][0]
        # 신규 PR 이 head 를 더 올릴 수 있으므로 prefix 비교가 아니라
        # 0010~0013 신규 마이그레이션이 모두 적용됐는지 검증.
        assert version is not None
        assert version >= "0013_failure_doc_id_widen"


@pytest.mark.asyncio
async def test_new_tables_present(pg_session_maker):
    """0010~0013 마이그레이션이 만든 테이블/인덱스 모두 존재."""
    expected_tables = [
        "knowledge_ingestion_document_failures",  # 0010
        "knowledge_bulk_upload_sessions",         # 0009
        "knowledge_feature_flags",                # 0011
    ]
    expected_indexes = [
        ("knowledge_ingestion_document_failures", "idx_kif_kb_failed_at"),
        ("knowledge_ingestion_document_failures", "idx_kif_run"),
        ("knowledge_ingestion_document_failures", "idx_kif_stage"),
        ("knowledge_ingestion_document_failures", "idx_kif_doc"),
        ("knowledge_audit_logs", "idx_audit_created_at"),  # 0012
        ("knowledge_feature_flags", "idx_ff_name"),         # 0011
    ]

    async with pg_session_maker() as session:
        for table in expected_tables:
            result = await session.execute(text(
                "SELECT 1 FROM information_schema.tables "
                f"WHERE table_name = '{table}'"
            ))
            assert result.scalar() is not None, f"missing table: {table}"

        for table, idx in expected_indexes:
            result = await session.execute(text(
                "SELECT 1 FROM pg_indexes "
                f"WHERE tablename = '{table}' AND indexname = '{idx}'"
            ))
            assert result.scalar() is not None, (
                f"missing index: {table}.{idx}"
            )


@pytest.mark.asyncio
async def test_doc_id_widened_to_128(pg_session_maker):
    """P2-3 / 0013 — knowledge_ingestion_document_failures.doc_id 가 VARCHAR(128)."""
    async with pg_session_maker() as session:
        result = await session.execute(text(
            "SELECT character_maximum_length "
            "FROM information_schema.columns "
            "WHERE table_name = 'knowledge_ingestion_document_failures' "
            "AND column_name = 'doc_id'"
        ))
        max_len = result.scalar()
        assert max_len == 128
