"""Pipeline failure contract — mock-only (F5: moved from tests/integration/).

DB/HTTP 호출 없이 IngestionResult.failure_result + IngestionFailureRepository
.record + Slack alert 의 인터페이스 일관성을 검증한다. 실제 PG 연결을 쓰는
true integration 은 ``tests/integration/test_failure_repo_real_db.py`` (P1-3,
``@pytest.mark.requires_postgres``) 가 담당.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.models import IngestionResult
from src.stores.postgres.repositories.ingestion_failures import (
    IngestionFailureRepository,
)


def _fake_session_maker():
    session = MagicMock()
    session.add = MagicMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.execute = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    return MagicMock(return_value=session), session


@pytest.mark.asyncio
async def test_failure_persistence_contract():
    """IngestionResult.failure_result + repo.record 가 서로 호환되는 형태."""
    res = IngestionResult.failure_result(
        reason="quality below silver",
        stage="quality_check",
        traceback="Traceback (most recent call last)\n...",
    )
    assert res.success is False
    assert res.stage == "quality_check"
    assert res.traceback is not None

    maker, session = _fake_session_maker()
    repo = IngestionFailureRepository(maker)
    row_id = await repo.record(
        run_id="r1", kb_id="kb-x", doc_id="doc-1",
        stage=res.stage, reason=res.reason or "", source_uri="/x.pdf",
        traceback=res.traceback,
    )
    assert row_id is not None
    session.add.assert_called_once()
    added = session.add.call_args[0][0]
    assert added.stage == "quality_check"
    assert added.reason == "quality below silver"


@pytest.mark.asyncio
async def test_metrics_contract():
    """Ingest metrics counter+stage helper API 가 정상 호출 가능."""
    from src.api.routes import metrics as M

    M._ingest_documents_total.clear()
    M._ingest_failures_total.clear()
    M._ingest_stage_duration_count.clear()
    try:
        M.inc_ingest("kb-x", "success")
        M.observe_ingest_stage("stage1_parse", 0.1)
        M.inc_ingest_failure("embed", "RuntimeError")
        text = M._render_prometheus()
        assert "ingest_documents_total_v2" in text
        assert "ingest_duration_seconds_bucket" in text
        assert "ingest_failures_total" in text
    finally:
        M._ingest_documents_total.clear()
        M._ingest_failures_total.clear()
        M._ingest_stage_duration_count.clear()


@pytest.mark.asyncio
async def test_alert_pipeline_contract():
    """run_repo + failure_repo + slack notify 가 동일 인터페이스로 연결됨."""
    from src.jobs.ingestion_alerts import run_ingestion_alerts

    run_repo = MagicMock()
    run_repo.recent_failure_streak = AsyncMock(return_value={"kb-z": 5})
    failure_repo = MagicMock()
    failure_repo.list_by_kb = AsyncMock(return_value=[
        {"doc_id": "d1", "stage": "embed", "reason": "boom"},
    ])

    sent: list[str] = []

    async def _fake_send(text: str) -> bool:
        sent.append(text)
        return True

    from src.notifications import slack as slack_mod
    orig = slack_mod.send
    slack_mod.send = _fake_send
    try:
        result = await run_ingestion_alerts(
            run_repo=run_repo, failure_repo=failure_repo, redis=None,
        )
    finally:
        slack_mod.send = orig

    assert result["fired"] >= 1
    assert any("kb-z" in s for s in sent)
