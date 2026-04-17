"""Domain exception hierarchy — structured error handling across all layers.

사용법:
    from src.core.exceptions import StorageError, VectorStoreError

    try:
        await qdrant.search(...)
    except VectorStoreError as e:
        logger.error("Qdrant 검색 실패: %s", e)

계층:
    KnowledgeBaseError (base)
    ├── ConfigurationError
    ├── StorageError
    │   ├── VectorStoreError   (Qdrant)
    │   ├── GraphStoreError    (Neo4j)
    │   ├── DatabaseError      (PostgreSQL)
    │   └── CacheError         (Redis)
    ├── ProviderError
    │   ├── EmbeddingError
    │   ├── LLMError
    │   └── OCRError
    ├── PipelineError
    │   ├── IngestionError
    │   ├── DedupError
    │   └── GraphRAGError
    ├── SearchError
    ├── ConnectorError
    ├── AuthenticationError
    └── TransitionError
"""

from __future__ import annotations


class KnowledgeBaseError(Exception):
    """Base exception for all knowledge-local errors."""


# --- Configuration ---

class ConfigurationError(KnowledgeBaseError):
    """Invalid or missing configuration."""


# --- Storage ---

class StorageError(KnowledgeBaseError):
    """Base for all data store errors."""


class VectorStoreError(StorageError):
    """Qdrant vector store operation failed."""


class GraphStoreError(StorageError):
    """Neo4j graph store operation failed."""


class DatabaseError(StorageError):
    """PostgreSQL database operation failed."""


class CacheError(StorageError):
    """Redis cache operation failed."""


# --- Providers ---

class ProviderError(KnowledgeBaseError):
    """Base for AI/ML provider errors."""


class EmbeddingError(ProviderError):
    """Embedding provider (TEI/Ollama/ONNX) failed."""


class LLMError(ProviderError):
    """LLM provider (SageMaker/Ollama) failed."""


class OCRError(ProviderError):
    """OCR provider (PaddleOCR) failed."""


# --- Pipeline ---

class PipelineError(KnowledgeBaseError):
    """Base for pipeline processing errors."""


class IngestionError(PipelineError):
    """Document ingestion pipeline failed."""


class DedupError(PipelineError):
    """Deduplication pipeline failed."""


class GraphRAGError(PipelineError):
    """GraphRAG entity/relationship extraction failed."""


# --- Search ---

class SearchError(KnowledgeBaseError):
    """Search pipeline or RAG error."""


# --- Connector ---

class ConnectorError(KnowledgeBaseError):
    """External data source connector error (Confluence, Git, etc.)."""


# --- Auth ---

class AuthenticationError(KnowledgeBaseError):
    """Authentication or authorization failure."""


# --- Lifecycle ---

class TransitionError(KnowledgeBaseError):
    """Invalid document lifecycle state transition."""
