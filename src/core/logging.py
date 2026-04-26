"""Unified JSON logging + trace_id propagation — PR-7 (G).

모든 entrypoint(API, dashboard, CLI, edge, worker, init scripts) 가 동일한
formatter 와 ``trace_id`` ContextVar 를 공유하도록 단일화한다.

Usage:
    from src.core.logging import configure_logging, set_trace_id, get_trace_id
    configure_logging(service="axiomedge-api")
    token = set_trace_id("abc123")  # 또는 RequestIDMiddleware 가 자동 호출
    ...
    trace_id_var.reset(token)

Env:
    LOG_LEVEL  — DEBUG/INFO/WARNING/ERROR (default INFO)
    LOG_FORMAT — json | text (default json; local dev 시 text 권장)
"""

from __future__ import annotations

import json
import logging
import os
from contextvars import ContextVar, Token
from datetime import datetime, timezone

# 외부에 노출하는 ContextVar — 미들웨어/CLI 진입점이 set_trace_id() 로 갱신.
trace_id_var: ContextVar[str] = ContextVar("trace_id", default="")


def get_trace_id() -> str:
    """현재 ContextVar 의 trace_id (없으면 empty string)."""
    return trace_id_var.get()


def set_trace_id(value: str) -> Token[str]:
    """trace_id 를 ContextVar 에 설정. reset 용 token 반환."""
    return trace_id_var.set(value)


class JsonFormatter(logging.Formatter):
    """Single-line JSON log formatter with trace_id, service, location.

    필드: timestamp(ISO8601 UTC), level, service, message, module, function,
    line, trace_id (optional), exception (optional).
    """

    def __init__(self, *, service: str = "axiomedge") -> None:
        super().__init__()
        self._service = service

    def format(self, record: logging.LogRecord) -> str:  # noqa: A003
        entry: dict[str, object] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "service": self._service,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        tid = get_trace_id()
        if tid:
            entry["trace_id"] = tid
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    """사람이 읽기 쉬운 text formatter (LOG_FORMAT=text 옵션)."""

    def __init__(self, *, service: str = "axiomedge") -> None:
        fmt = (
            "%(asctime)s [%(levelname)s] " + service +
            " %(name)s:%(funcName)s:%(lineno)d - %(message)s"
        )
        super().__init__(fmt=fmt, datefmt="%Y-%m-%dT%H:%M:%S%z")

    def format(self, record: logging.LogRecord) -> str:  # noqa: A003
        base = super().format(record)
        tid = get_trace_id()
        if tid:
            return f"{base} [trace_id={tid}]"
        return base


_CONFIGURED: bool = False


def configure_logging(
    *,
    service: str = "axiomedge",
    level: str | None = None,
    json_output: bool | None = None,
) -> None:
    """Root logger handler 를 단일 JSON/text 핸들러로 교체. Idempotent.

    Args:
        service: 서비스명 (api / cli-ingest / edge-server 등). 모든 라인에 포함.
        level: 미설정 시 ENV LOG_LEVEL 또는 INFO.
        json_output: 미설정 시 ENV LOG_FORMAT 기준 (default json=True).
    """
    global _CONFIGURED  # noqa: PLW0603

    if level is None:
        level = os.getenv("LOG_LEVEL", "INFO").upper()

    if json_output is None:
        fmt = os.getenv("LOG_FORMAT", "json").lower()
        json_output = fmt == "json"

    formatter: logging.Formatter
    if json_output:
        formatter = JsonFormatter(service=service)
    else:
        formatter = TextFormatter(service=service)

    handler = logging.StreamHandler()
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers = [handler]
    try:
        root.setLevel(level)
    except (ValueError, TypeError):
        root.setLevel(logging.INFO)

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Convenience helper — module-level loggers."""
    return logging.getLogger(name)
