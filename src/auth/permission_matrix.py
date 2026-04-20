"""Centralized permission matrix for HTTP routes (B-0 Day 4).

Single source of truth that maps ``(method, path)`` to a required
``(resource, action)`` permission. ``AuthMiddleware`` consults this table
after the user is authenticated and 403s on mismatch — so individual route
handlers don't need ``Depends(require_permission(...))`` boilerplate.

**Ordering matters**: rules are evaluated top-down, first match wins. Put
more specific patterns first.

**Unmatched paths** fall through with no permission requirement (auth alone
is sufficient). Day 5 audit will flag any business-critical endpoint that
slips through; until then, default-allow-on-auth keeps existing behavior
intact for routes the matrix doesn't yet cover.

Add new rules here when introducing endpoints — never inline them in
handlers, so the audit table stays canonical.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class PermissionRule:
    """One row of the matrix."""

    pattern: re.Pattern[str]
    methods: frozenset[str]  # {"GET", "POST", ...} or {"*"}
    resource: str
    action: str

    def matches(self, method: str, path: str) -> bool:
        if "*" not in self.methods and method not in self.methods:
            return False
        return self.pattern.match(path) is not None


def _rule(
    pattern: str, methods: Iterable[str] | str, resource: str, action: str,
) -> PermissionRule:
    methods_set = (
        frozenset(methods.split("|")) if isinstance(methods, str)
        else frozenset(methods)
    )
    return PermissionRule(re.compile(pattern), methods_set, resource, action)


# =============================================================================
# Permission rules
# Order: most-specific first. First match wins.
# =============================================================================

PERMISSION_RULES: list[PermissionRule] = [
    # ── Auth introspection (any authenticated user) — explicit allow
    # The dependency for these is just get_current_user; no extra perm.
    _rule(r"^/api/v1/auth/me$", "*", "_self", "_introspect"),
    _rule(r"^/api/v1/auth/logout$", "*", "_self", "_introspect"),
    _rule(r"^/api/v1/auth/change-password$", "*", "_self", "_introspect"),
    _rule(r"^/api/v1/auth/switch-org$", "*", "_self", "_introspect"),

    # ── Auth admin (user CRUD, ABAC policies) — org:user:manage
    _rule(r"^/api/v1/auth/users(?:/.*)?$", "GET|POST|PUT|DELETE|PATCH", "org:user", "manage"),
    _rule(r"^/api/v1/auth/policies(?:/.*)?$", "GET|POST|PUT|DELETE|PATCH", "org", "manage"),
    _rule(r"^/api/v1/auth/roles(?:/.*)?$", "GET|POST|PUT|DELETE|PATCH", "org:user", "manage"),

    # ── Distill (all paths under /api/v1/distill) — distill:manage
    # Includes builds, edge-servers, training-data, profiles, etc.
    _rule(r"^/api/v1/distill(?:/.*)?$", "*", "distill", "manage"),

    # ── Data sources — data_source:manage
    _rule(r"^/api/v1/admin/data-sources(?:/.*)?$", "*", "data_source", "manage"),

    # ── Ingestion / pipeline (admin)
    _rule(r"^/api/v1/admin/pipeline(?:/.*)?$", "*", "pipeline", "execute"),
    _rule(r"^/api/v1/admin/knowledge/ingest(?:/.*)?$", "*", "pipeline", "execute"),
    _rule(r"^/api/v1/knowledge/(?:ingest|upload)(?:/.*)?$", "*", "document", "write"),
    _rule(r"^/api/v1/jobs(?:/.*)?$", "*", "pipeline", "execute"),

    # ── Glossary writes (POST/PUT/PATCH/DELETE) — glossary:write
    _rule(r"^/api/v1/admin/glossary(?:/.*)?$", "POST|PUT|PATCH|DELETE", "glossary", "write"),
    # Glossary reads
    _rule(r"^/api/v1/admin/glossary(?:/.*)?$", "GET", "glossary", "read"),

    # ── KB lifecycle on a specific KB
    _rule(r"^/api/v1/admin/kb/[^/]+/members(?:/.*)?$", "POST|DELETE", "kb", "write"),
    _rule(r"^/api/v1/admin/kb/[^/]+(?:/.*)?$", "DELETE", "kb", "delete"),
    _rule(r"^/api/v1/admin/kb/[^/]+(?:/.*)?$", "PUT|PATCH", "kb", "write"),
    _rule(r"^/api/v1/admin/kb/[^/]+(?:/.*)?$", "GET", "kb", "read"),
    _rule(r"^/api/v1/admin/kb/?$", "POST", "kb", "create"),
    _rule(r"^/api/v1/admin/kb/?$", "GET", "kb", "read"),
    _rule(r"^/api/v1/admin/kb/search-cache/clear$", "POST", "kb", "write"),

    # ── /api/v1/kb/* (mostly read; /create is the personal-KB self-service path)
    # B-1 Day 1: /api/v1/kb/create is the ONLY route MEMBER can use to create
    # a KB, and it is hard-locked to tier=personal in the handler. So the
    # matrix permission is the lower-bar document:write (anyone who can write
    # documents can carve their personal KB). Team/global KB creation lives at
    # /api/v1/admin/kb (kb:create — OWNER/ADMIN only) further down this list.
    _rule(r"^/api/v1/kb/create$", "POST", "document", "write"),
    _rule(r"^/api/v1/kb/list$", "GET", "kb", "read"),
    _rule(r"^/api/v1/kb/[^/]+$", "DELETE", "kb", "delete"),
    _rule(r"^/api/v1/kb(?:/.*)?$", "GET", "kb", "read"),

    # ── Search
    _rule(r"^/api/v1/search/hub(?:/.*)?$", "POST|GET", "document", "search"),
    _rule(r"^/api/v1/admin/search(?:/.*)?$", "GET", "search", "analytics"),
    _rule(r"^/api/v1/search-groups(?:/.*)?$", "POST|PUT|DELETE|PATCH", "kb", "write"),
    _rule(r"^/api/v1/search-groups(?:/.*)?$", "GET", "kb", "read"),

    # ── Agentic
    _rule(r"^/api/v1/agentic(?:/.*)?$", "*", "agentic", "ask"),

    # ── Feedback admin (review)
    _rule(r"^/api/v1/admin/feedback(?:/.*)?$", "POST|PUT|PATCH|DELETE", "feedback", "review"),
    _rule(r"^/api/v1/admin/feedback(?:/.*)?$", "GET", "feedback", "review"),
    _rule(r"^/api/v1/admin/error-reports(?:/.*)?$", "*", "feedback", "review"),

    # ── Feedback / error-report user-scope (본인 것만 조회) — `feedback:submit` 권한
    # MEMBER/VIEWER 가 가지고 있어 /my-feedback 화면에서 사용. POST 는 이미
    # /api/v1/knowledge/feedback (feedback:submit) 으로 처리됨.
    _rule(r"^/api/v1/knowledge/feedback/my$", "GET", "feedback", "submit"),
    _rule(r"^/api/v1/knowledge/error-reports/my$", "GET", "feedback", "submit"),
    # 추천 검색어 (popular queries) — aggregate 통계라 모든 role 호출 가능.
    # `feedback:submit` 으로 권한 요구 (POST 사용자 화면 권한과 동등).
    _rule(r"^/api/v1/knowledge/popular-queries$", "GET", "feedback", "submit"),

    # ── Quality / golden-set / verification
    _rule(r"^/api/v1/admin/golden-set(?:/.*)?$", "POST|PUT|DELETE|PATCH", "quality", "write"),
    _rule(r"^/api/v1/admin/golden-set(?:/.*)?$", "GET", "quality", "read"),
    _rule(r"^/api/v1/admin/eval(?:/.*)?$", "POST|PUT|DELETE", "quality", "write"),
    _rule(r"^/api/v1/admin/eval(?:/.*)?$", "GET", "quality", "read"),
    _rule(r"^/api/v1/admin/verification(?:/.*)?$", "POST|PUT|DELETE", "quality", "write"),
    _rule(r"^/api/v1/admin/verification(?:/.*)?$", "GET", "quality", "read"),
    _rule(r"^/api/v1/admin/dedup(?:/.*)?$", "POST|PUT|DELETE", "quality", "write"),
    _rule(r"^/api/v1/admin/dedup(?:/.*)?$", "GET", "quality", "read"),
    _rule(r"^/api/v1/admin/trust-scores(?:/.*)?$", "POST|PUT|DELETE", "quality", "write"),
    _rule(r"^/api/v1/admin/trust-scores(?:/.*)?$", "GET", "quality", "read"),
    _rule(r"^/api/v1/admin/contributors(?:/.*)?$", "GET", "quality", "read"),
    _rule(r"^/api/v1/admin/transparency(?:/.*)?$", "GET", "quality", "read"),
    _rule(r"^/api/v1/admin/(?:vectorstore|embedding|cache|categories)/.*$", "GET", "quality", "read"),
    _rule(r"^/api/v1/admin/knowledge/.+/(?:provenance|lineage|versions)$", "GET", "quality", "read"),
    _rule(r"^/api/v1/admin/documents/[^/]+/(?:rollback|approve)$", "POST", "quality", "write"),
    _rule(r"^/api/v1/admin/eval-results(?:/.*)?$", "GET", "quality", "read"),

    # ── Ownership
    _rule(r"^/api/v1/admin/ownership(?:/.*)?$", "POST|PUT|DELETE", "kb", "write"),
    _rule(r"^/api/v1/admin/ownership(?:/.*)?$", "GET", "kb", "read"),

    # ── Misc admin (configuration, qdrant introspection)
    _rule(r"^/api/v1/admin/config/weights(?:/.*)?$", "*", "org", "manage"),
    _rule(r"^/api/v1/admin/qdrant(?:/.*)?$", "GET", "kb", "read"),
    _rule(r"^/api/v1/admin/categories(?:/.*)?$", "GET", "quality", "read"),
    _rule(r"^/api/v1/admin/ingestion(?:/.*)?$", "GET", "pipeline", "execute"),
]


# Sentinel resource for self-introspection routes — bypasses RBAC, only auth.
SELF_INTROSPECT_RESOURCE = "_self"


def find_required_permission(method: str, path: str) -> tuple[str, str] | None:
    """Look up the required (resource, action) for a request.

    Returns None when no rule matches OR when the matched rule is a
    self-introspection sentinel (auth alone is enough). The caller treats
    both cases the same way: pass through after auth.
    """
    for rule in PERMISSION_RULES:
        if rule.matches(method, path):
            if rule.resource == SELF_INTROSPECT_RESOURCE:
                return None
            return rule.resource, rule.action
    return None
