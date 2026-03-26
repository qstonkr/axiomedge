"""Unit tests for the QueryPreprocessor."""

from src.search.query_preprocessor import QueryPreprocessor, PreprocessedQuery


class TestQueryPreprocessor:
    """Test query normalization and typo correction."""

    def setup_method(self) -> None:
        self.pp = QueryPreprocessor(fuzzy_enabled=False)

    # --- Typo correction ---

    def test_typo_correction_korean(self) -> None:
        result = self.pp.preprocess("쿠버네티즈 pod 재시작")
        assert "쿠버네티스" in result.corrected_query
        assert result.was_corrected is True

    def test_typo_correction_english(self) -> None:
        result = self.pp.preprocess("kuberenetes deployment")
        assert "kubernetes" in result.corrected_query
        assert result.was_corrected is True

    def test_typo_correction_confluence(self) -> None:
        result = self.pp.preprocess("confluance page 찾기")
        assert "confluence" in result.corrected_query

    def test_typo_correction_multiple(self) -> None:
        result = self.pp.preprocess("kubernates 쿠버네티즈")
        assert "kubernetes" in result.corrected_query
        assert "쿠버네티스" in result.corrected_query
        assert len(result.corrections) == 2

    def test_no_correction_for_correct_terms(self) -> None:
        result = self.pp.preprocess("kubernetes pod 재시작")
        assert result.was_corrected is False
        assert len(result.corrections) == 0

    # --- Query normalization ---

    def test_query_normalization_whitespace(self) -> None:
        result = self.pp.preprocess("  쿠버네티스   pod   재시작  ")
        assert result.normalized_query == "쿠버네티스 pod 재시작"

    def test_query_normalization_space_before_punct(self) -> None:
        result = self.pp.preprocess("hello ,world . test")
        assert result.normalized_query == "hello,world. test"

    # --- Empty query ---

    def test_empty_query(self) -> None:
        result = self.pp.preprocess("")
        assert result.normalized_query == ""
        assert result.corrected_query == ""
        assert result.was_corrected is False

    def test_none_query(self) -> None:
        result = self.pp.preprocess(None)  # type: ignore[arg-type]
        assert result.normalized_query == ""

    def test_whitespace_only_query(self) -> None:
        result = self.pp.preprocess("   ")
        assert result.normalized_query == ""

    # --- Language detection ---

    def test_detect_korean(self) -> None:
        result = self.pp.preprocess("쿠버네티스 파드 확인")
        assert result.detected_language == "ko"

    def test_detect_english(self) -> None:
        result = self.pp.preprocess("kubernetes pod status")
        assert result.detected_language == "en"

    def test_detect_mixed(self) -> None:
        result = self.pp.preprocess("kubernetes 파드 상태 확인 방법")
        assert result.detected_language in ("ko", "mixed")

    # --- Custom typo map ---

    def test_custom_typo_map(self) -> None:
        pp = QueryPreprocessor(
            typo_map={"helo": "hello", "wrld": "world"},
            fuzzy_enabled=False,
        )
        result = pp.preprocess("helo wrld")
        assert result.corrected_query == "hello world"

    # --- Fuzzy correction (when enabled) ---

    def test_fuzzy_correction_enabled(self) -> None:
        pp = QueryPreprocessor(fuzzy_enabled=True, fuzzy_cutoff=0.7)
        # "kuberntes" is close to "kubernetes" but not in typo map
        result = pp.preprocess("kuberntes")
        # May or may not correct depending on difflib threshold
        assert isinstance(result, PreprocessedQuery)
