"""Unit tests for dashboard/services/api/_core.py — HTTP client helpers."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

# Make dashboard modules importable
_DASHBOARD_DIR = str(Path(__file__).resolve().parents[2] / "dashboard")
if _DASHBOARD_DIR not in sys.path:
    sys.path.insert(0, _DASHBOARD_DIR)

# Patch streamlit before importing the module
_st_mock = MagicMock()
_st_mock.cache_data = lambda **kw: lambda f: f
sys.modules.setdefault("streamlit", _st_mock)

from services.api._core import (
    PUBLISH_EXECUTE_TIMEOUT_SECONDS,
    _client,
    _delete,
    _get,
    _patch,
    _post,
    _put,
    _request,
    api_failed,
)
import services.config as cfg


# ===========================================================================
# api_failed
# ===========================================================================

class TestApiFailed:
    def test_failed_when_flag_true(self):
        assert api_failed({"error": "oops", "_api_failed": True}) is True

    def test_not_failed_when_flag_false(self):
        assert api_failed({"data": "ok", "_api_failed": False}) is False

    def test_not_failed_when_no_flag(self):
        assert api_failed({"data": "ok"}) is False

    def test_not_failed_for_none(self):
        assert api_failed(None) is False

    def test_not_failed_for_list(self):
        assert api_failed([1, 2, 3]) is False

    def test_not_failed_for_empty_dict(self):
        assert api_failed({}) is False


# ===========================================================================
# _client
# ===========================================================================

class TestClient:
    def test_returns_httpx_client(self):
        c = _client()
        assert isinstance(c, httpx.Client)
        c.close()

    def test_uses_default_timeout(self):
        c = _client()
        assert c._transport is not None  # client is configured
        c.close()

    def test_custom_timeout(self):
        c = _client(timeout=30)
        c.close()

    def test_base_url_set(self):
        c = _client()
        assert str(c.base_url).rstrip("/") == cfg.DASHBOARD_API_URL.rstrip("/")
        c.close()

    def test_content_type_header(self):
        c = _client()
        assert c.headers.get("content-type") == "application/json"
        c.close()


# ===========================================================================
# _request
# ===========================================================================

class TestRequest:
    def _mock_response(self, status_code=200, json_data=None):
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = status_code
        resp.json.return_value = json_data or {}
        resp.raise_for_status = MagicMock()
        if status_code >= 400:
            resp.raise_for_status.side_effect = httpx.HTTPStatusError(
                message=f"HTTP {status_code}",
                request=MagicMock(),
                response=resp,
            )
        return resp

    @patch("services.api._core._client")
    def test_successful_dict_response(self, mock_client_fn):
        resp = self._mock_response(200, {"result": "ok"})
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=ctx)
        ctx.__exit__ = MagicMock(return_value=False)
        ctx.request.return_value = resp
        mock_client_fn.return_value = ctx

        result = _request("GET", "/test")
        assert result == {"result": "ok"}

    @patch("services.api._core._client")
    def test_list_response_wrapped_in_items(self, mock_client_fn):
        resp = self._mock_response(200, [1, 2, 3])
        resp.json.return_value = [1, 2, 3]
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=ctx)
        ctx.__exit__ = MagicMock(return_value=False)
        ctx.request.return_value = resp
        mock_client_fn.return_value = ctx

        result = _request("GET", "/test")
        assert result == {"items": [1, 2, 3]}

    @patch("services.api._core._client")
    def test_http_error_returns_api_failed(self, mock_client_fn):
        resp = self._mock_response(500)
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=ctx)
        ctx.__exit__ = MagicMock(return_value=False)
        ctx.request.return_value = resp
        mock_client_fn.return_value = ctx

        result = _request("GET", "/fail")
        assert api_failed(result) is True

    @patch("services.api._core._client")
    def test_timeout_returns_api_failed(self, mock_client_fn):
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=ctx)
        ctx.__exit__ = MagicMock(return_value=False)
        ctx.request.side_effect = httpx.TimeoutException("timed out")
        mock_client_fn.return_value = ctx

        result = _request("GET", "/slow")
        assert api_failed(result) is True
        assert "Timeout" in result["error"]

    @patch("services.api._core._client")
    def test_connection_error_returns_api_failed(self, mock_client_fn):
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=ctx)
        ctx.__exit__ = MagicMock(return_value=False)
        ctx.request.side_effect = httpx.ConnectError("refused")
        mock_client_fn.return_value = ctx

        result = _request("GET", "/unreachable")
        assert api_failed(result) is True

    @patch("services.api._core._client")
    def test_non_json_response_returns_api_failed(self, mock_client_fn):
        resp = self._mock_response(200)
        resp.json.side_effect = ValueError("not json")
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=ctx)
        ctx.__exit__ = MagicMock(return_value=False)
        ctx.request.return_value = resp
        mock_client_fn.return_value = ctx

        result = _request("GET", "/html")
        assert api_failed(result) is True
        assert "Non-JSON" in result["error"]


# ===========================================================================
# _get, _post, _put, _patch helpers
# ===========================================================================

class TestHTTPHelpers:
    @patch("services.api._core._request")
    def test_get_filters_none_params(self, mock_req):
        mock_req.return_value = {"data": 1}
        _get("/test", a=1, b=None, c="x")
        call_kwargs = mock_req.call_args
        assert call_kwargs[1]["params"] == {"a": 1, "c": "x"}

    @patch("services.api._core._request")
    def test_get_removes_use_agents(self, mock_req):
        mock_req.return_value = {}
        _get("/test", use_agents=True, q="hello")
        assert "use_agents" not in mock_req.call_args[1]["params"]

    @patch("services.api._core._request")
    def test_post_sends_body(self, mock_req):
        mock_req.return_value = {"ok": True}
        _post("/test", {"key": "val"})
        mock_req.assert_called_once_with("POST", "/test", json_body={"key": "val"}, timeout=None)

    @patch("services.api._core._request")
    def test_post_empty_body_defaults_to_empty_dict(self, mock_req):
        mock_req.return_value = {}
        _post("/test")
        mock_req.assert_called_once_with("POST", "/test", json_body={}, timeout=None)

    @patch("services.api._core._request")
    def test_put_sends_body(self, mock_req):
        mock_req.return_value = {}
        _put("/test", {"name": "new"})
        mock_req.assert_called_once_with("PUT", "/test", json_body={"name": "new"})

    @patch("services.api._core._request")
    def test_patch_sends_body(self, mock_req):
        mock_req.return_value = {}
        _patch("/test", {"field": "value"})
        mock_req.assert_called_once_with("PATCH", "/test", json_body={"field": "value"})


# ===========================================================================
# _delete
# ===========================================================================

class TestDelete:
    @patch("services.api._core._client")
    def test_204_returns_success(self, mock_client_fn):
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 204
        resp.raise_for_status = MagicMock()
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=ctx)
        ctx.__exit__ = MagicMock(return_value=False)
        ctx.request.return_value = resp
        mock_client_fn.return_value = ctx

        result = _delete("/test/123")
        assert result == {"success": True}

    @patch("services.api._core._client")
    def test_200_with_json_returns_data(self, mock_client_fn):
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"deleted": True}
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=ctx)
        ctx.__exit__ = MagicMock(return_value=False)
        ctx.request.return_value = resp
        mock_client_fn.return_value = ctx

        result = _delete("/test/456")
        assert result == {"deleted": True}

    @patch("services.api._core._client")
    def test_http_error_returns_api_failed(self, mock_client_fn):
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 404
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            message="Not Found", request=MagicMock(), response=resp,
        )
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=ctx)
        ctx.__exit__ = MagicMock(return_value=False)
        ctx.request.return_value = resp
        mock_client_fn.return_value = ctx

        result = _delete("/test/missing")
        assert api_failed(result) is True


# ===========================================================================
# Constants
# ===========================================================================

class TestConstants:
    def test_publish_timeout(self):
        assert PUBLISH_EXECUTE_TIMEOUT_SECONDS == 180
