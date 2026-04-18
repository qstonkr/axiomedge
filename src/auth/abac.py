"""Attribute-Based Access Control (ABAC) Engine.

Evaluates access based on subject/resource/environment attributes.
Complements RBAC for fine-grained, context-aware access decisions.

Attribute categories:
    - subject: user attributes (department, org, role, etc.)
    - resource: resource attributes (kb.tier, data_classification, etc.)
    - action: the operation being performed
    - environment: contextual (time, IP range, etc.)

Condition operators:
    - eq: exact match
    - neq: not equal
    - in: value in list
    - not_in: value not in list
    - contains: string contains
    - starts_with: string prefix
    - between: numeric/time range [min, max]
    - exists: attribute exists (boolean)
    - regex: regex pattern match
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ABACContext:
    """Evaluation context for ABAC policies."""

    subject: dict[str, Any] = field(default_factory=dict)
    resource: dict[str, Any] = field(default_factory=dict)
    action: str = ""
    environment: dict[str, Any] = field(default_factory=dict)


@dataclass
class ABACDecision:
    """Result of ABAC policy evaluation."""

    allowed: bool
    reason: str
    matched_policy: str | None = None
    effect: str = "deny"  # allow | deny


class ABACEngine:
    """ABAC policy evaluation engine.

    Policies are evaluated in priority order (highest first).
    First matching policy determines the outcome.
    If no policy matches, default is DENY.
    """

    def __init__(self, policies: list[dict] | None = None) -> None:
        self._policies = sorted(
            policies or [],
            key=lambda p: p.get("priority", 0),
            reverse=True,
        )

    def load_policies(self, policies: list[dict]) -> None:
        """Load/replace policies (sorted by priority desc)."""
        self._policies = sorted(
            policies,
            key=lambda p: p.get("priority", 0),
            reverse=True,
        )

    def evaluate(self, context: ABACContext) -> ABACDecision:
        """Evaluate all active policies against the context.

        Returns the decision from the first matching policy.
        """
        for policy in self._policies:
            if not policy.get("is_active", True):
                continue

            # Check resource_type and action match
            policy_resource = policy.get("resource_type", "*")
            policy_action = policy.get("action", "*")

            resource_type = context.resource.get("type", "")
            if policy_resource != "*" and policy_resource != resource_type:
                continue
            if policy_action != "*" and policy_action != context.action:
                continue

            # Evaluate conditions
            conditions = policy.get("conditions", {})
            if self._evaluate_conditions(conditions, context):
                effect = policy.get("effect", "deny")
                return ABACDecision(
                    allowed=(effect == "allow"),
                    reason=f"Policy '{policy.get('name', '?')}' matched with effect='{effect}'",
                    matched_policy=policy.get("name"),
                    effect=effect,
                )

        return ABACDecision(
            allowed=False,
            reason="No ABAC policy matched; default deny",
        )

    def _evaluate_conditions(self, conditions: dict, context: ABACContext) -> bool:
        """Evaluate all conditions (AND logic - all must match)."""
        for attr_path, operator_value in conditions.items():
            value = self._resolve_attribute(attr_path, context)

            # Resolve __SAME_AS_RESOURCE__ sentinel: replace with the
            # corresponding attribute from the resource side of the context.
            resolved_op = {}
            for op, expected in operator_value.items():
                if expected == "__SAME_AS_RESOURCE__":
                    # Extract the attribute key (e.g. "organization_id" from "subject.organization_id")
                    parts = attr_path.split(".", 1)
                    attr_key = parts[1] if len(parts) == 2 else attr_path
                    expected = context.resource.get(attr_key)
                resolved_op[op] = expected

            if not self._check_operator(value, resolved_op):
                return False
        return True

    def _resolve_attribute(self, attr_path: str, context: ABACContext) -> Any:
        """Resolve dotted attribute path from context.

        Examples:
            "subject.department" -> context.subject["department"]
            "resource.data_classification" -> context.resource["data_classification"]
            "environment.ip_address" -> context.environment["ip_address"]
        """
        parts = attr_path.split(".", 1)
        if len(parts) != 2:
            return None

        category, key = parts
        source = {
            "subject": context.subject,
            "resource": context.resource,
            "environment": context.environment,
        }.get(category, {})

        # Support nested keys: "subject.metadata.team"
        keys = key.split(".")
        current = source
        for k in keys:
            if isinstance(current, dict):
                current = current.get(k)
            else:
                return None
        return current

    def _check_operator(self, value: Any, operator_value: dict[str, Any]) -> bool:
        """Check a single operator condition."""
        for op, expected in operator_value.items():
            if not self._check_single_op(op, value, expected):
                return False
        return True

    @staticmethod
    def _check_single_op(op: str, value: Any, expected: Any) -> bool:
        """Evaluate a single operator against value and expected."""
        if op == "eq":
            return value == expected
        if op == "neq":
            return value != expected
        if op == "in":
            return value in expected
        if op == "not_in":
            return value not in expected
        if op == "contains":
            return isinstance(value, str) and expected in value
        if op == "starts_with":
            return isinstance(value, str) and value.startswith(expected)
        if op == "between":
            return len(expected) == 2 and value is not None and expected[0] <= value <= expected[1]
        if op == "exists":
            if expected:
                return value is not None
            return value is None
        if op == "regex":
            return isinstance(value, str) and bool(re.match(expected, value))
        logger.warning("Unknown ABAC operator: %s", op)
        return False


# =============================================================================
# Pre-built ABAC Policies
# =============================================================================


DEFAULT_ABAC_POLICIES: list[dict] = [
    {
        "name": "deny_confidential_kb_external",
        "description": "외부 사용자는 confidential KB 접근 불가",
        "resource_type": "kb",
        "action": "*",
        "conditions": {
            "resource.data_classification": {"eq": "confidential"},
            "subject.provider": {"in": ["local"]},
        },
        "effect": "deny",
        "priority": 200,
        "is_active": True,
    },
    {
        "name": "allow_org_kb_read",
        "description": "같은 조직의 사용자는 BU KB 읽기 허용",
        "resource_type": "kb",
        "action": "read",
        "conditions": {
            "subject.organization_id": {"eq": "__SAME_AS_RESOURCE__"},
        },
        "effect": "allow",
        "priority": 100,
        "is_active": True,
    },
    {
        "name": "allow_global_kb_read",
        "description": "모든 인증 사용자는 global KB 읽기 허용",
        "resource_type": "kb",
        "action": "read",
        "conditions": {
            "resource.tier": {"eq": "global"},
        },
        "effect": "allow",
        "priority": 90,
        "is_active": True,
    },
    {
        "name": "deny_pipeline_non_business_hours",
        "description": "업무 시간 외 파이프라인 실행 제한 (비활성화됨)",
        "resource_type": "pipeline",
        "action": "execute",
        "conditions": {
            "environment.hour_of_day": {"not_in": [9, 10, 11, 12, 13, 14, 15, 16, 17]},
        },
        "effect": "deny",
        "priority": 50,
        "is_active": False,  # Disabled by default
    },
]
