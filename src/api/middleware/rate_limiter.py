"""In-memory sliding window rate limiter middleware.

Per-tenant scope (per-user or per-IP) supported.

Configurable via environment variables:
  - RATE_LIMIT_REQUESTS: max requests per window per-key (default 100)
  - RATE_LIMIT_WINDOW_SECONDS: window size in seconds (default 60)
  - RATE_LIMIT_SCOPE: "ip" (default) | "user" | "user_or_ip"
      ip          — quota by client IP (current behavior)
      user        — quota by authenticated user.sub (anonymous shares one bucket)
      user_or_ip  — user.sub when authenticated, IP fallback (recommended for prod)
  - TRUST_PROXY_HEADERS: "true" to honor X-Forwarded-For / X-Real-IP / CF-Connecting-IP
  - RATE_LIMIT_MAX_KEYS: cap distinct buckets to prevent memory exhaustion
      (LRU eviction; default 50000)

Production note: in-memory state is per-process. For multi-replica deployments
without sticky sessions, use a Redis-backed limiter (planned follow-up).
Per-instance limit acts as a safety floor in the meantime.
"""

from __future__ import annotations

import os
import time
from collections import OrderedDict
from threading import Lock

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

# Paths exempt from rate limiting
_EXEMPT_PATHS: frozenset[str] = frozenset({"/health", "/ready", "/metrics"})

_VALID_SCOPES: frozenset[str] = frozenset({"ip", "user", "user_or_ip"})


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


def _compute_rate_key(request: Request, scope_mode: str, trust_proxy: bool) -> str:
    """Compute the bucket key based on configured scope.

    Returns a prefix-tagged string so different scopes can coexist:
      "ip:1.2.3.4", "user:abc-123", etc.
    """
    if scope_mode in ("user", "user_or_ip"):
        user = getattr(request.state, "auth_user", None)
        sub = getattr(user, "sub", None) if user else None
        if sub and sub != "anonymous":
            return f"user:{sub}"
        if scope_mode == "user":
            return "user:anonymous"
    return f"ip:{_extract_client_ip(request, trust_proxy)}"


class RateLimiterMiddleware:
    """Per-tenant sliding window rate limiter (in-memory, single-process)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.max_requests = int(os.getenv("RATE_LIMIT_REQUESTS", "100"))
        self.window_seconds = int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60"))
        self.trust_proxy = os.getenv("TRUST_PROXY_HEADERS", "false").lower() == "true"
        scope_mode = os.getenv("RATE_LIMIT_SCOPE", "ip").lower()
        self.scope_mode = scope_mode if scope_mode in _VALID_SCOPES else "ip"
        self.max_keys = int(os.getenv("RATE_LIMIT_MAX_KEYS", "50000"))
        # OrderedDict for LRU bucket eviction (most-recently-used moved to end)
        self._requests: OrderedDict[str, list[float]] = OrderedDict()
        self._lock = Lock()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if path in _EXEMPT_PATHS:
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        rate_key = _compute_rate_key(request, self.scope_mode, self.trust_proxy)

        now = time.monotonic()
        window_start = now - self.window_seconds

        with self._lock:
            # LRU eviction: cap distinct keys to bound memory
            if rate_key in self._requests:
                self._requests.move_to_end(rate_key)
            elif len(self._requests) >= self.max_keys:
                self._requests.popitem(last=False)
            timestamps = self._requests.get(rate_key, [])
            timestamps = [t for t in timestamps if t > window_start]
            self._requests[rate_key] = timestamps

            if len(timestamps) >= self.max_requests:
                oldest = timestamps[0]
                retry_after = max(int(oldest - window_start) + 1, 1)
                response = JSONResponse(
                    status_code=429,
                    content={"detail": "Too Many Requests"},
                    headers={
                        "Retry-After": str(retry_after),
                        "X-RateLimit-Scope": self.scope_mode,
                    },
                )
                await response(scope, receive, send)
                return

            timestamps.append(now)

        await self.app(scope, receive, send)
