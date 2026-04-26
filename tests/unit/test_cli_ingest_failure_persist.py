"""CLI ingest — failure 영속화 helper (PR-1 A).

- _persist_failure: failure_repo None 이면 no-op, 정상이면 record() 호출
- _ingest_single_file: pipeline 결과 success=False 시 record() 1회 호출
- _ingest_single_file: caller 단계에서 raise 시 stage="caller" 로 기록 + 0 반환
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


class TestPersistFailureWrapper:
    @pytest.mark.asyncio
    async def test_noop_when_repo_none(self):
        from src.cli.ingest import _persist_failure

        await _persist_failure(
            None, run_id="r1", kb_id="kb", doc_id="d",
            source_uri="/x", stage="embed", reason="x",
        )
        # no exception, no behavior — 통과 자체가 검증

    @pytest.mark.asyncio
    async def test_noop_when_run_id_none(self):
        from src.cli.ingest import _persist_failure

        repo = MagicMock()
        repo.record = AsyncMock()
        await _persist_failure(
            repo, run_id=None, kb_id="kb", doc_id="d",
            source_uri=None, stage="embed", reason="x",
        )
        repo.record.assert_not_called()

    @pytest.mark.asyncio
    async def test_calls_record_with_args(self):
        from src.cli.ingest import _persist_failure

        repo = MagicMock()
        repo.record = AsyncMock()
        await _persist_failure(
            repo, run_id="r1", kb_id="kb-a", doc_id="doc-1",
            source_uri="/tmp/f.pdf", stage="store",
            reason="boom", traceback="tb",
        )
        repo.record.assert_awaited_once_with(
            run_id="r1", kb_id="kb-a", doc_id="doc-1",
            source_uri="/tmp/f.pdf", stage="store",
            reason="boom", traceback="tb",
        )

    @pytest.mark.asyncio
    async def test_swallows_repo_runtime_error(self):
        from src.cli.ingest import _persist_failure

        repo = MagicMock()
        repo.record = AsyncMock(side_effect=RuntimeError("db down"))

        # 예외가 호출자에게 전파되지 않아야 한다 (best-effort)
        await _persist_failure(
            repo, run_id="r", kb_id="kb", doc_id="d",
            source_uri=None, stage="x", reason="y",
        )
        repo.record.assert_awaited_once()


class TestIngestSingleFileFailureRouting:
    @pytest.mark.asyncio
    async def test_records_when_pipeline_returns_failure(
        self, monkeypatch, tmp_path,
    ):
        """pipeline.ingest 가 success=False 반환 시 failure_repo.record 1회."""
        from src.cli import ingest as cli_mod
        from src.core.models import IngestionResult

        f = tmp_path / "a.txt"
        f.write_text("hello world")

        # parse_file_enhanced mock — full_text 가진 결과
        fake_parse = MagicMock(full_text="hello world")
        monkeypatch.setattr(
            "src.pipelines.document_parser.parse_file_enhanced",
            lambda _: fake_parse,
        )

        pipeline = MagicMock()
        pipeline.ingest = AsyncMock(return_value=IngestionResult.failure_result(
            reason="quality below silver",
            stage="quality_check",
            traceback=None,
        ))

        repo = MagicMock()
        repo.record = AsyncMock()

        chunks = await cli_mod._ingest_single_file(
            str(f), "a.txt", "kb-x", pipeline,
            run_id="run-1", failure_repo=repo,
        )
        assert chunks == 0
        repo.record.assert_awaited_once()
        kwargs = repo.record.await_args.kwargs
        assert kwargs["stage"] == "quality_check"
        assert kwargs["run_id"] == "run-1"
        assert kwargs["kb_id"] == "kb-x"
        assert kwargs["source_uri"] == str(f)

    @pytest.mark.asyncio
    async def test_records_caller_stage_when_pipeline_raises(
        self, monkeypatch, tmp_path,
    ):
        """pipeline 외부에서 raise 시 stage='caller' 로 기록 + 0 반환 (swallow)."""
        from src.cli import ingest as cli_mod

        f = tmp_path / "b.txt"
        f.write_text("oops")

        # parse_file_enhanced 자체가 raise — caller 단계 실패 시뮬레이션
        def _raising_parse(_):
            raise OSError("disk gone")
        monkeypatch.setattr(
            "src.pipelines.document_parser.parse_file_enhanced",
            _raising_parse,
        )

        pipeline = MagicMock()  # 호출되지 않음
        repo = MagicMock()
        repo.record = AsyncMock()

        chunks = await cli_mod._ingest_single_file(
            str(f), "b.txt", "kb", pipeline,
            run_id="r1", failure_repo=repo,
        )
        assert chunks == 0
        repo.record.assert_awaited_once()
        kwargs = repo.record.await_args.kwargs
        assert kwargs["stage"] == "caller"
        assert "disk gone" in kwargs["reason"]
        assert kwargs["traceback"] is not None

    @pytest.mark.asyncio
    async def test_no_record_call_when_repo_none(
        self, monkeypatch, tmp_path,
    ):
        """failure_repo=None 인 경우 (DATABASE_URL 미설정) record 호출 없음."""
        from src.cli import ingest as cli_mod
        from src.core.models import IngestionResult

        f = tmp_path / "c.txt"
        f.write_text("x")

        fake_parse = MagicMock(full_text="x")
        monkeypatch.setattr(
            "src.pipelines.document_parser.parse_file_enhanced",
            lambda _: fake_parse,
        )

        pipeline = MagicMock()
        pipeline.ingest = AsyncMock(
            return_value=IngestionResult.failure_result(
                reason="x", stage="dedup",
            )
        )

        chunks = await cli_mod._ingest_single_file(
            str(f), "c.txt", "kb", pipeline,
            run_id=None, failure_repo=None,
        )
        assert chunks == 0
        # repo 가 None 이므로 외부 영향 없음 — 단지 graceful
