"""Standardized error responses and global exception handlers.

All API errors follow a consistent JSON format:
    {"detail": "Human-readable message", "error_code": "MACHINE_CODE", "status_code": 400}

Usage in routes:
    raise api_error(400, "Invalid KB ID", "INVALID_KB_ID")
    raise not_found("KB", kb_id)
    raise service_unavailable("Embedding provider")
"""

from __future__ import annotations

import logging

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Standard error response model (for OpenAPI docs)
# ---------------------------------------------------------------------------


class ErrorResponse(BaseModel):
    """Standard error response returned by all API endpoints."""

    detail: str
    error_code: str = "UNKNOWN_ERROR"
    status_code: int = 500


# ---------------------------------------------------------------------------
# Error factory helpers
# ---------------------------------------------------------------------------


def api_error(
    status_code: int, detail: str, error_code: str = "API_ERROR",
) -> HTTPException:
    """Create an HTTPException with standardized error body."""
    return HTTPException(
        status_code=status_code,
        detail={"detail": detail, "error_code": error_code, "status_code": status_code},
    )


def not_found(resource: str, resource_id: str = "") -> HTTPException:
    """404 Not Found helper."""
    msg = f"{resource} not found" + (f": {resource_id}" if resource_id else "")
    return api_error(404, msg, "NOT_FOUND")


def service_unavailable(service: str) -> HTTPException:
    """503 Service Unavailable helper."""
    return api_error(503, f"{service} not initialized", "SERVICE_UNAVAILABLE")


def bad_request(detail: str) -> HTTPException:
    """400 Bad Request helper."""
    return api_error(400, detail, "BAD_REQUEST")


# ---------------------------------------------------------------------------
# Global exception handlers (register in app.py)
# ---------------------------------------------------------------------------


async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """Normalize all HTTPException responses to standard format."""
    detail = exc.detail

    # Already structured (from api_error helper)
    if isinstance(detail, dict) and "error_code" in detail:
        return JSONResponse(status_code=exc.status_code, content=detail)

    # Plain string detail (from raise HTTPException(status_code=400, detail="..."))
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "detail": str(detail),
            "error_code": "HTTP_ERROR",
            "status_code": exc.status_code,
        },
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch-all for unhandled exceptions — return 500 with safe message."""
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "detail": "Internal server error",
            "error_code": "INTERNAL_ERROR",
            "status_code": 500,
        },
    )
