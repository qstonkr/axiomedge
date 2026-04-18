"""API version deprecation headers.

RFC 8594 (Sunset) + RFC draft-ietf-httpapi-deprecation-header.

Mark a route deprecated by registering its path + sunset date:

    from src.api.middleware.api_version import deprecate

    deprecate(
        "/api/v1/legacy/search",
        sunset="2026-12-31",
        successor="/api/v2/search",
    )

The middleware then attaches:
    Deprecation: true
    Sunset: Wed, 31 Dec 2026 00:00:00 GMT
    Link: </api/v2/search>; rel="successor-version"

Convention:
- New endpoints go under ``/api/v2/...``
- Old endpoints stay under ``/api/v1/...`` until sunset
- Breaking changes require a new version prefix; backward-compatible additions stay
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from starlette.requests import Request
from starlette.responses import Response


@dataclass(frozen=True)
class DeprecationPolicy:
    """Deprecation metadata for a single route path."""

    sunset: str  # RFC 7231 IMF-fixdate or YYYY-MM-DD
    successor: str | None = None  # URL of replacement endpoint
    note: str | None = None  # human-readable migration hint


# Path-prefix → policy. Longest matching prefix wins.
_REGISTRY: dict[str, DeprecationPolicy] = {}


def deprecate(path_prefix: str, sunset: str, successor: str | None = None, note: str | None = None) -> None:
    """Register a deprecation policy for routes under ``path_prefix``."""
    _REGISTRY[path_prefix] = DeprecationPolicy(sunset=sunset, successor=successor, note=note)


def clear_deprecations() -> None:
    """Test helper: reset registry."""
    _REGISTRY.clear()


def _format_sunset(value: str) -> str:
    """Convert YYYY-MM-DD to RFC 7231 IMF-fixdate. Pass through if already formatted."""
    try:
        dt = datetime.strptime(value, "%Y-%m-%d")
        return dt.strftime("%a, %d %b %Y 00:00:00 GMT")
    except ValueError:
        return value


def _match_policy(path: str) -> DeprecationPolicy | None:
    """Find the longest path-prefix match in registry."""
    matches = [(p, pol) for p, pol in _REGISTRY.items() if path.startswith(p)]
    if not matches:
        return None
    matches.sort(key=lambda kv: len(kv[0]), reverse=True)
    return matches[0][1]


async def add_deprecation_headers(request: Request, call_next: Callable) -> Response:
    """ASGI middleware that attaches Deprecation/Sunset headers to deprecated routes."""
    response: Response = await call_next(request)
    policy = _match_policy(request.url.path)
    if policy is None:
        return response
    response.headers["Deprecation"] = "true"
    response.headers["Sunset"] = _format_sunset(policy.sunset)
    if policy.successor:
        response.headers["Link"] = f'<{policy.successor}>; rel="successor-version"'
    if policy.note:
        response.headers["X-API-Deprecation-Note"] = policy.note
    return response
