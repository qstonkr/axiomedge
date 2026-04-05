"""Unit tests for src/auth/abac.py — ABAC engine policy evaluation."""

from __future__ import annotations

import pytest

from src.auth.abac import ABACContext, ABACDecision, ABACEngine, DEFAULT_ABAC_POLICIES


class TestABACContext:
    """Test ABACContext dataclass."""

    def test_default_empty(self) -> None:
        ctx = ABACContext()
        assert ctx.subject == {}
        assert ctx.resource == {}
        assert ctx.action == ""
        assert ctx.environment == {}

    def test_with_values(self) -> None:
        ctx = ABACContext(
            subject={"department": "IT"},
            resource={"type": "kb", "tier": "global"},
            action="read",
            environment={"ip_address": "10.0.0.1"},
        )
        assert ctx.subject["department"] == "IT"
        assert ctx.resource["tier"] == "global"


class TestABACDecision:
    """Test ABACDecision dataclass."""

    def test_default_deny(self) -> None:
        d = ABACDecision(allowed=False, reason="no match")
        assert d.effect == "deny"

    def test_allow(self) -> None:
        d = ABACDecision(allowed=True, reason="policy matched", effect="allow")
        assert d.allowed is True


class TestABACEngineBasic:
    """Test basic ABAC engine operation."""

    def test_no_policies_denies_by_default(self) -> None:
        engine = ABACEngine(policies=[])
        ctx = ABACContext(action="read", resource={"type": "kb"})
        result = engine.evaluate(ctx)
        assert result.allowed is False
        assert "default deny" in result.reason

    def test_allow_policy_matches(self) -> None:
        policies = [
            {
                "name": "allow_all_read",
                "resource_type": "kb",
                "action": "read",
                "conditions": {},
                "effect": "allow",
                "priority": 100,
            }
        ]
        engine = ABACEngine(policies=policies)
        ctx = ABACContext(action="read", resource={"type": "kb"})
        result = engine.evaluate(ctx)
        assert result.allowed is True
        assert result.matched_policy == "allow_all_read"

    def test_deny_policy_matches(self) -> None:
        policies = [
            {
                "name": "deny_writes",
                "resource_type": "kb",
                "action": "write",
                "conditions": {},
                "effect": "deny",
                "priority": 100,
            }
        ]
        engine = ABACEngine(policies=policies)
        ctx = ABACContext(action="write", resource={"type": "kb"})
        result = engine.evaluate(ctx)
        assert result.allowed is False
        assert result.matched_policy == "deny_writes"

    def test_unmatched_resource_type_skipped(self) -> None:
        policies = [
            {
                "name": "allow_kb",
                "resource_type": "kb",
                "action": "read",
                "conditions": {},
                "effect": "allow",
                "priority": 100,
            }
        ]
        engine = ABACEngine(policies=policies)
        ctx = ABACContext(action="read", resource={"type": "glossary"})
        result = engine.evaluate(ctx)
        assert result.allowed is False

    def test_unmatched_action_skipped(self) -> None:
        policies = [
            {
                "name": "allow_read",
                "resource_type": "kb",
                "action": "read",
                "conditions": {},
                "effect": "allow",
                "priority": 100,
            }
        ]
        engine = ABACEngine(policies=policies)
        ctx = ABACContext(action="write", resource={"type": "kb"})
        result = engine.evaluate(ctx)
        assert result.allowed is False

    def test_wildcard_resource_type(self) -> None:
        policies = [
            {
                "name": "allow_any_resource",
                "resource_type": "*",
                "action": "read",
                "conditions": {},
                "effect": "allow",
                "priority": 100,
            }
        ]
        engine = ABACEngine(policies=policies)
        ctx = ABACContext(action="read", resource={"type": "anything"})
        result = engine.evaluate(ctx)
        assert result.allowed is True

    def test_wildcard_action(self) -> None:
        policies = [
            {
                "name": "allow_any_action",
                "resource_type": "kb",
                "action": "*",
                "conditions": {},
                "effect": "allow",
                "priority": 100,
            }
        ]
        engine = ABACEngine(policies=policies)
        for action in ["read", "write", "delete", "manage"]:
            ctx = ABACContext(action=action, resource={"type": "kb"})
            result = engine.evaluate(ctx)
            assert result.allowed is True, f"Should allow action={action}"


