"""Tests for migration safety checker."""

from __future__ import annotations

from pathlib import Path

import pytest

# Import the check_file function from the script
import importlib.util
import sys

_spec = importlib.util.spec_from_file_location(
    "db_migration_check",
    Path(__file__).parent.parent.parent / "scripts" / "db_migration_check.py",
)
assert _spec is not None and _spec.loader is not None
_mod = importlib.util.module_from_spec(_spec)
sys.modules["db_migration_check"] = _mod
_spec.loader.exec_module(_mod)
check_file = _mod.check_file


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "0099_test.py"
    p.write_text(body)
    return p


def test_safe_migration_passes(tmp_path: Path) -> None:
    p = _write(tmp_path, """
def upgrade():
    op.add_column("users", sa.Column("nickname", sa.String(50), nullable=True))
""")
    assert check_file(p) == 0


def test_add_not_null_column_flagged(tmp_path: Path) -> None:
    p = _write(tmp_path, """
def upgrade():
    op.add_column("users", sa.Column("email", sa.String(255), nullable=False))
""")
    assert check_file(p) == 1


def test_drop_column_flagged(tmp_path: Path) -> None:
    p = _write(tmp_path, """
def upgrade():
    op.drop_column("users", "old_field")
""")
    assert check_file(p) == 1


def test_drop_table_flagged(tmp_path: Path) -> None:
    p = _write(tmp_path, """
def upgrade():
    op.drop_table("temp_logs")
""")
    assert check_file(p) == 1


def test_rename_column_flagged(tmp_path: Path) -> None:
    p = _write(tmp_path, """
def upgrade():
    op.alter_column("users", "username", new_column_name="user_name")
""")
    assert check_file(p) == 1


def test_create_index_without_concurrently_warns(tmp_path: Path) -> None:
    p = _write(tmp_path, """
def upgrade():
    op.create_index("idx_users_email", "users", ["email"])
""")
    # warning only — exits 0
    assert check_file(p) == 0


def test_create_index_concurrently_passes(tmp_path: Path) -> None:
    p = _write(tmp_path, """
def upgrade():
    op.create_index("idx_users_email", "users", ["email"], postgresql_concurrently=True)
""")
    assert check_file(p) == 0


def test_missing_file_returns_2(tmp_path: Path) -> None:
    assert check_file(tmp_path / "nonexistent.py") == 2
