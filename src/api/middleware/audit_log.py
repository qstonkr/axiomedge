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
            task = asyncio.create_task(_write_audit(request, audit))
            # GC 방지 — task 참조 보존 + 완료 시 자동 제거
            try:
                state = request.app.state
                bg = getattr(state, "_audit_bg_tasks", None)
                if bg is None:
                    bg = set()
                    state._audit_bg_tasks = bg
                bg.add(task)
                task.add_done_callback(bg.discard)
            except (AttributeError, RuntimeError):
                # state 미사용 환경 — 최소한 exception 은 logger 로
                task.add_done_callback(_log_task_exc)
        return response


def _log_task_exc(task: asyncio.Task) -> None:
    """Background task 의 silent exception 검출."""
    try:
        exc = task.exception()
    except (asyncio.CancelledError, asyncio.InvalidStateError):
        return
    if exc is not None:
        logger.warning("Audit background task raised: %r", exc)


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
        event_type = audit.get("event_type") or "unknown"
        # P0 — actor=_system 인 mutating event 는 인증 우회 가능성. event_type 에
        # `unauth.` prefix 를 붙이고 metric counter 로 노출.
        if actor == "_system" and not str(event_type).startswith("system."):
            event_type = f"unauth.{event_type}"
            try:
                from src.api.routes.metrics import inc as metrics_inc
                metrics_inc("audit_unauthenticated", 1)
            except (ImportError, AttributeError):
                pass
        await repo.write(
            knowledge_id=audit.get("knowledge_id") or "_unknown",
            event_type=event_type,
            actor=actor,
            details=audit.get("details") or {},
        )
    except (RuntimeError, OSError, AttributeError) as e:
        logger.warning("Audit log write task failed: %s", e)


def _resolve_actor(request: Request) -> str:
    """Resolve the user actor from middleware-populated state.

    P2-6 contract:
      - ``AuthMiddleware`` sets ``request.state.auth_user`` to a user object
        with attributes ``sub`` (preferred) or ``user_id``. Anonymous users
        have ``sub == "anonymous"``.
      - Some legacy paths use ``request.state.user``; we accept both.
      - Returns ``"_system"`` as a sentinel meaning "auth middleware was
        bypassed" — caller (audit middleware) flags such events with a
        ``unauth.`` prefix so they show up in the
        ``audit_unauthenticated_total`` Prometheus metric.
    """
    for attr in ("auth_user", "user"):
        try:
            user = getattr(request.state, attr, None)
        except (AttributeError, RuntimeError):
            user = None
        if user is None:
            continue
        # Try common identifier attributes in priority order.
        for ident_attr in ("sub", "user_id", "id", "email"):
            value = getattr(user, ident_attr, None)
            if value:
                return str(value)
        # Fallback to repr — better than _system in audit trail.
        return str(user)[:100]
    return "_system"
