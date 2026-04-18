"""Unit tests for src/auth/permission_matrix.py.

Verifies that ``find_required_permission`` resolves the canonical
(resource, action) pair for representative endpoints. The matrix is
order-sensitive — these tests pin the expected resolution so that adding
new rules later doesn't silently change which permission an existing
endpoint maps to.
"""

from __future__ import annotations

import pytest

from src.auth.permission_matrix import find_required_permission


@pytest.mark.parametrize("method,path,expected", [
    # Auth introspection — sentinel, returns None (auth-only)
    ("GET", "/api/v1/auth/me", None),
    ("POST", "/api/v1/auth/logout", None),
    ("POST", "/api/v1/auth/change-password", None),

    # Auth admin
    ("GET", "/api/v1/auth/users", ("org:user", "manage")),
    ("POST", "/api/v1/auth/users", ("org:user", "manage")),
    ("DELETE", "/api/v1/auth/users/abc", ("org:user", "manage")),
    ("GET", "/api/v1/auth/policies", ("org", "manage")),

    # Distill (everything)
    ("POST", "/api/v1/distill/builds", ("distill", "manage")),
    ("GET", "/api/v1/distill/profiles", ("distill", "manage")),
    ("DELETE", "/api/v1/distill/edge-servers/store-001", ("distill", "manage")),

    # Data sources
    ("GET", "/api/v1/admin/data-sources", ("data_source", "manage")),
    ("POST", "/api/v1/admin/data-sources/abc/trigger", ("data_source", "manage")),

    # Glossary
    ("POST", "/api/v1/admin/glossary", ("glossary", "write")),
    ("PUT", "/api/v1/admin/glossary/term-1", ("glossary", "write")),
    ("DELETE", "/api/v1/admin/glossary/term-1", ("glossary", "write")),
    ("GET", "/api/v1/admin/glossary", ("glossary", "read")),
    ("GET", "/api/v1/admin/glossary/domain-stats", ("glossary", "read")),

    # KB lifecycle
    ("POST", "/api/v1/admin/kb", ("kb", "create")),
    ("GET", "/api/v1/admin/kb", ("kb", "read")),
    ("GET", "/api/v1/admin/kb/abc", ("kb", "read")),
    ("GET", "/api/v1/admin/kb/abc/stats", ("kb", "read")),
    ("PUT", "/api/v1/admin/kb/abc", ("kb", "write")),
    ("DELETE", "/api/v1/admin/kb/abc", ("kb", "delete")),
    ("POST", "/api/v1/admin/kb/abc/members", ("kb", "write")),
    ("DELETE", "/api/v1/admin/kb/abc/members/u1", ("kb", "write")),

    # Legacy /api/v1/kb
    ("POST", "/api/v1/kb/create", ("kb", "create")),
    ("GET", "/api/v1/kb/list", ("kb", "read")),
    ("DELETE", "/api/v1/kb/abc", ("kb", "delete")),

    # Search / search-groups / agentic
    ("POST", "/api/v1/search/hub", ("document", "search")),
    ("GET", "/api/v1/admin/search/history", ("search", "analytics")),
    ("GET", "/api/v1/search-groups", ("kb", "read")),
    ("POST", "/api/v1/search-groups", ("kb", "write")),
    ("POST", "/api/v1/agentic/ask", ("agentic", "ask")),
    ("GET", "/api/v1/agentic/traces/abc", ("agentic", "ask")),

    # Pipeline + jobs + ingest
    ("POST", "/api/v1/admin/pipeline/publish/execute", ("pipeline", "execute")),
    ("POST", "/api/v1/admin/knowledge/ingest", ("pipeline", "execute")),
    ("POST", "/api/v1/knowledge/ingest", ("document", "write")),
    ("POST", "/api/v1/knowledge/upload", ("document", "write")),
    ("GET", "/api/v1/jobs/abc", ("pipeline", "execute")),

    # Quality / golden-set
    ("GET", "/api/v1/admin/golden-set", ("quality", "read")),
    ("POST", "/api/v1/admin/golden-set", ("quality", "write")),
    ("DELETE", "/api/v1/admin/golden-set/x", ("quality", "write")),
    ("POST", "/api/v1/admin/eval/trigger", ("quality", "write")),
    ("GET", "/api/v1/admin/eval/history", ("quality", "read")),
    ("GET", "/api/v1/admin/eval-results/summary", ("quality", "read")),
    ("POST", "/api/v1/admin/verification/doc-1/vote", ("quality", "write")),
    ("POST", "/api/v1/admin/dedup/resolve", ("quality", "write")),
    ("GET", "/api/v1/admin/contributors", ("quality", "read")),

    # Feedback admin
    ("GET", "/api/v1/admin/feedback/list", ("feedback", "review")),
    ("PATCH", "/api/v1/admin/feedback/abc", ("feedback", "review")),
    ("GET", "/api/v1/admin/error-reports", ("feedback", "review")),

    # Ownership
    ("GET", "/api/v1/admin/ownership/documents", ("kb", "read")),
    ("POST", "/api/v1/admin/ownership/documents", ("kb", "write")),

    # Misc admin
    ("PUT", "/api/v1/admin/config/weights", ("org", "manage")),
    ("GET", "/api/v1/admin/qdrant/collections", ("kb", "read")),

    # Unknown — falls through to None (auth-only)
    ("GET", "/api/v1/something/random", None),
    ("POST", "/api/v1/totally-new", None),
])
def test_find_required_permission(
    method: str, path: str, expected: tuple[str, str] | None,
) -> None:
    assert find_required_permission(method, path) == expected
