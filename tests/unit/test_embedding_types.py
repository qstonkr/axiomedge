"""Unit tests for EmbeddingProvider Protocol compliance."""

from src.embedding.types import EmbeddingProvider
from src.embedding.ollama_provider import OllamaEmbeddingProvider
from src.embedding.tei_provider import TEIEmbeddingProvider
from src.embedding.onnx_provider import OnnxBgeEmbeddingProvider


class TestEmbeddingProviderProtocol:
    """Verify all providers satisfy the EmbeddingProvider Protocol."""

    def test_ollama_satisfies_protocol(self) -> None:
        inst = object.__new__(OllamaEmbeddingProvider)
        assert isinstance(inst, EmbeddingProvider)

    def test_tei_satisfies_protocol(self) -> None:
        inst = object.__new__(TEIEmbeddingProvider)
        assert isinstance(inst, EmbeddingProvider)

    def test_onnx_satisfies_protocol(self) -> None:
        inst = object.__new__(OnnxBgeEmbeddingProvider)
        assert isinstance(inst, EmbeddingProvider)

    def test_all_have_backend(self) -> None:
        assert OllamaEmbeddingProvider.backend == "ollama"
        assert TEIEmbeddingProvider.backend == "tei"
        assert OnnxBgeEmbeddingProvider.backend == "onnx"

    def test_all_have_consistent_dimension(self) -> None:
        from src.config.weights import weights
        expected = weights.embedding.dimension
        assert OllamaEmbeddingProvider._DENSE_DIM == expected
        assert TEIEmbeddingProvider._DENSE_DIM == expected
        assert OnnxBgeEmbeddingProvider._DENSE_DIM == expected

    def test_arbitrary_class_does_not_satisfy(self) -> None:
        class NotAnEmbedder:
            pass
        assert not isinstance(NotAnEmbedder(), EmbeddingProvider)
