"""Unit tests for src/auth/role_service.py — Role & KB permission management."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.auth.role_service import RoleService


# ── Helpers ──


def _mock_session_factory():
    """Create a mock async session factory with context manager support."""
    session = AsyncMock()
    factory = MagicMock()
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=False)
    factory.return_value = ctx
    return factory, session


def _make_row(*values):
    """Create a mock row that supports indexing."""
    row = MagicMock()
    row.__getitem__ = lambda self, i: values[i]
    return row


class TestGetUserRoles:
    """Test RoleService.get_user_roles."""

    @pytest.mark.asyncio
    async def test_returns_empty_when_user_not_found(self) -> None:
        factory, session = _mock_session_factory()
        result_mock = MagicMock()
        result_mock.first.return_value = None
        session.execute.return_value = result_mock

        svc = RoleService(factory)
        roles = await svc.get_user_roles("nonexistent")
        assert roles == []

    @pytest.mark.asyncio
    async def test_returns_roles_for_existing_user(self) -> None:
        factory, session = _mock_session_factory()
        user_id = str(uuid.uuid4())

        # First call: find user
        user_result = MagicMock()
        user_result.first.return_value = _make_row(user_id)

        # Second call: get role assignments
        ur_mock = MagicMock()
        ur_mock.scope_type = "kb"
        ur_mock.scope_id = "test-kb"
        ur_mock.expires_at = None

        role_mock = MagicMock()
        role_mock.name = "editor"
        role_mock.display_name = "Editor"

        roles_result = MagicMock()
        roles_result.all.return_value = [(ur_mock, role_mock)]

        session.execute.side_effect = [user_result, roles_result]

        svc = RoleService(factory)
        roles = await svc.get_user_roles(user_id)
        assert len(roles) == 1
        assert roles[0]["role"] == "editor"
        assert roles[0]["scope_type"] == "kb"
        assert roles[0]["scope_id"] == "test-kb"
        assert roles[0]["expires_at"] is None


class TestAssignRole:
    """Test RoleService.assign_role."""

    @pytest.mark.asyncio
    async def test_assign_role_success(self) -> None:
        factory, session = _mock_session_factory()
        user_id = str(uuid.uuid4())
        role_id = str(uuid.uuid4())

        user_result = MagicMock()
        user_result.first.return_value = _make_row(user_id)

        role_obj = MagicMock()
        role_obj.id = role_id
        role_obj.name = "editor"
        role_result = MagicMock()
        role_result.scalar_one_or_none.return_value = role_obj

        session.execute.side_effect = [user_result, role_result]

        svc = RoleService(factory)
        result = await svc.assign_role(user_id, "editor", scope_type="kb", scope_id="my-kb")

        assert result["role"] == "editor"
        assert result["scope_type"] == "kb"
        assert result["scope_id"] == "my-kb"
        session.add.assert_called_once()
        session.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_assign_role_user_not_found(self) -> None:
        factory, session = _mock_session_factory()
        user_result = MagicMock()
        user_result.first.return_value = None
        session.execute.return_value = user_result

        svc = RoleService(factory)
        with pytest.raises(ValueError, match="User not found"):
            await svc.assign_role("bad-id", "editor")

    @pytest.mark.asyncio
    async def test_assign_role_role_not_found(self) -> None:
        factory, session = _mock_session_factory()

        user_result = MagicMock()
        user_result.first.return_value = _make_row("uid")

        role_result = MagicMock()
        role_result.scalar_one_or_none.return_value = None

        session.execute.side_effect = [user_result, role_result]

        svc = RoleService(factory)
        with pytest.raises(ValueError, match="Role not found"):
            await svc.assign_role("uid", "nonexistent_role")


class TestRevokeRole:
    """Test RoleService.revoke_role."""

    @pytest.mark.asyncio
    async def test_revoke_returns_false_when_user_not_found(self) -> None:
        factory, session = _mock_session_factory()
        result_mock = MagicMock()
        result_mock.first.return_value = None
        session.execute.return_value = result_mock

        svc = RoleService(factory)
        assert await svc.revoke_role("bad-id", "admin") is False

    @pytest.mark.asyncio
    async def test_revoke_returns_false_when_role_not_found(self) -> None:
        factory, session = _mock_session_factory()
        user_result = MagicMock()
        user_result.first.return_value = _make_row("uid")

        role_result = MagicMock()
        role_result.first.return_value = None

        session.execute.side_effect = [user_result, role_result]

        svc = RoleService(factory)
        assert await svc.revoke_role("uid", "bad-role") is False

    @pytest.mark.asyncio
    async def test_revoke_success(self) -> None:
        factory, session = _mock_session_factory()
        user_result = MagicMock()
        user_result.first.return_value = _make_row("uid")

        role_result = MagicMock()
        role_result.first.return_value = _make_row("role-id")

        delete_result = MagicMock()
        delete_result.rowcount = 1

        session.execute.side_effect = [user_result, role_result, delete_result]

        svc = RoleService(factory)
        assert await svc.revoke_role("uid", "editor") is True
        session.commit.assert_awaited_once()


class TestKBPermissions:
    """Test KB permission methods."""

    @pytest.mark.asyncio
    async def test_get_kb_permission_user_not_found(self) -> None:
        factory, session = _mock_session_factory()
        result_mock = MagicMock()
        result_mock.first.return_value = None
        session.execute.return_value = result_mock

        svc = RoleService(factory)
        assert await svc.get_kb_permission("bad-id", "kb1") is None

    @pytest.mark.asyncio
    async def test_get_kb_permission_returns_level(self) -> None:
        factory, session = _mock_session_factory()
        user_result = MagicMock()
        user_result.first.return_value = _make_row("uid")

        perm_result = MagicMock()
        perm_result.scalar_one_or_none.return_value = "manager"

        session.execute.side_effect = [user_result, perm_result]

        svc = RoleService(factory)
        assert await svc.get_kb_permission("uid", "kb1") == "manager"

    @pytest.mark.asyncio
    async def test_set_kb_permission_creates_new(self) -> None:
        factory, session = _mock_session_factory()
        user_result = MagicMock()
        user_result.first.return_value = _make_row("uid")

        existing_result = MagicMock()
        existing_result.scalar_one_or_none.return_value = None

        session.execute.side_effect = [user_result, existing_result]

        svc = RoleService(factory)
        result = await svc.set_kb_permission("uid", "kb1", "contributor")
        assert result["permission_level"] == "contributor"
        session.add.assert_called_once()
        session.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_set_kb_permission_updates_existing(self) -> None:
        factory, session = _mock_session_factory()
        user_result = MagicMock()
        user_result.first.return_value = _make_row("uid")

        existing_perm = MagicMock()
        existing_perm.permission_level = "reader"
        existing_result = MagicMock()
        existing_result.scalar_one_or_none.return_value = existing_perm

        session.execute.side_effect = [user_result, existing_result]

        svc = RoleService(factory)
        result = await svc.set_kb_permission("uid", "kb1", "manager", granted_by="admin")
        assert result["permission_level"] == "manager"
        assert existing_perm.permission_level == "manager"
        assert existing_perm.granted_by == "admin"

    @pytest.mark.asyncio
    async def test_remove_kb_permission_user_not_found(self) -> None:
        factory, session = _mock_session_factory()
        result_mock = MagicMock()
        result_mock.first.return_value = None
        session.execute.return_value = result_mock

        svc = RoleService(factory)
        assert await svc.remove_kb_permission("bad-id", "kb1") is False
