"""Unit tests for src/auth/models.py — Auth ORM model definitions."""

from __future__ import annotations

import pytest

from src.auth.models import (
    ABACPolicyModel,
    KBUserPermissionModel,
    PermissionModel,
    RefreshTokenModel,
    RoleModel,
    RolePermissionModel,
    UserActivityLogModel,
    UserModel,
    UserRoleModel,
)


class TestUserModel:
    """Test UserModel table definition and defaults."""

    def test_tablename(self) -> None:
        assert UserModel.__tablename__ == "auth_users"

    def test_primary_key_is_string_36(self) -> None:
        col = UserModel.__table__.columns["id"]
        assert col.primary_key is True
        assert isinstance(col.type, type(col.type))  # String

    def test_email_is_unique(self) -> None:
        col = UserModel.__table__.columns["email"]
        assert col.unique is True
        assert col.nullable is False

    def test_default_provider_is_local(self) -> None:
        col = UserModel.__table__.columns["provider"]
        assert col.default.arg == "local"

    def test_default_status_is_active(self) -> None:
        col = UserModel.__table__.columns["status"]
        assert col.default.arg == "active"

    def test_default_is_active_true(self) -> None:
        col = UserModel.__table__.columns["is_active"]
        assert col.default.arg is True

    def test_password_hash_is_nullable(self) -> None:
        col = UserModel.__table__.columns["password_hash"]
        assert col.nullable is True

    def test_has_role_assignments_relationship(self) -> None:
        assert "role_assignments" in UserModel.__mapper__.relationships

    def test_indexes_defined(self) -> None:
        index_names = {idx.name for idx in UserModel.__table__.indexes}
        assert "idx_auth_user_email" in index_names
        assert "idx_auth_user_provider" in index_names
        assert "idx_auth_user_org" in index_names
        assert "idx_auth_user_external" in index_names


class TestRefreshTokenModel:
    """Test RefreshTokenModel table definition."""

    def test_tablename(self) -> None:
        assert RefreshTokenModel.__tablename__ == "auth_refresh_tokens"

    def test_user_id_foreign_key(self) -> None:
        col = RefreshTokenModel.__table__.columns["user_id"]
        fks = list(col.foreign_keys)
        assert len(fks) == 1
        assert "auth_users.id" in str(fks[0].target_fullname)

    def test_default_rotation_count_zero(self) -> None:
        col = RefreshTokenModel.__table__.columns["rotation_count"]
        assert col.default.arg == 0

    def test_revoked_at_nullable(self) -> None:
        col = RefreshTokenModel.__table__.columns["revoked_at"]
        assert col.nullable is True


class TestRoleModel:
    """Test RoleModel table definition."""

    def test_tablename(self) -> None:
        assert RoleModel.__tablename__ == "auth_roles"

    def test_name_is_unique(self) -> None:
        col = RoleModel.__table__.columns["name"]
        assert col.unique is True

    def test_default_weight_zero(self) -> None:
        col = RoleModel.__table__.columns["weight"]
        assert col.default.arg == 0

    def test_default_is_system_false(self) -> None:
        col = RoleModel.__table__.columns["is_system"]
        assert col.default.arg is False

    def test_has_permissions_relationship(self) -> None:
        assert "permissions" in RoleModel.__mapper__.relationships


class TestUserRoleModel:
    """Test UserRoleModel table definition."""

    def test_tablename(self) -> None:
        assert UserRoleModel.__tablename__ == "auth_user_roles"

    def test_scope_fields_nullable(self) -> None:
        assert UserRoleModel.__table__.columns["scope_type"].nullable is True
        assert UserRoleModel.__table__.columns["scope_id"].nullable is True

    def test_unique_constraint_exists(self) -> None:
        constraints = [
            c.name for c in UserRoleModel.__table__.constraints
            if hasattr(c, "name") and c.name
        ]
        assert "uq_user_role_scope" in constraints

    def test_has_user_relationship(self) -> None:
        assert "user" in UserRoleModel.__mapper__.relationships

    def test_has_role_relationship(self) -> None:
        assert "role" in UserRoleModel.__mapper__.relationships


class TestPermissionModel:
    """Test PermissionModel table definition."""

    def test_tablename(self) -> None:
        assert PermissionModel.__tablename__ == "auth_permissions"

    def test_unique_constraint_resource_action(self) -> None:
        constraints = [
            c.name for c in PermissionModel.__table__.constraints
            if hasattr(c, "name") and c.name
        ]
        assert "uq_permission_resource_action" in constraints

    def test_description_nullable(self) -> None:
        col = PermissionModel.__table__.columns["description"]
        assert col.nullable is True


class TestKBUserPermissionModel:
    """Test KBUserPermissionModel table definition."""

    def test_tablename(self) -> None:
        assert KBUserPermissionModel.__tablename__ == "auth_kb_user_permissions"

    def test_default_permission_level(self) -> None:
        col = KBUserPermissionModel.__table__.columns["permission_level"]
        assert col.default.arg == "reader"

    def test_unique_constraint_kb_user(self) -> None:
        constraints = [
            c.name for c in KBUserPermissionModel.__table__.constraints
            if hasattr(c, "name") and c.name
        ]
        assert "uq_kb_user_perm" in constraints


class TestABACPolicyModel:
    """Test ABACPolicyModel table definition."""

    def test_tablename(self) -> None:
        assert ABACPolicyModel.__tablename__ == "auth_abac_policies"

    def test_default_effect_allow(self) -> None:
        col = ABACPolicyModel.__table__.columns["effect"]
        assert col.default.arg == "allow"

    def test_default_priority_zero(self) -> None:
        col = ABACPolicyModel.__table__.columns["priority"]
        assert col.default.arg == 0

    def test_name_unique(self) -> None:
        col = ABACPolicyModel.__table__.columns["name"]
        assert col.unique is True


class TestUserActivityLogModel:
    """Test UserActivityLogModel table definition."""

    def test_tablename(self) -> None:
        assert UserActivityLogModel.__tablename__ == "auth_user_activity_logs"

    def test_indexes_defined(self) -> None:
        index_names = {idx.name for idx in UserActivityLogModel.__table__.indexes}
        assert "idx_activity_user" in index_names
        assert "idx_activity_type" in index_names
        assert "idx_activity_created" in index_names
