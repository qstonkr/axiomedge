"""OpenTelemetry tracing — env-driven, no-op when OTLP endpoint absent.

Auto-instruments FastAPI + httpx. Manual spans for RAG pipeline stages.

Env vars:
  - OTEL_EXPORTER_OTLP_ENDPOINT: e.g., http://jaeger:4318 (no-op if absent)
  - OTEL_SERVICE_NAME: default "axiomedge-api"
  - OTEL_RESOURCE_ATTRIBUTES: comma-separated key=value (env, version)
  - OTEL_TRACES_SAMPLER: "always_on" | "always_off" | "traceidratio" (default "parentbased_always_on")
  - OTEL_TRACES_SAMPLER_ARG: ratio when sampler=traceidratio (e.g., "0.1")

Usage in code:
    from src.core.observability.tracing import tracer, trace_rag_stage

    # Plain span (only for ad-hoc instrumentation):
    with tracer.start_as_current_span("rag.embed", attributes={"kb_id": kb_id}):
        ...

    # RAG stage — trace + Prometheus histogram in one call (preferred):
    with trace_rag_stage("embed", kb_id=kb_id):
        ...

Tracer is always usable; when OTel disabled, span is no-op (zero overhead).
"""

from __future__ import annotations

import logging
import os
import time
from contextlib import contextmanager
from typing import Any, Iterator

from opentelemetry import trace

logger = logging.getLogger(__name__)


# Module-level tracer — usable from anywhere. Becomes no-op when SDK not initialized.
tracer = trace.get_tracer("axiomedge")


@contextmanager
def trace_rag_stage(stage: str, **attributes: Any) -> Iterator[None]:
    """Wrap a RAG pipeline stage with both an OTel span AND a Prometheus histogram.

    Use this instead of bare ``tracer.start_as_current_span`` for RAG stages so
    latency p95/p99 shows up in Prometheus too (Grafana alerting friendly).

    Stage names are bounded set: cache_check, preprocess, expand, classify, embed,
    qdrant_search, cross_encoder_rerank, composite_rerank, graph_expand,
    crag_evaluate, generate_answer.
    """
    # Local import to avoid circular dependency at module load time
    from src.api.routes.metrics import observe_rag_stage

    started = time.perf_counter()
    with tracer.start_as_current_span(f"rag.{stage}", attributes=attributes):
        try:
            yield
        finally:
            observe_rag_stage(stage, time.perf_counter() - started)


@contextmanager
def trace_ingest_stage(stage: str, **attributes: Any) -> Iterator[None]:
    """Wrap an ingestion stage with OTel span + Prometheus histogram (PR-9 H + PR-10 I).

    Stage 분류: stage1_parse, stage2_embed, stage2_store, stage3_quality,
    stage4_graph, stage5_index. ``observe_ingest_stage`` 가 metrics 모듈에 없으면
    histogram 은 skip (PR-10 머지 후 자동 활성).
    """
    started = time.perf_counter()
    with tracer.start_as_current_span(f"ingest.{stage}", attributes=attributes):
        try:
            yield
        finally:
            try:
                from src.api.routes.metrics import observe_ingest_stage
                observe_ingest_stage(stage, time.perf_counter() - started)
            except (ImportError, AttributeError):
                # PR-10 미머지 시: span 만 발생, metric 은 no-op
                pass


_initialized = False


def init_tracing(app: Any | None = None) -> bool:
    """Initialize OpenTelemetry SDK + auto-instrumentation if endpoint configured.

    Returns True when activated; False otherwise (no-op tracer remains).
    """
    global _initialized
    if _initialized:
        return True
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
    if not endpoint:
        logger.debug("OpenTelemetry: no OTLP endpoint configured — tracing disabled")
        return False

    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError as e:
        logger.warning("OpenTelemetry init skipped — opentelemetry not installed: %s", e)
        return False

    service_name = os.getenv("OTEL_SERVICE_NAME", "axiomedge-api")
    environment = os.getenv("APP_ENV", "development")
    resource = Resource.create({
        "service.name": service_name,
        "deployment.environment": environment,
    })
    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(endpoint=endpoint.rstrip("/") + "/v1/traces")
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    # Auto-instrument outgoing HTTP calls (TEI, Ollama, SageMaker via httpx)
    HTTPXClientInstrumentor().instrument()

    # PR-9 (H) — asyncpg auto-instrument (PG repo 모든 쿼리 span). env 토글.
    if os.getenv("OTEL_INSTRUMENT_ASYNCPG", "1").lower() in ("1", "true", "yes"):
        try:
            from opentelemetry.instrumentation.asyncpg import AsyncPGInstrumentor
            AsyncPGInstrumentor().instrument()
            logger.info("OTel: asyncpg instrumented")
        except ImportError:
            logger.debug(
                "OTel: opentelemetry-instrumentation-asyncpg not installed — skipped"
            )
        except (RuntimeError, ValueError, AttributeError) as e:
            logger.warning("OTel asyncpg instrument failed: %s", e)

    # PR-9 (H) — Neo4j: official instrumentor 미존재. driver 사용 시점에 manual
    # span 으로 보강 (별도 헬퍼). env 토글로 enable.
    # (Qdrant 도 official instrumentor 없음 — 기존 httpx 자동계측으로 cover.)

    # Auto-instrument FastAPI request lifecycle (when app provided)
    if app is not None:
        FastAPIInstrumentor.instrument_app(app, excluded_urls="/health,/ready,/metrics")

    _initialized = True
    logger.info(
        "OpenTelemetry initialized — service=%s env=%s endpoint=%s",
        service_name, environment, endpoint,
    )
    return True


def neo4j_query_enabled() -> bool:
    """Whether to wrap Neo4j queries in manual spans (PR-9 H, env-toggled)."""
    return os.getenv("OTEL_INSTRUMENT_NEO4J", "1").lower() in (
        "1", "true", "yes",
    )
