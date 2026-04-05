"""Unit tests for src/auth/rbac.py — RBAC engine permission evaluation."""

from __future__ import annotations

import pytest

from src.auth.rbac import AccessDecision, DEFAULT_ROLES, RBACEngine


class TestAccessDecision:
    """Test AccessDecision dataclass."""

    def test_allowed_decision(self) -> None:
        d = AccessDecision(allowed=True, reason="granted", matched_role="admin")
        assert d.allowed is True
        assert d.matched_role == "admin"
        assert d.matched_permission is None

    def test_denied_decision(self) -> None:
        d = AccessDecision(allowed=False, reason="denied")
        assert d.allowed is False
        assert d.matched_role is None


class TestRBACEngineDefaults:
    """Test RBACEngine with default role definitions."""

    def setup_method(self) -> None:
        self.engine = RBACEngine()

    # ── Viewer role ──

    def test_viewer_can_read_kb(self) -> None:
        roles = [{"role": "viewer"}]
        result = self.engine.check_permission(roles, "kb", "read")
        assert result.allowed is True
        assert result.matched_role == "viewer"

    def test_viewer_can_search(self) -> None:
        roles = [{"role": "viewer"}]
        result = self.engine.check_permission(roles, "search", "query")
        assert result.allowed is True

    def test_viewer_cannot_write_kb(self) -> None:
        roles = [{"role": "viewer"}]
        result = self.engine.check_permission(roles, "kb", "write")
        assert result.allowed is False

    def test_viewer_cannot_delete_kb(self) -> None:
        roles = [{"role": "viewer"}]
        result = self.engine.check_permission(roles, "kb", "delete")
        assert result.allowed is False

    def test_viewer_cannot_execute_pipeline(self) -> None:
        roles = [{"role": "viewer"}]
        result = self.engine.check_permission(roles, "pipeline", "execute")
        assert result.allowed is False

    # ── Contributor role ──

    def test_contributor_can_execute_pipeline(self) -> None:
        roles = [{"role": "contributor"}]
        result = self.engine.check_permission(roles, "pipeline", "execute")
        assert result.allowed is True

    def test_contributor_can_write_glossary(self) -> None:
        roles = [{"role": "contributor"}]
        result = self.engine.check_permission(roles, "glossary", "write")
        assert result.allowed is True

    def test_contributor_cannot_write_kb(self) -> None:
        roles = [{"role": "contributor"}]
        result = self.engine.check_permission(roles, "kb", "write")
        assert result.allowed is False

    # ── Editor role ──

    def test_editor_can_write_kb(self) -> None:
        roles = [{"role": "editor"}]
        result = self.engine.check_permission(roles, "kb", "write")
        assert result.allowed is True

    def test_editor_can_import_glossary(self) -> None:
        roles = [{"role": "editor"}]
        result = self.engine.check_permission(roles, "glossary", "import")
        assert result.allowed is True

    def test_editor_cannot_delete_kb(self) -> None:
        roles = [{"role": "editor"}]
        result = self.engine.check_permission(roles, "kb", "delete")
        assert result.allowed is False

    # ── KB Manager role ──

    def test_kb_manager_can_delete_kb(self) -> None:
        roles = [{"role": "kb_manager"}]
        result = self.engine.check_permission(roles, "kb", "delete")
        assert result.allowed is True

    def test_kb_manager_can_manage_pipeline(self) -> None:
        roles = [{"role": "kb_manager"}]
        result = self.engine.check_permission(roles, "pipeline", "manage")
        assert result.allowed is True

    def test_kb_manager_cannot_manage_users(self) -> None:
        roles = [{"role": "kb_manager"}]
        result = self.engine.check_permission(roles, "admin", "users")
        assert result.allowed is False

    # ── Admin role (wildcard) ──

    def test_admin_wildcard_grants_everything(self) -> None:
        roles = [{"role": "admin"}]
        result = self.engine.check_permission(roles, "admin", "users")
        assert result.allowed is True
        assert result.matched_permission == "*:*"

    def test_admin_can_access_any_resource(self) -> None:
        roles = [{"role": "admin"}]
        for resource, action in [
            ("kb", "delete"),
            ("glossary", "import"),
            ("pipeline", "manage"),
            ("admin", "system"),
            ("nonexistent", "anything"),
        ]:
            result = self.engine.check_permission(roles, resource, action)
            assert result.allowed is True, f"Admin should have {resource}:{action}"