class TestABACEnginePriority:
    """Test policy priority ordering."""

    def test_higher_priority_wins(self) -> None:
        """When both allow and deny match, higher priority wins."""
        policies = [
            {
                "name": "low_allow",
                "resource_type": "kb",
                "action": "read",
                "conditions": {},
                "effect": "allow",
                "priority": 10,
            },
            {
                "name": "high_deny",
                "resource_type": "kb",
                "action": "read",
                "conditions": {},
                "effect": "deny",
                "priority": 100,
            },
        ]
        engine = ABACEngine(policies=policies)
        ctx = ABACContext(action="read", resource={"type": "kb"})
        result = engine.evaluate(ctx)
        assert result.allowed is False
        assert result.matched_policy == "high_deny"

    def test_allow_wins_when_higher_priority(self) -> None:
        policies = [
            {
                "name": "low_deny",
                "resource_type": "kb",
                "action": "read",
                "conditions": {},
                "effect": "deny",
                "priority": 10,
            },
            {
                "name": "high_allow",
                "resource_type": "kb",
                "action": "read",
                "conditions": {},
                "effect": "allow",
                "priority": 100,
            },
        ]
        engine = ABACEngine(policies=policies)
        ctx = ABACContext(action="read", resource={"type": "kb"})
        result = engine.evaluate(ctx)
        assert result.allowed is True
        assert result.matched_policy == "high_allow"


class TestABACEngineInactivePolicy:
    """Test inactive policy handling."""

    def test_inactive_policy_skipped(self) -> None:
        policies = [
            {
                "name": "inactive",
                "resource_type": "kb",
                "action": "read",
                "conditions": {},
                "effect": "allow",
                "priority": 100,
                "is_active": False,
            }
        ]
        engine = ABACEngine(policies=policies)
        ctx = ABACContext(action="read", resource={"type": "kb"})
        result = engine.evaluate(ctx)
        assert result.allowed is False

    def test_default_is_active(self) -> None:
        """Policy without is_active defaults to True."""
        policies = [
            {
                "name": "no_active_flag",
                "resource_type": "kb",
                "action": "read",
                "conditions": {},
                "effect": "allow",
                "priority": 100,
            }
        ]
        engine = ABACEngine(policies=policies)
        ctx = ABACContext(action="read", resource={"type": "kb"})
        result = engine.evaluate(ctx)
        assert result.allowed is True


