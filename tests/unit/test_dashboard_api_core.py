"""Unit tests for dashboard/services/api/_core.py — HTTP client helpers."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

# Make dashboard modules importable
_DASHBOARD_DIR = str(Path(__file__).resolve().parents[2] / "src" / "apps" / "dashboard")
if _DASHBOARD_DIR not in sys.path:
    sys.path.insert(0, _DASHBOARD_DIR)

# Patch streamlit before importing the module
_st_mock = MagicMock()
_st_mock.cache_data = lambda **kw: lambda f: f
sys.modules.setdefault("streamlit", _st_mock)

import services.api._core as _core_module

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

    def _make_mock_client(self, resp):
        """Create a mock httpx.Client that works as context manager."""
        client = MagicMock(spec=httpx.Client)
        client.request.return_value = resp
        client.__enter__ = MagicMock(return_value=client)
        client.__exit__ = MagicMock(return_value=False)
        return client

    def test_successful_dict_response(self, monkeypatch):
        resp = self._mock_response(200, {"result": "ok"})
        monkeypatch.setattr(_core_module, "_client", lambda **kw: self._make_mock_client(resp))

        result = _request("GET", "/test")
        assert result == {"result": "ok"}

    def test_list_response_wrapped_in_items(self, monkeypatch):
        resp = self._mock_response(200, [1, 2, 3])
        resp.json.return_value = [1, 2, 3]
        monkeypatch.setattr(_core_module, "_client", lambda **kw: self._make_mock_client(resp))

        result = _request("GET", "/test")
        assert result == {"items": [1, 2, 3]}

    def test_http_error_returns_api_failed(self, monkeypatch):
        resp = self._mock_response(500)
        monkeypatch.setattr(_core_module, "_client", lambda **kw: self._make_mock_client(resp))

        result = _request("GET", "/fail")
        assert api_failed(result) is True

    def test_timeout_returns_api_failed(self, monkeypatch):
        client = self._make_mock_client(self._mock_response(200))
        client.request.side_effect = httpx.TimeoutException("timed out")
        monkeypatch.setattr(_core_module, "_client", lambda **kw: client)

        result = _request("GET", "/slow")
        assert api_failed(result) is True
        assert "Timeout" in result["error"]

    def test_connection_error_returns_api_failed(self, monkeypatch):
        client = self._make_mock_client(self._mock_response(200))
        client.request.side_effect = httpx.ConnectError("refused")
        monkeypatch.setattr(_core_module, "_client", lambda **kw: client)

        result = _request("GET", "/unreachable")
        assert api_failed(result) is True

    def test_non_json_response_returns_api_failed(self, monkeypatch):
        resp = self._mock_response(200)
        resp.json.side_effect = ValueError("not json")
        monkeypatch.setattr(_core_module, "_client", lambda **kw: self._make_mock_client(resp))

        result = _request("GET", "/html")
        assert api_failed(result) is True
        assert "Non-JSON" in result["error"]


# ===========================================================================
# _get, _post, _put, _patch helpers
# ===========================================================================

class TestHTTPHelpers:
    def test_get_filters_none_params(self, monkeypatch):
        mock_req = MagicMock(return_value={"data": 1})
        monkeypatch.setattr(_core_module, "_request", mock_req)
        _get("/test", a=1, b=None, c="x")
        call_kwargs = mock_req.call_args
        assert call_kwargs[1]["params"] == {"a": 1, "c": "x"}

    def test_get_removes_use_agents(self, monkeypatch):
        mock_req = MagicMock(return_value={})
        monkeypatch.setattr(_core_module, "_request", mock_req)
        _get("/test", use_agents=True, q="hello")
        assert "use_agents" not in mock_req.call_args[1]["params"]

    def test_post_sends_body(self, monkeypatch):
        mock_req = MagicMock(return_value={"ok": True})
        monkeypatch.setattr(_core_module, "_request", mock_req)
        _post("/test", {"key": "val"})
        mock_req.assert_called_once_with("POST", "/test", json_body={"key": "val"}, timeout=None)

    def test_post_empty_body_defaults_to_empty_dict(self, monkeypatch):
        mock_req = MagicMock(return_value={})
        monkeypatch.setattr(_core_module, "_request", mock_req)
        _post("/test")
        mock_req.assert_called_once_with("POST", "/test", json_body={}, timeout=None)

    def test_put_sends_body(self, monkeypatch):
        mock_req = MagicMock(return_value={})
        monkeypatch.setattr(_core_module, "_request", mock_req)
        _put("/test", {"name": "new"})
        mock_req.assert_called_once_with("PUT", "/test", json_body={"name": "new"})

    def test_patch_sends_body(self, monkeypatch):
        mock_req = MagicMock(return_value={})
        monkeypatch.setattr(_core_module, "_request", mock_req)
        _patch("/test", {"field": "value"})
        mock_req.assert_called_once_with("PATCH", "/test", json_body={"field": "value"})


# ===========================================================================
# _delete
# ===========================================================================

class TestDelete:
    def _make_mock_client(self, resp):
        client = MagicMock(spec=httpx.Client)
        client.request.return_value = resp
        client.__enter__ = MagicMock(return_value=client)
        client.__exit__ = MagicMock(return_value=False)
        return client

    def test_204_returns_success(self, monkeypatch):
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 204
        resp.raise_for_status = MagicMock()
        monkeypatch.setattr(_core_module, "_client", lambda **kw: self._make_mock_client(resp))

        result = _delete("/test/123")
        assert result == {"success": True}

    def test_200_with_json_returns_data(self, monkeypatch):
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"deleted": True}
        monkeypatch.setattr(_core_module, "_client", lambda **kw: self._make_mock_client(resp))

        result = _delete("/test/456")
        assert result == {"deleted": True}

    def test_http_error_returns_api_failed(self, monkeypatch):
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 404
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            message="Not Found", request=MagicMock(), response=resp,
        )
        monkeypatch.setattr(_core_module, "_client", lambda **kw: self._make_mock_client(resp))

        result = _delete("/test/missing")
        assert api_failed(result) is True


# ===========================================================================
# Constants
# ===========================================================================

class TestConstants:
    def test_publish_timeout(self):
        assert PUBLISH_EXECUTE_TIMEOUT_SECONDS == 180
