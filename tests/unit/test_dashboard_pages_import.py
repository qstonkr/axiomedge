"""Dashboard page syntax / 정의 검증 (C2).

Streamlit page 는 import 시점에 ``st.columns([...])`` 같은 magic 을 unpack
하므로 mock 환경에서 직접 import 하기 어렵다. 대신 ast.parse 로 syntax 검증
+ 모듈 source 에 핵심 helper 함수 정의가 있는지 grep — 회귀 가드.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


_PAGES_DIR = Path(__file__).resolve().parents[2] / "src" / "apps" / "dashboard" / "pages"


PAGE_FILES = [
    "ingestion_runs.py",   # P0-6
    "audit_logs.py",       # C3
    "feature_flags.py",    # C4
]


@pytest.mark.parametrize("filename", PAGE_FILES)
def test_dashboard_page_compiles(filename):
    """Page 가 syntax 오류 없이 컴파일 (ast.parse) 됨."""
    src = (_PAGES_DIR / filename).read_text(encoding="utf-8")
    ast.parse(src, filename=str(_PAGES_DIR / filename))


def test_ingestion_runs_helpers_defined():
    """P0-6 — `_fetch_runs` / `_fetch_failures` 함수 정의 존재."""
    src = (_PAGES_DIR / "ingestion_runs.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    func_names = {
        node.name for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
    }
    assert "_fetch_runs" in func_names
    assert "_fetch_failures" in func_names


def test_audit_logs_helpers_defined():
    """C3 — `_fetch_audit_logs` 함수 정의 존재."""
    src = (_PAGES_DIR / "audit_logs.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    func_names = {
        node.name for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
    }
    assert "_fetch_audit_logs" in func_names


def test_feature_flags_helpers_defined():
    """C4 — `_fetch_flags` 함수 정의 존재."""
    src = (_PAGES_DIR / "feature_flags.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    func_names = {
        node.name for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
    }
    assert "_fetch_flags" in func_names


def test_pages_use_render_sidebar():
    """3 page 모두 render_sidebar(show_admin=True) 호출 → admin 전용 강제."""
    for filename in PAGE_FILES:
        src = (_PAGES_DIR / filename).read_text(encoding="utf-8")
        assert "render_sidebar(show_admin=True)" in src, (
            f"{filename} must require admin via render_sidebar"
        )
