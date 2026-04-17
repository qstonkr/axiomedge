"""Security headers middleware — adds defense-in-depth HTTP headers.

Headers:
    X-Content-Type-Options: nosniff
    X-Frame-Options: DENY
    Referrer-Policy: strict-origin-when-cross-origin
    Strict-Transport-Security (HTTPS only)
    Content-Security-Policy (env-gated)
"""

from __future__ import annotations

import os

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to all responses."""

    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[override]
        response = await call_next(request)

        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["X-XSS-Protection"] = "0"  # Modern: rely on CSP

        # HSTS — only when HTTPS is enabled
        if os.getenv("HTTPS_ENABLED", "").lower() in ("true", "1"):
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )

        # CSP — opt-in via env
        csp = os.getenv("CONTENT_SECURITY_POLICY")
        if csp:
            response.headers["Content-Security-Policy"] = csp

        return response
