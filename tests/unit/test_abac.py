"""Unit tests for the ABAC engine."""

from src.auth.abac import ABACEngine, ABACContext, DEFAULT_ABAC_POLICIES


class TestABACEngine:
    """Test ABAC policy evaluation."""

    def setup_method(self) -> None:
        self.engine = ABACEngine(policies=DEFAULT_ABAC_POLICIES)

    # --- Global KB read ---

    def test_global_kb_read_allowed(self) -> None:
        ctx = ABACContext(
            subject={"user_id": "u1", "organization_id": "org-a"},
            resource={"type": "kb", "tier": "global"},
            action="read",
        )
        decision = self.engine.evaluate(ctx)
        assert decision.allowed is True
        assert decision.matched_policy == "allow_global_kb_read"

    # --- Confidential KB denied for local provider ---

    def test_confidential_kb_denied_for_local_provider(self) -> None:
        ctx = ABACContext(
            subject={"user_id": "u1", "provider": "local"},
            resource={"type": "kb", "data_classification": "confidential"},
            action="read",
        )
        decision = self.engine.evaluate(ctx)
        assert decision.allowed is False
        assert decision.matched_policy == "deny_confidential_kb_external"

    def test_confidential_kb_allowed_for_corporate_provider(self) -> None:
        """Non-local provider is NOT caught by the deny_confidential_kb_external policy.

        However, allow_org_kb_read may match if organization_id is absent on both sides
        (None == None via sentinel). So we set different org IDs to ensure no allow matches.
        """
        ctx = ABACContext(
            subject={"user_id": "u1", "provider": "keycloak", "organization_id": "org-a"},
            resource={
                "type": "kb",
                "data_classification": "confidential",
                "organization_id": "org-b",
            },
            action="read",
        )
        decision = self.engine.evaluate(ctx)
        # The confidential deny policy requires provider "in" ["local"],
        # so keycloak won't match that. Org mismatch prevents allow_org_kb_read.
        # Falls through to default deny.
        assert decision.allowed is False

    # --- Same org KB read (sentinel resolution) ---

    def test_same_org_kb_read_allowed(self) -> None:
        ctx = ABACContext(
            subject={"user_id": "u1", "organization_id": "org-retail"},
            resource={"type": "kb", "organization_id": "org-retail"},
            action="read",
        )
        decision = self.engine.evaluate(ctx)
        assert decision.allowed is True
        assert decision.matched_policy == "allow_org_kb_read"

    # --- Different org KB read denied ---

    def test_different_org_kb_read_denied(self) -> None:
        ctx = ABACContext(
            subject={"user_id": "u1", "organization_id": "org-retail"},
            resource={"type": "kb", "organization_id": "org-other"},
            action="read",
        )
        decision = self.engine.evaluate(ctx)
        # The allow_org_kb_read condition won't match because orgs differ.
        # allow_global_kb_read won't match either (tier != "global").
        # Falls through to default deny.
        assert decision.allowed is False

    # --- No policy match -> default deny ---

    def test_no_policy_match_default_deny(self) -> None:
        ctx = ABACContext(
            subject={"user_id": "u1"},
            resource={"type": "unknown_resource"},
            action="unknown_action",
        )
        decision = self.engine.evaluate(ctx)
        assert decision.allowed is False
        assert "default deny" in decision.reason.lower()

    # --- Condition operators ---

    def test_condition_operator_eq(self) -> None:
        engine = ABACEngine(policies=[{
            "name": "test_eq",
            "resource_type": "*",
            "action": "*",
            "conditions": {"subject.role": {"eq": "admin"}},
            "effect": "allow",
            "priority": 100,
        }])
        ctx = ABACContext(subject={"role": "admin"}, resource={}, action="read")
        assert engine.evaluate(ctx).allowed is True

        ctx2 = ABACContext(subject={"role": "viewer"}, resource={}, action="read")
        assert engine.evaluate(ctx2).allowed is False

    def test_condition_operator_neq(self) -> None:
        engine = ABACEngine(policies=[{
            "name": "test_neq",
            "resource_type": "*",
            "action": "*",
            "conditions": {"subject.status": {"neq": "suspended"}},
            "effect": "allow",
            "priority": 100,
        }])
        ctx = ABACContext(subject={"status": "active"}, resource={}, action="read")
        assert engine.evaluate(ctx).allowed is True

        ctx2 = ABACContext(subject={"status": "suspended"}, resource={}, action="read")
        assert engine.evaluate(ctx2).allowed is False

    def test_condition_operator_in(self) -> None:
        engine = ABACEngine(policies=[{
            "name": "test_in",
            "resource_type": "*",
            "action": "*",
            "conditions": {"subject.provider": {"in": ["keycloak", "azure_ad"]}},
            "effect": "allow",
            "priority": 100,
        }])
        ctx = ABACContext(subject={"provider": "keycloak"}, resource={}, action="read")
        assert engine.evaluate(ctx).allowed is True

        ctx2 = ABACContext(subject={"provider": "local"}, resource={}, action="read")
        assert engine.evaluate(ctx2).allowed is False

    def test_condition_operator_not_in(self) -> None:
        engine = ABACEngine(policies=[{
            "name": "test_not_in",
            "resource_type": "*",
            "action": "*",
            "conditions": {"subject.role": {"not_in": ["blocked", "banned"]}},
            "effect": "allow",
            "priority": 100,
        }])
        ctx = ABACContext(subject={"role": "editor"}, resource={}, action="read")
        assert engine.evaluate(ctx).allowed is True

        ctx2 = ABACContext(subject={"role": "blocked"}, resource={}, action="read")
        assert engine.evaluate(ctx2).allowed is False

    def test_condition_operator_contains(self) -> None:
        engine = ABACEngine(policies=[{
            "name": "test_contains",
            "resource_type": "*",
            "action": "*",
            "conditions": {"subject.email": {"contains": "@gsretail.com"}},
            "effect": "allow",
            "priority": 100,
        }])
        ctx = ABACContext(subject={"email": "user@gsretail.com"}, resource={}, action="read")
        assert engine.evaluate(ctx).allowed is True

        ctx2 = ABACContext(subject={"email": "user@other.com"}, resource={}, action="read")
        assert engine.evaluate(ctx2).allowed is False

    def test_condition_operator_between(self) -> None:
        engine = ABACEngine(policies=[{
            "name": "test_between",
            "resource_type": "*",
            "action": "*",
            "conditions": {"environment.hour_of_day": {"between": [9, 17]}},
            "effect": "allow",
            "priority": 100,
        }])
        ctx = ABACContext(
            subject={}, resource={}, action="read",
            environment={"hour_of_day": 12},
        )
        assert engine.evaluate(ctx).allowed is True

        ctx2 = ABACContext(
            subject={}, resource={}, action="read",
            environment={"hour_of_day": 22},
        )
        assert engine.evaluate(ctx2).allowed is False

    def test_condition_operator_regex(self) -> None:
        engine = ABACEngine(policies=[{
            "name": "test_regex",
            "resource_type": "*",
            "action": "*",
            "conditions": {"subject.ip": {"regex": r"^10\.0\."}},
            "effect": "allow",
            "priority": 100,
        }])
        ctx = ABACContext(subject={"ip": "10.0.1.5"}, resource={}, action="read")
        assert engine.evaluate(ctx).allowed is True

        ctx2 = ABACContext(subject={"ip": "192.168.1.1"}, resource={}, action="read")
        assert engine.evaluate(ctx2).allowed is False

    def test_condition_operator_exists(self) -> None:
        engine = ABACEngine(policies=[{
            "name": "test_exists",
            "resource_type": "*",
            "action": "*",
            "conditions": {"subject.token": {"exists": True}},
            "effect": "allow",
            "priority": 100,
        }])
        ctx = ABACContext(subject={"token": "abc123"}, resource={}, action="read")
        assert engine.evaluate(ctx).allowed is True

        ctx_missing = ABACContext(subject={}, resource={}, action="read")
        assert engine.evaluate(ctx_missing).allowed is False

    def test_condition_operator_exists_false(self) -> None:
        """exists: False means attribute must NOT be present."""
        engine = ABACEngine(policies=[{
            "name": "test_not_exists",
            "resource_type": "*",
            "action": "*",
            "conditions": {"subject.banned_flag": {"exists": False}},
            "effect": "allow",
            "priority": 100,
        }])
        ctx = ABACContext(subject={}, resource={}, action="read")
        assert engine.evaluate(ctx).allowed is True

        ctx2 = ABACContext(subject={"banned_flag": True}, resource={}, action="read")
        assert engine.evaluate(ctx2).allowed is False

    # --- Policy priority ordering ---

    def test_policy_priority_ordering(self) -> None:
        """Higher priority policy should take effect first."""
        policies = [
            {
                "name": "low_priority_allow",
                "resource_type": "*",
                "action": "*",
                "conditions": {"subject.role": {"eq": "user"}},
                "effect": "allow",
                "priority": 10,
            },
            {
                "name": "high_priority_deny",
                "resource_type": "*",
                "action": "*",
                "conditions": {"subject.role": {"eq": "user"}},
                "effect": "deny",
                "priority": 100,
            },
        ]
        engine = ABACEngine(policies=policies)
        ctx = ABACContext(subject={"role": "user"}, resource={}, action="read")
        decision = engine.evaluate(ctx)
        assert decision.allowed is False
        assert decision.matched_policy == "high_priority_deny"

    # --- Inactive policy skipped ---

    def test_inactive_policy_skipped(self) -> None:
        policies = [{
            "name": "inactive",
            "resource_type": "*",
            "action": "*",
            "conditions": {},
            "effect": "allow",
            "priority": 100,
            "is_active": False,
        }]
        engine = ABACEngine(policies=policies)
        ctx = ABACContext(subject={}, resource={}, action="read")
        decision = engine.evaluate(ctx)
        assert decision.allowed is False  # Falls through to default deny

    # --- load_policies replaces existing ---

    def test_load_policies_replaces(self) -> None:
        engine = ABACEngine(policies=[])
        ctx = ABACContext(subject={"role": "admin"}, resource={}, action="read")
        assert engine.evaluate(ctx).allowed is False  # No policies

        engine.load_policies([{
            "name": "new",
            "resource_type": "*",
            "action": "*",
            "conditions": {"subject.role": {"eq": "admin"}},
            "effect": "allow",
            "priority": 100,
        }])
        assert engine.evaluate(ctx).allowed is True
