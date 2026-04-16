"""Lucene query sanitization utilities (SSOT).

All Lucene special character escaping and query building should use
these functions instead of inline regex replacements.

Created: 2026-03-09 (Knowledge Graph Refactoring Phase 1)
"""

from __future__ import annotations

import re

_LUCENE_SPECIAL = re.compile(r'[+\-&|!(){}[\]^"~*?:\\/]')
_LUCENE_AND = re.compile(r"\bAND\b")
_LUCENE_OR = re.compile(r"\bOR\b")
_LUCENE_NOT = re.compile(r"\bNOT\b")
_MULTI_SPACE = re.compile(r"\s+")
MAX_LUCENE_TERMS = 20


def sanitize_lucene(text: str) -> str:
    """Remove Lucene special characters and reserved words.

    Matches the behaviour previously in ``Neo4jGraphStore._sanitize_lucene``.
    """
    s = _LUCENE_SPECIAL.sub(" ", text)
    s = _LUCENE_AND.sub(" ", s)
    s = _LUCENE_OR.sub(" ", s)
    s = _LUCENE_NOT.sub(" ", s)
    return _MULTI_SPACE.sub(" ", s).strip()


def build_lucene_or_query(
    terms: list[str],
    max_terms: int = MAX_LUCENE_TERMS,
) -> str:
    """Build a sanitized ``OR`` query string from *terms*."""
    cleaned = [sanitize_lucene(t) for t in terms if t and t.strip()]
    cleaned = [t for t in cleaned if t][:max_terms]
    return " OR ".join(cleaned) if cleaned else ""
