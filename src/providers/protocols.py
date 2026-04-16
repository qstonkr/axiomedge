"""Provider Protocol re-exports — central location for all store interfaces.

The actual Protocol definitions live in ``src/pipeline/ingestion_contracts.py``
(historical location). This module re-exports them for easier discovery:

    from src.providers.protocols import IVectorStore, IGraphStore, ISearchEngine
"""

from src.pipeline.ingestion_contracts import (  # noqa: F401
    IEmbedder,
    IGraphStore,
    ISearchEngine,
    ISparseEmbedder,
    IVectorStore,
    NoOpEmbedder,
    NoOpGraphStore,
    NoOpSparseEmbedder,
    NoOpVectorStore,
)

__all__ = [
    "IEmbedder",
    "IGraphStore",
    "ISearchEngine",
    "ISparseEmbedder",
    "IVectorStore",
    "NoOpEmbedder",
    "NoOpGraphStore",
    "NoOpSparseEmbedder",
    "NoOpVectorStore",
]
