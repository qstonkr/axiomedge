"""Health check service for Knowledge Dashboard Local."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import httpx

from services import config as cfg
from services.logging_config import get_logger

logger = get_logger(__name__)

HEALTH_CHECK_TIMEOUT = int(os.getenv("KNOWLEDGE_DASHBOARD_HEALTH_TIMEOUT", "5"))


def check_health() -> dict[str, Any]:
    """Run health checks against upstream dependencies."""
    api_ok = _check_api()
    neo4j_ok = _check_neo4j()
    qdrant_ok = _check_qdrant()

    if api_ok and neo4j_ok and qdrant_ok:
        status = "healthy"
    elif api_ok:
        status = "degraded"
    else:
        status = "unhealthy"

    return {
        "status": status,
        "checks": {
            "api": api_ok,
            "neo4j": neo4j_ok,
            "qdrant": qdrant_ok,
        },
        "version": os.getenv("BUILD_VERSION", "local"),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def _check_api() -> bool:
    """Check local FastAPI server reachability."""
    try:
        with httpx.Client(
            base_url=cfg.DASHBOARD_API_URL,
            timeout=HEALTH_CHECK_TIMEOUT,
        ) as client:
            resp = client.get("/api/v1/admin/kb", params={"page_size": "1"})
            return resp.status_code < 500
    except Exception:  # noqa: BLE001
        logger.debug("Health check: API unreachable at %s", cfg.DASHBOARD_API_URL)
        return False


def _check_neo4j() -> bool:
    """Check Neo4j connectivity."""
    try:
        from neo4j import GraphDatabase

        driver = GraphDatabase.driver(
            cfg.NEO4J_URI,
            auth=(cfg.NEO4J_USER, cfg.NEO4J_PASSWORD),
        )
        try:
            driver.verify_connectivity()
            return True
        finally:
            driver.close()
    except ImportError:
        logger.debug("Health check: neo4j package not installed")
        return False
    except Exception:  # noqa: BLE001
        logger.debug("Health check: Neo4j unreachable at %s", cfg.NEO4J_URI)
        return False


def _check_qdrant() -> bool:
    """Check Qdrant vector DB reachability."""
    qdrant_url = getattr(cfg, "QDRANT_URL", None)
    if not qdrant_url:
        return False
    try:
        with httpx.Client(timeout=HEALTH_CHECK_TIMEOUT) as client:
            resp = client.get(f"{qdrant_url}/healthz")
            return resp.status_code < 500
    except Exception:  # noqa: BLE001
        logger.debug("Health check: Qdrant unreachable at %s", qdrant_url)
        return False