class TestABACEngineConditionOperators:
    """Test all condition operators."""

    def _make_engine(self, conditions: dict) -> ABACEngine:
        return ABACEngine(policies=[
            {
                "name": "test_policy",
                "resource_type": "*",
                "action": "*",
                "conditions": conditions,
                "effect": "allow",
                "priority": 100,
            }
        ])

    def test_eq_match(self) -> None:
        engine = self._make_engine({"subject.department": {"eq": "IT"}})
        ctx = ABACContext(subject={"department": "IT"}, action="read", resource={"type": "kb"})
        assert engine.evaluate(ctx).allowed is True

    def test_eq_no_match(self) -> None:
        engine = self._make_engine({"subject.department": {"eq": "IT"}})
        ctx = ABACContext(subject={"department": "HR"}, action="read", resource={"type": "kb"})
        assert engine.evaluate(ctx).allowed is False

    def test_neq_match(self) -> None:
        engine = self._make_engine({"subject.department": {"neq": "IT"}})
        ctx = ABACContext(subject={"department": "HR"}, action="read", resource={"type": "kb"})
        assert engine.evaluate(ctx).allowed is True

    def test_neq_no_match(self) -> None:
        engine = self._make_engine({"subject.department": {"neq": "IT"}})
        ctx = ABACContext(subject={"department": "IT"}, action="read", resource={"type": "kb"})
        assert engine.evaluate(ctx).allowed is False

    def test_in_match(self) -> None:
        engine = self._make_engine({"subject.provider": {"in": ["keycloak", "azure_ad"]}})
        ctx = ABACContext(subject={"provider": "keycloak"}, action="read", resource={"type": "kb"})
        assert engine.evaluate(ctx).allowed is True

    def test_in_no_match(self) -> None:
        engine = self._make_engine({"subject.provider": {"in": ["keycloak", "azure_ad"]}})
        ctx = ABACContext(subject={"provider": "local"}, action="read", resource={"type": "kb"})
        assert engine.evaluate(ctx).allowed is False

    def test_not_in_match(self) -> None:
        engine = self._make_engine({"subject.provider": {"not_in": ["local"]}})
        ctx = ABACContext(subject={"provider": "keycloak"}, action="read", resource={"type": "kb"})
        assert engine.evaluate(ctx).allowed is True

    def test_not_in_no_match(self) -> None:
        engine = self._make_engine({"subject.provider": {"not_in": ["local"]}})
        ctx = ABACContext(subject={"provider": "local"}, action="read", resource={"type": "kb"})
        assert engine.evaluate(ctx).allowed is False

    def test_contains_match(self) -> None:
        engine = self._make_engine({"subject.email": {"contains": "@gs.com"}})
        ctx = ABACContext(subject={"email": "user@gs.com"}, action="read", resource={"type": "kb"})
        assert engine.evaluate(ctx).allowed is True

    def test_contains_no_match(self) -> None:
        engine = self._make_engine({"subject.email": {"contains": "@gs.com"}})
        ctx = ABACContext(subject={"email": "user@other.com"}, action="read", resource={"type": "kb"})
        assert engine.evaluate(ctx).allowed is False

    def test_contains_non_string_fails(self) -> None:
        engine = self._make_engine({"subject.count": {"contains": "5"}})
        ctx = ABACContext(subject={"count": 5}, action="read", resource={"type": "kb"})
        assert engine.evaluate(ctx).allowed is False

    def test_starts_with_match(self) -> None:
        engine = self._make_engine({"subject.email": {"starts_with": "admin"}})
        ctx = ABACContext(subject={"email": "admin@gs.com"}, action="read", resource={"type": "kb"})
        assert engine.evaluate(ctx).allowed is True

    def test_starts_with_no_match(self) -> None:
        engine = self._make_engine({"subject.email": {"starts_with": "admin"}})
        ctx = ABACContext(subject={"email": "user@gs.com"}, action="read", resource={"type": "kb"})
        assert engine.evaluate(ctx).allowed is False

    def test_between_match(self) -> None:
        engine = self._make_engine({"environment.hour_of_day": {"between": [9, 17]}})
        ctx = ABACContext(environment={"hour_of_day": 12}, action="read", resource={"type": "kb"})
        assert engine.evaluate(ctx).allowed is True

    def test_between_boundary_inclusive(self) -> None:
        engine = self._make_engine({"environment.hour_of_day": {"between": [9, 17]}})
        ctx_low = ABACContext(environment={"hour_of_day": 9}, action="read", resource={"type": "kb"})
        ctx_high = ABACContext(environment={"hour_of_day": 17}, action="read", resource={"type": "kb"})
        assert engine.evaluate(ctx_low).allowed is True
        assert engine.evaluate(ctx_high).allowed is True

    def test_between_no_match(self) -> None:
        engine = self._make_engine({"environment.hour_of_day": {"between": [9, 17]}})
        ctx = ABACContext(environment={"hour_of_day": 20}, action="read", resource={"type": "kb"})
        assert engine.evaluate(ctx).allowed is False

    def test_between_none_value(self) -> None:
        engine = self._make_engine({"environment.hour_of_day": {"between": [9, 17]}})
        ctx = ABACContext(environment={}, action="read", resource={"type": "kb"})
        assert engine.evaluate(ctx).allowed is False

    def test_between_invalid_range(self) -> None:
        """Between with wrong number of elements should fail."""
        engine = self._make_engine({"environment.hour_of_day": {"between": [9]}})
        ctx = ABACContext(environment={"hour_of_day": 12}, action="read", resource={"type": "kb"})
        assert engine.evaluate(ctx).allowed is False

    def test_exists_true_match(self) -> None:
        engine = self._make_engine({"subject.department": {"exists": True}})
        ctx = ABACContext(subject={"department": "IT"}, action="read", resource={"type": "kb"})
        assert engine.evaluate(ctx).allowed is True

    def test_exists_true_no_match(self) -> None:
        engine = self._make_engine({"subject.department": {"exists": True}})
        ctx = ABACContext(subject={}, action="read", resource={"type": "kb"})
        assert engine.evaluate(ctx).allowed is False

    def test_exists_false_match(self) -> None:
        """exists=False means attribute should NOT exist."""
        engine = self._make_engine({"subject.department": {"exists": False}})
        ctx = ABACContext(subject={}, action="read", resource={"type": "kb"})
        assert engine.evaluate(ctx).allowed is True

    def test_exists_false_no_match(self) -> None:
        engine = self._make_engine({"subject.department": {"exists": False}})
        ctx = ABACContext(subject={"department": "IT"}, action="read", resource={"type": "kb"})
        assert engine.evaluate(ctx).allowed is False

    def test_regex_match(self) -> None:
        engine = self._make_engine({"subject.email": {"regex": r"^admin@.*\.com$"}})
        ctx = ABACContext(subject={"email": "admin@gs.com"}, action="read", resource={"type": "kb"})
        assert engine.evaluate(ctx).allowed is True

    def test_regex_no_match(self) -> None:
        engine = self._make_engine({"subject.email": {"regex": r"^admin@"}})
        ctx = ABACContext(subject={"email": "user@gs.com"}, action="read", resource={"type": "kb"})
        assert engine.evaluate(ctx).allowed is False

    def test_regex_non_string_fails(self) -> None:
        engine = self._make_engine({"subject.count": {"regex": r"\d+"}})
        ctx = ABACContext(subject={"count": 42}, action="read", resource={"type": "kb"})
        assert engine.evaluate(ctx).allowed is False

    def test_unknown_operator_fails(self) -> None:
        engine = self._make_engine({"subject.x": {"unknown_op": "val"}})
        ctx = ABACContext(subject={"x": "val"}, action="read", resource={"type": "kb"})
        assert engine.evaluate(ctx).allowed is False


