"""Tests for src/api/errors.py — error factories + exception handlers."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from src.api.errors import (
    ErrorResponse,
    api_error,
    bad_request,
    http_exception_handler,
    not_found,
    service_unavailable,
    unhandled_exception_handler,
)


# ---------------------------------------------------------------------------
# Error Response model
# ---------------------------------------------------------------------------


class TestErrorResponse:
    def test_defaults(self) -> None:
        resp = ErrorResponse(detail="test")
        assert resp.error_code == "UNKNOWN_ERROR"
        assert resp.status_code == 500

    def test_custom(self) -> None:
        resp = ErrorResponse(detail="bad", error_code="BAD", status_code=400)
        assert resp.detail == "bad"


# ---------------------------------------------------------------------------
# Error factory helpers
# ---------------------------------------------------------------------------


class TestErrorFactories:
    def test_api_error(self) -> None:
        exc = api_error(422, "validation failed", "VALIDATION")
        assert isinstance(exc, HTTPException)
        assert exc.status_code == 422
        assert exc.detail["error_code"] == "VALIDATION"

    def test_not_found_with_id(self) -> None:
        exc = not_found("KB", "kb-123")
        assert exc.status_code == 404
        assert "kb-123" in exc.detail["detail"]

    def test_not_found_without_id(self) -> None:
        exc = not_found("Document")
        assert exc.status_code == 404
        assert "Document not found" in exc.detail["detail"]

    def test_service_unavailable(self) -> None:
        exc = service_unavailable("Qdrant")
        assert exc.status_code == 503

    def test_bad_request(self) -> None:
        exc = bad_request("missing param")
        assert exc.status_code == 400


# ---------------------------------------------------------------------------
# Exception handlers
# ---------------------------------------------------------------------------


def _mock_request(method: str = "GET", path: str = "/test") -> MagicMock:
    req = MagicMock()
    req.method = method
    req.url = MagicMock()
    req.url.path = path
    return req


class TestHttpExceptionHandler:
    def test_structured_detail(self) -> None:
        exc = api_error(400, "bad", "BAD")
        resp = http_exception_handler(_mock_request(), exc)
        assert resp.status_code == 400
        import json
        body = json.loads(resp.body)
        assert body["error_code"] == "BAD"

    def test_plain_string_detail(self) -> None:
        exc = HTTPException(status_code=403, detail="forbidden")
        resp = http_exception_handler(_mock_request(), exc)
        assert resp.status_code == 403
        import json
        body = json.loads(resp.body)
        assert body["error_code"] == "HTTP_ERROR"
        assert body["detail"] == "forbidden"


class TestUnhandledExceptionHandler:
    @pytest.mark.parametrize(
        "exc_cls_path,expected_status,expected_code",
        [
            ("AuthenticationError", 401, "AUTH_ERROR"),
            ("ConfigurationError", 500, "CONFIG_ERROR"),
            ("StorageError", 503, "STORAGE_ERROR"),
            ("SearchError", 500, "SEARCH_ERROR"),
            ("PipelineError", 500, "PIPELINE_ERROR"),
            ("ConnectorError", 502, "CONNECTOR_ERROR"),
            ("KnowledgeBaseError", 500, "DOMAIN_ERROR"),
        ],
    )
    def test_domain_exception_mapping(
        self, exc_cls_path: str, expected_status: int, expected_code: str,
    ) -> None:
        import src.core.exceptions as exc_mod
        exc_cls = getattr(exc_mod, exc_cls_path)
        exc = exc_cls("test error")

        resp = unhandled_exception_handler(_mock_request(), exc)
        assert resp.status_code == expected_status
        import json
        body = json.loads(resp.body)
        assert body["error_code"] == expected_code

    def test_generic_exception(self) -> None:
        resp = unhandled_exception_handler(
            _mock_request(), RuntimeError("unexpected"),
        )
        assert resp.status_code == 500
        import json
        body = json.loads(resp.body)
        assert body["error_code"] == "INTERNAL_ERROR"
