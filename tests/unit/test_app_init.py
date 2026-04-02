"""Unit tests for src/api/app.py — app creation, state, formatter, init orchestration."""

from __future__ import annotations

import asyncio
import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# AppState
# ---------------------------------------------------------------------------

class TestAppState:
    def test_get_state_returns_app_state(self):
        from src.api.app import _get_state
        from src.api.state import AppState

        state = _get_state()
        assert isinstance(state, AppState)

    def test_state_dict_access(self):
        from src.api.state import AppState

        state = AppState()
        state["embedder"] = "mock_embedder"
        assert state["embedder"] == "mock_embedder"
        assert state.get("embedder") == "mock_embedder"
        assert state.get("nonexistent") is None
        assert state.get("nonexistent", "default") == "default"

    def test_state_contains(self):
        from src.api.state import AppState

        state = AppState()
        assert "embedder" not in state
        state["embedder"] = "mock"
        assert "embedder" in state

    def test_state_setdefault(self):
        from src.api.state import AppState

        state = AppState()
        val = state.setdefault("embedder", "default_val")
        assert val == "default_val"
        assert state["embedder"] == "default_val"

        # Calling again returns existing value
        val2 = state.setdefault("embedder", "other")
        assert val2 == "default_val"

    def test_state_getitem_keyerror(self):
        from src.api.state import AppState

        state = AppState()
        with pytest.raises(KeyError):
            _ = state["nonexistent_field_xyz"]


# ---------------------------------------------------------------------------
# JSONFormatter
# ---------------------------------------------------------------------------

class TestJSONFormatter:
    def test_format_produces_valid_json(self):
        from src.api.app import JSONFormatter

        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="hello %s",
            args=("world",),
            exc_info=None,
        )
        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["level"] == "INFO"
        assert parsed["message"] == "hello world"
        assert "timestamp" in parsed
        assert "module" in parsed
        assert "function" in parsed

    def test_format_with_exception(self):
        from src.api.app import JSONFormatter

        formatter = JSONFormatter()
        try:
            raise ValueError("test error")
        except ValueError:
            import sys

            record = logging.LogRecord(
                name="test",
                level=logging.ERROR,
                pathname="test.py",
                lineno=1,
                msg="failed",
                args=(),
                exc_info=sys.exc_info(),
            )
        output = formatter.format(record)
        parsed = json.loads(output)
        assert "exception" in parsed
        assert "ValueError" in parsed["exception"]


# ---------------------------------------------------------------------------
# FastAPI app object
# ---------------------------------------------------------------------------

class TestAppCreation:
    def test_app_exists_and_has_routes(self):
        from src.api.app import app

        assert app.title == "Knowledge Local"
        # Verify some routes are registered
        route_paths = [r.path for r in app.routes]
        assert "/health" in route_paths

    def test_app_has_cors_middleware(self):
        from src.api.app import app

        middleware_classes = [type(m).__name__ for m in app.user_middleware]
        # CORSMiddleware is added via add_middleware
        assert any("CORS" in str(m) for m in app.user_middleware)


# ---------------------------------------------------------------------------
# _init_services orchestrator (mock all sub-inits)
# ---------------------------------------------------------------------------

class TestInitServicesOrchestrator:
    def test_init_services_calls_all_sub_inits(self):
        """Verify _init_services calls each _init_* function in order."""
        mock_settings = MagicMock()
        mock_settings.database.database_url = "postgresql://test"
        mock_settings.neo4j.enabled = False

        with (
            patch("src.api.app._init_database", new_callable=AsyncMock) as m_db,
            patch("src.api.app._init_cache", new_callable=AsyncMock) as m_cache,
            patch("src.api.app._init_dedup", new_callable=AsyncMock) as m_dedup,
            patch("src.api.app._init_vectordb", new_callable=AsyncMock) as m_vec,
            patch("src.api.app._init_graph", new_callable=AsyncMock) as m_graph,
            patch("src.api.app._init_embedding", new_callable=AsyncMock) as m_emb,
            patch("src.api.app._init_llm", new_callable=AsyncMock) as m_llm,
            patch("src.api.app._init_search_services", new_callable=AsyncMock) as m_search,
            patch("src.api.app._init_auth", new_callable=AsyncMock) as m_auth,
            patch("src.config.get_settings", return_value=mock_settings),
        ):
            from src.api.app import _init_services

            asyncio.run(_init_services())

            m_db.assert_called_once()
            m_cache.assert_called_once()
            m_dedup.assert_called_once()
            m_vec.assert_called_once()
            m_graph.assert_called_once()
            m_emb.assert_called_once()
            m_llm.assert_called_once()
            m_search.assert_called_once()
            m_auth.assert_called_once()

    def test_init_services_continues_on_db_failure(self):
        """If _init_database raises, other services still initialize."""
        mock_settings = MagicMock()

        with (
            patch("src.api.app._init_database", new_callable=AsyncMock, side_effect=Exception("DB down")) as m_db,
            patch("src.api.app._init_cache", new_callable=AsyncMock) as m_cache,
            patch("src.api.app._init_dedup", new_callable=AsyncMock) as m_dedup,
            patch("src.api.app._init_vectordb", new_callable=AsyncMock) as m_vec,
            patch("src.api.app._init_graph", new_callable=AsyncMock) as m_graph,
            patch("src.api.app._init_embedding", new_callable=AsyncMock) as m_emb,
            patch("src.api.app._init_llm", new_callable=AsyncMock) as m_llm,
            patch("src.api.app._init_search_services", new_callable=AsyncMock) as m_search,
            patch("src.api.app._init_auth", new_callable=AsyncMock) as m_auth,
            patch("src.config.get_settings", return_value=mock_settings),
        ):
            from src.api.app import _init_services

            asyncio.run(_init_services())

            m_db.assert_called_once()
            # All other inits should still be called
            m_cache.assert_called_once()
            m_emb.assert_called_once()
            m_search.assert_called_once()


# ---------------------------------------------------------------------------
# Lifespan context manager
# ---------------------------------------------------------------------------

class TestLifespan:
    def test_lifespan_sets_app_state(self):
        """Verify lifespan attaches _app_state to app.state."""
        from fastapi import FastAPI

        from src.api.app import lifespan

        test_app = FastAPI()

        async def _run():
            with (
                patch("src.api.app._init_services", new_callable=AsyncMock),
                patch("src.api.app._shutdown_services", new_callable=AsyncMock),
            ):
                async with lifespan(test_app):
                    assert hasattr(test_app.state, "_app_state")

        asyncio.run(_run())
