"""Unit tests for embedding guard: sparse_token_hash + vector validation."""

from src.nlp.embedding.embedding_guard import (
    EXPECTED_DIMENSION,
    sparse_token_hash,
    validate_vector,
    safe_embedding_or_zero,
)


class TestSparseTokenHash:
    def test_returns_positive_int(self) -> None:
        h = sparse_token_hash("hello")
        assert isinstance(h, int)
        assert h > 0

    def test_range_1_to_99999(self) -> None:
        for word in ["test", "안녕", "hello", "a", "xyz123", ""]:
            h = sparse_token_hash(word)
            assert 1 <= h <= 99999, f"hash({word!r}) = {h}"

    def test_deterministic(self) -> None:
        assert sparse_token_hash("test") == sparse_token_hash("test")

    def test_different_tokens_different_hashes(self) -> None:
        h1 = sparse_token_hash("hello")
        h2 = sparse_token_hash("world")
        assert h1 != h2

    def test_empty_string(self) -> None:
        h = sparse_token_hash("")
        assert 1 <= h <= 99999


class TestExpectedDimension:
    def test_matches_config_weights(self) -> None:
        from src.config.weights import weights
        assert EXPECTED_DIMENSION == weights.embedding.dimension
        assert EXPECTED_DIMENSION == 1024


class TestValidateVector:
    def test_valid_vector(self) -> None:
        vec = [0.1] * 1024
        result = validate_vector(vec)
        assert result.is_valid

    def test_empty_vector_invalid(self) -> None:
        result = validate_vector([])
        assert not result.is_valid

    def test_wrong_dimension_invalid(self) -> None:
        result = validate_vector([0.1] * 512)
        assert not result.is_valid
        assert any("dimension_mismatch" in i for i in result.issues)

    def test_nan_invalid(self) -> None:
        vec = [0.1] * 1023 + [float("nan")]
        result = validate_vector(vec)
        assert not result.is_valid

    def test_inf_invalid(self) -> None:
        vec = [0.1] * 1023 + [float("inf")]
        result = validate_vector(vec)
        assert not result.is_valid

    def test_zero_vector_invalid(self) -> None:
        vec = [0.0] * 1024
        result = validate_vector(vec)
        assert not result.is_valid


class TestSafeEmbeddingOrZero:
    def test_none_returns_zeros(self) -> None:
        result = safe_embedding_or_zero(None)
        assert len(result) == EXPECTED_DIMENSION
        assert all(v == 0.0 for v in result)

    def test_valid_vector_returned(self) -> None:
        vec = [0.5] * 1024
        result = safe_embedding_or_zero(vec)
        assert result == vec
