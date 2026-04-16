"""Unit tests for EntityResolver."""

from __future__ import annotations

from src.stores.neo4j.entity_resolver import (
    NORMALIZATION_RULES,
    _basic_normalize,
)


class TestNormalizeEntityName:

    def test_normalize_entity_name(self):
        """Known abbreviations should be expanded to full names."""
        assert _basic_normalize("k8s") == "Kubernetes"
        assert _basic_normalize("K8S") == "Kubernetes"
        assert _basic_normalize("py") == "Python"
        assert _basic_normalize("pg") == "PostgreSQL"
        assert _basic_normalize("psql") == "PostgreSQL"
        assert _basic_normalize("gcp") == "Google Cloud Platform"
        assert _basic_normalize("aws") == "Amazon Web Services"

    def test_normalize_preserves_unknown_names(self):
        """Names not in the normalization rules should be returned as-is."""
        assert _basic_normalize("SomeCustomName") == "SomeCustomName"
        assert _basic_normalize("") == ""
        assert _basic_normalize("OREO") == "OREO"


class TestAbbreviationExpansion:

    def test_abbreviation_expansion(self):
        """All entries in NORMALIZATION_RULES should be present and correct."""
        assert NORMALIZATION_RULES["k8s"] == "Kubernetes"
        assert NORMALIZATION_RULES["js"] == "JavaScript"
        assert NORMALIZATION_RULES["ts"] == "TypeScript"
        assert NORMALIZATION_RULES["es"] == "Elasticsearch"

        # Verify case-insensitive lookup via _basic_normalize
        for abbrev, full_name in NORMALIZATION_RULES.items():
            assert _basic_normalize(abbrev) == full_name
            assert _basic_normalize(abbrev.upper()) == full_name
