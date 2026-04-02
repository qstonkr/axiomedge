"""Unit tests for src/embedding/provider_factory.py."""

import pytest

from src.embedding.provider_factory import create_embedding_provider


class TestCreateEmbeddingProvider:
    """Test provider factory error handling."""

    def test_invalid_type_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Unknown embedding provider type"):
            create_embedding_provider(provider_type="nonexistent")

    def test_invalid_type_case_insensitive(self) -> None:
        with pytest.raises(ValueError, match="Unknown embedding provider type"):
            create_embedding_provider(provider_type="INVALID_PROVIDER")

    def test_valid_types_accepted(self) -> None:
        # These should NOT raise ValueError (they may raise other errors
        # due to missing services, but that's expected)
        for provider_type in ("tei", "ollama", "onnx"):
            try:
                create_embedding_provider(provider_type=provider_type)
            except ValueError:
                pytest.fail(f"provider_type={provider_type!r} raised ValueError unexpectedly")
            except Exception:
                # Other errors (import, connection) are expected without services
                pass

    def test_return_type_annotation(self) -> None:
        """Verify the function has the correct return type annotation."""
        import inspect
        sig = inspect.signature(create_embedding_provider)
        # Return annotation should reference EmbeddingProvider
        ret = sig.return_annotation
        assert "EmbeddingProvider" in str(ret)

    def test_auto_detect_delegates_to_providers(self) -> None:
        """Auto-detect with provider_type=None should attempt provider creation."""
        # We just verify it doesn't raise ValueError (it tries real providers)
        # It may succeed (if ONNX is available) or raise RuntimeError
        try:
            provider = create_embedding_provider(provider_type=None)
            # If it succeeds, we got a valid provider
            assert provider is not None
        except RuntimeError:
            # Expected when no providers are available
            pass
