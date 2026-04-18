"""Tests for OpenTelemetry init guard (env-driven, no-op when endpoint absent)."""

from __future__ import annotations

import os
from unittest.mock import patch

from src.core.observability import tracing as tracing_mod


def test_no_endpoint_returns_false() -> None:
    tracing_mod._initialized = False
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("OTEL_EXPORTER_OTLP_ENDPOINT", None)
        assert tracing_mod.init_tracing(app=None) is False


def test_empty_endpoint_returns_false() -> None:
    tracing_mod._initialized = False
    with patch.dict(os.environ, {"OTEL_EXPORTER_OTLP_ENDPOINT": ""}, clear=False):
        assert tracing_mod.init_tracing(app=None) is False


def test_idempotent_when_already_initialized() -> None:
    tracing_mod._initialized = True
    assert tracing_mod.init_tracing(app=None) is True


def test_tracer_is_always_usable_when_disabled() -> None:
    """No-op tracer must support context manager pattern even when SDK disabled."""
    tracing_mod._initialized = False
    # No init — tracer falls back to NoOpTracer
    with tracing_mod.tracer.start_as_current_span("test.span") as span:
        assert span is not None  # NoOpSpan also has interface
