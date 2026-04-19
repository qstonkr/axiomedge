"""Shared fixtures for unit tests.

Ensures dashboard modules are importable and streamlit is mocked
before any dashboard test imports.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

# Make dashboard modules importable from all test files
_DASHBOARD_DIR = str(Path(__file__).resolve().parents[2] / "src" / "apps" / "dashboard")
if _DASHBOARD_DIR not in sys.path:
    sys.path.insert(0, _DASHBOARD_DIR)

# Ensure streamlit is mocked before any dashboard import
if "streamlit" not in sys.modules:
    _st_mock = MagicMock()
    _cache_mock = MagicMock(side_effect=lambda **kw: lambda f: f)
    _cache_mock.clear = MagicMock()
    _st_mock.cache_data = _cache_mock
    _cache_res_mock = MagicMock(side_effect=lambda **kw: lambda f: f)
    _cache_res_mock.clear = MagicMock()
    _st_mock.cache_resource = _cache_res_mock
    _st_mock.session_state = {}
    sys.modules["streamlit"] = _st_mock


# ─────────────────────────────────────────────────────────────────────────────
# Auth bypass for unit tests that mount routes on a bare FastAPI() — B-0 의
# RBAC enforcement 후 ``Depends(get_current_user/org)`` 가 실제 token 검증을
# 시도해 401. domain 로직만 검증하는 test 는 fake user/org 를 inject 해야 함.
#
# ``autouse=False`` — auth 자체 동작을 검증하는 test (test_auth_*) 와 충돌
# 하지 않도록 opt-in. domain test 파일 상단에:
#
#     pytestmark = pytest.mark.usefixtures("bypass_route_auth")
# ─────────────────────────────────────────────────────────────────────────────

import pytest as _pytest




@_pytest.fixture
def bypass_route_auth(monkeypatch):
    """매 ``FastAPI()`` 인스턴스의 ``dependency_overrides`` 에 fake auth 주입."""
    from fastapi import FastAPI

    from src.auth.dependencies import OrgContext, get_current_org, get_current_user
    from src.auth.providers import AuthUser

    fake_user = AuthUser(
        sub="test-user-001",
        email="test@unit.local",
        display_name="Unit Test User",
        provider="test",
        roles=["admin"],
        groups=[],
        department=None,
        organization_id="default-org",
        active_org_id="default-org",
    )
    fake_org = OrgContext(id="default-org", user_role_in_org="ADMIN")

    async def _fake_user():
        return fake_user

    async def _fake_org():
        return fake_org

    orig_init = FastAPI.__init__

    def _patched_init(self, *a, **kw):
        orig_init(self, *a, **kw)
        self.dependency_overrides.setdefault(get_current_user, _fake_user)
        self.dependency_overrides.setdefault(get_current_org, _fake_org)

    monkeypatch.setattr(FastAPI, "__init__", _patched_init)
    yield
