"""Core HTTP helpers for the Knowledge API client.

Provides _client, _request, _get, _post, _put, _patch, _delete and api_failed.
"""

from __future__ import annotations

import time
from typing import Any

import httpx
import streamlit as st

from services import config as cfg
from services.logging_config import get_logger
from services.validators import sanitize_input, validate_page_params

logger = get_logger(__name__)

PUBLISH_EXECUTE_TIMEOUT_SECONDS = 180

__all__ = [
    "PUBLISH_EXECUTE_TIMEOUT_SECONDS",
    "api_failed",
    "sanitize_input",
    "validate_page_params",
    # private helpers re-exported for sibling modules
    "_client",
    "_request",
    "_get",
    "_post",
    "_put",
    "_patch",
    "_delete",
    # B1 — public method aliases for direct ``api_client.get/post/...`` usage
    "get",
    "post",
    "put",
    "patch",
    "delete",
    "cfg",
    "logger",
    "st",
    "time",
    "httpx",
]


# ---------------------------------------------------------------------------
# HTTP helpers (simplified for local)
# ---------------------------------------------------------------------------

def _resolve_auth_token() -> str:
    """B4 — admin token resolution.

    Order:
      1. ``DASHBOARD_API_TOKEN`` env var (static — preferred for production)
      2. Streamlit ``st.session_state.api_token`` (set by login flow)
      3. empty (auth-less dev mode — AUTH_ENABLED=false 필요)
    """
    static = getattr(cfg, "DASHBOARD_API_TOKEN", "") or ""
    if static:
        return static
    try:
        token = st.session_state.get("api_token", "") if hasattr(
            st, "session_state",
        ) else ""
        return str(token or "")
    except (AttributeError, KeyError, RuntimeError):
        return ""


def _client(*, timeout: int | None = None) -> httpx.Client:
    """Synchronous httpx client for local FastAPI server.

    B4 — Authorization 헤더 자동 첨부. AUTH_ENABLED=true 환경에서 admin
    endpoint 가 401 반환하지 않도록 함.
    """
    headers = {"Content-Type": "application/json"}
    token = _resolve_auth_token()
    if token:
        # 두 형태 모두 호환 (Bearer 또는 raw — AuthMiddleware 의 provider 가 결정)
        headers["Authorization"] = (
            token if token.lower().startswith("bearer ") else f"Bearer {token}"
        )
    return httpx.Client(
        base_url=cfg.DASHBOARD_API_URL,
        headers=headers,
        timeout=timeout or cfg.API_TIMEOUT,
    )


