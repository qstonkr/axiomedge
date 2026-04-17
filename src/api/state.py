"""Typed application state — replaces untyped _state dict.

Provides attribute access with dict-style __getitem__/get for backward compatibility.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import async_sessionmaker

    # Stores
    from src.stores.neo4j.client import Neo4jClient
    from src.stores.neo4j.repository import Neo4jGraphRepository
    from src.stores.neo4j.integrity import GraphIntegrityChecker
    from src.stores.neo4j.multi_hop_searcher import MultiHopSearcher
    from src.stores.qdrant.client import QdrantClientProvider
    from src.stores.qdrant.collections import QdrantCollectionManager
    from src.stores.qdrant.search import QdrantSearchEngine
    from src.stores.qdrant.store import QdrantStoreOperations
    from src.stores.redis.redis_cache import SearchCache
    from src.stores.redis.dedup_cache import DedupCache
    from src.stores.redis.multi_layer_cache import MultiLayerCache
    from src.stores.redis.idempotency_cache import IdempotencyCache
    from src.stores.postgres.repositories.kb_registry import KBRegistryRepository
    from src.stores.postgres.repositories.glossary import GlossaryRepository
    from src.stores.postgres.repositories.feedback import FeedbackRepository
    from src.stores.postgres.repositories.trust_score import TrustScoreRepository
    from src.stores.postgres.repositories.lifecycle import LifecycleRepository
    from src.stores.postgres.repositories.data_source import DataSourceRepository
    from src.stores.postgres.repositories.traceability import TraceabilityRepository
    from src.stores.postgres.repositories.category import CategoryRepository
    from src.stores.postgres.repositories.search_group import SearchGroupRepository
    from src.stores.postgres.repositories.usage_log import UsageLogRepository
    from src.stores.postgres.repositories.ownership import OwnershipRepository
    from src.stores.postgres.repositories.ingestion_run import IngestionRunRepository

    # NLP
    from src.pipelines.ingestion_contracts import IEmbedder

    # Search
    from src.search.query_preprocessor import QueryPreprocessor
    from src.search.composite_reranker import CompositeReranker
    from src.search.query_classifier import QueryClassifier
    from src.search.tiered_response import TieredResponseGenerator
    from src.search.answer_service import AnswerService
    from src.search.crag_evaluator import CRAGRetrievalEvaluator
    from src.search.query_expansion import QueryExpansionService
    from src.search.rag_pipeline import KnowledgeRAGPipeline
    from src.search.graph_expander import GraphExpander

    # Pipelines
    from src.pipelines.graphrag.extractor import GraphRAGExtractor
    from src.pipelines.term_extractor import TermExtractor
    from src.pipelines.dedup.dedup_pipeline import DedupPipeline
    from src.pipelines.dedup.result_tracker import DedupResultTracker
    from src.pipelines.dedup.redis_index import RedisDedupIndex

    # Auth
    from src.auth.service import AuthService
    from src.auth.jwt_service import JWTService
    from src.auth.token_store import TokenStore
    from src.auth.rbac import RBACEngine
    from src.auth.abac import ABACEngine

    # Services
    from src.search.trust_score_service import TrustScoreService
    from src.pipelines.freshness_ranker import FreshnessPredictor


@dataclass
class AppState:
    """Typed application state container.

    All service fields default to None for graceful degradation.
    Dict-style access (state["key"], state.get("key")) supported for backward compat.
    """

    # Database & Repositories
    db_session_factory: async_sessionmaker | None = None
    kb_registry: KBRegistryRepository | None = None
    glossary_repo: GlossaryRepository | None = None
    doc_owner_repo: OwnershipRepository | None = None
    topic_owner_repo: OwnershipRepository | None = None
    error_report_repo: Any = None  # ErrorReportRepository (optional)
    feedback_repo: FeedbackRepository | None = None
    ingestion_run_repo: IngestionRunRepository | None = None
    trust_score_repo: TrustScoreRepository | None = None
    lifecycle_repo: LifecycleRepository | None = None
    data_source_repo: DataSourceRepository | None = None
    provenance_repo: TraceabilityRepository | None = None
    category_repo: CategoryRepository | None = None
    search_group_repo: SearchGroupRepository | None = None
    usage_log_repo: UsageLogRepository | None = None

    # Domain Services
    term_extractor: TermExtractor | None = None
    trust_score_service: TrustScoreService | None = None
    lifecycle_service: Any = None  # LifecycleService (lightweight)
    freshness_predictor: FreshnessPredictor | None = None

    # Cache
    search_cache: SearchCache | None = None
    dedup_cache: DedupCache | None = None
    multi_layer_cache: MultiLayerCache | None = None
    idempotency_cache: IdempotencyCache | None = None

    # Dedup Pipeline
    dedup_pipeline: DedupPipeline | None = None
    dedup_result_tracker: DedupResultTracker | None = None
    redis_dedup_index: RedisDedupIndex | None = None

    # Vector DB (Qdrant)
    qdrant_provider: QdrantClientProvider | None = None
    qdrant_collections: QdrantCollectionManager | None = None
    qdrant_search: QdrantSearchEngine | None = None
    qdrant_store: QdrantStoreOperations | None = None

    # Graph DB (Neo4j)
    neo4j: Neo4jClient | None = None
    graph_repo: Neo4jGraphRepository | None = None
    graph_expander: GraphExpander | None = None
    graph_integrity: GraphIntegrityChecker | None = None
    multi_hop_searcher: MultiHopSearcher | None = None

    # AI Providers
    embedder: IEmbedder | None = None
    llm: Any = None  # OllamaClient | SageMakerLLMClient (다형적)
    graphrag_extractor: GraphRAGExtractor | None = None

    # Search Services
    query_preprocessor: QueryPreprocessor | None = None
    composite_reranker: CompositeReranker | None = None
    query_classifier: QueryClassifier | None = None
    tiered_response_generator: TieredResponseGenerator | None = None
    answer_service: AnswerService | None = None
    crag_evaluator: CRAGRetrievalEvaluator | None = None
    query_expander: QueryExpansionService | None = None
    rag_pipeline: KnowledgeRAGPipeline | None = None

    # Auth
    auth_provider: Any = None  # AuthProvider (Protocol, 다형적)
    rbac_engine: RBACEngine | None = None
    abac_engine: ABACEngine | None = None
    auth_service: AuthService | None = None
    jwt_service: JWTService | None = None
    token_store: TokenStore | None = None

    # Distill
    distill_repo: Any = None  # DistillRepository (optional plugin)

    # Internal
    _shutting_down: bool = False
    _background_tasks: set[asyncio.Task] | None = None

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
        """Dict-compatible setdefault."""
        val = getattr(self, key, None)
        if val is not None:
            return val
        setattr(self, key, default)
        return default

    def __contains__(self, key: str) -> bool:
        val = getattr(self, key, None)
        return val is not None
