"""Tests for src/core/providers/protocols.py — re-export verification."""

from __future__ import annotations

import importlib


def test_all_exports_importable() -> None:
    mod = importlib.import_module("src.core.providers.protocols")
    for name in mod.__all__:
        assert hasattr(mod, name), f"{name} listed in __all__ but not found"


def test_expected_protocols_present() -> None:
    from src.core.providers.protocols import (
        IEmbedder,
        IGraphStore,
        ISearchEngine,
        ISparseEmbedder,
        IVectorStore,
    )
    for proto in (IEmbedder, IGraphStore, ISearchEngine, ISparseEmbedder, IVectorStore):
        assert hasattr(proto, "__protocol_attrs__") or callable(proto)


def test_noop_implementations_present() -> None:
    from src.core.providers.protocols import (
        NoOpEmbedder,
        NoOpGraphStore,
        NoOpSparseEmbedder,
        NoOpVectorStore,
    )
    for cls in (NoOpEmbedder, NoOpGraphStore, NoOpSparseEmbedder, NoOpVectorStore):
        assert cls is not None
