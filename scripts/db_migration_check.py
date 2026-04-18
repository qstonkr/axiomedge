#!/usr/bin/env python3
"""Sanity-check an Alembic migration file for zero-downtime safety.

Checks for common dangerous patterns and warns the author. Not a substitute
for human review — just a fast sanity gate before merge / apply.

Usage:
    python scripts/db_migration_check.py migrations/versions/0002_xxx.py
    make db-check FILE=migrations/versions/0002_xxx.py

Exit code: 0 if no issues; 1 if any DANGEROUS pattern detected.
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


# Dangerous patterns + remediation hints.
# Each entry: (pattern_regex, severity, hint)
_DANGEROUS_PATTERNS: list[tuple[str, str, str]] = [
    # add_column followed (within 400 chars) by nullable=False — handles nested Column(...)
    (
        r"add_column\([\s\S]{0,400}?nullable\s*=\s*False",
        "DANGEROUS",
        "ADD NOT NULL column → use 3-step (nullable + backfill + alter). See migrations/PATTERNS.md §1",
    ),
    (
        r"\bdrop_column\(",
        "DANGEROUS",
        "DROP COLUMN → ensure ALL app instances stopped reading it BEFORE applying. See PATTERNS.md §2",
    ),
    (
        r"\bdrop_table\(",
        "DANGEROUS",
        "DROP TABLE → same as drop_column. See PATTERNS.md §2",
    ),
    (
        r"alter_column\([\s\S]{0,200}?new_column_name\s*=",
        "DANGEROUS",
        "RENAME COLUMN → use Expand-Contract (3 steps with trigger). See PATTERNS.md §3",
    ),
    (
        r"alter_column\([\s\S]{0,200}?type_\s*=",
        "WARN",
        "ALTER COLUMN TYPE — verify backward-compat. Truncating types need 3-step. PATTERNS.md §5",
    ),
    (
        r"create_index\([\s\S]{0,300}?postgresql_concurrently\s*=\s*True",
        "OK",
        "CONCURRENTLY index creation — good for zero-downtime",
    ),
]


def _has_create_index_without_concurrent(text: str) -> list[int]:
    """Find create_index calls that lack postgresql_concurrently=True nearby."""
    line_nos: list[int] = []
    for m in re.finditer(r"create_index\(", text):
        snippet = text[m.start() : m.start() + 300]
        if "postgresql_concurrently=True" not in snippet.replace(" ", ""):
            line_nos.append(text[: m.start()].count("\n") + 1)
    return line_nos


def _has_fk_without_not_valid(text: str) -> list[int]:
    """Find create_foreign_key calls that lack NOT VALID hint nearby."""
    line_nos: list[int] = []
    for m in re.finditer(r"create_foreign_key\(", text):
        snippet = text[m.start() : m.start() + 400]
        if "NOT VALID" not in snippet:
            line_nos.append(text[: m.start()].count("\n") + 1)
    return line_nos


def _extract_upgrade_block(text: str) -> str:
    """Return only the body of the ``upgrade()`` function — destructive ops in
    ``downgrade()`` are intentional (rollback) and must not be flagged.

    Heuristic: from ``def upgrade(`` to next top-level ``def`` (or EOF).
    """
    m = re.search(r"def\s+upgrade\s*\(", text)
    if not m:
        return text  # no upgrade fn — fall back to whole file
    start = m.start()
    next_def = re.search(r"\ndef\s+\w+\s*\(", text[start + 1 :])
    end = start + 1 + next_def.start() if next_def else len(text)
    return text[start:end]


def check_file(path: Path) -> int:
    if not path.exists():
        logger.error("File not found: %s", path)
        return 2

    raw_text = path.read_text()
    text = _extract_upgrade_block(raw_text)
    # Compute line offset so reported lines match the original file
    offset = raw_text.find(text) if text != raw_text else 0
    line_offset = raw_text[:offset].count("\n")

    issues: list[tuple[str, str, int]] = []  # (severity, hint, line_no)

    for pattern, severity, hint in _DANGEROUS_PATTERNS:
        for m in re.finditer(pattern, text):
            line_no = text[: m.start()].count("\n") + 1 + line_offset
            issues.append((severity, hint, line_no))

    for ln in _has_create_index_without_concurrent(text):
        issues.append((
            "WARN",
            "CREATE INDEX without CONCURRENTLY — locks writes on large tables. PATTERNS.md §4",
            ln + line_offset,
        ))
    for ln in _has_fk_without_not_valid(text):
        issues.append((
            "WARN",
            "FK constraint without NOT VALID — full table scan. PATTERNS.md §6",
            ln + line_offset,
        ))

    dangerous_count = sum(1 for sev, _, _ in issues if sev == "DANGEROUS")
    warn_count = sum(1 for sev, _, _ in issues if sev == "WARN")
    ok_count = sum(1 for sev, _, _ in issues if sev == "OK")

    logger.info("Checking %s", path)
    if not issues:
        logger.info("  ✓ No risky patterns detected")
        return 0

    for sev, hint, line in sorted(issues, key=lambda x: x[2]):
        marker = {"DANGEROUS": "❌", "WARN": "⚠️ ", "OK": "✓"}[sev]
        logger.info("  %s line %d [%s]: %s", marker, line, sev, hint)

    logger.info(
        "  Summary: %d dangerous, %d warnings, %d OK markers",
        dangerous_count, warn_count, ok_count,
    )

    if dangerous_count:
        logger.error("Dangerous patterns require multi-step migration — see migrations/PATTERNS.md")
        return 1
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("file", type=Path, help="migration file to check")
    args = parser.parse_args()
    return check_file(args.file)


if __name__ == "__main__":
    sys.exit(main())
