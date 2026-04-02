"""Typed application state — replaces untyped _state dict.

Provides attribute access with dict-style __getitem__/get for backward compatibility.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class AppState:
    """Typed application state container.

    All service fields default to None for graceful degradation.
    Dict-style access (state["key"], state.get("key")) supported for backward compat.
    """

    # Database & Repositories
    db_session_factory: Any = None
    kb_registry: Any = None
    glossary_repo: Any = None
    doc_owner_repo: Any = None
    topic_owner_repo: Any = None
    error_report_repo: Any = None
    feedback_repo: Any = None
    ingestion_run_repo: Any = None
    trust_score_repo: Any = None
    lifecycle_repo: Any = None
    data_source_repo: Any = None
    provenance_repo: Any = None
    category_repo: Any = None
    search_group_repo: Any = None
    usage_log_repo: Any = None

    # Domain Services
    term_extractor: Any = None
    trust_score_service: Any = None
    lifecycle_service: Any = None
    freshness_predictor: Any = None

    # Cache
    search_cache: Any = None
    dedup_cache: Any = None
    multi_layer_cache: Any = None
    idempotency_cache: Any = None

    # Dedup Pipeline
    dedup_pipeline: Any = None
    dedup_result_tracker: Any = None
    redis_dedup_index: Any = None

    # Vector DB (Qdrant)
    qdrant_provider: Any = None
    qdrant_collections: Any = None
    qdrant_search: Any = None
    qdrant_store: Any = None

    # Graph DB (Neo4j)
    neo4j: Any = None
    graph_repo: Any = None
    graph_expander: Any = None
    graph_integrity: Any = None
    multi_hop_searcher: Any = None

    # AI Providers
    embedder: Any = None
    llm: Any = None
    graphrag_extractor: Any = None

    # Search Services
    query_preprocessor: Any = None
    composite_reranker: Any = None
    query_classifier: Any = None
    tiered_response_generator: Any = None
    answer_service: Any = None
    crag_evaluator: Any = None
    query_expander: Any = None
    rag_pipeline: Any = None

    # Auth
    auth_provider: Any = None
    rbac_engine: Any = None
    abac_engine: Any = None
    auth_service: Any = None
    jwt_service: Any = None
    token_store: Any = None

    # Internal
    _shutting_down: bool = False
    _background_tasks: Any = None

    # --- Dict-style backward compatibility ---

    def __getitem__(self, key: str) -> Any:
        try:
            return getattr(self, key)
        except AttributeError:
            raise KeyError(key)

    def __setitem__(self, key: str, value: Any) -> None:
        setattr(self, key, value)

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)

    def setdefault(self, key: str, default: Any = None) -> Any:
        """Dict-compatible setdefault: return existing value or set and return default."""
        val = getattr(self, key, None)
        if val is not None:
            return val
        setattr(self, key, default)
        return default

    def __contains__(self, key: str) -> bool:
        """Check if a service is initialized (field exists and is not None).

        Note: For bool fields like _shutting_down, this returns True even when False.
        This matches the semantic of 'service is present', not 'value is truthy'.
        """
        val = getattr(self, key, None)
        return val is not None