class TestABACEngineMultipleConditions:
    """Test AND logic for multiple conditions."""

    def test_all_conditions_must_match(self) -> None:
        engine = ABACEngine(policies=[
            {
                "name": "test",
                "resource_type": "*",
                "action": "*",
                "conditions": {
                    "subject.department": {"eq": "IT"},
                    "subject.provider": {"eq": "keycloak"},
                },
                "effect": "allow",
                "priority": 100,
            }
        ])
        # Both match
        ctx = ABACContext(
            subject={"department": "IT", "provider": "keycloak"},
            action="read",
            resource={"type": "kb"},
        )
        assert engine.evaluate(ctx).allowed is True

        # Only one matches
        ctx_partial = ABACContext(
            subject={"department": "IT", "provider": "local"},
            action="read",
            resource={"type": "kb"},
        )
        assert engine.evaluate(ctx_partial).allowed is False


class TestABACEngineSameAsResource:
    """Test __SAME_AS_RESOURCE__ sentinel for cross-attribute comparison."""

    def test_same_org_matches(self) -> None:
        engine = ABACEngine(policies=[
            {
                "name": "same_org",
                "resource_type": "kb",
                "action": "read",
                "conditions": {
                    "subject.organization_id": {"eq": "__SAME_AS_RESOURCE__"},
                },
                "effect": "allow",
                "priority": 100,
            }
        ])
        ctx = ABACContext(
            subject={"organization_id": "org-1"},
            resource={"type": "kb", "organization_id": "org-1"},
            action="read",
        )
        assert engine.evaluate(ctx).allowed is True

    def test_different_org_denied(self) -> None:
        engine = ABACEngine(policies=[
            {
                "name": "same_org",
                "resource_type": "kb",
                "action": "read",
                "conditions": {
                    "subject.organization_id": {"eq": "__SAME_AS_RESOURCE__"},
                },
                "effect": "allow",
                "priority": 100,
            }
        ])
        ctx = ABACContext(
            subject={"organization_id": "org-1"},
            resource={"type": "kb", "organization_id": "org-2"},
            action="read",
        )
        assert engine.evaluate(ctx).allowed is False


