"""Tests for source_type whitelist — Cypher injection defense."""

from __future__ import annotations

from src.pipelines.graphrag.source_defaults import is_valid_source_type


class TestIsValidSourceType:
    def test_known_source_accepted(self):
        # Ships with the repo (Task 2 created confluence.yaml)
        assert is_valid_source_type("confluence") is True

    def test_unknown_source_rejected(self):
        assert is_valid_source_type("unknown_source_xyz") is False

    def test_injection_attempt_rejected(self):
        # Cypher injection via source_type string
        assert is_valid_source_type("confluence\nDROP DATABASE") is False
        assert is_valid_source_type("../../../etc/passwd") is False
        assert is_valid_source_type("x; y") is False

    def test_empty_rejected(self):
        assert is_valid_source_type("") is False

    def test_case_exact(self):
        # Filenames are lowercase; uppercase should be rejected (strict)
        assert is_valid_source_type("Confluence") is False
