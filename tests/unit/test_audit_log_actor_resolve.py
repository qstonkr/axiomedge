"""``_resolve_actor`` contract — P2-6.

AuthMiddleware 가 세팅하는 ``request.state.auth_user.sub`` 가 우선이며,
legacy ``state.user`` 도 fallback. 미세팅 시 ``_system`` sentinel.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from src.api.middleware.audit_log import _resolve_actor


def _request_with_state(**state_kwargs):
    req = MagicMock()
    req.state = SimpleNamespace(**state_kwargs)
    return req


class TestResolveActor:
    def test_uses_auth_user_sub(self):
        req = _request_with_state(
            auth_user=SimpleNamespace(sub="alice", user_id="u1"),
        )
        assert _resolve_actor(req) == "alice"

    def test_falls_back_to_user_id(self):
        req = _request_with_state(
            auth_user=SimpleNamespace(user_id="u-42"),
        )
        assert _resolve_actor(req) == "u-42"

    def test_falls_back_to_email(self):
        req = _request_with_state(
            auth_user=SimpleNamespace(email="bob@x.com"),
        )
        assert _resolve_actor(req) == "bob@x.com"

    def test_legacy_user_attr_supported(self):
        req = _request_with_state(
            user=SimpleNamespace(sub="legacy-user"),
        )
        assert _resolve_actor(req) == "legacy-user"

    def test_auth_user_takes_priority_over_user(self):
        req = _request_with_state(
            auth_user=SimpleNamespace(sub="primary"),
            user=SimpleNamespace(sub="legacy"),
        )
        assert _resolve_actor(req) == "primary"

    def test_no_user_returns_system(self):
        req = _request_with_state()
        assert _resolve_actor(req) == "_system"

    def test_user_without_recognized_attrs_repr_fallback(self):
        class _Weird:
            def __str__(self) -> str:
                return "weird-user-repr"
        req = _request_with_state(auth_user=_Weird())
        assert _resolve_actor(req) == "weird-user-repr"
