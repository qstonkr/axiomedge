"""CLI ingest_directory 파일 단위 병렬화 — PR-4 (C).

- Semaphore 가 max in-flight 를 제한
- 모든 파일이 처리됨 (skip 또는 ingest)
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest


class TestParallelIngestDirectory:
    @pytest.mark.asyncio
    async def test_semaphore_caps_in_flight(self, tmp_path, monkeypatch):
        """Sem(2) → 동시 in-flight 가 2를 넘지 않음."""
        # 10개 파일 생성
        for i in range(10):
            (tmp_path / f"f{i}.txt").write_text(f"content {i}")

        # parse_file_enhanced — 즉시 텍스트 반환
        fake_parse = MagicMock(full_text="hello")
        monkeypatch.setattr(
            "src.pipelines.document_parser.parse_file_enhanced",
            lambda _: fake_parse,
        )

        # pipeline.ingest 가 async 라 in-flight 카운트 검증 가능
        max_in_flight = 0
        in_flight = 0
        in_flight_lock = asyncio.Lock()

        async def _slow_ingest(raw, collection_name):
            nonlocal max_in_flight, in_flight
            async with in_flight_lock:
                in_flight += 1
                max_in_flight = max(max_in_flight, in_flight)
            try:
                await asyncio.sleep(0.02)
            finally:
                async with in_flight_lock:
                    in_flight -= 1
            from src.core.models import IngestionResult
            return IngestionResult.success_result(chunks_stored=3)

        # _init_services / pipeline / hash check 등을 모두 mock
        from src.cli import ingest as cli_mod

        fake_pipeline = MagicMock()
        fake_pipeline.ingest = AsyncMock(side_effect=_slow_ingest)
        fake_provider = MagicMock()
        fake_provider.close = AsyncMock()

        async def _init():
            return (None, None, None, None, None, fake_provider)
        monkeypatch.setattr(cli_mod, "_init_services", _init)
        monkeypatch.setattr(
            "src.pipelines.ingestion.IngestionPipeline",
            lambda **_kw: fake_pipeline,
        )
        # incremental hashes 비활성
        async def _no_hashes(*_a, **_kw):
            return set()
        monkeypatch.setattr(cli_mod, "_get_ingested_hashes", _no_hashes)
        # run-tracking off (DATABASE_URL 미설정 시뮬레이션)
        async def _no_run(*_a, **_kw):
            return (None, None, None)
        monkeypatch.setattr(cli_mod, "_init_run_tracking", _no_run)
        # 병렬도를 2 로 강제 — async 함수
        async def _force_two(kb_id=None):  # noqa: ARG001
            return 2
        monkeypatch.setattr(cli_mod, "_resolve_file_parallel", _force_two)
        # _should_skip_file 무력화
        async def _no_skip(*_a, **_kw):
            return False
        monkeypatch.setattr(cli_mod, "_should_skip_file", _no_skip)

        await cli_mod.ingest_directory(str(tmp_path), "kb-test", force=False)

        assert fake_pipeline.ingest.await_count == 10
        # Sem(2) → in-flight ≤ 2
        assert max_in_flight <= 2

    @pytest.mark.asyncio
    async def test_resolve_file_parallel_uses_settings(self):
        from src.cli.ingest import _resolve_file_parallel

        n = await _resolve_file_parallel()
        assert isinstance(n, int)
        assert n >= 1

    @pytest.mark.asyncio
    async def test_resolve_file_parallel_fallback_on_settings_error(
        self, monkeypatch,
    ):
        from src.cli import ingest as cli_mod

        def _raising():
            raise RuntimeError("settings broken")
        monkeypatch.setattr("src.config.get_settings", _raising)
        # Feature flag check 도 같은 RuntimeError 로 무력화 → settings fallback → 1
        async def _ff_off(*_a, **_kw):
            raise RuntimeError("ff broken")
        monkeypatch.setattr("src.core.feature_flags.get_flag", _ff_off)

        n = await cli_mod._resolve_file_parallel()
        assert n == 1

    @pytest.mark.asyncio
    async def test_feature_flag_disable_forces_serial(self, monkeypatch):
        """ENABLE_INGESTION_FILE_PARALLEL=false 면 settings 와 무관하게 1 반환."""
        from src.cli import ingest as cli_mod

        async def _flag_disabled(name, **_kw):  # noqa: ARG001
            return False
        monkeypatch.setattr(
            "src.core.feature_flags.get_flag", _flag_disabled,
        )
        n = await cli_mod._resolve_file_parallel(kb_id="kb-x")
        assert n == 1
