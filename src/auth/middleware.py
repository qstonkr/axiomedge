"""Auth Middleware - Request-level authentication and activity logging.

Adds user context to request.state for downstream handlers.
Logs user activities for "나의 활동" dashboard.

**B-0 Day 2**: This middleware is now the SSOT for authentication enforcement.
Every request to a non-public path is verified up front; downstream
``Depends(get_current_user)`` reads the cached user from ``request.state``
instead of re-parsing headers. Routes therefore cannot accidentally skip
auth — opt-in via the public-path whitelist below.
"""

from __future__ import annotations

import logging
import time
from typing import Callable

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from src.auth.dependencies import AUTH_ENABLED, _ANONYMOUS_USER
from src.auth.providers import AuthenticationError

logger = logging.getLogger(__name__)

# Exact paths that skip auth entirely.
_PUBLIC_PATHS = frozenset({
    "/health",
    "/ready",
    "/metrics",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/favicon.ico",
    "/",
    "/api/v1/auth/login",
    "/api/v1/auth/refresh",
    "/api/v1/auth/register",
})

# Path prefixes that skip auth (Swagger assets, static, etc.).
_PUBLIC_PREFIXES: tuple[str, ...] = (
    "/docs",
    "/redoc",
    "/static/",
)


def _is_public(path: str) -> bool:
    """Return True if the path bypasses auth enforcement."""
    return path in _PUBLIC_PATHS or any(path.startswith(p) for p in _PUBLIC_PREFIXES)


class AuthMiddleware(BaseHTTPMiddleware):
    """Middleware that attaches user context and logs activities."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        path = request.url.path

        # Skip auth for public paths
        if _is_public(path):
            return await call_next(request)

        # Dev shortcut — anonymous admin user. Streamlit + tests rely on this.
        if not AUTH_ENABLED:
            request.state.auth_user = _ANONYMOUS_USER
            start = time.monotonic()
            response = await call_next(request)
            return response

        # Verify the token here so all downstream handlers can rely on
        # request.state.auth_user being populated. A failure short-circuits
        # the request — defense-in-depth for routes that forget Depends.
        try:
            user = await self._verify_request(request)
        except AuthenticationError as e:
            return JSONResponse({"detail": e.detail}, status_code=e.status_code)

        if user is None:
            return JSONResponse(
                {"detail": "Missing authentication token"}, status_code=401,
            )

        request.state.auth_user = user

        start = time.monotonic()
        response = await call_next(request)
        duration_ms = (time.monotonic() - start) * 1000

        # Activity logging for significant operations (async, non-blocking)
        if response.status_code < 400:
            await self._maybe_log_activity(request, path, duration_ms)

        return response

    async def _verify_request(self, request: Request):
        """Extract and verify the bearer token, returning the AuthUser or None.

        Falls back to None when no token is provided so the caller can return
        a unified 401 with the correct detail (separate from "invalid token").
        """
        token = self._extract_token(request)
        if not token:
            return None

        state = getattr(request.app.state, "_app_state", None)
        if not state:
            raise AuthenticationError(
                "Application state not initialized", status_code=503,
            )
        auth_provider = state.get("auth_provider")
        if not auth_provider:
            raise AuthenticationError(
                "Auth provider not initialized", status_code=503,
            )

        return await auth_provider.verify_token(token)

    @staticmethod
    def _extract_token(request: Request) -> str:
        """Pull a token from Authorization header, X-API-Key, or HttpOnly cookie."""
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            return auth_header[7:]
        if auth_header.startswith("ApiKey "):
            return auth_header[7:]
        api_key = request.headers.get("X-API-Key", "")
        if api_key:
            return api_key
        return request.cookies.get("access_token", "")

    async def _maybe_log_activity(self, request: Request, path: str, duration_ms: float) -> None:
        """Log significant user activities."""
        user = getattr(request.state, "auth_user", None)
        if not user or user.sub == "anonymous":
            return

        # Map paths to activity types
        activity = self._classify_activity(request.method, path)
        if not activity:
            return

        try:
            state = getattr(request.app.state, "_app_state", None)
            auth_service = state.get("auth_service") if state else None
            if auth_service:
                await auth_service.log_activity(
                    user_id=user.sub,
                    activity_type=activity["type"],
                    resource_type=activity["resource"],
                    resource_id=activity.get("resource_id"),
                    kb_id=activity.get("kb_id"),
                    details={"method": request.method, "path": path, "duration_ms": round(duration_ms, 1)},
                    ip_address=request.client.host if request.client else None,
                    user_agent=request.headers.get("user-agent", "")[:500],
                )
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.debug("Activity logging failed: %s", e)

    _ACTIVITY_RULES: list[tuple[str, str, str, str]] = [
        ("/search", "POST", "search", "search"),
        ("/knowledge/file-upload-ingest", "POST", "upload", "document"),
        ("/knowledge/ingest", "POST", "ingest", "pipeline"),
        ("/knowledge/ask", "POST", "query", "rag"),
        ("/feedback", "POST", "feedback", "feedback"),
    ]

    _PATH_GLOSSARY = "/glossary"
    _PATH_KB = "/kb"

    _RESOURCE_METHOD_MAP: list[tuple[str, str, dict]] = [
        (_PATH_GLOSSARY, "POST", {"type": "create", "resource": "glossary"}),
        (_PATH_GLOSSARY, "PUT", {"type": "edit", "resource": "glossary"}),
        (_PATH_GLOSSARY, "PATCH", {"type": "edit", "resource": "glossary"}),
        (_PATH_KB, "POST", {"type": "create", "resource": "kb"}),
        (_PATH_KB, "PUT", {"type": "edit", "resource": "kb"}),
        (_PATH_KB, "PATCH", {"type": "edit", "resource": "kb"}),
    ]

    def _classify_activity(self, method: str, path: str) -> dict | None:
        """Classify request into activity type."""
        for path_pattern, req_method, act_type, resource in self._ACTIVITY_RULES:
            if path_pattern in path and method == req_method:
                return {"type": act_type, "resource": resource}
        for path_pattern, req_method, result in self._RESOURCE_METHOD_MAP:
            if path_pattern in path and method == req_method:
                return result
        return None
