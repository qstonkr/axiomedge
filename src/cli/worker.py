"""CLI: Run Knowledge Local in API or Worker mode.

API mode (default): All routes registered (search, RAG, ingestion, admin, etc.)
Worker mode: Ingestion and pipeline routes only (no search/RAG/glossary).

Usage:
    python -m cli.worker                     # API mode (default)
    python -m cli.worker --mode worker       # Worker mode (ingestion only)
    python -m cli.worker --mode api --port 8001
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncIterator

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()


# ---------------------------------------------------------------------------
# Structured JSON logging (shared with app.py)
# ---------------------------------------------------------------------------
class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
        }
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry, ensure_ascii=False)


_handler = logging.StreamHandler()
_handler.setFormatter(JSONFormatter())
logging.root.handlers = [_handler]
logging.root.setLevel(logging.INFO)

logger = logging.getLogger(__name__)


def _register_routes(app: FastAPI, mode: str) -> None:
    """Register routes based on mode.

    API mode: all routes.
    Worker mode: health + ingest + pipeline + admin + kb + jobs + metrics only.
    """
    from src.api.routes import (
        health, ingest, admin, kb, pipeline, quality,
        feedback, data_sources, whitelist,
    )
    from src.api.routes import metrics as metrics_route
    from src.api.routes import jobs as jobs_route

    # Always registered (both modes)
    app.include_router(health.router)
    app.include_router(ingest.router)
    app.include_router(pipeline.router)
    app.include_router(admin.router)
    app.include_router(kb.router)
    app.include_router(kb.admin_router)
    app.include_router(quality.router)
    app.include_router(feedback.admin_router)
    app.include_router(feedback.knowledge_router)
    app.include_router(data_sources.router)
    app.include_router(whitelist.router)
    app.include_router(metrics_route.router)
    app.include_router(jobs_route.router)

    if mode == "api":
        # Search, RAG, glossary, ownership, analytics, auth - API mode only
        from src.api.routes import (
            search, glossary, ownership, search_analytics, rag,
        )
        from src.api.routes import search_groups
        from src.api.routes import auth as auth_routes

        app.include_router(search.router)
        app.include_router(glossary.router)
        app.include_router(ownership.admin_router)
        app.include_router(ownership.knowledge_router)
        app.include_router(search_analytics.router)
        app.include_router(rag.knowledge_router)
        app.include_router(rag.rag_query_router)
        app.include_router(search_groups.router)
        app.include_router(auth_routes.router)

        logger.info("API mode: all routes registered")
    else:
        logger.info("Worker mode: ingestion/pipeline routes only")


def create_app(mode: str = "api") -> FastAPI:
    """Create a FastAPI application for the given mode."""
    from src.api.app import _init_services, _shutdown_services

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        await _init_services()
        yield
        await _shutdown_services()

    title = "Knowledge Local" if mode == "api" else "Knowledge Local Worker"
    application = FastAPI(
        title=title,
        description=f"Knowledge Management System ({mode} mode)",
        version="0.1.0",
        lifespan=lifespan,
    )

    _register_routes(application, mode)

    if mode == "api":
        from src.auth.middleware import AuthMiddleware
        application.add_middleware(AuthMiddleware)

    # Production-safe CORS — APP_ENV=production requires explicit origins
    _is_prod = os.getenv("APP_ENV", "development").lower() == "production"
    _cors_default = "" if _is_prod else "*"
    cors_origins = [o.strip() for o in os.getenv("CORS_ORIGINS", _cors_default).split(",") if o.strip()]
    if _is_prod and (not cors_origins or "*" in cors_origins):
        raise RuntimeError(
            "APP_ENV=production requires explicit CORS_ORIGINS (no wildcard). "
            "Example: CORS_ORIGINS=https://app.example.com"
        )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins or ["*"],
        allow_credentials=True if cors_origins and "*" not in cors_origins else False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    return application


def main() -> None:
    parser = argparse.ArgumentParser(description="Knowledge Local Server")
    parser.add_argument(
        "--mode",
        choices=["api", "worker"],
        default="api",
        help="Run mode: 'api' (full routes) or 'worker' (ingestion only)",
    )
    parser.add_argument("--host", default="0.0.0.0", help="Bind host")
    parser.add_argument("--port", type=int, default=8000, help="Bind port")
    parser.add_argument("--workers", type=int, default=1, help="Number of workers")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload")

    args = parser.parse_args()

    # Set mode as env var so lifespan can read it if needed
    os.environ["KNOWLEDGE_SERVER_MODE"] = args.mode

    app = create_app(args.mode)

    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        workers=args.workers,
        log_level="info",
    )


if __name__ == "__main__":
    main()
