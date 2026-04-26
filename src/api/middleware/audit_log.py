"""Audit log middleware — PR-12 (J).

라우트 핸들러가 ``request.state.audit = {...}`` 으로 메타데이터를 세팅해 두면
응답이 종료된 직후 비동기 task 로 audit_log 에 영속화한다.

- 핸들러 침투를 최소화하기 위해 ``request.state.audit`` 미세팅 시 no-op
- audit 기록 실패가 응답 자체를 깨뜨리지 않도록 fail-safe wrapper
- 실제 write 는 ``asyncio.create_task`` 로 fire-and-forget
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger(__name__)


class AuditLogMiddleware(BaseHTTPMiddleware):
    """Optional audit logging — only fires when handler opted in."""

    async def dispatch(  # type: ignore[override]
        self,
        request: Request,
        call_next,
    ) -> Response:
        response = await call_next(request)

        audit: dict[str, Any] | None = None
        try:
            audit = getattr(request.state, "audit", None)
        except (AttributeError, RuntimeError):
            audit = None

        # 4xx/5xx 응답은 audit 기록 skip — 의미 있는 mutating 만 남김
        if audit and 200 <= response.status_code < 300:
            asyncio.create_task(_write_audit(request, audit))
        return response


async def _write_audit(request: Request, audit: dict[str, Any]) -> None:
    """Background task — failure 가 미들웨어 흐름에 전파되지 않게 격리."""
    try:
        repo = None
        # state lookup — app.state 또는 _state 양쪽 모두 시도
        app = request.app
        try:
            repo = getattr(app.state, "audit_log_repo", None)
        except AttributeError:
            repo = None
        if repo is None:
            try:
                from src.api.app import _get_state
                repo = _get_state().get("audit_log_repo")
            except (ImportError, AttributeError, RuntimeError):
                repo = None
        if repo is None:
            return

        actor = audit.get("actor") or _resolve_actor(request)
        await repo.write(
            knowledge_id=audit.get("knowledge_id") or "_unknown",
            event_type=audit.get("event_type") or "unknown",
            actor=actor,
            details=audit.get("details") or {},
        )
    except (RuntimeError, OSError, AttributeError) as e:
        logger.warning("Audit log write task failed: %s", e)


def _resolve_actor(request: Request) -> str:
    """user 미들웨어가 request.state.user 를 세팅했으면 그 user_id 사용."""
    try:
        user = getattr(request.state, "user", None)
        if user is not None:
            return str(getattr(user, "user_id", "")) or str(user)
    except (AttributeError, RuntimeError):
        pass
    return "_system"
