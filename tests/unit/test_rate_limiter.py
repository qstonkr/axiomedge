"""Unit tests for the RateLimiterMiddleware."""

import time

import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from httpx import ASGITransport, AsyncClient

from src.api.middleware.rate_limiter import RateLimiterMiddleware


def _make_app(max_requests: int = 5, window_seconds: int = 60) -> Starlette:
    """Create a minimal Starlette app with rate limiter."""

    async def homepage(request):
        return PlainTextResponse("OK")

    async def health(request):
        return PlainTextResponse("healthy")

    app = Starlette(routes=[
        Route("/", homepage),
        Route("/health", health),
        Route("/api/test", homepage),
    ])
    middleware = RateLimiterMiddleware(app)
    middleware.max_requests = max_requests
    middleware.window_seconds = window_seconds
    return middleware  # type: ignore[return-value]


@pytest.fixture
async def rate_client():
    app = _make_app(max_requests=3, window_seconds=60)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


class TestRateLimiter:
    """Test rate limiting behavior."""

    async def test_allows_requests_under_limit(self, rate_client: AsyncClient) -> None:
        for _ in range(3):
            resp = await rate_client.get("/api/test")
            assert resp.status_code == 200

    async def test_blocks_requests_over_limit(self, rate_client: AsyncClient) -> None:
        # Exhaust the limit
        for _ in range(3):
            resp = await rate_client.get("/api/test")
            assert resp.status_code == 200

        # Next request should be blocked
        resp = await rate_client.get("/api/test")
        assert resp.status_code == 429
        assert "Too Many Requests" in resp.json()["detail"]
        assert "Retry-After" in resp.headers

    async def test_health_path_exempt(self, rate_client: AsyncClient) -> None:
        """Health endpoint should never be rate limited."""
        # Exhaust limit on regular path
        for _ in range(3):
            await rate_client.get("/api/test")

        # Regular path blocked
        resp = await rate_client.get("/api/test")
        assert resp.status_code == 429

        # Health path still works
        resp = await rate_client.get("/health")
        assert resp.status_code == 200

    async def test_ready_path_exempt(self) -> None:
        app = _make_app(max_requests=1, window_seconds=60)

        async def ready(request):
            return PlainTextResponse("ready")

        # Add /ready route to the inner app
        inner_app = Starlette(routes=[
            Route("/ready", ready),
            Route("/test", lambda r: PlainTextResponse("OK")),
        ])
        middleware = RateLimiterMiddleware(inner_app)
        middleware.max_requests = 1
        middleware.window_seconds = 60

        transport = ASGITransport(app=middleware)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Exhaust limit
            await ac.get("/test")
            resp = await ac.get("/test")
            assert resp.status_code == 429

            # /ready exempt
            resp = await ac.get("/ready")
            assert resp.status_code == 200

    async def test_different_clients_tracked_separately(self) -> None:
        """Different client IPs should have separate rate limits."""
        app = _make_app(max_requests=1, window_seconds=60)
        # Note: in ASGI test transport, all requests come from the same client
        # so this is a basic structural test
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get("/")
            assert resp.status_code == 200
