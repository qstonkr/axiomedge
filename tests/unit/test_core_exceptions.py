"""Tests for src/core/exceptions.py — domain exception hierarchy."""

from __future__ import annotations

import pytest

from src.core.exceptions import (
    AuthenticationError,
    CacheError,
    ConfigurationError,
    ConnectorError,
    DatabaseError,
    DedupError,
    EmbeddingError,
    GraphRAGError,
    GraphStoreError,
    IngestionError,
    KnowledgeBaseError,
    LLMError,
    OCRError,
    PipelineError,
    ProviderError,
    SearchError,
    StorageError,
    TransitionError,
    VectorStoreError,
)


class TestExceptionHierarchy:
    """Verify that every exception is a subclass of KnowledgeBaseError."""

    @pytest.mark.parametrize(
        "exc_cls",
        [
            ConfigurationError,
            StorageError,
            VectorStoreError,
            GraphStoreError,
            DatabaseError,
            CacheError,
            ProviderError,
            EmbeddingError,
            LLMError,
            OCRError,
            PipelineError,
            IngestionError,
            DedupError,
            GraphRAGError,
            SearchError,
            ConnectorError,
            AuthenticationError,
            TransitionError,
        ],
    )
    def test_is_subclass_of_base(self, exc_cls: type[Exception]) -> None:
        assert issubclass(exc_cls, KnowledgeBaseError)

    @pytest.mark.parametrize(
        "child,parent",
        [
            (VectorStoreError, StorageError),
            (GraphStoreError, StorageError),
            (DatabaseError, StorageError),
            (CacheError, StorageError),
            (EmbeddingError, ProviderError),
            (LLMError, ProviderError),
            (OCRError, ProviderError),
            (IngestionError, PipelineError),
            (DedupError, PipelineError),
            (GraphRAGError, PipelineError),
        ],
    )
    def test_intermediate_hierarchy(
        self, child: type[Exception], parent: type[Exception],
    ) -> None:
        assert issubclass(child, parent)

    def test_raise_and_catch(self) -> None:
        with pytest.raises(KnowledgeBaseError):
            raise VectorStoreError("Qdrant timeout")

    def test_message_preserved(self) -> None:
        msg = "test error message"
        exc = StorageError(msg)
        assert str(exc) == msg

    def test_storage_not_provider(self) -> None:
        assert not issubclass(StorageError, ProviderError)

    def test_provider_not_pipeline(self) -> None:
        assert not issubclass(ProviderError, PipelineError)
