"""Role-Based Access Control (RBAC) Engine.

Evaluates user permissions based on role assignments.
Supports scoped roles (global, per-KB, per-organization).

Permission format: "resource:action"
    - org:manage, org:user:manage
    - kb:read, kb:write, kb:delete, kb:create, kb:manage
    - document:read, document:write, document:delete, document:search
    - glossary:read, glossary:write
    - feedback:submit, feedback:review
    - quality:read, quality:write
    - agentic:ask
    - data_source:manage
    - distill:manage
    - audit_log:read
    - search:query, search:analytics

**Canonical roles (B-0)**: OWNER, ADMIN, MEMBER, VIEWER
**Legacy roles** (deprecated, kept for backward compat): viewer, contributor,
editor, kb_manager, admin — mapped to new roles per docs/MIGRATION_GUIDE.md.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Permission constants (used across multiple roles)
_PERM_KB_READ = "kb:read"
_PERM_KB_WRITE = "kb:write"
_PERM_GLOSSARY_READ = "glossary:read"
_PERM_GLOSSARY_WRITE = "glossary:write"
_PERM_DOCUMENT_READ = "document:read"
_PERM_DOCUMENT_WRITE = "document:write"
_PERM_DOCUMENT_SEARCH = "document:search"
_PERM_SEARCH_QUERY = "search:query"
_PERM_SEARCH_ANALYTICS = "search:analytics"
_PERM_FEEDBACK_SUBMIT = "feedback:submit"
_PERM_FEEDBACK_WRITE = "feedback:write"
_PERM_AGENTIC_ASK = "agentic:ask"
_PERM_QUALITY_READ = "quality:read"
_PERM_ACTIVITY_VIEW_OWN = "activity:view_own"
_PERM_PIPELINE_EXECUTE = "pipeline:execute"


@dataclass
class AccessDecision:
    """Result of an access control evaluation."""

    allowed: bool
    reason: str
    matched_role: str | None = None
    matched_permission: str | None = None


# Canonical roles (B-0+). These are the roles new systems should use.
# Legacy roles are still seeded for backward compat but flagged is_legacy=True.
CANONICAL_ROLES: dict[str, dict] = {
    "OWNER": {
        "display_name": "Organization Owner",
        "weight": 9000,
        "is_legacy": False,
        "permissions": [
            "*:*",  # OWNER has every permission, including org:manage (billing/destroy)
        ],
    },
    "ADMIN": {
        "display_name": "Administrator",
        "weight": 3000,
        "is_legacy": False,
        "permissions": [
            # Org user management (no billing/destroy)
            "org:user:manage",
            # KB lifecycle
            "kb:create", "kb:delete", _PERM_KB_READ, _PERM_KB_WRITE, "kb:manage",
            # Documents
            _PERM_DOCUMENT_READ, _PERM_DOCUMENT_WRITE, "document:delete", _PERM_DOCUMENT_SEARCH,
            # Glossary
            _PERM_GLOSSARY_READ, _PERM_GLOSSARY_WRITE, "glossary:delete", "glossary:import",
            # Search
            _PERM_SEARCH_QUERY, _PERM_SEARCH_ANALYTICS,
            # Feedback
            _PERM_FEEDBACK_SUBMIT, _PERM_FEEDBACK_WRITE, "feedback:review",
            # Quality
            _PERM_QUALITY_READ, "quality:write",
            # Agentic
            _PERM_AGENTIC_ASK,
            # Pipeline / data sources / distill / audit
            _PERM_PIPELINE_EXECUTE, "pipeline:manage",
            "data_source:manage", "distill:manage", "audit_log:read",
            # Ownership
            "ownership:assign", "ownership:manage",
            # Activity
            _PERM_ACTIVITY_VIEW_OWN, "activity:view_team", "activity:view_all",
        ],
    },
    "MEMBER": {
        "display_name": "Member",
        "weight": 2000,
        "is_legacy": False,
        "permissions": [
            _PERM_KB_READ,
            _PERM_DOCUMENT_READ, _PERM_DOCUMENT_WRITE, _PERM_DOCUMENT_SEARCH,
            _PERM_GLOSSARY_READ, _PERM_GLOSSARY_WRITE,
            _PERM_SEARCH_QUERY,
            _PERM_FEEDBACK_SUBMIT, _PERM_FEEDBACK_WRITE,
            _PERM_AGENTIC_ASK,
            _PERM_QUALITY_READ,
            _PERM_PIPELINE_EXECUTE,
            _PERM_ACTIVITY_VIEW_OWN,
        ],
    },
    "VIEWER": {
        "display_name": "Viewer",
        "weight": 1000,
        "is_legacy": False,
        "permissions": [
            _PERM_KB_READ,
            _PERM_DOCUMENT_READ, _PERM_DOCUMENT_SEARCH,
            _PERM_GLOSSARY_READ,
            _PERM_SEARCH_QUERY,
            _PERM_FEEDBACK_SUBMIT,
            _PERM_AGENTIC_ASK,
            _PERM_ACTIVITY_VIEW_OWN,
        ],
    },
}


# Legacy roles (deprecated). Kept seeded for backward compat with users
# created before B-0. New role assignments should use CANONICAL_ROLES.
LEGACY_ROLES: dict[str, dict] = {
    "viewer": {
        "display_name": "뷰어 (legacy)",
        "weight": 10,
        "is_legacy": True,
        "permissions": [
            _PERM_KB_READ,
            _PERM_GLOSSARY_READ,
            _PERM_SEARCH_QUERY,
            _PERM_ACTIVITY_VIEW_OWN,
        ],
    },
    "contributor": {
        "display_name": "기여자 (legacy)",
        "weight": 20,
        "is_legacy": True,
        "permissions": [
            _PERM_KB_READ,
            _PERM_GLOSSARY_READ, _PERM_GLOSSARY_WRITE,
            _PERM_SEARCH_QUERY, _PERM_SEARCH_ANALYTICS,
            _PERM_PIPELINE_EXECUTE,
            _PERM_FEEDBACK_WRITE,
            _PERM_ACTIVITY_VIEW_OWN,
        ],
    },
    "editor": {
        "display_name": "편집자 (legacy)",
        "weight": 30,
        "is_legacy": True,
        "permissions": [
            _PERM_KB_READ, _PERM_KB_WRITE,
            _PERM_GLOSSARY_READ, _PERM_GLOSSARY_WRITE, "glossary:import",
            _PERM_SEARCH_QUERY, _PERM_SEARCH_ANALYTICS,
            _PERM_PIPELINE_EXECUTE,
            _PERM_FEEDBACK_WRITE, "feedback:review",
            "ownership:assign",
            _PERM_ACTIVITY_VIEW_OWN,
        ],
    },
    "kb_manager": {
        "display_name": "KB 관리자 (legacy)",
        "weight": 40,
        "is_legacy": True,
        "permissions": [
            _PERM_KB_READ, _PERM_KB_WRITE, "kb:delete", "kb:manage",
            _PERM_GLOSSARY_READ, _PERM_GLOSSARY_WRITE, "glossary:import", "glossary:delete",
            _PERM_SEARCH_QUERY, _PERM_SEARCH_ANALYTICS,
            _PERM_PIPELINE_EXECUTE, "pipeline:manage",
            _PERM_FEEDBACK_WRITE, "feedback:review",
            "ownership:assign", "ownership:manage",
            "data_source:manage",
            _PERM_ACTIVITY_VIEW_OWN, "activity:view_team",
        ],
    },
    "admin": {
        "display_name": "시스템 관리자 (legacy)",
        "weight": 100,
        "is_legacy": True,
        "permissions": [
            "*:*",
        ],
    },
}


# Combined dict — used by RBACEngine + seed_defaults().
# Both canonical and legacy are kept active so existing tokens/users keep working.
DEFAULT_ROLES: dict[str, dict] = {**CANONICAL_ROLES, **LEGACY_ROLES}


# Mapping: legacy role name → canonical role name.
# Used by docs + future migration script.
LEGACY_TO_CANONICAL: dict[str, str] = {
    "admin": "ADMIN",
    "kb_manager": "ADMIN",  # KB-scoped MEMBER would be more precise; admin is safer
    "editor": "MEMBER",
    "contributor": "MEMBER",
    "viewer": "VIEWER",
}


class RBACEngine:
    """RBAC permission evaluation engine.

    Checks if a user has a specific permission based on their roles.
    Supports wildcard permissions (*:*) and scoped role evaluation.
    """

    def __init__(self, role_definitions: dict[str, dict] | None = None) -> None:
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
