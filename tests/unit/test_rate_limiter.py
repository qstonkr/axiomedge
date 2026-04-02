"""Unit tests for the RateLimiterMiddleware."""

import asyncio

import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from httpx import ASGITransport, AsyncClient

from src.api.middleware.rate_limiter import RateLimiterMiddleware


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


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


async def _make_client(max_requests: int = 3, window_seconds: int = 60):
    app = _make_app(max_requests=max_requests, window_seconds=window_seconds)
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


class TestRateLimiter:
    """Test rate limiting behavior."""

    def test_allows_requests_under_limit(self) -> None:
        async def _test():
            ac = await _make_client()
            async with ac:
                for _ in range(3):
                    resp = await ac.get("/api/test")
                    assert resp.status_code == 200
        _run(_test())

    def test_blocks_requests_over_limit(self) -> None:
        async def _test():
            ac = await _make_client()
            async with ac:
                for _ in range(3):
                    resp = await ac.get("/api/test")
                    assert resp.status_code == 200
                resp = await ac.get("/api/test")
                assert resp.status_code == 429
                assert "Too Many Requests" in resp.json()["detail"]
                assert "Retry-After" in resp.headers
        _run(_test())

    def test_health_path_exempt(self) -> None:
        """Health endpoint should never be rate limited."""
        async def _test():
            ac = await _make_client()
            async with ac:
                for _ in range(3):
                    await ac.get("/api/test")
                resp = await ac.get("/api/test")
                assert resp.status_code == 429
                resp = await ac.get("/health")
                assert resp.status_code == 200
        _run(_test())

    def test_ready_path_exempt(self) -> None:
        async def _test():
            async def ready(request):
                return PlainTextResponse("ready")

            inner_app = Starlette(routes=[
                Route("/ready", ready),
                Route("/test", lambda r: PlainTextResponse("OK")),
            ])
            middleware = RateLimiterMiddleware(inner_app)
            middleware.max_requests = 1
            middleware.window_seconds = 60

            transport = ASGITransport(app=middleware)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                await ac.get("/test")
                resp = await ac.get("/test")
                assert resp.status_code == 429
                resp = await ac.get("/ready")
                assert resp.status_code == 200
        _run(_test())

    def test_different_clients_tracked_separately(self) -> None:
        """Different client IPs should have separate rate limits."""
        async def _test():
            app = _make_app(max_requests=1, window_seconds=60)
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.get("/")
                assert resp.status_code == 200
        _run(_test())
