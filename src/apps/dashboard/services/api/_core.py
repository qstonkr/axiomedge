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
    "cfg",
    "logger",
    "st",
    "time",
    "httpx",
]


# ---------------------------------------------------------------------------
# HTTP helpers (simplified for local)
# ---------------------------------------------------------------------------

def _client(*, timeout: int | None = None) -> httpx.Client:
    """Synchronous httpx client for local FastAPI server."""
    return httpx.Client(
        base_url=cfg.DASHBOARD_API_URL,
        headers={"Content-Type": "application/json"},
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