class TestRBACEngineMultipleRoles:
    """Test with multiple role assignments."""

    def setup_method(self) -> None:
        self.engine = RBACEngine()

    def test_highest_role_grants_permission(self) -> None:
        """User with viewer + editor should get editor permissions."""
        roles = [{"role": "viewer"}, {"role": "editor"}]
        result = self.engine.check_permission(roles, "kb", "write")
        assert result.allowed is True
        assert result.matched_role == "editor"

    def test_denied_when_no_role_has_permission(self) -> None:
        """Neither viewer nor contributor can delete KB."""
        roles = [{"role": "viewer"}, {"role": "contributor"}]
        result = self.engine.check_permission(roles, "kb", "delete")
        assert result.allowed is False


class TestRBACEngineScopedRoles:
    """Test scoped role evaluation (per-KB, per-organization)."""

    def setup_method(self) -> None:
        self.engine = RBACEngine()

    def test_global_role_applies_everywhere(self) -> None:
        """Global role (no scope) grants permission regardless of scope filter."""
        roles = [{"role": "editor", "scope_type": None, "scope_id": None}]
        result = self.engine.check_permission(
            roles, "kb", "write", scope_type="kb", scope_id="kb-123"
        )
        assert result.allowed is True

    def test_scoped_role_matches_correct_scope(self) -> None:
        """KB-scoped editor can write to their KB."""
        roles = [{"role": "editor", "scope_type": "kb", "scope_id": "kb-123"}]
        result = self.engine.check_permission(
            roles, "kb", "write", scope_type="kb", scope_id="kb-123"
        )
        assert result.allowed is True

    def test_scoped_role_denied_for_different_scope(self) -> None:
        """KB-scoped editor cannot write to a different KB."""
        roles = [{"role": "editor", "scope_type": "kb", "scope_id": "kb-123"}]
        result = self.engine.check_permission(
            roles, "kb", "write", scope_type="kb", scope_id="kb-456"
        )
        assert result.allowed is False

    def test_scoped_role_denied_for_different_scope_type(self) -> None:
        """KB-scoped role does not match organization scope."""
        roles = [{"role": "editor", "scope_type": "kb", "scope_id": "kb-123"}]
        result = self.engine.check_permission(
            roles, "kb", "write", scope_type="organization", scope_id="org-1"
        )
        assert result.allowed is False

    def test_mixed_global_and_scoped_roles(self) -> None:
        """Global viewer + scoped editor: editor perms only in scoped KB."""
        roles = [
            {"role": "viewer", "scope_type": None, "scope_id": None},
            {"role": "editor", "scope_type": "kb", "scope_id": "kb-123"},
        ]
        # Can read any KB (global viewer)
        result = self.engine.check_permission(
            roles, "kb", "read", scope_type="kb", scope_id="kb-999"
        )
        assert result.allowed is True

        # Can write scoped KB
        result = self.engine.check_permission(
            roles, "kb", "write", scope_type="kb", scope_id="kb-123"
        )
        assert result.allowed is True

        # Cannot write other KB
        result = self.engine.check_permission(
            roles, "kb", "write", scope_type="kb", scope_id="kb-999"
        )
        assert result.allowed is False


class TestRBACEngineEdgeCases:
    """Test edge cases and error handling."""

    def setup_method(self) -> None:
        self.engine = RBACEngine()

    def test_empty_roles_denied(self) -> None:
        result = self.engine.check_permission([], "kb", "read")
        assert result.allowed is False

    def test_unknown_role_denied(self) -> None:
        roles = [{"role": "nonexistent_role"}]
        result = self.engine.check_permission(roles, "kb", "read")
        assert result.allowed is False

    def test_missing_role_key_in_dict(self) -> None:
        """Role assignment without 'role' key should be skipped."""
        roles = [{"scope_type": "kb"}]
        result = self.engine.check_permission(roles, "kb", "read")
        assert result.allowed is False

    def test_reason_contains_permission_string(self) -> None:
        """Denied reason should mention the requested permission."""
        roles = [{"role": "viewer"}]
        result = self.engine.check_permission(roles, "admin", "users")
        assert "admin:users" in result.reason


