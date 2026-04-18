"""In-memory sliding window rate limiter middleware.

Uses pure Starlette middleware (not BaseHTTPMiddleware) to avoid asyncio conflicts.
Configurable via environment variables:
  - RATE_LIMIT_REQUESTS: max requests per window (default 100)
  - RATE_LIMIT_WINDOW_SECONDS: window size in seconds (default 60)
  - TRUST_PROXY_HEADERS: "true" to honor X-Forwarded-For / X-Real-IP / CF-Connecting-IP
    (only enable when actually behind a trusted proxy — clients can otherwise spoof IPs)
"""

from __future__ import annotations

import os
import time
from collections import defaultdict
from threading import Lock

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

# Paths exempt from rate limiting
_EXEMPT_PATHS: frozenset[str] = frozenset({"/health", "/ready", "/metrics"})


def _extract_client_ip(request: Request, trust_proxy: bool) -> str:
    """Extract client IP, honoring proxy headers only when explicitly trusted.

    Order when trust_proxy=True:
      1. CF-Connecting-IP (Cloudflare — single IP, not spoofable past CF)
      2. X-Real-IP (nginx single IP)
      3. X-Forwarded-For (first IP in comma-separated chain — original client)
      4. Fall back to direct connection
    """
    if trust_proxy:
        cf_ip = request.headers.get("cf-connecting-ip", "").strip()
        if cf_ip:
            return cf_ip
        real_ip = request.headers.get("x-real-ip", "").strip()
        if real_ip:
            return real_ip
        xff = request.headers.get("x-forwarded-for", "")
        if xff:
            first = xff.split(",")[0].strip()
            if first:
                return first
    return request.client.host if request.client else "unknown"


class RateLimiterMiddleware:
    """Sliding window rate limiter implemented as raw ASGI middleware."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.max_requests = int(os.getenv("RATE_LIMIT_REQUESTS", "100"))
        self.window_seconds = int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60"))
        self.trust_proxy = os.getenv("TRUST_PROXY_HEADERS", "false").lower() == "true"
        # client_ip -> list of request timestamps
        self._requests: dict[str, list[float]] = defaultdict(list)
        self._lock = Lock()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if path in _EXEMPT_PATHS:
            await self.app(scope, receive, send)
            return

        # Extract client IP (proxy-aware when TRUST_PROXY_HEADERS=true)
        request = Request(scope)
        client_ip = _extract_client_ip(request, self.trust_proxy)

        now = time.monotonic()
        window_start = now - self.window_seconds

        with self._lock:
            # Prune expired timestamps
            timestamps = self._requests[client_ip]
            self._requests[client_ip] = [t for t in timestamps if t > window_start]
            timestamps = self._requests[client_ip]

            if len(timestamps) >= self.max_requests:
                # Calculate Retry-After from the oldest request in the window
                oldest = timestamps[0]
                retry_after = int(oldest - window_start) + 1
                if retry_after < 1:
                    retry_after = 1

                response = JSONResponse(
                    status_code=429,
                    content={"detail": "Too Many Requests"},
                    headers={"Retry-After": str(retry_after)},
                )
                await response(scope, receive, send)
                return

            timestamps.append(now)

        await self.app(scope, receive, send)
