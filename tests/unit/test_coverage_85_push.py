"""Coverage push tests — health, auth middleware, text_shape_mapper, neo4j indexer.

Targets ~160 newly covered statements across 4 files to push total past 85%.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _run(coro):
    """Run coroutine in a fresh loop (no pytest-asyncio needed)."""
    return asyncio.run(coro)


def _make_auth_middleware():
    from src.auth.middleware import AuthMiddleware
    return AuthMiddleware.__new__(AuthMiddleware)


# ===================================================================
# 1. src/api/routes/health.py  — _check_services branch coverage
# ===================================================================

class TestCheckServicesNeo4j:
    """Neo4j health-check branches (lines 38-43)."""

    def test_neo4j_healthy(self):
        from src.api.routes.health import _check_services

        state = {"neo4j": AsyncMock(health_check=AsyncMock(return_value=True))}
        checks = _run(_check_services(state))
        assert checks["neo4j"] is True

    def test_neo4j_none(self):
        from src.api.routes.health import _check_services

        state = {}
        checks = _run(_check_services(state))
        assert checks["neo4j"] is False

    def test_neo4j_exception(self):
        from src.api.routes.health import _check_services

        neo4j = AsyncMock()
        neo4j.health_check = AsyncMock(side_effect=RuntimeError("boom"))
        state = {"neo4j": neo4j}
        checks = _run(_check_services(state))
        assert checks["neo4j"] is False


class TestCheckServicesEmbedding:
    """Embedding health-check branches (lines 46-51)."""

    def test_embedding_ready(self):
        from src.api.routes.health import _check_services

        embedder = MagicMock()
        embedder.is_ready.return_value = True
        state = {"embedder": embedder}
        checks = _run(_check_services(state))
        assert checks["embedding"] is True

    def test_embedding_not_ready(self):
        from src.api.routes.health import _check_services

        embedder = MagicMock()
        embedder.is_ready.return_value = False
        state = {"embedder": embedder}
        checks = _run(_check_services(state))
        assert checks["embedding"] is False

    def test_embedding_exception(self):
        from src.api.routes.health import _check_services

        embedder = MagicMock()
        embedder.is_ready.side_effect = RuntimeError("fail")
        state = {"embedder": embedder}
        checks = _run(_check_services(state))
        assert checks["embedding"] is False


class TestCheckServicesLLM:
    """LLM (Ollama) health-check branches (lines 54-65)."""

    def test_llm_healthy(self):
        from src.api.routes.health import _check_services

        mock_resp = MagicMock(status_code=200)
        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.get = AsyncMock(return_value=mock_resp)

        llm = MagicMock()
        llm._config.base_url = "http://localhost:11434"

        with patch("httpx.AsyncClient", return_value=mock_http):
            state = {"llm": llm}
            checks = _run(_check_services(state))
            assert checks["llm"] is True

    def test_llm_unhealthy_status(self):
        from src.api.routes.health import _check_services

        mock_resp = MagicMock(status_code=503)
        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.get = AsyncMock(return_value=mock_resp)

        llm = MagicMock()
        llm._config.base_url = "http://localhost:11434"

        with patch("httpx.AsyncClient", return_value=mock_http):
            state = {"llm": llm}
            checks = _run(_check_services(state))
            assert checks["llm"] is False

    def test_llm_none(self):
        from src.api.routes.health import _check_services

        state = {}
        checks = _run(_check_services(state))
        assert checks["llm"] is False

    def test_llm_exception(self):
        from src.api.routes.health import _check_services

        llm = MagicMock()
        llm._config.base_url = "http://localhost:11434"

        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.get = AsyncMock(side_effect=ConnectionError("refused"))

        with patch("httpx.AsyncClient", return_value=mock_http):
            state = {"llm": llm}
            checks = _run(_check_services(state))
            assert checks["llm"] is False


class TestCheckServicesRedis:
    """Redis health-check branches (lines 68-77)."""

    def test_redis_healthy(self):
        from src.api.routes.health import _check_services

        cache = MagicMock()
        cache._redis = AsyncMock()
        cache._redis.ping = AsyncMock(return_value=True)
        state = {"search_cache": cache}
        checks = _run(_check_services(state))
        assert checks["redis"] is True

    def test_redis_none(self):
        from src.api.routes.health import _check_services

        state = {}
        checks = _run(_check_services(state))
        assert checks["redis"] is False

    def test_redis_exception(self):
        from src.api.routes.health import _check_services

        cache = MagicMock()
        cache._redis = AsyncMock()
        cache._redis.ping = AsyncMock(side_effect=ConnectionError("err"))
        state = {"search_cache": cache}
        checks = _run(_check_services(state))
        assert checks["redis"] is False


class TestCheckServicesDatabase:
    """PostgreSQL health-check branches (lines 80-85)."""

    def test_database_present(self):
        from src.api.routes.health import _check_services

        state = {"db_session_factory": MagicMock()}
        checks = _run(_check_services(state))
        assert checks["database"] is True

    def test_database_none(self):
        from src.api.routes.health import _check_services

        state = {}
        checks = _run(_check_services(state))
        assert checks["database"] is False


class TestCheckServicesPaddleOCR:
    """PaddleOCR health-check branches (lines 88-98)."""

    def test_paddleocr_healthy(self):
        from src.api.routes.health import _check_services

        mock_resp = MagicMock(status_code=200)
        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.get = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=mock_http):
            state = {}
            checks = _run(_check_services(state))
            assert checks["paddleocr"] is True

    def test_paddleocr_unhealthy(self):
        from src.api.routes.health import _check_services

        mock_resp = MagicMock(status_code=500)
        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.get = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=mock_http):
            state = {}
            checks = _run(_check_services(state))
            assert checks["paddleocr"] is False

    def test_paddleocr_exception(self):
        from src.api.routes.health import _check_services

        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.get = AsyncMock(side_effect=ConnectionError("nope"))

        with patch("httpx.AsyncClient", return_value=mock_http):
            state = {}
            checks = _run(_check_services(state))
            assert checks["paddleocr"] is False

    def test_paddleocr_custom_url(self):
        from src.api.routes.health import _check_services

        mock_resp = MagicMock(status_code=200)
        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.get = AsyncMock(return_value=mock_resp)

        with (
            patch.dict(
                "os.environ",
                {"PADDLEOCR_API_URL": "http://ocr-host:9999/ocr"},
            ),
            patch("httpx.AsyncClient", return_value=mock_http),
        ):
            state = {}
            checks = _run(_check_services(state))
            assert checks["paddleocr"] is True
            call_url = mock_http.get.call_args[0][0]
            # .replace("/ocr", "/health") replaces first match
            assert call_url.endswith("/health")


class TestHealthEndpointStatus:
    """health() route: healthy vs degraded (lines 103-109)."""

    def test_healthy_when_qdrant_and_embedding_true(self):
        import src.api.app  # noqa: F401
        from src.api.routes.health import health

        async def fake_check(state):
            return {
                "qdrant": True,
                "embedding": True,
                "neo4j": False,
                "llm": False,
                "redis": False,
                "database": False,
                "paddleocr": False,
            }

        with (
            patch(
                "src.api.routes.health._check_services",
                side_effect=fake_check,
            ),
            patch("src.api.routes.health._get_state", return_value={}),
        ):
            result = _run(health())
            assert result["status"] == "healthy"

    def test_degraded_when_qdrant_false(self):
        import src.api.app  # noqa: F401
        from src.api.routes.health import health

        async def fake_check(state):
            return {
                "qdrant": False,
                "embedding": True,
                "neo4j": False,
                "llm": False,
                "redis": False,
                "database": False,
                "paddleocr": False,
            }

        with (
            patch(
                "src.api.routes.health._check_services",
                side_effect=fake_check,
            ),
            patch("src.api.routes.health._get_state", return_value={}),
        ):
            result = _run(health())
            assert result["status"] == "degraded"


# ===================================================================
# 2. src/auth/middleware.py  — dispatch branches
# ===================================================================

class TestAuthMiddlewareDispatch:
    """Cover dispatch() method lines 41-62."""

    def _make_request(self, path: str, method: str = "GET"):
        req = MagicMock()
        req.url = SimpleNamespace(path=path)
        req.method = method
        req.state = SimpleNamespace()
        req.client = SimpleNamespace(host="127.0.0.1")
        req.headers = {"user-agent": "test-agent"}
        req.app = MagicMock()
        return req

    def test_dispatch_public_path_health(self):
        """Public paths skip auth and return immediately."""
        mw = _make_auth_middleware()
        req = self._make_request("/health")
        resp = MagicMock(status_code=200)
        call_next = AsyncMock(return_value=resp)

        result = _run(mw.dispatch(req, call_next))
        assert result is resp
        call_next.assert_awaited_once_with(req)

    def test_dispatch_public_path_docs(self):
        mw = _make_auth_middleware()
        req = self._make_request("/docs/something")
        resp = MagicMock(status_code=200)
        call_next = AsyncMock(return_value=resp)

        result = _run(mw.dispatch(req, call_next))
        assert result is resp

    def test_dispatch_public_path_login(self):
        mw = _make_auth_middleware()
        req = self._make_request("/api/v1/auth/login")
        resp = MagicMock(status_code=200)
        call_next = AsyncMock(return_value=resp)

        result = _run(mw.dispatch(req, call_next))
        assert result is resp

    def test_dispatch_auth_disabled(self):
        """When AUTH_ENABLED=false, sets anonymous user."""
        from src.auth.dependencies import _ANONYMOUS_USER

        mw = _make_auth_middleware()
        req = self._make_request("/api/v1/search", "POST")
        resp = MagicMock(status_code=200)
        call_next = AsyncMock(return_value=resp)

        with patch("src.auth.middleware.AUTH_ENABLED", False):
            result = _run(mw.dispatch(req, call_next))
            assert result is resp
            assert req.state.auth_user is _ANONYMOUS_USER

    def test_dispatch_auth_enabled_no_log_on_error_status(self):
        """When auth enabled + response 4xx, skip activity logging."""
        mw = _make_auth_middleware()
        req = self._make_request("/api/v1/search", "POST")
        resp = MagicMock(status_code=401)
        call_next = AsyncMock(return_value=resp)

        with patch("src.auth.middleware.AUTH_ENABLED", True):
            result = _run(mw.dispatch(req, call_next))
            assert result is resp
            assert req.state.auth_user is None

    def test_dispatch_auth_enabled_logs_activity(self):
        """When auth enabled + 2xx, calls _maybe_log_activity."""
        mw = _make_auth_middleware()
        mw._maybe_log_activity = AsyncMock()
        req = self._make_request("/api/v1/search", "POST")
        resp = MagicMock(status_code=200)
        call_next = AsyncMock(return_value=resp)

        with patch("src.auth.middleware.AUTH_ENABLED", True):
            result = _run(mw.dispatch(req, call_next))
            assert result is resp
            mw._maybe_log_activity.assert_awaited_once()


class TestMaybeLogActivityExtended:
    """Additional dispatch-level _maybe_log_activity branches."""

    def test_log_activity_with_auth_service_logs(self):
        """Full path: user + matching activity + auth_service present."""
        mw = _make_auth_middleware()
        user = SimpleNamespace(sub="user-123")

        req = MagicMock()
        req.state = SimpleNamespace(auth_user=user)
        req.method = "POST"
        req.client = SimpleNamespace(host="10.0.0.1")
        req.headers = {"user-agent": "Mozilla/5.0"}

        auth_svc = AsyncMock()
        app_state = MagicMock()
        app_state.get.return_value = auth_svc
        req.app = MagicMock()
        req.app.state._app_state = app_state

        _run(mw._maybe_log_activity(req, "/api/v1/search", 42.5))
        auth_svc.log_activity.assert_awaited_once()
        call_kw = auth_svc.log_activity.call_args.kwargs
        assert call_kw["user_id"] == "user-123"
        assert call_kw["activity_type"] == "search"

    def test_log_activity_no_app_state(self):
        """No _app_state on request.app.state — should not raise."""
        mw = _make_auth_middleware()
        user = SimpleNamespace(sub="user-123")

        req = MagicMock()
        req.state = SimpleNamespace(auth_user=user)
        req.method = "POST"
        req.client = SimpleNamespace(host="10.0.0.1")
        req.headers = {"user-agent": "test"}
        req.app = MagicMock()
        req.app.state._app_state = None

        # Should not raise
        _run(mw._maybe_log_activity(req, "/api/v1/search", 10.0))

    def test_log_activity_auth_service_raises(self):
        """auth_service.log_activity raises — caught silently."""
        mw = _make_auth_middleware()
        user = SimpleNamespace(sub="user-456")

        req = MagicMock()
        req.state = SimpleNamespace(auth_user=user)
        req.method = "POST"
        req.client = SimpleNamespace(host="10.0.0.1")
        req.headers = {"user-agent": "test"}

        auth_svc = AsyncMock()
        auth_svc.log_activity = AsyncMock(
            side_effect=RuntimeError("db down")
        )
        app_state = MagicMock()
        app_state.get.return_value = auth_svc
        req.app = MagicMock()
        req.app.state._app_state = app_state

        # Should not raise
        _run(mw._maybe_log_activity(req, "/api/v1/search", 10.0))

    def test_log_activity_no_client(self):
        """request.client is None — ip_address should be None."""
        mw = _make_auth_middleware()
        user = SimpleNamespace(sub="user-789")

        req = MagicMock()
        req.state = SimpleNamespace(auth_user=user)
        req.method = "POST"
        req.client = None
        req.headers = {"user-agent": "bot"}

        auth_svc = AsyncMock()
        app_state = MagicMock()
        app_state.get.return_value = auth_svc
        req.app = MagicMock()
        req.app.state._app_state = app_state

        _run(mw._maybe_log_activity(req, "/api/v1/search", 5.0))
        call_kw = auth_svc.log_activity.call_args.kwargs
        assert call_kw["ip_address"] is None

    def test_log_activity_kb_patch(self):
        """PATCH /kb path -> edit activity."""
        mw = _make_auth_middleware()
        user = SimpleNamespace(sub="u1")
        req = MagicMock()
        req.state = SimpleNamespace(auth_user=user)
        req.method = "PATCH"
        req.client = SimpleNamespace(host="1.2.3.4")
        req.headers = {"user-agent": "x"}

        auth_svc = AsyncMock()
        app_state = MagicMock()
        app_state.get.return_value = auth_svc
        req.app = MagicMock()
        req.app.state._app_state = app_state

        _run(mw._maybe_log_activity(req, "/api/v1/kb/abc", 1.0))
        call_kw = auth_svc.log_activity.call_args.kwargs
        assert call_kw["activity_type"] == "edit"
        assert call_kw["resource_type"] == "kb"

    def test_log_activity_glossary_patch(self):
        """PATCH /glossary path -> edit activity."""
        mw = _make_auth_middleware()
        user = SimpleNamespace(sub="u2")
        req = MagicMock()
        req.state = SimpleNamespace(auth_user=user)
        req.method = "PATCH"
        req.client = SimpleNamespace(host="1.2.3.4")
        req.headers = {"user-agent": "x"}

        auth_svc = AsyncMock()
        app_state = MagicMock()
        app_state.get.return_value = auth_svc
        req.app = MagicMock()
        req.app.state._app_state = app_state

        _run(mw._maybe_log_activity(req, "/api/v1/glossary/t1", 1.0))
        call_kw = auth_svc.log_activity.call_args.kwargs
        assert call_kw["activity_type"] == "edit"
        assert call_kw["resource_type"] == "glossary"


# ===================================================================
# 3. src/pipelines/cv/text_shape_mapper.py — full mapper coverage
# ===================================================================

def _make_shape(
    bbox: tuple[int, int, int, int],
    area: float,
    contour=None,
):
    """Build a DetectedShape with a rectangular contour."""
    from src.pipelines.cv.models import DetectedShape

    x, y, w, h = bbox
    if contour is None:
        contour = np.array([
            [[x, y]],
            [[x + w, y]],
            [[x + w, y + h]],
            [[x, y + h]],
        ], dtype=np.int32)
    return DetectedShape(
        shape_type="rectangle",
        bbox=bbox,
        center=(x + w / 2, y + h / 2),
        area=area,
        contour=contour,
    )


def _make_ocr_box(text: str, cx: float, cy: float):
    from src.pipelines.cv.models import OCRBox

    return OCRBox(
        text=text,
        polygon=[[cx - 5, cy - 5], [cx + 5, cy - 5],
                 [cx + 5, cy + 5], [cx - 5, cy + 5]],
        confidence=0.95,
        center=(cx, cy),
    )


class TestTextShapeMapper:
    """Cover TextShapeMapper.map + helpers (lines 21-91)."""

    def test_empty_inputs(self):
        from src.pipelines.cv.text_shape_mapper import TextShapeMapper

        mapper = TextShapeMapper()
        shape_texts, unmapped = mapper.map([], [])
        assert shape_texts == {}
        assert unmapped == []

    def test_whitespace_only_text_skipped(self):
        from src.pipelines.cv.text_shape_mapper import TextShapeMapper

        mapper = TextShapeMapper()
        box = _make_ocr_box("   ", 50, 50)
        shape = _make_shape((0, 0, 100, 100), 10000)
        shape_texts, unmapped = mapper.map([box], [shape])
        assert shape_texts == {}
        assert unmapped == []

    def test_text_inside_shape(self):
        from src.pipelines.cv.text_shape_mapper import TextShapeMapper

        mapper = TextShapeMapper()
        shape = _make_shape((0, 0, 200, 200), 40000)
        box = _make_ocr_box("Hello", 100, 100)
        shape_texts, unmapped = mapper.map([box], [shape])
        assert 0 in shape_texts
        assert "Hello" in shape_texts[0]
        assert unmapped == []

    def test_text_outside_all_shapes(self):
        from src.pipelines.cv.text_shape_mapper import TextShapeMapper

        mapper = TextShapeMapper()
        shape = _make_shape((0, 0, 50, 50), 2500)
        box = _make_ocr_box("Far away", 500, 500)
        shape_texts, unmapped = mapper.map([box], [shape])
        assert shape_texts == {}
        assert "Far away" in unmapped

    def test_text_maps_to_smallest_shape(self):
        from src.pipelines.cv.text_shape_mapper import TextShapeMapper

        mapper = TextShapeMapper()
        big = _make_shape((0, 0, 300, 300), 90000)
        small = _make_shape((40, 40, 60, 60), 3600)
        box = _make_ocr_box("Inner", 70, 70)
        shape_texts, unmapped = mapper.map([box], [big, small])
        # Should map to index 1 (smaller shape)
        assert 1 in shape_texts
        assert "Inner" in shape_texts[1]

    def test_bbox_margin_fallback(self):
        """Text just outside polygon but within 10px margin of bbox."""
        from src.pipelines.cv.text_shape_mapper import TextShapeMapper

        mapper = TextShapeMapper()
        shape = _make_shape((100, 100, 50, 50), 2500)
        # Point at (95, 125) — outside polygon but within 10px margin
        box = _make_ocr_box("Margin", 95, 125)
        shape_texts, unmapped = mapper.map([box], [shape])
        assert 0 in shape_texts
        assert "Margin" in shape_texts[0]

    def test_multiple_texts_same_shape(self):
        from src.pipelines.cv.text_shape_mapper import TextShapeMapper

        mapper = TextShapeMapper()
        shape = _make_shape((0, 0, 200, 200), 40000)
        b1 = _make_ocr_box("Line1", 50, 50)
        b2 = _make_ocr_box("Line2", 60, 60)
        shape_texts, unmapped = mapper.map([b1, b2], [shape])
        assert len(shape_texts[0]) == 2

    def test_point_in_shape_polygon_test(self):
        """Directly test _point_in_shape for inside polygon."""
        from src.pipelines.cv.text_shape_mapper import TextShapeMapper

        mapper = TextShapeMapper()
        shape = _make_shape((10, 10, 100, 100), 10000)
        assert mapper._point_in_shape((60, 60), shape) is True

    def test_point_in_shape_outside_everything(self):
        from src.pipelines.cv.text_shape_mapper import TextShapeMapper

        mapper = TextShapeMapper()
        shape = _make_shape((10, 10, 20, 20), 400)
        # Way outside bbox + margin
        assert mapper._point_in_shape((500, 500), shape) is False

    def test_find_smallest_no_shapes(self):
        from src.pipelines.cv.text_shape_mapper import TextShapeMapper

        mapper = TextShapeMapper()
        assert mapper._find_smallest_containing_shape((50, 50), []) is None


# ===================================================================
# 4. src/stores/neo4j/indexer.py — ensure_indexes
# ===================================================================

class TestEnsureIndexes:
    """Cover ensure_indexes (lines 22-49)."""

    def test_ensure_indexes_success_no_errors(self):
        from src.stores.neo4j.indexer import ensure_indexes

        mock_client = AsyncMock()
        schema_result = {
            "constraints_created": 3,
            "indexes_created": 2,
            "fulltext_indexes_created": 5,
            "errors": [],
        }
        with patch(
            "src.stores.neo4j.indexer.apply_schema",
            new_callable=AsyncMock,
            return_value=schema_result,
        ):
            result = _run(ensure_indexes(mock_client))
            assert result["constraints_created"] == 3
            assert result["indexes_created"] == 2
            assert result["fulltext_indexes_created"] == 5
            assert result["errors"] == []

    def test_ensure_indexes_with_errors(self):
        from src.stores.neo4j.indexer import ensure_indexes

        mock_client = AsyncMock()
        schema_result = {
            "constraints_created": 1,
            "indexes_created": 0,
            "fulltext_indexes_created": 0,
            "errors": ["Constraint error: something"],
        }
        with patch(
            "src.stores.neo4j.indexer.apply_schema",
            new_callable=AsyncMock,
            return_value=schema_result,
        ):
            result = _run(ensure_indexes(mock_client))
            assert len(result["errors"]) == 1

    def test_ensure_indexes_apply_schema_raises(self):
        from src.stores.neo4j.indexer import ensure_indexes

        mock_client = AsyncMock()
        with patch(
            "src.stores.neo4j.indexer.apply_schema",
            new_callable=AsyncMock,
            side_effect=RuntimeError("neo4j down"),
        ):
            result = _run(ensure_indexes(mock_client))
            assert result["constraints_created"] == 0
            assert "neo4j down" in result["errors"][0]
