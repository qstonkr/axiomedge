"""RequestIDMiddleware — PR-7 (G).

- 헤더 없으면 새 ID 생성 + 응답 헤더 echo
- 헤더 있으면 그대로 사용
- 핸들러가 ContextVar 통해 trace_id 읽을 수 있음
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.middleware.request_id import RequestIDMiddleware
from src.core.logging import get_trace_id


def _build_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(RequestIDMiddleware)

    @app.get("/echo")
    async def echo() -> dict[str, str]:
        return {"trace_id": get_trace_id()}

    return app


class TestRequestIDMiddleware:
    def test_generates_new_id_when_header_absent(self):
        client = TestClient(_build_app())
        resp = client.get("/echo")
        assert resp.status_code == 200
        rid = resp.headers.get("X-Request-ID")
        assert rid and len(rid) == 16
        assert resp.json()["trace_id"] == rid

    def test_echoes_incoming_header(self):
        client = TestClient(_build_app())
        resp = client.get("/echo", headers={"X-Request-ID": "client-trace-123"})
        assert resp.headers["X-Request-ID"] == "client-trace-123"
        assert resp.json()["trace_id"] == "client-trace-123"

    def test_each_request_isolated(self):
        client = TestClient(_build_app())
        a = client.get("/echo").headers["X-Request-ID"]
        b = client.get("/echo").headers["X-Request-ID"]
        assert a != b
