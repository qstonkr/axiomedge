"""Standalone Qdrant vector database adapter.

Extracted from oreo-ecosystem's infrastructure/adapters/qdrant/ with
framework dependencies (FeatureFlags, StatsD, KPI) removed.
"""

from .client import QdrantClientProvider, QdrantConfig, QdrantSearchResult
from .collections import QdrantCollectionManager
from .search import QdrantSearchEngine
from .store import QdrantStoreOperations

__all__ = [
    "QdrantClientProvider",
    "QdrantCollectionManager",
    "QdrantConfig",
    "QdrantSearchEngine",
    "QdrantSearchResult",
    "QdrantStoreOperations",
]
