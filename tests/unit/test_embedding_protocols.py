"""Unit tests for embedding Protocol definitions — sub-protocols and runtime checking."""

from __future__ import annotations

from typing import Any

import pytest

from src.embedding.types import (
    AsyncEmbeddingProvider,
    EmbeddingProvider,
    SyncEmbeddingEncoder,
)


# ---------------------------------------------------------------------------
# Minimal mock classes for Protocol conformance testing
# ---------------------------------------------------------------------------


class ValidSyncEncoder:
    """Satisfies SyncEmbeddingEncoder."""

    backend: str = "test"

    def is_ready(self) -> bool:
        return True

    def encode(
        self,
        texts: list[str],
        return_dense: bool = True,
        return_sparse: bool = True,
        return_colbert_vecs: bool = False,
        **kwargs: Any,
    ) -> dict[str, Any]:
        return {"dense_vecs": [], "lexical_weights": [], "colbert_vecs": []}

    @property
    def dimension(self) -> int:
        return 1024

    def close(self) -> None:
        pass


class ValidAsyncProvider:
    """Satisfies AsyncEmbeddingProvider."""

    async def embed(self, text: str) -> list[float]:
        return [0.0] * 1024

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] * 1024 for _ in texts]


class ValidFullProvider(ValidSyncEncoder, ValidAsyncProvider):
    """Satisfies full EmbeddingProvider (both sync + async)."""

    pass


class MissingBackend:
    """Missing 'backend' attribute — should not satisfy SyncEmbeddingEncoder."""

    def is_ready(self) -> bool:
        return True

    def encode(self, texts: list[str], **kwargs: Any) -> dict[str, Any]:
        return {}

    @property
    def dimension(self) -> int:
        return 1024

    def close(self) -> None:
        pass


class MissingEncode:
    """Missing 'encode' method — should not satisfy SyncEmbeddingEncoder."""

    backend: str = "test"

    def is_ready(self) -> bool:
        return True

    @property
    def dimension(self) -> int:
        return 1024

    def close(self) -> None:
        pass


class MissingEmbed:
    """Missing 'embed' method — should not satisfy AsyncEmbeddingProvider."""

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return []


class EmptyClass:
    """Satisfies nothing."""

    pass


# ---------------------------------------------------------------------------
# SyncEmbeddingEncoder Protocol
# ---------------------------------------------------------------------------


class TestSyncEmbeddingEncoder:
    def test_valid_implementation_satisfies_protocol(self):
        assert isinstance(ValidSyncEncoder(), SyncEmbeddingEncoder)

    def test_missing_backend_fails(self):
        assert not isinstance(MissingBackend(), SyncEmbeddingEncoder)

    def test_missing_encode_fails(self):
        assert not isinstance(MissingEncode(), SyncEmbeddingEncoder)

    def test_empty_class_fails(self):
        assert not isinstance(EmptyClass(), SyncEmbeddingEncoder)

    def test_protocol_is_runtime_checkable(self):
        # Confirm @runtime_checkable is applied
        assert hasattr(SyncEmbeddingEncoder, "__protocol_attrs__") or hasattr(
            SyncEmbeddingEncoder, "__abstractmethods__"
        )


# ---------------------------------------------------------------------------
# AsyncEmbeddingProvider Protocol
# ---------------------------------------------------------------------------


class TestAsyncEmbeddingProvider:
    def test_valid_implementation_satisfies_protocol(self):
        assert isinstance(ValidAsyncProvider(), AsyncEmbeddingProvider)

    def test_missing_embed_fails(self):
        assert not isinstance(MissingEmbed(), AsyncEmbeddingProvider)

    def test_empty_class_fails(self):
        assert not isinstance(EmptyClass(), AsyncEmbeddingProvider)


# ---------------------------------------------------------------------------
# EmbeddingProvider (unified) Protocol
# ---------------------------------------------------------------------------


class TestEmbeddingProviderProtocol:
    def test_full_provider_satisfies_unified_protocol(self):
        assert isinstance(ValidFullProvider(), EmbeddingProvider)

    def test_sync_only_does_not_satisfy_unified(self):
        assert not isinstance(ValidSyncEncoder(), EmbeddingProvider)

    def test_async_only_does_not_satisfy_unified(self):
        assert not isinstance(ValidAsyncProvider(), EmbeddingProvider)

    def test_empty_class_fails(self):
        assert not isinstance(EmptyClass(), EmbeddingProvider)
