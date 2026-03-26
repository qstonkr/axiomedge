"""Unit tests for the RBAC engine."""

from src.auth.rbac import RBACEngine, DEFAULT_ROLES


class TestRBACEngine:
    """Test RBACEngine permission checks."""

    def setup_method(self) -> None:
        self.engine = RBACEngine()

    # --- Wildcard ---

    def test_admin_has_wildcard_permission(self) -> None:
        roles = [{"role": "admin"}]
        decision = self.engine.check_permission(roles, "kb", "read")
        assert decision.allowed is True
        assert decision.matched_permission == "*:*"
        assert decision.matched_role == "admin"

    def test_admin_wildcard_grants_any_resource(self) -> None:
        roles = [{"role": "admin"}]
        for resource, action in [
            ("glossary", "delete"),
            ("pipeline", "manage"),
            ("admin", "system"),
            ("nonexistent", "whatever"),
        ]:
            decision = self.engine.check_permission(roles, resource, action)
            assert decision.allowed is True, f"admin should have {resource}:{action}"

    # --- Viewer ---

    def test_viewer_cannot_write_glossary(self) -> None:
        roles = [{"role": "viewer"}]
        decision = self.engine.check_permission(roles, "glossary", "write")
        assert decision.allowed is False

    def test_viewer_can_read_glossary(self) -> None:
        roles = [{"role": "viewer"}]
        decision = self.engine.check_permission(roles, "glossary", "read")
        assert decision.allowed is True

    # --- Editor ---

    def test_editor_can_write_glossary(self) -> None:
        roles = [{"role": "editor"}]
        decision = self.engine.check_permission(roles, "glossary", "write")
        assert decision.allowed is True
        assert decision.matched_role == "editor"

    def test_editor_cannot_delete_kb(self) -> None:
        roles = [{"role": "editor"}]
        decision = self.engine.check_permission(roles, "kb", "delete")
        assert decision.allowed is False

    # --- Scoped roles ---

    def test_scoped_role_only_applies_to_matching_scope(self) -> None:
        roles = [{"role": "editor", "scope_type": "kb", "scope_id": "kb-123"}]

        # Matches scope
        decision = self.engine.check_permission(
            roles, "glossary", "write", scope_type="kb", scope_id="kb-123"
        )
        assert decision.allowed is True

        # Different scope_id -> denied
        decision = self.engine.check_permission(
            roles, "glossary", "write", scope_type="kb", scope_id="kb-999"
        )
        assert decision.allowed is False

        # Different scope_type -> denied
        decision = self.engine.check_permission(
            roles, "glossary", "write", scope_type="organization", scope_id="kb-123"
        )
        assert decision.allowed is False

    def test_global_role_applies_everywhere(self) -> None:
        """A role without scope_type applies regardless of request scope."""
        roles = [{"role": "editor"}]
        decision = self.engine.check_permission(
            roles, "glossary", "write", scope_type="kb", scope_id="kb-123"
        )
        assert decision.allowed is True

    # --- Effective permissions ---

    def test_get_effective_permissions(self) -> None:
        roles = [{"role": "viewer"}, {"role": "contributor"}]
        perms = self.engine.get_effective_permissions(roles)
        # Contributor has everything viewer has plus more
        assert "kb:read" in perms
        assert "glossary:write" in perms
        assert "pipeline:execute" in perms
        # But not editor-level
        assert "kb:write" not in perms

    def test_get_effective_permissions_admin_contains_wildcard(self) -> None:
        roles = [{"role": "admin"}]
        perms = self.engine.get_effective_permissions(roles)
        assert "*:*" in perms

    # --- Highest role ---

    def test_get_highest_role(self) -> None:
        roles = [{"role": "viewer"}, {"role": "editor"}, {"role": "contributor"}]
        highest = self.engine.get_highest_role(roles)
        assert highest == "editor"

    def test_get_highest_role_admin(self) -> None:
        roles = [{"role": "viewer"}, {"role": "admin"}]
        highest = self.engine.get_highest_role(roles)
        assert highest == "admin"

    # --- Unknown role ---

    def test_unknown_role_denied(self) -> None:
        roles = [{"role": "nonexistent_role"}]
        decision = self.engine.check_permission(roles, "kb", "read")
        assert decision.allowed is False

    def test_unknown_role_not_in_highest(self) -> None:
        roles = [{"role": "nonexistent"}]
        assert self.engine.get_highest_role(roles) is None

    # --- Empty roles ---

    def test_empty_roles_denied(self) -> None:
        decision = self.engine.check_permission([], "kb", "read")
        assert decision.allowed is False

    # --- Custom role definitions ---

    def test_custom_role_definitions(self) -> None:
        custom_roles = {
            "superuser": {
                "weight": 999,
                "permissions": ["*:*"],
            },
        }
        engine = RBACEngine(role_definitions=custom_roles)
        decision = engine.check_permission([{"role": "superuser"}], "kb", "read")
        assert decision.allowed is True
        # Default roles should not exist in custom engine
        decision = engine.check_permission([{"role": "admin"}], "kb", "read")
        assert decision.allowed is False
