"""Unit tests for scripts/graph_cleanup.py."""

from __future__ import annotations

import re
from unittest.mock import MagicMock, patch

import pytest

from scripts.graph_cleanup import (
    KB_ID_NORMALIZE,
    NON_PERSON_BLOCKLIST,
    PLACEHOLDER_NAMES,
    RE_LONE_JAMO,
    RE_REPEATED_CHAR,
    STORE_PRODUCT_REMOVE,
    STORE_TO_SYSTEM_BLOCKLIST,
    _apply_rule,
    _kb_filter,
    _run_query,
)


# ---------------------------------------------------------------------------
# _kb_filter
# ---------------------------------------------------------------------------


class TestKbFilter:
    def test_none_returns_empty(self) -> None:
        assert _kb_filter(None) == ""

    def test_with_id_returns_clause(self) -> None:
        result = _kb_filter("itops_general")
        assert "itops_general" in result
        assert "n.kb_id" in result


# ---------------------------------------------------------------------------
# _run_query
# ---------------------------------------------------------------------------


class TestRunQuery:
    def test_returns_list_of_dicts(self) -> None:
        mock_record1 = MagicMock()
        mock_record1.__iter__ = MagicMock(return_value=iter([("cnt", 5)]))
        mock_record1.keys.return_value = ["cnt"]
        mock_record1.__getitem__ = lambda self, key: 5

        mock_result = MagicMock()
        # _run_query iterates over result and calls dict(record)
        mock_record = {"cnt": 5}
        mock_result.__iter__ = MagicMock(return_value=iter([mock_record]))

        mock_session = MagicMock()
        mock_session.run.return_value = mock_result

        results = _run_query(mock_session, "MATCH (n) RETURN count(n) AS cnt")
        assert results == [{"cnt": 5}]


# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------


class TestRegexPatterns:
    def test_repeated_char_matches(self) -> None:
        assert RE_REPEATED_CHAR.search("aaaaabc")
        assert RE_REPEATED_CHAR.search("xyyyyyz")

    def test_repeated_char_no_match(self) -> None:
        assert RE_REPEATED_CHAR.search("abc") is None
        assert RE_REPEATED_CHAR.search("aabb") is None

    def test_lone_jamo_matches(self) -> None:
        # 3+ consecutive lone jamo (ㄱㄴㄷ)
        assert RE_LONE_JAMO.search("ㄱㄴㄷ")

    def test_lone_jamo_no_match(self) -> None:
        assert RE_LONE_JAMO.search("가나다") is None
        assert RE_LONE_JAMO.search("abc") is None


# ---------------------------------------------------------------------------
# Blocklists coverage
# ---------------------------------------------------------------------------


class TestBlocklists:
    def test_placeholder_names_are_set(self) -> None:
        assert isinstance(PLACEHOLDER_NAMES, set)
        assert "명시되지 않음" in PLACEHOLDER_NAMES
        assert "unknown" in PLACEHOLDER_NAMES
        assert "N/A" in PLACEHOLDER_NAMES
        assert "TBD" in PLACEHOLDER_NAMES

    def test_non_person_blocklist(self) -> None:
        assert "JIRA" in NON_PERSON_BLOCKLIST
        assert "Kubernetes" in NON_PERSON_BLOCKLIST
        assert "관리자" in NON_PERSON_BLOCKLIST

    def test_store_to_system_blocklist(self) -> None:
        assert "JIRA" in STORE_TO_SYSTEM_BLOCKLIST
        assert "Grafana" in STORE_TO_SYSTEM_BLOCKLIST

    def test_store_product_remove(self) -> None:
        assert "iPhone" in STORE_PRODUCT_REMOVE

    def test_kb_id_normalize(self) -> None:
        assert KB_ID_NORMALIZE["itops-general"] == "itops_general"
        assert KB_ID_NORMALIZE["partner-talk"] == "partnertalk"


# ---------------------------------------------------------------------------
# _apply_rule
# ---------------------------------------------------------------------------


class TestApplyRule:
    def test_delete_action(self) -> None:
        mock_session = MagicMock()
        mock_result = MagicMock()
        mock_result.__iter__ = MagicMock(return_value=iter([{"fixed": 3}]))
        mock_session.run.return_value = mock_result

        rule = {
            "action": "delete",
            "match": "MATCH (n:Person) WHERE size(n.name) <= 2",
            "label": "short_name",
        }
        result = _apply_rule(mock_session, rule)
        assert result == 3

    def test_relabel_action(self) -> None:
        mock_session = MagicMock()
        mock_result = MagicMock()
        mock_result.__iter__ = MagicMock(return_value=iter([{"fixed": 2}]))
        mock_session.run.return_value = mock_result

        rule = {
            "action": "relabel",
            "match": "MATCH (n:Person) WHERE n.name CONTAINS '(주)'",
            "label": "company_name",
        }
        result = _apply_rule(mock_session, rule)
        assert result == 2

    def test_unknown_action_returns_zero(self) -> None:
        mock_session = MagicMock()
        rule = {
            "action": "unknown",
            "match": "MATCH (n:Person)",
            "label": "test",
        }
        result = _apply_rule(mock_session, rule)
        assert result == 0
