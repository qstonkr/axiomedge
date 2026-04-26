"""Request-ID middleware — PR-7 (G).

들어오는 요청의 ``X-Request-ID`` 헤더를 ``trace_id`` ContextVar 에 주입하여
모든 로그 라인에 자동으로 trace_id 가 포함되도록 한다. 헤더가 없으면 16-char
랜덤 id 를 생성하고 응답에도 echo 한다.

OpenTelemetry SpanContext 가 활성이면 traceparent 의 trace_id 를 우선 사용
(분산 추적과 동일 id 공유).
"""

from __future__ import annotations

import secrets

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from src.core.logging import set_trace_id, trace_id_var


class RequestIDMiddleware(BaseHTTPMiddleware):
    """X-Request-ID 회수 + ContextVar 주입 + 응답 헤더 echo."""

    HEADER = "X-Request-ID"

    async def dispatch(  # type: ignore[override]
        self,
        request: Request,
        call_next,
    ) -> Response:
        incoming = request.headers.get(self.HEADER, "").strip()
        rid = incoming or self._otel_trace_id() or secrets.token_hex(8)

        token = set_trace_id(rid)
        try:
            response = await call_next(request)
        finally:
            trace_id_var.reset(token)
        response.headers[self.HEADER] = rid
        return response

    @staticmethod
    def _otel_trace_id() -> str:
        """활성 OTel SpanContext 의 trace_id (16자) — 미설치 시 빈 문자열."""
        try:
            from opentelemetry import trace as _trace

            span = _trace.get_current_span()
            ctx = span.get_span_context() if span else None
            if ctx and ctx.is_valid:
                return format(ctx.trace_id, "032x")[:16]
        except (ImportError, AttributeError, ValueError, RuntimeError):
            pass
        return ""
