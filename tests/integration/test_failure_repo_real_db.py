"""Real PG round-trip — IngestionFailureRepository (P1-3).

Mock 이 아닌 실제 PostgreSQL 에 row 를 INSERT/SELECT/DELETE 하여 다음을 검증:
- FK CASCADE: run 삭제 시 failures 가 자동 정리되는가
- 4KB hybrid traceback truncation 이 PG Text 컬럼에 정확히 저장되는가
- list_by_run / doc_ids_for_run 의 순서·distinct 정합성

PG 미가용 시 자동 skip (require_postgres fixture).
"""

from __future__ import annotations

import pytest

from src.stores.postgres.repositories.ingestion_failures import (
    IngestionFailureRepository,
)


pytestmark = pytest.mark.requires_postgres


@pytest.mark.asyncio
async def test_record_and_list_round_trip(
    pg_session_maker, seed_ingestion_run,
):
    run_id, kb_id = seed_ingestion_run
    repo = IngestionFailureRepository(pg_session_maker)

    for i in range(3):
        rid = await repo.record(
            run_id=run_id, kb_id=kb_id,
            doc_id=f"doc-{i}", source_uri=f"/tmp/f{i}.pdf",
            stage="stage2_embed", reason=f"boom-{i}",
            traceback="Traceback: line 1\n  line 2\n  line 3",
        )
        assert rid is not None

    rows = await repo.list_by_run(run_id)
    assert len(rows) == 3
    doc_ids = {r["doc_id"] for r in rows}
    assert doc_ids == {"doc-0", "doc-1", "doc-2"}

    distinct = await repo.doc_ids_for_run(run_id)
    assert sorted(distinct) == sorted(["doc-0", "doc-1", "doc-2"])


@pytest.mark.asyncio
async def test_traceback_hybrid_persisted(
    pg_session_maker, seed_ingestion_run,
):
    run_id, kb_id = seed_ingestion_run
    repo = IngestionFailureRepository(pg_session_maker)

    head = "FIRST_FRAME_X" * 100
    middle = "y" * 30000
    tail = "LAST_FRAME_Y" * 100
    big_tb = head + middle + tail

    await repo.record(
        run_id=run_id, kb_id=kb_id, doc_id="big-doc",
        stage="stage2_store", reason="oom", traceback=big_tb,
    )
    rows = await repo.list_by_run(run_id)
    assert len(rows) == 1
    persisted_tb = rows[0]["traceback"]
    assert persisted_tb is not None
    assert "FIRST_FRAME_X" in persisted_tb
    assert "LAST_FRAME_Y" in persisted_tb
    assert "[truncated middle frames]" in persisted_tb
    assert len(persisted_tb) <= 4200


@pytest.mark.asyncio
async def test_fk_cascade_on_run_delete(pg_session_maker):
    """Run 삭제 시 failures 도 자동으로 정리됨 (FK CASCADE)."""
    import uuid
    from datetime import datetime, timezone
    from sqlalchemy import delete, select
    from src.stores.postgres.models import (
        IngestionDocumentFailureModel,
        IngestionRunModel,
    )

    run_id = str(uuid.uuid4())
    kb_id = f"_test_cascade_{run_id[:8]}"

    async with pg_session_maker() as session:
        session.add(IngestionRunModel(
            id=run_id, kb_id=kb_id,
            source_type="test", source_name="cascade",
            status="running",
            started_at=datetime.now(timezone.utc),
        ))
        await session.commit()

    repo = IngestionFailureRepository(pg_session_maker)
    for i in range(2):
        await repo.record(
            run_id=run_id, kb_id=kb_id, doc_id=f"casc-{i}",
            stage="x", reason="y",
        )
    assert len(await repo.list_by_run(run_id)) == 2

    async with pg_session_maker() as session:
        await session.execute(
            delete(IngestionRunModel).where(IngestionRunModel.id == run_id)
        )
        await session.commit()

    async with pg_session_maker() as session:
        result = await session.execute(
            select(IngestionDocumentFailureModel).where(
                IngestionDocumentFailureModel.run_id == run_id,
            )
        )
        assert result.scalars().all() == []
