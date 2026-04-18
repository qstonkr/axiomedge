"""Tests for Organization + OrgMembership SQLAlchemy models (data model prep)."""

from __future__ import annotations

from src.stores.postgres.models import OrganizationModel, OrgMembershipModel


def test_organization_table_name() -> None:
    assert OrganizationModel.__tablename__ == "organizations"


def test_org_membership_table_name() -> None:
    assert OrgMembershipModel.__tablename__ == "org_memberships"


def test_organization_has_required_columns() -> None:
    cols = {c.name for c in OrganizationModel.__table__.columns}
    required = {
        "id", "slug", "name", "status", "settings",
        "sso_provider", "sso_metadata",
        "max_users", "max_kbs", "max_storage_gb",
        "created_at", "updated_at",
    }
    assert required.issubset(cols), f"missing: {required - cols}"


def test_organization_slug_unique() -> None:
    slug_col = OrganizationModel.__table__.columns["slug"]
    assert slug_col.unique is True


def test_org_membership_has_required_columns() -> None:
    cols = {c.name for c in OrgMembershipModel.__table__.columns}
    required = {
        "id", "user_id", "organization_id", "role",
        "invited_by", "invited_at", "joined_at", "status",
    }
    assert required.issubset(cols), f"missing: {required - cols}"


def test_org_membership_unique_constraint_on_user_org() -> None:
    constraint_names = {c.name for c in OrgMembershipModel.__table__.constraints}
    assert "uq_org_member_user_org" in constraint_names


def test_kb_config_already_has_org_fk_column() -> None:
    """Existing KBConfigModel.organization_id is the future FK target."""
    from src.stores.postgres.models import KBConfigModel
    cols = {c.name for c in KBConfigModel.__table__.columns}
    assert "organization_id" in cols


def test_user_model_already_has_org_column() -> None:
    """Existing UserModel.organization_id is the future FK target."""
    from src.auth.models import UserModel
    cols = {c.name for c in UserModel.__table__.columns}
    assert "organization_id" in cols
