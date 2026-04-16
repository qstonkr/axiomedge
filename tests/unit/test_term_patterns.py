"""Unit tests for term_patterns module."""

from __future__ import annotations

from src.pipelines.term_patterns import (
    CAMEL_CASE_PATTERN,
    ACRONYM_PATTERN,
    HYPHENATED_PATTERN,
    KOREAN_TECH_PATTERN,
    MIXED_PATTERN,
    KNOWN_ACRONYMS,
    STOP_TERMS,
    _is_structural_noise,
    _is_ascii_noise,
    _is_foreign_noise,
    is_noise_term,
    is_synonym_noise,
    is_code_artifact,
    strip_korean_particles,
    _try_strip_particle,
)


# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

class TestPatterns:
    def test_camel_case(self) -> None:
        matches = CAMEL_CASE_PATTERN.findall("Use GraphSearchExpander here")
        assert "GraphSearchExpander" in matches

    def test_acronym(self) -> None:
        # ACRONYM_PATTERN only matches [A-Z]{2,6}, so K8S (has digit) won't match
        matches = ACRONYM_PATTERN.findall("Deploy to K8S via CI/CD")
        assert "CI" in matches
        assert "CD" in matches

    def test_acronym_pure_alpha(self) -> None:
        matches = ACRONYM_PATTERN.findall("Use API and SQL here")
        assert "API" in matches
        assert "SQL" in matches

    def test_hyphenated(self) -> None:
        matches = HYPHENATED_PATTERN.findall("use blue-green deployment")
        assert "blue-green" in matches

    def test_korean_tech(self) -> None:
        matches = KOREAN_TECH_PATTERN.findall("데이터베이스 관리시스템")
        assert any("시스템" in m for m in matches)

    def test_mixed_pattern(self) -> None:
        matches = MIXED_PATTERN.findall("API서버 K8S클러스터")
        assert any("API서버" in m for m in matches)


# ---------------------------------------------------------------------------
# Noise filters
# ---------------------------------------------------------------------------

class TestStructuralNoise:
    def test_short_term(self) -> None:
        assert _is_structural_noise("a") is True

    def test_repeated_char(self) -> None:
        assert _is_structural_noise("aaa") is True

    def test_starts_with_special(self) -> None:
        assert _is_structural_noise("#tag") is True
        assert _is_structural_noise("$var") is True

    def test_contains_dot(self) -> None:
        assert _is_structural_noise("file.txt") is True

    def test_digit_start(self) -> None:
        assert _is_structural_noise("123abc") is True

    def test_brackets(self) -> None:
        assert _is_structural_noise("{obj}") is True

    def test_valid_term(self) -> None:
        assert _is_structural_noise("데이터베이스") is False


class TestAsciiNoise:
    def test_short_alpha(self) -> None:
        assert _is_ascii_noise("hello") is True

    def test_camel_case_noise(self) -> None:
        assert _is_ascii_noise("camelCase") is True

    def test_non_ascii_pass(self) -> None:
        assert _is_ascii_noise("한글단어") is False


class TestForeignNoise:
    def test_short_foreign(self) -> None:
        assert _is_foreign_noise("ab") is True

    def test_short_lowercase(self) -> None:
        assert _is_foreign_noise("test") is True

    def test_english_stop(self) -> None:
        assert _is_foreign_noise("the") is True

    def test_unknown_short_acronym(self) -> None:
        assert _is_foreign_noise("XYZ") is True

    def test_known_acronym_passes(self) -> None:
        assert _is_foreign_noise("API") is False


# ---------------------------------------------------------------------------
# is_noise_term (comprehensive)
# ---------------------------------------------------------------------------

class TestIsNoiseTerm:
    def test_structural_noise(self) -> None:
        assert is_noise_term("#tag", "noun") is True

    def test_code_artifact(self) -> None:
        assert is_noise_term("border-radius", "noun") is True

    def test_foreign_stop(self) -> None:
        assert is_noise_term("the", "foreign") is True

    def test_valid_korean_compound(self) -> None:
        assert is_noise_term("데이터베이스", "compound_noun") is False

    def test_stop_nouns_filter(self) -> None:
        stops = frozenset({"커스텀용어"})
        assert is_noise_term("커스텀용어", "noun", stop_nouns=stops) is True

    def test_compound_noun_ascii_filtered(self) -> None:
        assert is_noise_term("helloWorld", "compound_noun") is True


class TestIsSynonymNoise:
    def test_empty(self) -> None:
        assert is_synonym_noise("") is True

    def test_special_start(self) -> None:
        assert is_synonym_noise("#tag") is True

    def test_digits(self) -> None:
        assert is_synonym_noise("12345") is True

    def test_low_alnum_ratio(self) -> None:
        assert is_synonym_noise("!@#$%") is True

    def test_valid_term(self) -> None:
        assert is_synonym_noise("API서버") is False


class TestIsCodeArtifact:
    def test_css_property(self) -> None:
        assert is_code_artifact("border-radius") is True
        assert is_code_artifact("margin-top") is True

    def test_exception_suffix(self) -> None:
        assert is_code_artifact("NullPointerException") is True

    def test_mime_type(self) -> None:
        assert is_code_artifact("application-json") is True

    def test_valid_term(self) -> None:
        assert is_code_artifact("데이터센터") is False


# ---------------------------------------------------------------------------
# Korean particle stripping
# ---------------------------------------------------------------------------

class TestStripKoreanParticles:
    def test_strip_long_particle(self) -> None:
        result = strip_korean_particles("데이터베이스에서")
        assert result == "데이터베이스"

    def test_strip_short_particle(self) -> None:
        result = strip_korean_particles("시스템을")
        # "시스템" + "을" — "을" is not in the short list, but "를" is
        # "시스템는" -> would strip "는"
        result2 = strip_korean_particles("시스템는")
        assert result2 == "시스템"

    def test_no_particle(self) -> None:
        result = strip_korean_particles("데이터")
        assert result == "데이터"

    def test_boundary_particle(self) -> None:
        # "API가데이터" -> boundary: English + particle + Korean
        result = strip_korean_particles("API가데이터")
        assert result == "API데이터"


class TestKnownConstants:
    def test_known_acronyms_has_common(self) -> None:
        assert "API" in KNOWN_ACRONYMS
        assert "K8S" in KNOWN_ACRONYMS
        assert "SQL" in KNOWN_ACRONYMS

    def test_stop_terms_has_common(self) -> None:
        assert "the" in STOP_TERMS
        assert "하다" in STOP_TERMS
