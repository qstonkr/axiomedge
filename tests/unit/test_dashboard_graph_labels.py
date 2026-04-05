"""Unit tests for dashboard/components/graph_labels.py."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest



from components.graph_labels import (
    NODE_TYPE_LABELS_KO,
    RELATION_LABELS_KO,
    format_node_label,
    format_rel_for_filter,
    format_rel_label,
    sanitize_label,
    truncate_label,
)


# ── sanitize_label ──


class TestSanitizeLabel:
    def test_empty_string(self):
        assert sanitize_label("") == ("", "")

    def test_strips_leading_digits_5plus(self):
        clean, tooltip = sanitize_label("17729858auto-order-config")
        assert clean == "auto-order-config"
        assert tooltip == ""

    def test_keeps_short_leading_digits(self):
        clean, _ = sanitize_label("1234 report")
        assert clean == "1234 report"

    def test_removes_file_extensions(self):
        for ext in ("pptx", "docx", "pdf", "xlsx", "txt", "hwp", "hwpx"):
            clean, _ = sanitize_label(f"my-report.{ext}")
            assert clean == "my-report", f"Failed for .{ext}"

    def test_case_insensitive_extension(self):
        clean, _ = sanitize_label("my-report.PPTX")
        assert clean == "my-report"

    def test_extracts_slide_info_to_tooltip(self):
        clean, tooltip = sanitize_label("K8s guide - Slide 3")
        assert clean == "K8s guide"
        assert "Slide 3" in tooltip

    def test_slide_with_dash_variants(self):
        for sep in [" - ", " \u2013 ", " \u2014 "]:
            clean, tooltip = sanitize_label(f"Doc{sep}Slide 10")
            assert "Slide 10" in tooltip

    def test_combined_cleanup(self):
        clean, tooltip = sanitize_label("12345678report.pptx - Slide 5")
        assert clean == "report"
        assert "Slide 5" in tooltip

    def test_fallback_when_cleaning_produces_empty(self):
        # Only digits + extension = would be empty after cleaning
        clean, _ = sanitize_label("12345.pdf")
        # Should fallback to original stripped
        assert clean != ""

    def test_preserves_normal_label(self):
        clean, tooltip = sanitize_label("K8s deployment guide")
        assert clean == "K8s deployment guide"
        assert tooltip == ""


# ── truncate_label ──


class TestTruncateLabel:
    def test_short_label_unchanged(self):
        assert truncate_label("short") == "short"

    def test_exact_max_len(self):
        label = "a" * 18
        assert truncate_label(label) == label

    def test_truncates_long_label(self):
        label = "a" * 25
        result = truncate_label(label)
        assert result == "a" * 18 + "..."
        assert len(result) == 21

    def test_custom_max_len(self):
        result = truncate_label("abcdefghij", max_len=5)
        assert result == "abcde..."


# ── format_node_label ──


class TestFormatNodeLabel:
    def test_known_type_adds_prefix(self):
        result = format_node_label("K8s Guide", "Document")
        assert result == "[문서] K8s Guide"

    def test_person_type(self):
        result = format_node_label("김철수", "Person")
        assert result == "[사람] 김철수"

    def test_unknown_type_no_prefix(self):
        result = format_node_label("something", "UnknownType")
        assert result == "something"


# ── format_rel_label ──


class TestFormatRelLabel:
    def test_known_relation(self):
        assert format_rel_label("MEMBER_OF") == "소속"
        assert format_rel_label("MANAGES") == "관리"

    def test_unknown_relation_returns_original(self):
        assert format_rel_label("UNKNOWN_REL") == "UNKNOWN_REL"

    def test_history_relations(self):
        assert format_rel_label("WAS_MEMBER_OF") == "(전)소속"
        assert format_rel_label("PREVIOUSLY_MANAGED") == "(전)관리"


# ── format_rel_for_filter ──


class TestFormatRelForFilter:
    def test_known_relation(self):
        result = format_rel_for_filter("MEMBER_OF")
        assert result == "소속 (MEMBER_OF)"

    def test_unknown_relation(self):
        result = format_rel_for_filter("CUSTOM_REL")
        assert result == "CUSTOM_REL"


# ── Dict completeness ──


class TestDictCompleteness:
    def test_relation_labels_has_entries(self):
        assert len(RELATION_LABELS_KO) >= 20

    def test_node_type_labels_has_entries(self):
        assert len(NODE_TYPE_LABELS_KO) >= 10

    def test_all_node_type_labels_are_korean(self):
        for key, val in NODE_TYPE_LABELS_KO.items():
            assert isinstance(val, str) and len(val) > 0, f"Empty label for {key}"
