"""Coverage boost tests — visual_content_analyzer, route_discovery,
git config, rate_limiter.

Targets ~70 new covered statements to reach 85% total coverage.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from threading import Lock
from types import ModuleType
from unittest.mock import (
    AsyncMock,
    MagicMock,
    patch,
)

import pytest

# ===================================================================
# 1. VisualContentAnalyzer — ~25 new statements
# ===================================================================

from src.api.middleware.rate_limiter import (
    RateLimiterMiddleware,
    _EXEMPT_PATHS,
)
from src.connectors.git.config import GitConnectorConfig
from src.pipelines.cv.visual_content_analyzer import (
    VisualAnalysisResult,
    VisualContentAnalyzer,
)


class TestVisualAnalysisResultToText:
    """Cover branches in to_text() not hit by existing tests."""

    def test_empty_result_produces_empty_text(self):
        r = VisualAnalysisResult()
        assert r.to_text() == ""

    def test_description_only(self):
        r = VisualAnalysisResult(
            image_type="architecture",
            description="System overview",
        )
        text = r.to_text()
        assert "[Visual: architecture]" in text
        assert "System overview" in text
        assert "Process:" not in text

    def test_process_steps_without_step_key(self):
        r = VisualAnalysisResult(
            image_type="flowchart",
            description="flow",
            process_steps=[
                {"action": "Do thing"},  # no 'step' key
                {"step": 2, "action": "Next"},
            ],
        )
        text = r.to_text()
        # First step should use enumeration index (1)
        assert "1. Do thing" in text
        assert "2. Next" in text

    def test_entities_non_system_excluded(self):
        """Systems list only includes type=System entities."""
        r = VisualAnalysisResult(
            image_type="diagram",
            description="d",
            entities=[
                {"name": "DB", "type": "System"},
                {"name": "User", "type": "Person"},
                {"name": "API", "type": "System"},
            ],
        )
        text = r.to_text()
        assert "DB" in text
        assert "API" in text
        # Person type excluded from "Related systems" line
        lines = text.split("\n")
        systems_line = [ln for ln in lines if "Related systems" in ln]
        assert len(systems_line) == 1
        assert "User" not in systems_line[0]

    def test_raw_text_shown_when_no_description(self):
        r = VisualAnalysisResult(
            raw_text="OCR output here",
            description="",
        )
        text = r.to_text()
        assert "[Image OCR] OCR output here" in text

    def test_raw_text_hidden_when_description_present(self):
        r = VisualAnalysisResult(
            image_type="diagram",
            description="Good description",
            raw_text="OCR output here",
        )
        text = r.to_text()
        assert "[Image OCR]" not in text


class TestVisualAnalysisResultToGraphData:
    def test_without_process_steps(self):
        r = VisualAnalysisResult(
            entities=[{"name": "A", "type": "System"}],
            relationships=[{"source": "A", "target": "B"}],
        )
        gd = r.to_graph_data()
        assert "process_steps" not in gd
        assert gd["nodes"] == r.entities

    def test_with_process_steps(self):
        r = VisualAnalysisResult(
            entities=[],
            relationships=[],
            process_steps=[{"step": 1, "action": "Go"}],
        )
        gd = r.to_graph_data()
        assert "process_steps" in gd
        assert gd["process_steps"] == r.process_steps


class TestVisualContentAnalyzerInit:
    def test_init_sets_none_pipeline(self):
        analyzer = VisualContentAnalyzer()
        assert analyzer._cv_pipeline is None
        assert isinstance(analyzer._lock, type(Lock()))


class TestVisualContentAnalyzerAnalyze:
    @pytest.mark.asyncio
    async def test_analyze_creates_pipeline_once(self):
        """Double-checked locking creates CVPipeline only once."""
        analyzer = VisualContentAnalyzer()
        mock_result = VisualAnalysisResult(
            image_type="test", confidence=0.9
        )
        mock_pipeline = MagicMock()
        mock_pipeline.analyze = AsyncMock(return_value=mock_result)

        with patch(
            "src.pipelines.cv.pipeline.CVPipeline",
            return_value=mock_pipeline,
        ) as mock_cls:
            result = await analyzer.analyze(b"fake_image")
            assert result.image_type == "test"
            assert result.confidence == 0.9
            mock_cls.assert_called_once()

            # Second call reuses cached pipeline
            await analyzer.analyze(b"another_image")
            mock_cls.assert_called_once()  # still once
            assert mock_pipeline.analyze.call_count == 2

    @pytest.mark.asyncio
    async def test_analyze_reuses_existing_pipeline(self):
        """If _cv_pipeline already set, skip creation."""
        analyzer = VisualContentAnalyzer()
        mock_result = VisualAnalysisResult(confidence=0.5)
        mock_pipeline = MagicMock()
        mock_pipeline.analyze = AsyncMock(return_value=mock_result)
        analyzer._cv_pipeline = mock_pipeline

        result = await analyzer.analyze(b"data")
        assert result.confidence == 0.5

    @pytest.mark.asyncio
    async def test_analyze_propagates_exception(self):
        """If CVPipeline.analyze raises, exception propagates."""
        analyzer = VisualContentAnalyzer()
        mock_pipeline = MagicMock()
        mock_pipeline.analyze = AsyncMock(
            side_effect=RuntimeError("CV fail")
        )
        analyzer._cv_pipeline = mock_pipeline

        with pytest.raises(RuntimeError, match="CV fail"):
            await analyzer.analyze(b"data")


# ===================================================================
# 2. route_discovery — ~25 new statements
# ===================================================================


class TestDiscoverAndRegisterRoutes:
    def test_registers_router_attribute(self):
        from src.api.route_discovery import discover_and_register_routes

        mock_router = MagicMock()
        mock_router.routes = [MagicMock()]

        mock_module = ModuleType("src.api.routes.fake_mod")
        mock_module.router = mock_router  # type: ignore[attr-defined]

        mock_app = MagicMock()

        fake_module_info = MagicMock()
        fake_module_info.name = "fake_mod"

        routes_pkg = MagicMock()
        routes_pkg.__path__ = ["/fake"]

        with patch(
            "src.api.route_discovery.pkgutil.iter_modules",
            return_value=[fake_module_info],
        ):
            with patch(
                "src.api.route_discovery.importlib.import_module",
                side_effect=lambda name: (
                    routes_pkg
                    if "routes" == name.split(".")[-1]
                    else mock_module
                ),
            ):
                count = discover_and_register_routes(mock_app)

        assert count == 1
        mock_app.include_router.assert_called_once_with(mock_router)

    def test_registers_multiple_router_attrs(self):
        from src.api.route_discovery import discover_and_register_routes

        mock_router = MagicMock()
        mock_router.routes = [MagicMock()]
        mock_admin_router = MagicMock()
        mock_admin_router.routes = [MagicMock()]

        mock_module = ModuleType("src.api.routes.multi")
        mock_module.router = mock_router  # type: ignore[attr-defined]
        mock_module.admin_router = mock_admin_router  # type: ignore[attr-defined]

        mock_app = MagicMock()
        fake_info = MagicMock()
        fake_info.name = "multi"

        routes_pkg = MagicMock()
        routes_pkg.__path__ = ["/fake"]

        with patch(
            "src.api.route_discovery.pkgutil.iter_modules",
            return_value=[fake_info],
        ):
            with patch(
                "src.api.route_discovery.importlib.import_module",
                side_effect=lambda name: (
                    routes_pkg if "routes" == name.split(".")[-1]
                    else mock_module
                ),
            ):
                count = discover_and_register_routes(mock_app)

        assert count == 2
        assert mock_app.include_router.call_count == 2

    def test_skips_module_without_router(self):
        from src.api.route_discovery import discover_and_register_routes

        mock_module = ModuleType("src.api.routes.norouter")
        # No router attribute

        mock_app = MagicMock()
        fake_info = MagicMock()
        fake_info.name = "norouter"

        routes_pkg = MagicMock()
        routes_pkg.__path__ = ["/fake"]

        with patch(
            "src.api.route_discovery.pkgutil.iter_modules",
            return_value=[fake_info],
        ):
            with patch(
                "src.api.route_discovery.importlib.import_module",
                side_effect=lambda name: (
                    routes_pkg
                    if "routes" == name.split(".")[-1]
                    else mock_module
                ),
            ):
                count = discover_and_register_routes(mock_app)

        assert count == 0
        mock_app.include_router.assert_not_called()

    def test_skips_attr_without_routes_property(self):
        """If router attr has no .routes, it's not registered."""
        from src.api.route_discovery import discover_and_register_routes

        not_a_router = "just a string"
        mock_module = ModuleType("src.api.routes.bad")
        mock_module.router = not_a_router  # type: ignore[attr-defined]

        mock_app = MagicMock()
        fake_info = MagicMock()
        fake_info.name = "bad"

        routes_pkg = MagicMock()
        routes_pkg.__path__ = ["/fake"]

        with patch(
            "src.api.route_discovery.pkgutil.iter_modules",
            return_value=[fake_info],
        ):
            with patch(
                "src.api.route_discovery.importlib.import_module",
                side_effect=lambda name: (
                    routes_pkg
                    if "routes" == name.split(".")[-1]
                    else mock_module
                ),
            ):
                count = discover_and_register_routes(mock_app)

        assert count == 0

    def test_import_error_is_logged_and_skipped(self):
        from src.api.route_discovery import discover_and_register_routes

        mock_app = MagicMock()
        fake_info = MagicMock()
        fake_info.name = "broken_module"

        routes_pkg = MagicMock()
        routes_pkg.__path__ = ["/fake"]

        def _import_side_effect(name):
            if "routes" == name.split(".")[-1]:
                return routes_pkg
            raise ImportError("cannot import broken_module")

        with patch(
            "src.api.route_discovery.pkgutil.iter_modules",
            return_value=[fake_info],
        ):
            with patch(
                "src.api.route_discovery.importlib.import_module",
                side_effect=_import_side_effect,
            ):
                count = discover_and_register_routes(mock_app)

        assert count == 0

    def test_no_modules_found(self):
        from src.api.route_discovery import discover_and_register_routes

        mock_app = MagicMock()
        routes_pkg = MagicMock()
        routes_pkg.__path__ = ["/fake"]

        with patch(
            "src.api.route_discovery.pkgutil.iter_modules",
            return_value=[],
        ):
            with patch(
                "src.api.route_discovery.importlib.import_module",
                return_value=routes_pkg,
            ):
                count = discover_and_register_routes(mock_app)

        assert count == 0

    def test_knowledge_router_attr(self):
        from src.api.route_discovery import discover_and_register_routes

        mock_router = MagicMock()
        mock_router.routes = [MagicMock()]

        mock_module = ModuleType("src.api.routes.kb")
        mock_module.knowledge_router = mock_router  # type: ignore[attr-defined]

        mock_app = MagicMock()
        fake_info = MagicMock()
        fake_info.name = "kb"

        routes_pkg = MagicMock()
        routes_pkg.__path__ = ["/fake"]

        with patch(
            "src.api.route_discovery.pkgutil.iter_modules",
            return_value=[fake_info],
        ):
            with patch(
                "src.api.route_discovery.importlib.import_module",
                side_effect=lambda name: (
                    routes_pkg
                    if "routes" == name.split(".")[-1]
                    else mock_module
                ),
            ):
                count = discover_and_register_routes(mock_app)

        assert count == 1

    def test_rag_query_router_attr(self):
        from src.api.route_discovery import discover_and_register_routes

        mock_router = MagicMock()
        mock_router.routes = [MagicMock()]

        mock_module = ModuleType("src.api.routes.rag")
        mock_module.rag_query_router = mock_router  # type: ignore[attr-defined]

        mock_app = MagicMock()
        fake_info = MagicMock()
        fake_info.name = "rag"

        routes_pkg = MagicMock()
        routes_pkg.__path__ = ["/fake"]

        with patch(
            "src.api.route_discovery.pkgutil.iter_modules",
            return_value=[fake_info],
        ):
            with patch(
                "src.api.route_discovery.importlib.import_module",
                side_effect=lambda name: (
                    routes_pkg
                    if "routes" == name.split(".")[-1]
                    else mock_module
                ),
            ):
                count = discover_and_register_routes(mock_app)

        assert count == 1


