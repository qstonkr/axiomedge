"""Role-Based Access Control (RBAC) Engine.

Evaluates user permissions based on role assignments.
Supports scoped roles (global, per-KB, per-organization).

Permission format: "resource:action"
    - kb:read, kb:write, kb:delete, kb:manage
    - glossary:read, glossary:write, glossary:import
    - pipeline:execute, pipeline:manage
    - search:query, search:analytics
    - admin:users, admin:roles, admin:system
    - activity:view_own, activity:view_all
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class AccessDecision:
    """Result of an access control evaluation."""

    allowed: bool
    reason: str
    matched_role: str | None = None
    matched_permission: str | None = None


# Default role hierarchy with permissions
DEFAULT_ROLES: dict[str, dict] = {
    "viewer": {
        "display_name": "뷰어",
        "weight": 10,
        "permissions": [
            "kb:read",
            "glossary:read",
            "search:query",
            "activity:view_own",
        ],
    },
    "contributor": {
        "display_name": "기여자",
        "weight": 20,
        "permissions": [
            "kb:read",
            "glossary:read", "glossary:write",
            "search:query", "search:analytics",
            "pipeline:execute",
            "feedback:write",
            "activity:view_own",
        ],
    },
    "editor": {
        "display_name": "편집자",
        "weight": 30,
        "permissions": [
            "kb:read", "kb:write",
            "glossary:read", "glossary:write", "glossary:import",
            "search:query", "search:analytics",
            "pipeline:execute",
            "feedback:write", "feedback:review",
            "ownership:assign",
            "activity:view_own",
        ],
    },
    "kb_manager": {
        "display_name": "KB 관리자",
        "weight": 40,
        "permissions": [
            "kb:read", "kb:write", "kb:delete", "kb:manage",
            "glossary:read", "glossary:write", "glossary:import", "glossary:delete",
            "search:query", "search:analytics",
            "pipeline:execute", "pipeline:manage",
            "feedback:write", "feedback:review",
            "ownership:assign", "ownership:manage",
            "data_source:manage",
            "activity:view_own", "activity:view_team",
        ],
    },
    "admin": {
        "display_name": "시스템 관리자",
        "weight": 100,
        "permissions": [
            "*:*",  # Wildcard: all permissions
        ],
    },
}


class RBACEngine:
    """RBAC permission evaluation engine.

    Checks if a user has a specific permission based on their roles.
    Supports wildcard permissions (*:*) and scoped role evaluation.
    """

    def __init__(self, role_definitions: dict[str, dict] | None = None):
        self._roles = role_definitions or DEFAULT_ROLES

    def check_permission(
        self,
        user_roles: list[dict],  # [{"role": "editor", "scope_type": None, "scope_id": None}]
        resource: str,
        action: str,
        scope_type: str | None = None,
        scope_id: str | None = None,
    ) -> AccessDecision:
        """Check if any of the user's roles grant the requested permission.

        Args:
            user_roles: List of role assignments with optional scope.
            resource: Resource type (e.g., "kb", "glossary").
            action: Action (e.g., "read", "write").
            scope_type: Optional scope filter ("kb", "organization").
            scope_id: Optional scope value.

        Returns:
            AccessDecision with allowed/denied result and reason.
        """
        required = f"{resource}:{action}"

        for role_assignment in user_roles:
            role_name = role_assignment.get("role", "")
            role_scope_type = role_assignment.get("scope_type")
            role_scope_id = role_assignment.get("scope_id")

            # Scope matching:
            # - Global role (scope_type=None) applies everywhere
            # - Scoped role applies only when scope matches
            if role_scope_type is not None:
                if scope_type != role_scope_type or scope_id != role_scope_id:
                    continue

            role_def = self._roles.get(role_name)
            if not role_def:
                continue

            permissions = role_def.get("permissions", [])

            # Check wildcard
            if "*:*" in permissions:
                return AccessDecision(
                    allowed=True,
                    reason=f"Role '{role_name}' has wildcard permission",
                    matched_role=role_name,
                    matched_permission="*:*",
                )

            # Check exact match
            if required in permissions:
                return AccessDecision(
                    allowed=True,
                    reason=f"Role '{role_name}' grants '{required}'",
                    matched_role=role_name,
                    matched_permission=required,
                )

            # Check resource wildcard (e.g., "kb:*")
            resource_wildcard = f"{resource}:*"
            if resource_wildcard in permissions:
                return AccessDecision(
                    allowed=True,
                    reason=f"Role '{role_name}' grants '{resource_wildcard}'",
                    matched_role=role_name,
                    matched_permission=resource_wildcard,
                )

        return AccessDecision(
            allowed=False,
            reason=f"No role grants '{required}'",
        )

    def get_effective_permissions(self, user_roles: list[dict]) -> set[str]:
        """Get all effective permissions for a user across all roles."""
        permissions: set[str] = set()
        for role_assignment in user_roles:
            role_name = role_assignment.get("role", "")
            role_def = self._roles.get(role_name)
            if role_def:
                permissions.update(role_def.get("permissions", []))
        return permissions

    def get_highest_role(self, user_roles: list[dict]) -> str | None:
        """Get the highest-weight role name."""
        best_name = None
        best_weight = -1
        for role_assignment in user_roles:
            role_name = role_assignment.get("role", "")
            role_def = self._roles.get(role_name)
            if role_def and role_def.get("weight", 0) > best_weight:
                best_weight = role_def["weight"]
                best_name = role_name
        return best_name