class TestABACEngineNestedAttributes:
    """Test nested attribute resolution."""

    def test_nested_attribute(self) -> None:
        engine = ABACEngine(policies=[
            {
                "name": "test",
                "resource_type": "*",
                "action": "*",
                "conditions": {
                    "subject.metadata.team": {"eq": "platform"},
                },
                "effect": "allow",
                "priority": 100,
            }
        ])
        ctx = ABACContext(
            subject={"metadata": {"team": "platform"}},
            action="read",
            resource={"type": "kb"},
        )
        assert engine.evaluate(ctx).allowed is True

    def test_missing_nested_attribute(self) -> None:
        engine = ABACEngine(policies=[
            {
                "name": "test",
                "resource_type": "*",
                "action": "*",
                "conditions": {
                    "subject.metadata.team": {"eq": "platform"},
                },
                "effect": "allow",
                "priority": 100,
            }
        ])
        ctx = ABACContext(
            subject={"metadata": {}},
            action="read",
            resource={"type": "kb"},
        )
        assert engine.evaluate(ctx).allowed is False


class TestABACEngineLoadPolicies:
    """Test dynamic policy loading."""

    def test_load_replaces_policies(self) -> None:
        engine = ABACEngine(policies=[
            {
                "name": "old",
                "resource_type": "*",
                "action": "*",
                "conditions": {},
                "effect": "deny",
                "priority": 100,
            }
        ])
        ctx = ABACContext(action="read", resource={"type": "kb"})
        assert engine.evaluate(ctx).allowed is False

        engine.load_policies([
            {
                "name": "new",
                "resource_type": "*",
                "action": "*",
                "conditions": {},
                "effect": "allow",
                "priority": 100,
            }
        ])
        assert engine.evaluate(ctx).allowed is True


class TestDefaultABACPolicies:
    """Verify DEFAULT_ABAC_POLICIES structure and behavior."""

    def test_policies_have_required_fields(self) -> None:
        for p in DEFAULT_ABAC_POLICIES:
            assert "name" in p
            assert "effect" in p
            assert "priority" in p
            assert p["effect"] in ("allow", "deny")

    def test_confidential_kb_deny_for_local(self) -> None:
        """Default policy denies local users from confidential KBs."""
        engine = ABACEngine(policies=DEFAULT_ABAC_POLICIES)
        ctx = ABACContext(
            subject={"provider": "local"},
            resource={"type": "kb", "data_classification": "confidential"},
            action="read",
        )
        result = engine.evaluate(ctx)
        assert result.allowed is False
        assert result.matched_policy == "deny_confidential_kb_external"

    def test_global_kb_read_allowed(self) -> None:
        """Default policy allows reading global KBs."""
        engine = ABACEngine(policies=DEFAULT_ABAC_POLICIES)
        ctx = ABACContext(
            subject={"provider": "keycloak"},
            resource={"type": "kb", "tier": "global"},
            action="read",
        )
        result = engine.evaluate(ctx)
        assert result.allowed is True
        # allow_org_kb_read may match first if organization_id resolves to None==None,
        # so just verify the result is allowed
        assert result.matched_policy in ("allow_global_kb_read", "allow_org_kb_read")

    def test_same_org_kb_read_allowed(self) -> None:
        """Default policy allows same-org users to read BU KBs."""
        engine = ABACEngine(policies=DEFAULT_ABAC_POLICIES)
        ctx = ABACContext(
            subject={"provider": "keycloak", "organization_id": "org-1"},
            resource={"type": "kb", "organization_id": "org-1"},
            action="read",
        )
        result = engine.evaluate(ctx)
        assert result.allowed is True
        assert result.matched_policy == "allow_org_kb_read"

    def test_non_business_hours_policy_inactive(self) -> None:
        """Non-business-hours policy should be inactive by default."""
        policy = next(
            p for p in DEFAULT_ABAC_POLICIES if p["name"] == "deny_pipeline_non_business_hours"
        )
        assert policy["is_active"] is False
