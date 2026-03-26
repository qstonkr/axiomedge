"""Auth Middleware - Request-level authentication and activity logging.

Adds user context to request.state for downstream handlers.
Logs user activities for "나의 활동" dashboard.
"""

from __future__ import annotations

import logging
import time
from typing import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from src.auth.dependencies import AUTH_ENABLED, _ANONYMOUS_USER

logger = logging.getLogger(__name__)

# Paths that skip auth entirely
_PUBLIC_PATHS = frozenset({
    "/health",
    "/ready",
    "/metrics",
    "/docs",
    "/redoc",
    "/openapi.json",
})


class AuthMiddleware(BaseHTTPMiddleware):
    """Middleware that attaches user context and logs activities."""

    def __init__(self, app: ASGIApp):
        super().__init__(app)

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        path = request.url.path

        # Skip auth for public paths
        if path in _PUBLIC_PATHS or path.startswith("/docs") or path.startswith("/redoc"):
            return await call_next(request)

        # Attach user to request.state (lightweight, no DB call)
        if AUTH_ENABLED:
            request.state.auth_user = None  # Will be resolved by Depends(get_current_user)
        else:
            request.state.auth_user = _ANONYMOUS_USER

        start = time.monotonic()
        response = await call_next(request)
        duration_ms = (time.monotonic() - start) * 1000

        # Activity logging for significant operations (async, non-blocking)
        if AUTH_ENABLED and response.status_code < 400:
            await self._maybe_log_activity(request, path, duration_ms)

        return response

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
            from src.api.app import _get_state
            auth_service = _get_state().get("auth_service")
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
        except Exception as e:
            logger.debug("Activity logging failed: %s", e)

    def _classify_activity(self, method: str, path: str) -> dict | None:
        """Classify request into activity type."""
        # Search
        if "/search" in path and method == "POST":
            return {"type": "search", "resource": "search"}
        # KB operations
        if "/knowledge/file-upload-ingest" in path and method == "POST":
            return {"type": "upload", "resource": "document"}
        if "/knowledge/ingest" in path and method == "POST":
            return {"type": "ingest", "resource": "pipeline"}
        if "/knowledge/ask" in path and method == "POST":
            return {"type": "query", "resource": "rag"}
        # Glossary
        if "/glossary" in path:
            if method == "POST":
                return {"type": "create", "resource": "glossary"}
            elif method in ("PUT", "PATCH"):
                return {"type": "edit", "resource": "glossary"}
        # Feedback
        if "/feedback" in path and method == "POST":
            return {"type": "feedback", "resource": "feedback"}
        # KB management
        if "/kb" in path:
            if method == "POST":
                return {"type": "create", "resource": "kb"}
            elif method in ("PUT", "PATCH"):
                return {"type": "edit", "resource": "kb"}

        return None