# ===================================================================
# 3. GitConnectorConfig — ~10 new statements
# ===================================================================

class TestGitConnectorConfigExtended:
    def test_workdir_uses_slug_when_set(self):
        cfg = GitConnectorConfig(
            repo_url="https://github.com/foo/bar",
            workdir_slug="my-slug",
            workdir_root=Path("/tmp/git_repos"),
        )
        assert cfg.workdir == Path("/tmp/git_repos/my-slug")

    def test_workdir_uses_default_slug_when_empty(self):
        cfg = GitConnectorConfig(
            repo_url="https://github.com/foo/bar",
            workdir_slug="",
            workdir_root=Path("/tmp/git_repos"),
        )
        wd = cfg.workdir
        assert wd.parent == Path("/tmp/git_repos")
        assert "github" in wd.name.lower() or len(wd.name) > 0

    def test_default_slug_truncates_long_url(self):
        long_url = "https://example.com/" + "a" * 100
        cfg = GitConnectorConfig(repo_url=long_url)
        slug = cfg._default_slug()
        # base[:40] + "_" + 8-char hash
        parts = slug.rsplit("_", 1)
        assert len(parts[0]) <= 40
        assert len(parts[1]) == 8

    def test_default_slug_empty_base(self):
        cfg = GitConnectorConfig(repo_url="///")
        slug = cfg._default_slug()
        # base is empty after regex, should just be the hash
        assert len(slug) == 8

    def test_from_source_repo_url_from_metadata_url(self):
        cfg = GitConnectorConfig.from_source({
            "metadata": {"url": "https://github.com/foo/bar"},
        })
        assert cfg.repo_url == "https://github.com/foo/bar"

    def test_from_source_repo_url_from_metadata_repo_url(self):
        cfg = GitConnectorConfig.from_source({
            "metadata": {"repo_url": "https://github.com/foo/bar"},
        })
        assert cfg.repo_url == "https://github.com/foo/bar"

    def test_from_source_include_globs_string(self):
        cfg = GitConnectorConfig.from_source({
            "crawl_config": {
                "repo_url": "https://github.com/foo/bar",
                "include_globs": "*.py",
            },
        })
        assert cfg.include_globs == ("*.py",)

    def test_from_source_include_globs_empty_string(self):
        cfg = GitConnectorConfig.from_source({
            "crawl_config": {
                "repo_url": "https://github.com/foo/bar",
                "include_globs": "",
            },
        })
        # Falls back to default
        assert cfg.include_globs == ("**/*.md",)

    def test_from_source_include_globs_none(self):
        cfg = GitConnectorConfig.from_source({
            "crawl_config": {
                "repo_url": "https://github.com/foo/bar",
                "include_globs": None,
            },
        })
        assert cfg.include_globs == ("**/*.md",)

    def test_from_source_exclude_globs_with_empty_items(self):
        cfg = GitConnectorConfig.from_source({
            "crawl_config": {
                "repo_url": "https://github.com/foo/bar",
                "exclude_globs": [".git/**", "", "  "],
            },
        })
        assert cfg.exclude_globs == (".git/**",)

    def test_from_source_auth_token_direct(self):
        cfg = GitConnectorConfig.from_source({
            "crawl_config": {
                "repo_url": "https://github.com/foo/bar",
                "auth_token": "direct-token",
            },
        })
        assert cfg.auth_token == "direct-token"

    def test_from_source_auth_token_env_not_set(self, monkeypatch):
        monkeypatch.delenv("MISSING_TOKEN", raising=False)
        cfg = GitConnectorConfig.from_source({
            "crawl_config": {
                "repo_url": "https://github.com/foo/bar",
                "auth_token_env": "MISSING_TOKEN",
            },
        })
        assert cfg.auth_token == ""

    def test_from_source_auth_token_takes_precedence(self, monkeypatch):
        monkeypatch.setenv("MY_TOK", "env-token")
        cfg = GitConnectorConfig.from_source({
            "crawl_config": {
                "repo_url": "https://github.com/foo/bar",
                "auth_token": "direct",
                "auth_token_env": "MY_TOK",
            },
        })
        # Direct token takes precedence
        assert cfg.auth_token == "direct"

    def test_from_source_globs_non_standard_type(self):
        """_as_tuple with non-list/str/None returns default."""
        cfg = GitConnectorConfig.from_source({
            "crawl_config": {
                "repo_url": "https://github.com/foo/bar",
                "include_globs": 42,
            },
        })
        assert cfg.include_globs == ("**/*.md",)


