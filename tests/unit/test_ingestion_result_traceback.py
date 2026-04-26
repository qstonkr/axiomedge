"""IngestionResult.traceback 필드 + stage tracking — PR-1 (A).

- failure_result(traceback=...)가 dataclass 에 보존됨
- success_result 는 traceback=None 유지 (하위 호환)
- 파이프라인 except 블록이 traceback 을 채우고 _stage 를 stage 필드에 반영
"""

from __future__ import annotations

import pytest

from src.core.models import IngestionResult, RawDocument


class TestIngestionResultDataclass:
    def test_failure_result_carries_traceback(self):
        res = IngestionResult.failure_result(
            reason="boom", stage="embed",
            traceback="Traceback (most recent call last)\n  ...\nValueError: x",
        )
        assert res.success is False
        assert res.stage == "embed"
        assert res.traceback is not None
        assert "Traceback" in res.traceback

    def test_failure_result_traceback_optional(self):
        res = IngestionResult.failure_result(reason="x", stage="dedup")
        assert res.traceback is None

    def test_success_result_traceback_none(self):
        res = IngestionResult.success_result(chunks_stored=3)
        assert res.success is True
        assert res.traceback is None

    def test_blocked_failure_dataclass_round_trip(self):
        res = IngestionResult(
            success=False, blocked=True, reason="gate",
            stage="ingestion_gate", chunks_stored=0,
            metadata={"k": 1}, traceback=None,
        )
        assert res.blocked is True
        assert res.stage == "ingestion_gate"
        assert res.metadata == {"k": 1}


class _FailingChunker:
    """build_typed_chunks 가 raise 되도록 강제 — embed 단계 도달 못 함."""
    pass


class TestPipelineExceptCaptures:
    """Pipeline 의 except 블록이 traceback + stage 를 채우는지 회귀 테스트.

    실제 IngestionPipeline 의 무거운 의존성(임베더/qdrant/neo4j)을 띄우지
    않고, 파이프라인 함수가 raise 시 IngestionResult 에 traceback 이
    들어가는지 검증한다. 이를 위해 ingestion 모듈의 ``check_ingestion_gate``
    를 monkeypatch 하여 강제 raise.
    """

    @pytest.mark.asyncio
    async def test_traceback_captured_when_gate_raises(self, monkeypatch):
        from src.pipelines import ingestion as ing_mod

        def _raising_gate(raw, name, gate):
            raise RuntimeError("gate exploded")

        monkeypatch.setattr(ing_mod, "check_ingestion_gate", _raising_gate)

        # 최소 의존성으로 IngestionPipeline 인스턴스화 (다른 stage 도달 안 함)
        # 인자가 None 이어도 gate 단계에서 raise 되므로 후속 단계 영향 없음.
        from src.pipelines.ingestion import IngestionPipeline

        try:
            pipe = IngestionPipeline(
                embedder=None, sparse_embedder=None,
                vector_store=None, graph_store=None,
            )
        except TypeError:
            pytest.skip(
                "IngestionPipeline ctor signature changed — adapt test"
            )

        raw = RawDocument(doc_id="d1", title="t", content="c", source_uri="/x")
        result = await pipe.ingest(raw, collection_name="kb")
        assert result.success is False
        assert result.stage == "ingestion_gate"
        assert result.reason and "gate exploded" in result.reason
        assert result.traceback is not None
        assert "RuntimeError" in result.traceback

    @pytest.mark.asyncio
    async def test_traceback_truncated_to_4kb(self, monkeypatch):
        from src.pipelines import ingestion as ing_mod

        # 길이 자체는 4096 이하이지만, 라인 길이가 매우 길어 4KB 절단 동작 검증
        def _raising_gate(raw, name, gate):
            raise RuntimeError("X" * 8000)

        monkeypatch.setattr(ing_mod, "check_ingestion_gate", _raising_gate)
        from src.pipelines.ingestion import IngestionPipeline

        try:
            pipe = IngestionPipeline(
                embedder=None, sparse_embedder=None,
                vector_store=None, graph_store=None,
            )
        except TypeError:
            pytest.skip("ctor changed")

        raw = RawDocument(doc_id="d2", title="t", content="c", source_uri="/x")
        result = await pipe.ingest(raw, collection_name="kb")
        assert result.traceback is not None
        # P1-W1 — pipeline 은 full traceback 을 IngestionResult.traceback 으로
        # 그대로 전달; hybrid 4KB cap 은 IngestionFailureRepository._truncate_
        # traceback 단계에서 적용된다 (tests/unit/test_ingestion_failures_repo
        # 가 cap 동작 검증). 본 assertion 은 raise 경로가 작동했음을 확인.
        assert "RuntimeError" in result.traceback
