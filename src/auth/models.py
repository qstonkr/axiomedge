"""Auth ORM Models - Users, Roles, Permissions, KB Access, ABAC Policies.

All auth-related tables use KnowledgeBase declarative base for unified
Alembic migration management.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from src.stores.postgres.models import KnowledgeBase

_FK_AUTH_USER_ID = "auth_users.id"


# =============================================================================
# Users
# =============================================================================


class UserModel(KnowledgeBase):
    """Local user account. Synced from IdP on first login."""

    __tablename__ = "auth_users"

    id = Column(String(36), primary_key=True)
    external_id = Column(String(255), nullable=True, unique=True)  # IdP subject ID
    provider = Column(String(20), nullable=False, default="local")  # keycloak | azure_ad | local | internal
    email = Column(String(255), nullable=False, unique=True)
    display_name = Column(String(255), nullable=False)
    password_hash = Column(String(128), nullable=True)  # bcrypt, nullable for IdP users
    status = Column(String(20), nullable=False, default="active")  # active | inactive | locked
    department = Column(String(255), nullable=True)
    organization_id = Column(String(100), nullable=True)
    hr_org_code = Column(String(50), nullable=True)  # oreo-ecosystem 호환
    hr_dept_code = Column(String(50), nullable=True)  # oreo-ecosystem 호환
    avatar_url = Column(String(500), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    last_login_at = Column(DateTime(timezone=True), nullable=True)
    metadata_ = Column("metadata", JSONB, nullable=False, default=dict)  # IdP claims snapshot
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    role_assignments = relationship("UserRoleModel", back_populates="user", lazy="selectin")

    __table_args__ = (
        Index("idx_auth_user_email", "email"),
        Index("idx_auth_user_provider", "provider"),
        Index("idx_auth_user_org", "organization_id"),
        Index("idx_auth_user_external", "external_id"),
    )


# =============================================================================
# Refresh Tokens (session tracking + token rotation)
# =============================================================================


class RefreshTokenModel(KnowledgeBase):
    """Refresh token store for internal auth. Tracks token families for rotation."""

    __tablename__ = "auth_refresh_tokens"

    id = Column(String(36), primary_key=True)  # jti
    user_id = Column(String(36), ForeignKey(_FK_AUTH_USER_ID, ondelete="CASCADE"), nullable=False)
    family_id = Column(String(36), nullable=False)  # Token family for rotation detection
    rotation_count = Column(Integer, nullable=False, default=0)
    token_hash = Column(String(128), nullable=False)  # SHA256(refresh_token)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    ip_address = Column(String(45), nullable=True)
    user_agent = Column(String(500), nullable=True)
    created_at = Column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        Index("idx_refresh_token_user", "user_id"),
        Index("idx_refresh_token_family", "family_id"),
        Index("idx_refresh_token_expires", "expires_at"),
    )


# =============================================================================
# Roles (RBAC)
# =============================================================================


class RoleModel(KnowledgeBase):
    """Predefined roles with hierarchical weight for conflict resolution."""

    __tablename__ = "auth_roles"

    id = Column(String(36), primary_key=True)
    name = Column(String(100), nullable=False, unique=True)
    display_name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    weight = Column(Integer, nullable=False, default=0)  # Higher = more authority
    is_system = Column(Boolean, nullable=False, default=False)  # Cannot be deleted
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    # Relationships
    permissions = relationship("RolePermissionModel", back_populates="role", lazy="selectin")

    __table_args__ = (
        Index("idx_auth_role_weight", "weight"),
    )


class UserRoleModel(KnowledgeBase):
    """User-to-Role mapping (M:N). Optional scope to KB or organization."""

    __tablename__ = "auth_user_roles"

    id = Column(String(36), primary_key=True)
    user_id = Column(String(36), ForeignKey(_FK_AUTH_USER_ID, ondelete="CASCADE"), nullable=False)
    role_id = Column(String(36), ForeignKey("auth_roles.id", ondelete="CASCADE"), nullable=False)

    # Scope: NULL = global, else scoped to specific KB/org
    scope_type = Column(String(20), nullable=True)  # NULL | kb | organization
    scope_id = Column(String(100), nullable=True)  # kb_id or org_id

    granted_by = Column(String(36), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    # Relationships
    user = relationship("UserModel", back_populates="role_assignments")
    role = relationship("RoleModel")

    __table_args__ = (
        UniqueConstraint("user_id", "role_id", "scope_type", "scope_id", name="uq_user_role_scope"),
        Index("idx_auth_ur_user", "user_id"),
        Index("idx_auth_ur_role", "role_id"),
        Index("idx_auth_ur_scope", "scope_type", "scope_id"),
    )


# =============================================================================
# Permissions (RBAC)
# =============================================================================


class PermissionModel(KnowledgeBase):
    """Fine-grained permissions. Format: resource:action (e.g., kb:read, glossary:write)."""

    __tablename__ = "auth_permissions"

    id = Column(String(36), primary_key=True)
    resource = Column(String(100), nullable=False)  # kb, glossary, pipeline, admin, search, ...
    action = Column(String(50), nullable=False)  # read, write, delete, execute, manage
    description = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        UniqueConstraint("resource", "action", name="uq_permission_resource_action"),
        Index("idx_auth_perm_resource", "resource"),
    )


class RolePermissionModel(KnowledgeBase):
    """Role-to-Permission mapping (M:N)."""

    __tablename__ = "auth_role_permissions"

    id = Column(String(36), primary_key=True)
    role_id = Column(String(36), ForeignKey("auth_roles.id", ondelete="CASCADE"), nullable=False)
    permission_id = Column(String(36), ForeignKey("auth_permissions.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    # Relationships
    role = relationship("RoleModel", back_populates="permissions")
    permission = relationship("PermissionModel")

    __table_args__ = (
        UniqueConstraint("role_id", "permission_id", name="uq_role_permission"),
        Index("idx_auth_rp_role", "role_id"),
        Index("idx_auth_rp_perm", "permission_id"),
    )


# =============================================================================
# KB-Level Access Control
# =============================================================================


class KBUserPermissionModel(KnowledgeBase):
    """Direct KB-level permission grants (bypass RBAC for fine-grained control)."""

    __tablename__ = "auth_kb_user_permissions"

    id = Column(String(36), primary_key=True)
    kb_id = Column(String(100), nullable=False)
    user_id = Column(String(36), ForeignKey(_FK_AUTH_USER_ID, ondelete="CASCADE"), nullable=False)
    permission_level = Column(String(20), nullable=False, default="reader")  # reader | contributor | manager | owner
    granted_by = Column(String(36), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        UniqueConstraint("kb_id", "user_id", name="uq_kb_user_perm"),
        Index("idx_auth_kb_perm_kb", "kb_id"),
        Index("idx_auth_kb_perm_user", "user_id"),
        Index("idx_auth_kb_perm_level", "permission_level"),
    )


# =============================================================================
# ABAC Policies
# =============================================================================


class ABACPolicyModel(KnowledgeBase):
    """Attribute-Based Access Control policies.

    Each policy evaluates subject/resource/action/environment attributes.
    Stored as JSON conditions for runtime evaluation.

    Example policy:
    {
        "conditions": {
            "subject.department": {"eq": "IT운영"},
            "resource.data_classification": {"in": ["internal", "public"]},
            "environment.time_of_day": {"between": ["09:00", "18:00"]}
        },
        "effect": "allow",
        "priority": 100
    }
    """

    __tablename__ = "auth_abac_policies"

    id = Column(String(36), primary_key=True)
    name = Column(String(255), nullable=False, unique=True)
    description = Column(Text, nullable=True)
    resource_type = Column(String(100), nullable=False)  # kb, document, glossary, pipeline, system
    action = Column(String(50), nullable=False)  # read, write, delete, execute, manage, *
    conditions = Column(JSONB, nullable=False, default=dict)  # Attribute conditions
    effect = Column(String(10), nullable=False, default="allow")  # allow | deny
    priority = Column(Integer, nullable=False, default=0)  # Higher = evaluated first
    is_active = Column(Boolean, nullable=False, default=True)
    created_by = Column(String(36), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("idx_abac_resource_action", "resource_type", "action"),
        Index("idx_abac_active_priority", "is_active", "priority"),
    )


# =============================================================================
# Activity Log (user activities for "나의 활동")
# =============================================================================


class UserActivityLogModel(KnowledgeBase):
    """User activity log for personal dashboard ("나의 활동")."""

    __tablename__ = "auth_user_activity_logs"

    id = Column(String(36), primary_key=True)
    user_id = Column(String(36), ForeignKey(_FK_AUTH_USER_ID, ondelete="CASCADE"), nullable=False)
    activity_type = Column(String(50), nullable=False)  # search, view, upload, edit, feedback, export, ...
    resource_type = Column(String(50), nullable=False)  # kb, document, glossary, term, ...
    resource_id = Column(String(255), nullable=True)
    kb_id = Column(String(100), nullable=True)
    details = Column(JSONB, nullable=False, default=dict)  # Activity-specific data
    ip_address = Column(String(45), nullable=True)
    user_agent = Column(String(500), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("idx_activity_user", "user_id"),
        Index("idx_activity_user_type", "user_id", "activity_type"),
        Index("idx_activity_type", "activity_type"),
        Index("idx_activity_created", "created_at"),
        Index("idx_activity_resource", "resource_type", "resource_id"),
    )
