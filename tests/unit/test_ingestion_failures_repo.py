"""IngestionFailureRepository — record / list_by_run / list_by_kb /
doc_ids_for_run / delete_by_run_and_docs.

PR-1 (A): 파일별 실패 영속화 repository 의 핵심 메서드 라운드트립.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.stores.postgres.repositories.ingestion_failures import (
    IngestionFailureRepository,
)


def _make_session_maker():
    session = MagicMock()
    session.add = MagicMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.execute = AsyncMock()

    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)

    return MagicMock(return_value=session), session


class TestRecord:
    @pytest.mark.asyncio
    async def test_record_persists_row_and_returns_id(self):
        maker, session = _make_session_maker()
        repo = IngestionFailureRepository(maker)

        row_id = await repo.record(
            run_id="run-1", kb_id="kb-a", doc_id="doc-1",
            stage="embed", reason="boom",
            source_uri="/tmp/f.pdf",
            traceback="Traceback: ...",
        )
        assert row_id is not None
        assert isinstance(row_id, str)
        session.add.assert_called_once()
        session.commit.assert_awaited_once()

        added = session.add.call_args[0][0]
        assert added.run_id == "run-1"
        assert added.kb_id == "kb-a"
        assert added.doc_id == "doc-1"
        assert added.stage == "embed"
        assert added.reason == "boom"
        assert added.source_uri == "/tmp/f.pdf"
        assert added.attempt == 1

    @pytest.mark.asyncio
    async def test_record_truncates_traceback_hybrid(self):
        """P1-4: head 1KB + tail 3KB hybrid — 양쪽 frame 보존."""
        maker, session = _make_session_maker()
        repo = IngestionFailureRepository(maker)

        # head/tail 양쪽에 marker 문자를 심어서 보존 여부 검증
        head_marker = "HEAD_FRAME_FUNC_X"
        tail_marker = "TAIL_FRAME_FUNC_Y"
        big_tb = head_marker + "x" * 20000 + tail_marker
        await repo.record(
            run_id="r", kb_id="kb", doc_id="d",
            stage="pipeline", reason="oom", traceback=big_tb,
        )
        added = session.add.call_args[0][0]
        assert added.traceback is not None
        # head 와 tail 모두 보존
        assert head_marker in added.traceback
        assert tail_marker in added.traceback
        assert "[truncated middle frames]" in added.traceback
        # 총 크기는 head + marker + tail 이내 (~4.2KB 안)
        assert len(added.traceback) <= 4200

    @pytest.mark.asyncio
    async def test_record_short_traceback_not_truncated(self):
        """4KB 이하 traceback 은 그대로 유지."""
        maker, session = _make_session_maker()
        repo = IngestionFailureRepository(maker)
        small_tb = "Traceback (most recent call last)\n  File 'a.py'\n  ValueError"
        await repo.record(
            run_id="r", kb_id="kb", doc_id="d",
            stage="pipeline", reason="x", traceback=small_tb,
        )
        added = session.add.call_args[0][0]
        assert added.traceback == small_tb
        assert "[truncated" not in added.traceback

    @pytest.mark.asyncio
    async def test_record_swallows_db_error_and_returns_none(self):
        from sqlalchemy.exc import SQLAlchemyError
        maker, session = _make_session_maker()
        session.commit = AsyncMock(side_effect=SQLAlchemyError("db down"))
        repo = IngestionFailureRepository(maker)

        row_id = await repo.record(
            run_id="r", kb_id="kb", doc_id="d",
            stage="x", reason="y",
        )
        assert row_id is None
        session.rollback.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_record_uses_default_reason_when_empty(self):
        maker, session = _make_session_maker()
        repo = IngestionFailureRepository(maker)

        await repo.record(
            run_id="r", kb_id="kb", doc_id="d",
            stage="x", reason="",
        )
        added = session.add.call_args[0][0]
        assert added.reason == "(no reason)"


class TestListByRun:
    @pytest.mark.asyncio
    async def test_list_by_run_returns_serialized_dicts(self):
        maker, session = _make_session_maker()
        m1 = MagicMock(
            id="r1", run_id="run-1", kb_id="kb", doc_id="d1",
            source_uri=None, stage="embed", reason="x",
            traceback=None, attempt=1, failed_at=None,
        )
        scalars_result = MagicMock()
        scalars_result.all = MagicMock(return_value=[m1])
        result_obj = MagicMock()
        result_obj.scalars = MagicMock(return_value=scalars_result)
        session.execute = AsyncMock(return_value=result_obj)

        repo = IngestionFailureRepository(maker)
        rows = await repo.list_by_run("run-1")
        assert len(rows) == 1
        assert rows[0]["run_id"] == "run-1"
        assert rows[0]["doc_id"] == "d1"


class TestDocIdsForRun:
    @pytest.mark.asyncio
    async def test_doc_ids_returns_distinct_list(self):
        maker, session = _make_session_maker()
        result_obj = MagicMock()
        result_obj.all = MagicMock(return_value=[("d1",), ("d2",)])
        session.execute = AsyncMock(return_value=result_obj)

        repo = IngestionFailureRepository(maker)
        ids = await repo.doc_ids_for_run("run-1")
        assert ids == ["d1", "d2"]


class TestDeleteByRunAndDocs:
    @pytest.mark.asyncio
    async def test_delete_returns_rowcount(self):
        maker, session = _make_session_maker()
        result_obj = MagicMock(rowcount=2)
        session.execute = AsyncMock(return_value=result_obj)

        repo = IngestionFailureRepository(maker)
        n = await repo.delete_by_run_and_docs("run-1", ["d1", "d2"])
        assert n == 2
        session.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delete_empty_list_is_noop(self):
        maker, session = _make_session_maker()
        repo = IngestionFailureRepository(maker)
        n = await repo.delete_by_run_and_docs("run-1", [])
        assert n == 0
        session.execute.assert_not_called()
