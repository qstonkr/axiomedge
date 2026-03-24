"""Structured JSON logging for Knowledge Dashboard Local.

Simplified from oreo-ecosystem version.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any

import streamlit as st


class _JsonFormatter(logging.Formatter):
    """Emit each log record as a single JSON line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "message": record.getMessage(),
            "module": record.module,
            "logger": record.name,
        }

        trace_id = getattr(record, "trace_id", "")
        if trace_id:
            payload["trace_id"] = trace_id

        for key in (
            "method", "url", "status", "duration_ms", "attempt",
            "error", "error_type", "response_body", "kb_id",
            "user_id", "user_email",
        ):
            val = getattr(record, key, None)
            if val is not None:
                payload[key] = val

        if record.exc_info and record.exc_info[1]:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, ensure_ascii=False, default=str)


_INITIALISED = False


def init_logging() -> None:
    """Initialise root logger with JSON formatter. Safe to call multiple times."""
    global _INITIALISED  # noqa: PLW0603
    if _INITIALISED:
        return
    _INITIALISED = True

    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    handler = logging.StreamHandler()
    handler.setFormatter(_JsonFormatter())

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    """Return a named logger. Ensures init_logging() has been called."""
    init_logging()
    return logging.getLogger(name)


def get_trace_id() -> str:
    """Generate a trace ID (simplified for local use)."""
    try:
        headers = st.context.headers
        for header_name in ("X-Trace-Id", "X-Request-Id"):
            value = headers.get(header_name)
            if value:
                return value
    except Exception:
        pass
    return uuid.uuid4().hex[:16]