class TestRBACResourceWildcard:
    """Test resource-level wildcard (e.g., 'kb:*')."""

    def test_resource_wildcard_grants_all_actions(self) -> None:
        """Custom role with 'kb:*' should grant any kb action."""
        custom_roles = {
            "kb_full": {
                "display_name": "KB Full",
                "weight": 25,
                "permissions": ["kb:*"],
            },
        }
        engine = RBACEngine(role_definitions=custom_roles)
        roles = [{"role": "kb_full"}]

        assert engine.check_permission(roles, "kb", "read").allowed is True
        assert engine.check_permission(roles, "kb", "write").allowed is True
        assert engine.check_permission(roles, "kb", "delete").allowed is True
        # Different resource should be denied
        assert engine.check_permission(roles, "glossary", "read").allowed is False


class TestGetEffectivePermissions:
    """Test get_effective_permissions method."""

    def setup_method(self) -> None:
        self.engine = RBACEngine()

    def test_single_role_permissions(self) -> None:
        roles = [{"role": "viewer"}]
        perms = self.engine.get_effective_permissions(roles)
        assert "kb:read" in perms
        assert "search:query" in perms
        assert "kb:write" not in perms

    def test_multiple_roles_union(self) -> None:
        """Effective permissions should be the union of all role permissions."""
        roles = [{"role": "viewer"}, {"role": "contributor"}]
        perms = self.engine.get_effective_permissions(roles)
        assert "kb:read" in perms
        assert "glossary:write" in perms
        assert "pipeline:execute" in perms

    def test_admin_has_wildcard(self) -> None:
        roles = [{"role": "admin"}]
        perms = self.engine.get_effective_permissions(roles)
        assert "*:*" in perms

    def test_unknown_role_returns_empty(self) -> None:
        roles = [{"role": "fake"}]
        perms = self.engine.get_effective_permissions(roles)
        assert len(perms) == 0


class TestGetHighestRole:
    """Test get_highest_role method."""

    def setup_method(self) -> None:
        self.engine = RBACEngine()

    def test_single_role(self) -> None:
        roles = [{"role": "viewer"}]
        assert self.engine.get_highest_role(roles) == "viewer"

    def test_multiple_roles_returns_highest(self) -> None:
        roles = [{"role": "viewer"}, {"role": "kb_manager"}, {"role": "editor"}]
        assert self.engine.get_highest_role(roles) == "kb_manager"

    def test_admin_is_highest(self) -> None:
        roles = [{"role": "viewer"}, {"role": "admin"}]
        assert self.engine.get_highest_role(roles) == "admin"

    def test_empty_roles(self) -> None:
        assert self.engine.get_highest_role([]) is None

    def test_unknown_role_skipped(self) -> None:
        roles = [{"role": "nonexistent"}]
        assert self.engine.get_highest_role(roles) is None


class TestDefaultRolesIntegrity:
    """Verify DEFAULT_ROLES structure is consistent."""

    def test_all_roles_have_required_keys(self) -> None:
        for name, role_def in DEFAULT_ROLES.items():
            assert "display_name" in role_def, f"{name} missing display_name"
            assert "weight" in role_def, f"{name} missing weight"
            assert "permissions" in role_def, f"{name} missing permissions"

    def test_role_weights_are_ordered(self) -> None:
        """Weights should increase: viewer < contributor < editor < kb_manager < admin."""
        ordered = ["viewer", "contributor", "editor", "kb_manager", "admin"]
        for i in range(len(ordered) - 1):
            w1 = DEFAULT_ROLES[ordered[i]]["weight"]
            w2 = DEFAULT_ROLES[ordered[i + 1]]["weight"]
            assert w1 < w2, f"{ordered[i]} ({w1}) should have lower weight than {ordered[i+1]} ({w2})"

    def test_admin_has_wildcard_only(self) -> None:
        """Admin permissions should include '*:*'."""
        assert "*:*" in DEFAULT_ROLES["admin"]["permissions"]

    def test_permission_format(self) -> None:
        """All permissions should be 'resource:action' or '*:*'."""
        for name, role_def in DEFAULT_ROLES.items():
            for perm in role_def["permissions"]:
                assert ":" in perm, f"Invalid permission format '{perm}' in role '{name}'"
