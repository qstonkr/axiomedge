"""OpenTelemetry tracing — env-driven, no-op when OTLP endpoint absent.

Auto-instruments FastAPI + httpx. Manual spans for RAG pipeline stages.

Env vars:
  - OTEL_EXPORTER_OTLP_ENDPOINT: e.g., http://jaeger:4318 (no-op if absent)
  - OTEL_SERVICE_NAME: default "axiomedge-api"
  - OTEL_RESOURCE_ATTRIBUTES: comma-separated key=value (env, version)
  - OTEL_TRACES_SAMPLER: "always_on" | "always_off" | "traceidratio" (default "parentbased_always_on")
  - OTEL_TRACES_SAMPLER_ARG: ratio when sampler=traceidratio (e.g., "0.1")

Usage in code:
    from src.core.observability.tracing import tracer

    with tracer.start_as_current_span("rag.embed", attributes={"kb_id": kb_id}):
        ...

Tracer is always usable; when OTel disabled, span is no-op (zero overhead).
"""

from __future__ import annotations

import logging
import os
from typing import Any

from opentelemetry import trace

logger = logging.getLogger(__name__)


# Module-level tracer — usable from anywhere. Becomes no-op when SDK not initialized.
tracer = trace.get_tracer("axiomedge")


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

    # Auto-instrument FastAPI request lifecycle (when app provided)
    if app is not None:
        FastAPIInstrumentor.instrument_app(app, excluded_urls="/health,/ready,/metrics")

    _initialized = True
    logger.info(
        "OpenTelemetry initialized — service=%s env=%s endpoint=%s",
        service_name, environment, endpoint,
    )
    return True
