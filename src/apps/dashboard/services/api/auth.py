"""Auth: users, roles, permissions, ABAC, activities API functions."""

from __future__ import annotations

from typing import Any

import streamlit as st

from services.api._core import (
    _delete,
    _get,
    _post,
    _request,
)


# ============================================================================
# Auth: Users, Roles, Permissions, ABAC
# ============================================================================

def list_auth_users() -> dict:
    return _get("/api/v1/auth/users")


def create_auth_user(body: dict) -> dict:
    return _post("/api/v1/auth/users", body)


def assign_user_role(user_id: str, body: dict) -> dict:
    return _post(f"/api/v1/auth/users/{user_id}/roles", body)


def get_kb_permissions(kb_id: str) -> dict:
    return _get(f"/api/v1/auth/kb/{kb_id}/permissions")


def add_kb_permission(kb_id: str, body: dict) -> dict:
    return _post(f"/api/v1/auth/kb/{kb_id}/permissions", body)


def remove_kb_permission(kb_id: str, user_id: str) -> dict:
    return _delete(f"/api/v1/auth/kb/{kb_id}/permissions/{user_id}")


def list_abac_policies() -> dict:
    return _get("/api/v1/auth/abac/policies")


def create_abac_policy(body: dict) -> dict:
    return _post("/api/v1/auth/abac/policies", body)


def delete_abac_policy(policy_id: str) -> dict:
    return _delete(f"/api/v1/auth/abac/policies/{policy_id}")


# ============================================================================
# My Activities
# ============================================================================

@st.cache_data(ttl=60)
def get_my_activities_summary() -> dict:
    return _get("/api/v1/auth/my-activities/summary")


def get_my_activities(**params: Any) -> dict:
    clean = {k: v for k, v in params.items() if v is not None}
    return _request("GET", "/api/v1/auth/my-activities", params=clean)
