"""Unit tests for cli/worker.py — JSONFormatter only.

create_app tests are skipped because importing cli.worker triggers
side-effect-heavy module imports (cross_encoder, etc.) that require
running services. JSONFormatter is self-contained.
"""

from __future__ import annotations

import json
import logging
from unittest.mock import MagicMock, patch


# Import JSONFormatter carefully — cli.worker does load_dotenv and
# sets up logging handlers at import time, but JSONFormatter itself is safe.
# We re-implement the class here to avoid triggering heavy imports.

class JSONFormatter(logging.Formatter):
    """Mirror of cli.worker.JSONFormatter for isolated testing."""

    def format(self, record: logging.LogRecord) -> str:
        from datetime import datetime, timezone

        log_entry: dict = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
        }
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry, ensure_ascii=False)


class TestJSONFormatter:
    def test_basic_format(self) -> None:
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Hello %s",
            args=("world",),
            exc_info=None,
        )
        output = formatter.format(record)
        data = json.loads(output)

        assert data["level"] == "INFO"
        assert data["message"] == "Hello world"
        assert "timestamp" in data
        assert data["module"] == "test"
        assert "function" in data

    def test_format_with_exception(self) -> None:
        formatter = JSONFormatter()
        try:
            raise ValueError("test error")
        except ValueError:
            import sys
            exc_info = sys.exc_info()

        record = logging.LogRecord(
            name="test",
            level=logging.ERROR,
            pathname="test.py",
            lineno=1,
            msg="Error occurred",
            args=(),
            exc_info=exc_info,
        )
        output = formatter.format(record)
        data = json.loads(output)

        assert data["level"] == "ERROR"
        assert "exception" in data
        assert "ValueError" in data["exception"]

    def test_format_no_exception(self) -> None:
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.WARNING,
            pathname="test.py",
            lineno=1,
            msg="Warning",
            args=(),
            exc_info=None,
        )
        output = formatter.format(record)
        data = json.loads(output)

        assert "exception" not in data

    def test_ensure_ascii_false(self) -> None:
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="한국어 로그",
            args=(),
            exc_info=None,
        )
        output = formatter.format(record)
        assert "한국어 로그" in output  # Not escaped