def _request(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
    timeout: int | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Core request helper - simple single-attempt for local."""
    t0 = time.monotonic()
    try:
        with _client(timeout=timeout) as client:
            resp = client.request(method, path, params=params, json=json_body)
            duration_ms = round((time.monotonic() - t0) * 1000, 1)
            resp.raise_for_status()
            logger.info("API %s %s -> %s (%.0fms)", method, path, resp.status_code, duration_ms)
            try:
                data = resp.json()
                if isinstance(data, list):
                    return {"items": data}
                return data
            except ValueError:
                return {"error": f"Non-JSON response ({resp.status_code})", "_api_failed": True}
    except httpx.HTTPStatusError as exc:
        duration_ms = round((time.monotonic() - t0) * 1000, 1)
        logger.warning("API %s %s -> %s (%.0fms)", method, path, exc.response.status_code, duration_ms)
        return {"error": str(exc), "_api_failed": True}
    except httpx.TimeoutException as exc:
        logger.warning("API %s %s -> timeout", method, path)
        return {"error": f"Timeout: {exc}", "_api_failed": True}
    except httpx.RequestError as exc:
        logger.warning("API %s %s -> connection error: %s", method, path, exc)
        return {"error": str(exc), "_api_failed": True}


def _get(path: str, **params: Any) -> dict[str, Any]:
    clean = {k: v for k, v in params.items() if v is not None and k != "use_agents"}
    return _request("GET", path, params=clean)


def _post(path: str, body: dict[str, Any] | None = None, *, _retries: int | None = None,
          timeout: int | None = None, **_kwargs: Any) -> dict[str, Any]:
    return _request("POST", path, json_body=body if body is not None else {}, timeout=timeout)


def _put(path: str, body: dict[str, Any] | None = None, **_kwargs: Any) -> dict[str, Any]:
    return _request("PUT", path, json_body=body if body is not None else {})


def _patch(path: str, body: dict[str, Any] | None = None, **_kwargs: Any) -> dict[str, Any]:
    return _request("PATCH", path, json_body=body if body is not None else {})


# ---------------------------------------------------------------------------
# B1 + B2 — Public REST verb helpers.
#
# 신규 admin pages (P0-W1) 가 ``api_client.get(path, params={...})`` 형태로
# 호출하므로 dict-style params 와 cache_key kwarg (Streamlit 캐시 후크용,
# 본 helper 에선 무시) 를 정상 처리한다. list 응답은 그대로 list 로 반환
# (``_request`` 의 ``{"items": data}`` wrap 을 unwrap).
# ---------------------------------------------------------------------------


def get(
    path: str,
    *,
    params: dict[str, Any] | None = None,
    cache_key: str | None = None,  # noqa: ARG001 — page hint, ignored here
    timeout: int | None = None,
    **extra_params: Any,
) -> Any:
    """Public GET helper. Returns list when API yields array, else dict."""
    merged: dict[str, Any] = {}
    if params:
        merged.update(params)
    merged.update(
        {k: v for k, v in extra_params.items()
         if v is not None and k != "use_agents"}
    )
    clean = {k: v for k, v in merged.items() if v is not None}
    result = _request("GET", path, params=clean, timeout=timeout)
    # ``_request`` 가 list 를 ``{"items": [...]}`` 로 wrap 한다 — original list
    # 모양을 페이지에서 기대하면 unwrap.
    if (
        isinstance(result, dict) and len(result) == 1
        and isinstance(result.get("items"), list)
    ):
        return result["items"]
    return result


def post(
    path: str,
    *,
    json: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
    timeout: int | None = None,
    **_kwargs: Any,
) -> Any:
    """Public POST helper. ``json=`` 또는 ``body=`` 둘 다 허용."""
    payload = json if json is not None else body
    return _post(path, payload, timeout=timeout)


def put(
    path: str,
    *,
    json: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
    **_kwargs: Any,
) -> Any:
    payload = json if json is not None else body
    return _put(path, payload)


def patch(
    path: str,
    *,
    json: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
    **_kwargs: Any,
) -> Any:
    payload = json if json is not None else body
    return _patch(path, payload)


def delete(path: str, **_kwargs: Any) -> Any:
    return _delete(path)


def _delete(path: str, **_kwargs: Any) -> dict[str, Any]:
    """DELETE request. 204 No Content treated as success."""
    t0 = time.monotonic()
    try:
        with _client() as client:
            resp = client.request("DELETE", path)
            duration_ms = round((time.monotonic() - t0) * 1000, 1)
            resp.raise_for_status()
            logger.info("API DELETE %s -> %s (%.0fms)", path, resp.status_code, duration_ms)
            if resp.status_code == 204:
                return {"success": True}
            try:
                data = resp.json()
                if isinstance(data, list):
                    return {"items": data}
                return data
            except ValueError:
                return {"success": True}
    except httpx.HTTPStatusError as exc:
        return {"error": str(exc), "_api_failed": True}
    except httpx.RequestError as exc:
        return {"error": str(exc), "_api_failed": True}


# ---------------------------------------------------------------------------
# Streamlit cache helpers
# ---------------------------------------------------------------------------

def api_failed(result: dict | list | None) -> bool:
    """Check if an API call failed."""
    if not isinstance(result, dict):
        return False
    return bool(result.get("_api_failed"))
