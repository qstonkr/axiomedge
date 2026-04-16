"""Unit tests for config_weights hot-reload and serialization."""

from src.config.weights import Weights


class TestWeights:
    """Test Weights singleton behavior."""

    def setup_method(self) -> None:
        self.w = Weights()

    def test_to_dict_returns_all_sections(self) -> None:
        d = self.w.to_dict()
        expected_sections = set(Weights._SECTION_CLASSES.keys())
        assert set(d.keys()) == expected_sections
        # Each section should be a non-empty dict
        for section_name, section_dict in d.items():
            assert isinstance(section_dict, dict), f"{section_name} should be a dict"
            assert len(section_dict) > 0, f"{section_name} should have fields"

    def test_update_from_dict_flat_key(self) -> None:
        original = self.w.reranker.model_weight
        applied = self.w.update_from_dict({"reranker.model_weight": 0.99})
        assert "reranker.model_weight" in applied
        assert applied["reranker.model_weight"]["old"] == original
        assert applied["reranker.model_weight"]["new"] == 0.99
        assert self.w.reranker.model_weight == 0.99

    def test_update_from_dict_nested_key(self) -> None:
        applied = self.w.update_from_dict({
            "llm": {"temperature": 0.5, "max_tokens": 4096}
        })
        assert "llm.temperature" in applied
        assert "llm.max_tokens" in applied
        assert self.w.llm.temperature == 0.5
        assert self.w.llm.max_tokens == 4096

    def test_update_invalid_section_ignored(self) -> None:
        applied = self.w.update_from_dict({"nonexistent.field": 42})
        assert len(applied) == 0

    def test_update_invalid_field_ignored(self) -> None:
        applied = self.w.update_from_dict({"reranker.nonexistent_field": 42})
        assert len(applied) == 0

    def test_reset_restores_defaults(self) -> None:
        # Modify a value
        self.w.update_from_dict({"reranker.model_weight": 0.01})
        assert self.w.reranker.model_weight == 0.01

        # Reset
        self.w.reset()
        assert self.w.reranker.model_weight == 0.6  # Default

    def test_reset_restores_all_sections(self) -> None:
        self.w.update_from_dict({
            "reranker.faq_boost": 9.9,
            "llm.temperature": 0.0,
            "pipeline.max_workers": 99,
        })
        self.w.reset()

        fresh = Weights()
        assert self.w.to_dict() == fresh.to_dict()

    def test_type_coercion_string_to_float(self) -> None:
        applied = self.w.update_from_dict({"reranker.model_weight": "0.75"})
        assert self.w.reranker.model_weight == 0.75

    def test_type_coercion_string_to_int(self) -> None:
        applied = self.w.update_from_dict({"pipeline.max_workers": "8"})
        assert self.w.pipeline.max_workers == 8

    def test_defaults_match_expected_values(self) -> None:
        """Verify some known defaults for regression detection."""
        w = Weights()
        assert w.reranker.model_weight == 0.6
        assert w.reranker.base_weight == 0.3
        assert w.reranker.faq_boost == 1.2
        assert w.preprocessor.fuzzy_cutoff == 0.89
        assert w.llm.temperature == 0.7
        assert w.chunking.max_chunk_chars == 2500
        assert w.search.top_k == 5
