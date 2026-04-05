"""Unit tests for src/auth/user_crud.py — User CRUD operations."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.auth.user_crud import UserCRUD


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


@dataclass
class FakeAuthUser:
    """Mimics AuthUser for testing without importing providers."""
    sub: str
    email: str
    display_name: str
    provider: str
    roles: list[str] = field(default_factory=list)
    groups: list[str] = field(default_factory=list)
    department: str | None = None
    organization_id: str | None = None
    raw_claims: dict[str, Any] = field(default_factory=dict)


def _make_user_model(**kwargs):
    """Create a mock UserModel object."""
    user = MagicMock()
    defaults = {
        "id": str(uuid.uuid4()),
        "external_id": "local:test@test.com",
        "email": "test@test.com",
        "display_name": "Test User",
        "provider": "local",
        "department": None,
        "organization_id": None,
        "is_active": True,
        "status": "active",
        "last_login_at": None,
        "created_at": None,
    }
    defaults.update(kwargs)
    for k, v in defaults.items():
        setattr(user, k, v)
    return user


class TestSyncUserFromIdp:
    """Test UserCRUD.sync_user_from_idp."""

    @pytest.mark.asyncio
    async def test_creates_new_user_on_first_login(self) -> None:
        factory, session = _mock_session_factory()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        session.execute.return_value = result_mock

        crud = UserCRUD(factory)
        auth_user = FakeAuthUser(
            sub="ext-123", email="new@test.com",
            display_name="New User", provider="keycloak",
        )
        result = await crud.sync_user_from_idp(auth_user)
        assert result["email"] == "new@test.com"
        session.add.assert_called_once()
        session.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_updates_existing_user(self) -> None:
        factory, session = _mock_session_factory()
        existing_user = _make_user_model(id="uid-1", email="old@test.com")
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = existing_user
        session.execute.return_value = result_mock

        crud = UserCRUD(factory)
        auth_user = FakeAuthUser(
            sub="ext-123", email="updated@test.com",
            display_name="Updated", provider="keycloak",
            department="IT",
        )
        result = await crud.sync_user_from_idp(auth_user)
        assert existing_user.email == "updated@test.com"
        assert existing_user.display_name == "Updated"
        assert existing_user.department == "IT"
        session.commit.assert_awaited_once()


class TestCreateUser:
    """Test UserCRUD.create_user."""

    @pytest.mark.asyncio
    async def test_create_user_success(self) -> None:
        factory, session = _mock_session_factory()

        # Email check returns no existing user
        check_result = MagicMock()
        check_result.scalar_one_or_none.return_value = None

        # Default role lookup
        role_mock = MagicMock()
        role_mock.id = "role-viewer-id"
        role_result = MagicMock()
        role_result.scalar_one_or_none.return_value = role_mock

        session.execute.side_effect = [check_result, role_result]

        crud = UserCRUD(factory)
        result = await crud.create_user(
            email="user@test.com",
            display_name="Test User",
            department="Engineering",
        )
        assert result["email"] == "user@test.com"
        assert result["display_name"] == "Test User"
        assert result["role"] == "viewer"
        session.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_create_user_duplicate_email_raises(self) -> None:
        factory, session = _mock_session_factory()
        check_result = MagicMock()
        check_result.scalar_one_or_none.return_value = _make_user_model()
        session.execute.return_value = check_result

        crud = UserCRUD(factory)
        with pytest.raises(ValueError, match="already exists"):
            await crud.create_user(email="dup@test.com", display_name="Dup")


class TestUpdateUser:
    """Test UserCRUD.update_user."""

    @pytest.mark.asyncio
    async def test_update_user_fields(self) -> None:
        factory, session = _mock_session_factory()
        user = _make_user_model(id="uid-1")
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = user
        session.execute.return_value = result_mock

        crud = UserCRUD(factory)
        result = await crud.update_user(
            "uid-1", display_name="New Name", department="Sales", is_active=False,
        )
        assert result["updated"] is True
        assert user.display_name == "New Name"
        assert user.department == "Sales"
        assert user.is_active is False
        assert user.status == "inactive"

    @pytest.mark.asyncio
    async def test_update_user_not_found(self) -> None:
        factory, session = _mock_session_factory()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        session.execute.return_value = result_mock

        crud = UserCRUD(factory)
        assert await crud.update_user("bad-id") is None


class TestDeleteUser:
    """Test UserCRUD.delete_user."""

    @pytest.mark.asyncio
    async def test_delete_existing_user(self) -> None:
        factory, session = _mock_session_factory()
        user = _make_user_model()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = user
        session.execute.return_value = result_mock

        crud = UserCRUD(factory)
        assert await crud.delete_user("uid-1") is True
        session.delete.assert_awaited_once_with(user)
        session.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delete_nonexistent_user(self) -> None:
        factory, session = _mock_session_factory()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        session.execute.return_value = result_mock

        crud = UserCRUD(factory)
        assert await crud.delete_user("bad-id") is False


class TestGetUser:
    """Test UserCRUD.get_user."""

    @pytest.mark.asyncio
    async def test_get_existing_user(self) -> None:
        factory, session = _mock_session_factory()
        user = _make_user_model(
            id="uid-1", email="user@test.com",
            display_name="User", provider="local",
        )
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = user
        session.execute.return_value = result_mock

        crud = UserCRUD(factory)
        result = await crud.get_user("uid-1")
        assert result is not None
        assert result["id"] == "uid-1"
        assert result["email"] == "user@test.com"
        assert result["provider"] == "local"

    @pytest.mark.asyncio
    async def test_get_nonexistent_user(self) -> None:
        factory, session = _mock_session_factory()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        session.execute.return_value = result_mock

        crud = UserCRUD(factory)
        assert await crud.get_user("bad-id") is None


class TestListUsers:
    """Test UserCRUD.list_users."""

    @pytest.mark.asyncio
    async def test_list_users_returns_dicts(self) -> None:
        factory, session = _mock_session_factory()
        users = [
            _make_user_model(id="u1", email="a@test.com", display_name="A"),
            _make_user_model(id="u2", email="b@test.com", display_name="B"),
        ]
        result_mock = MagicMock()
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = users
        result_mock.scalars.return_value = scalars_mock
        session.execute.return_value = result_mock

        crud = UserCRUD(factory)
        result = await crud.list_users(limit=10, offset=0)
        assert len(result) == 2
        assert result[0]["id"] == "u1"
        assert result[1]["email"] == "b@test.com"
