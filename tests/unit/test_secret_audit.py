"""Phase 3 — secret event audit log + token value 누설 검증.

핵심 보장:
1. ``ActivityLogger.log_secret_event`` 가 ``UserActivityLogModel`` 행 생성.
2. ``details`` JSONB 에 token value 자체가 절대 포함되지 않음.
3. 라우트의 _store_secret/_delete_secret 가 audit 호출 (성공/실패 모두).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# ActivityLogger.log_secret_event — token value 안 들어감
# ---------------------------------------------------------------------------


class TestLogSecretEvent:
    @pytest.mark.asyncio
    async def test_creates_activity_row_with_correct_fields(self):
        from src.auth.activity_logger import ActivityLogger

        # in-memory capture — log_activity 의 model 인자 검증
        captured: list[dict] = []

        class _CapturingLogger(ActivityLogger):
            async def log_activity(self, **kwargs):
                captured.append(kwargs)

        log = _CapturingLogger(session_factory=None)  # session 사용 안 함
        await log.log_secret_event(
            actor_user_id="user-1",
            action="secret_update",
            source_id="ds-1",
            organization_id="org-1",
            success=True,
        )

        assert len(captured) == 1
        ev = captured[0]
        assert ev["activity_type"] == "secret_update"
        assert ev["resource_type"] == "data_source_secret"
        assert ev["resource_id"] == "ds-1"
        assert ev["details"] == {
            "organization_id": "org-1",
            "success": True,
            "error": None,
        }
        # 가장 중요: token value 흔적 없음
        details_str = str(ev)
        for forbidden in ("ghp_", "Bearer ", "secret-value", "password"):
            assert forbidden not in details_str

    @pytest.mark.asyncio
    async def test_failure_records_error_message(self):
        from src.auth.activity_logger import ActivityLogger

        captured: list[dict] = []

        class _CapturingLogger(ActivityLogger):
            async def log_activity(self, **kwargs):
                captured.append(kwargs)

        log = _CapturingLogger(session_factory=None)
        await log.log_secret_event(
            actor_user_id="u1",
            action="secret_create",
            source_id="ds-2",
            organization_id="org-1",
            success=False,
            error="SECRET_BOX_KEY 미설정",
        )
        assert captured[0]["details"]["success"] is False
        assert captured[0]["details"]["error"] == "SECRET_BOX_KEY 미설정"


# ---------------------------------------------------------------------------
# 라우트 helper — _store_secret/_delete_secret 가 audit 호출
# ---------------------------------------------------------------------------


class TestRouteHelpersInvokeAudit:
    @pytest.mark.asyncio
    async def test_store_secret_logs_secret_update(
        self, monkeypatch: pytest.MonkeyPatch,
    ):
        from cryptography.fernet import Fernet
        monkeypatch.setenv("SECRET_BOX_BACKEND", "fernet")
        monkeypatch.setenv("SECRET_BOX_KEY", Fernet.generate_key().decode())
        from src.auth.secret_box import reset_secret_box
        from src.config.settings import reset_settings
        reset_settings()
        reset_secret_box()

        from src.api.routes.data_sources import _store_secret

        repo = AsyncMock()
        repo.set_secret_path = AsyncMock(return_value=True)
        audit = AsyncMock()
        await _store_secret(
            repo, "org-1", "ds-1", "ghp_top-secret",
            actor_user_id="u-1", activity_logger=audit,
        )
        audit.log_secret_event.assert_awaited_once()
        kwargs = audit.log_secret_event.call_args.kwargs
        assert kwargs["action"] == "secret_update"
        assert kwargs["success"] is True
        # token value 절대 audit 호출 인자에 없음
        assert "ghp_top-secret" not in str(kwargs)

        reset_secret_box()
        reset_settings()

    @pytest.mark.asyncio
    async def test_delete_secret_logs_secret_delete(
        self, monkeypatch: pytest.MonkeyPatch,
    ):
        from cryptography.fernet import Fernet
        monkeypatch.setenv("SECRET_BOX_BACKEND", "fernet")
        monkeypatch.setenv("SECRET_BOX_KEY", Fernet.generate_key().decode())
        from src.auth.secret_box import reset_secret_box
        from src.config.settings import reset_settings
        reset_settings()
        reset_secret_box()

        from src.api.routes.data_sources import _delete_secret

        repo = AsyncMock()
        repo.set_secret_path = AsyncMock(return_value=True)
        audit = AsyncMock()
        await _delete_secret(
            repo, "org-1", "ds-1",
            actor_user_id="u-1", activity_logger=audit,
        )
        audit.log_secret_event.assert_awaited_once()
        assert audit.log_secret_event.call_args.kwargs["action"] == "secret_delete"

        reset_secret_box()
        reset_settings()

    @pytest.mark.asyncio
    async def test_audit_skipped_if_no_logger(self):
        """activity_logger=None 이면 audit skip — 라우트가 state 에서 못 가져와도 secret 동작."""
        from src.api.routes.data_sources import _delete_secret

        repo = AsyncMock()
        repo.set_secret_path = AsyncMock(return_value=True)
        # SecretBox 미설정 환경 — _delete_secret 의 try 가 silently skip
        # → 그래도 set_secret_path 는 호출, audit 는 skip (logger=None).
        await _delete_secret(repo, "org-1", "ds-1")
        repo.set_secret_path.assert_awaited_once()
