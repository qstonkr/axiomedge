"""GraphRAG sub-package -- re-exports for backward compatibility."""

from .extractor import (  # noqa: F401
    GraphRAGBatchProcessor,
    GraphRAGExtractor,
    _OllamaLLMClient,
    _SageMakerLLMClient,
    _SHARED_EXECUTOR,
)
from .models import (  # noqa: F401
    ExtractionResult,
    GraphNode,
    GraphRelationship,
)
from .prompts import (  # noqa: F401
    ALLOWED_NODES,
    ALLOWED_RELATIONSHIPS,
    DEFAULT_SCHEMA_PROFILE,
    HISTORY_RELATIONSHIP_MAP,
    KB_SCHEMA_PROFILES,
    KOREAN_EXTRACTION_PROMPT,
    _is_safe_cypher_label,
    build_extraction_prompt,
    get_kb_schema,
)
from .schema_resolver import SchemaResolver, invalidate_cache  # noqa: F401
from .schema_types import IndexSpec, SchemaOptions, SchemaProfile  # noqa: F401
from .source_defaults import is_valid_source_type  # noqa: F401

__all__ = [
    "ALLOWED_NODES",
    "ALLOWED_RELATIONSHIPS",
    "DEFAULT_SCHEMA_PROFILE",
    "ExtractionResult",
    "GraphNode",
    "GraphRAGBatchProcessor",
    "GraphRAGExtractor",
    "GraphRelationship",
    "HISTORY_RELATIONSHIP_MAP",
    "IndexSpec",
    "KB_SCHEMA_PROFILES",
    "KOREAN_EXTRACTION_PROMPT",
    "SchemaOptions",
    "SchemaProfile",
    "SchemaResolver",
    "_OllamaLLMClient",
    "_SHARED_EXECUTOR",
    "_SageMakerLLMClient",
    "_is_safe_cypher_label",
    "build_extraction_prompt",
    "get_kb_schema",
    "invalidate_cache",
    "is_valid_source_type",
]