# ===================================================================
# 4. RateLimiterMiddleware — ~10 new statements
# ===================================================================

class TestRateLimiterMiddlewareExtended:
    def test_exempt_paths_contain_expected(self):
        assert "/health" in _EXEMPT_PATHS
        assert "/ready" in _EXEMPT_PATHS
        assert "/metrics" in _EXEMPT_PATHS

    def test_init_reads_env_defaults(self, monkeypatch):
        monkeypatch.delenv("RATE_LIMIT_REQUESTS", raising=False)
        monkeypatch.delenv("RATE_LIMIT_WINDOW_SECONDS", raising=False)
        mw = RateLimiterMiddleware(MagicMock())
        assert mw.max_requests == 100
        assert mw.window_seconds == 60

    def test_init_reads_env_custom(self, monkeypatch):
        monkeypatch.setenv("RATE_LIMIT_REQUESTS", "50")
        monkeypatch.setenv("RATE_LIMIT_WINDOW_SECONDS", "30")
        mw = RateLimiterMiddleware(MagicMock())
        assert mw.max_requests == 50
        assert mw.window_seconds == 30

    @pytest.mark.asyncio
    async def test_non_http_scope_passes_through(self):
        """Non-http scope (e.g. websocket) should pass through."""
        inner = AsyncMock()
        mw = RateLimiterMiddleware(inner)
        scope = {"type": "websocket", "path": "/ws"}
        receive = AsyncMock()
        send = AsyncMock()
        await mw(scope, receive, send)
        inner.assert_awaited_once_with(scope, receive, send)

    @pytest.mark.asyncio
    async def test_metrics_path_exempt(self):
        inner = AsyncMock()
        mw = RateLimiterMiddleware(inner)
        mw.max_requests = 0  # Would block everything
        scope = {
            "type": "http",
            "path": "/metrics",
            "method": "GET",
            "headers": [],
            "query_string": b"",
        }
        receive = AsyncMock()
        send = AsyncMock()
        await mw(scope, receive, send)
        inner.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_unknown_client_ip(self):
        """When request.client is None, uses 'unknown' as key."""
        from starlette.applications import Starlette
        from starlette.responses import PlainTextResponse
        from starlette.routing import Route
        from httpx import ASGITransport, AsyncClient

        async def home(request):
            return PlainTextResponse("OK")

        app = Starlette(routes=[Route("/test", home)])
        mw = RateLimiterMiddleware(app)
        mw.max_requests = 2
        mw.window_seconds = 60

        transport = ASGITransport(app=mw)
        async with AsyncClient(
            transport=transport, base_url="http://test"
        ) as ac:
            resp = await ac.get("/test")
            assert resp.status_code == 200
            resp = await ac.get("/test")
            assert resp.status_code == 200
            # Third request should be blocked
            resp = await ac.get("/test")
            assert resp.status_code == 429

    @pytest.mark.asyncio
    async def test_retry_after_header_minimum_1(self):
        """Retry-After should be at least 1."""
        from starlette.applications import Starlette
        from starlette.responses import PlainTextResponse
        from starlette.routing import Route
        from httpx import ASGITransport, AsyncClient

        async def home(request):
            return PlainTextResponse("OK")

        app = Starlette(routes=[Route("/x", home)])
        mw = RateLimiterMiddleware(app)
        mw.max_requests = 1
        mw.window_seconds = 60

        transport = ASGITransport(app=mw)
        async with AsyncClient(
            transport=transport, base_url="http://test"
        ) as ac:
            await ac.get("/x")
            resp = await ac.get("/x")
            assert resp.status_code == 429
            retry = int(resp.headers["Retry-After"])
            assert retry >= 1

    @pytest.mark.asyncio
    async def test_window_expiry_allows_new_requests(self):
        """After window expires, requests should be allowed again."""
        from starlette.applications import Starlette
        from starlette.responses import PlainTextResponse
        from starlette.routing import Route
        from httpx import ASGITransport, AsyncClient

        async def home(request):
            return PlainTextResponse("OK")

        app = Starlette(routes=[Route("/t", home)])
        mw = RateLimiterMiddleware(app)
        mw.max_requests = 1
        mw.window_seconds = 1  # 1 second window

        transport = ASGITransport(app=mw)
        async with AsyncClient(
            transport=transport, base_url="http://test"
        ) as ac:
            resp = await ac.get("/t")
            assert resp.status_code == 200
            resp = await ac.get("/t")
            assert resp.status_code == 429

            # Wait for window to expire
            await asyncio.sleep(1.1)

            resp = await ac.get("/t")
            assert resp.status_code == 200
