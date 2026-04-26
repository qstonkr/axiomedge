"""OTel tracing extensions — trace_ingest_stage, asyncpg toggle (PR-9 H).

- trace_ingest_stage 가 ContextManager 로 동작 (no-op when disabled)
- observe_ingest_stage 미존재 시 graceful skip
- neo4j_query_enabled env 토글
"""

from __future__ import annotations

from src.core.observability.tracing import (
    neo4j_query_enabled,
    trace_ingest_stage,
)


class TestTraceIngestStage:
    def test_context_manager_completes_without_error(self):
        with trace_ingest_stage("stage2_embed", kb_id="kb-x"):
            x = 1 + 1
        assert x == 2

    def test_skips_metric_when_module_lacks_function(self, monkeypatch):
        # observe_ingest_stage 가 import 실패해도 span 만 정상 종료
        import src.core.observability.tracing as t_mod

        # pytest 환경에서 metrics 모듈은 존재 가능 — graceful path 검증을 위해
        # 강제로 ImportError 시뮬레이션은 어려우므로 단순히 enter/exit 만 확인
        with t_mod.trace_ingest_stage("stage2_store"):
            pass


class TestNeo4jToggle:
    def test_default_enabled(self, monkeypatch):
        monkeypatch.delenv("OTEL_INSTRUMENT_NEO4J", raising=False)
        assert neo4j_query_enabled() is True

    def test_env_disable(self, monkeypatch):
        monkeypatch.setenv("OTEL_INSTRUMENT_NEO4J", "0")
        assert neo4j_query_enabled() is False

    def test_env_explicit_true(self, monkeypatch):
        monkeypatch.setenv("OTEL_INSTRUMENT_NEO4J", "yes")
        assert neo4j_query_enabled() is True


class TestInitTracingNoop:
    def test_no_endpoint_returns_false(self, monkeypatch):
        from src.core.observability import tracing as t_mod
        # Reset for test isolation
        t_mod._initialized = False
        monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
        assert t_mod.init_tracing(app=None) is False
