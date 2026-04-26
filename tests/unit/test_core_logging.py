"""src.core.logging — JsonFormatter, trace_id ContextVar, configure_logging.

PR-7 (G).
"""

from __future__ import annotations

import asyncio
import json
import logging

import pytest

from src.core.logging import (
    JsonFormatter,
    TextFormatter,
    configure_logging,
    get_trace_id,
    set_trace_id,
    trace_id_var,
)


def _make_record(name: str = "x", level: int = logging.INFO,
                 msg: str = "hello") -> logging.LogRecord:
    return logging.LogRecord(
        name=name, level=level, pathname=__file__, lineno=10,
        msg=msg, args=None, exc_info=None,
    )


class TestJsonFormatter:
    def test_emits_json_with_required_fields(self):
        f = JsonFormatter(service="svc-a")
        out = f.format(_make_record(msg="hi"))
        data = json.loads(out)
        assert data["level"] == "INFO"
        assert data["service"] == "svc-a"
        assert data["message"] == "hi"
        assert "timestamp" in data
        assert "module" in data and "function" in data
        # 기본 ContextVar 비어있음 → trace_id 키 없음
        assert "trace_id" not in data

    def test_includes_trace_id_when_set(self):
        f = JsonFormatter()
        token = set_trace_id("abc12345")
        try:
            data = json.loads(f.format(_make_record()))
            assert data["trace_id"] == "abc12345"
        finally:
            trace_id_var.reset(token)

    def test_includes_exception_field(self):
        try:
            raise ValueError("oops")
        except ValueError:
            import sys
            record = logging.LogRecord(
                name="x", level=logging.ERROR, pathname=__file__,
                lineno=1, msg="m", args=None, exc_info=sys.exc_info(),
            )
        out = JsonFormatter().format(record)
        data = json.loads(out)
        assert "exception" in data
        assert "ValueError" in data["exception"]


class TestTextFormatter:
    def test_appends_trace_id_in_brackets(self):
        f = TextFormatter(service="svc")
        token = set_trace_id("tid-9")
        try:
            out = f.format(_make_record(msg="x"))
            assert "trace_id=tid-9" in out
        finally:
            trace_id_var.reset(token)


class TestConfigureLogging:
    def test_replaces_root_handlers(self, monkeypatch):
        monkeypatch.setenv("LOG_FORMAT", "json")
        configure_logging(service="t")
        root = logging.getLogger()
        assert len(root.handlers) == 1
        # idempotent
        configure_logging(service="t")
        assert len(root.handlers) == 1

    def test_text_format_via_env(self, monkeypatch):
        monkeypatch.setenv("LOG_FORMAT", "text")
        configure_logging(service="t2")
        root = logging.getLogger()
        formatter = root.handlers[0].formatter
        assert isinstance(formatter, TextFormatter)


class TestContextVarIsolation:
    @pytest.mark.asyncio
    async def test_each_task_has_own_trace_id(self):
        captured: list[str] = []

        async def worker(label: str) -> None:
            token = set_trace_id(label)
            try:
                await asyncio.sleep(0)
                captured.append(get_trace_id())
            finally:
                trace_id_var.reset(token)

        await asyncio.gather(worker("A"), worker("B"), worker("C"))
        assert sorted(captured) == ["A", "B", "C"]
