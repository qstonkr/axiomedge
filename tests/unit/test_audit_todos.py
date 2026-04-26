"""TODO 분류 스크립트 — PR-14 (L)."""

from __future__ import annotations

import scripts.ops.audit_todos as A


class TestClassify:
    def test_noqa_marks_lint(self):
        assert A._classify("src/x.py", "x = 1  # noqa: E501 TODO") == \
            "lint-suppressed"

    def test_connector_path(self):
        assert A._classify("src/connectors/notion/x.py", "# TODO") == \
            "connector-debt"

    def test_test_path(self):
        assert A._classify("tests/unit/test_a.py", "# TODO") == "test-debt"

    def test_default_general(self):
        assert A._classify("src/api/x.py", "# TODO refactor") == "general"


class TestPriority:
    def test_old_general_is_p0(self):
        assert A._priority(200, "general") == "P0"

    def test_old_test_is_p1(self):
        # test-debt 이라도 90일 초과면 P1 (P0 는 non-test 한정)
        assert A._priority(200, "test-debt") == "P1"

    def test_p1_threshold(self):
        assert A._priority(100, "general") == "P1"

    def test_p2_recent(self):
        assert A._priority(10, "general") == "P2"


class TestRender:
    def test_renders_table_header(self):
        items = [
            A.Item(
                path="src/x.py", line=1, tag="TODO",
                text="fix me", category="general",
                age_days=200, priority="P0",
            ),
        ]
        md = A.render_markdown(items)
        assert "# Tech Debt Audit" in md
        assert "| P0 | 200 | general | TODO |" in md
        assert "src/x.py:1" in md

    def test_sort_priority_then_age(self):
        items = [
            A.Item("a.py", 1, "TODO", "x", "general", 10, "P2"),
            A.Item("b.py", 2, "TODO", "y", "general", 200, "P0"),
            A.Item("c.py", 3, "TODO", "z", "general", 100, "P1"),
        ]
        md = A.render_markdown(items)
        # P0 가 먼저, 그 다음 P1, P2
        idx_b = md.index("b.py")
        idx_c = md.index("c.py")
        idx_a = md.index("a.py")
        assert idx_b < idx_c < idx_a
