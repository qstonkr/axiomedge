"""Knowledge Local - FastAPI Application.

Standalone knowledge management API server.
All oreo framework dependencies removed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncIterator

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from src.api.errors import (
    ErrorResponse as _ErrorResponse,
    http_exception_handler as _http_exc_handler,
    unhandled_exception_handler as _unhandled_exc_handler,
)

from src.api.state import AppState

load_dotenv()

_DEFAULT_REDIS_URL = "redis://localhost:6379"


# ---------------------------------------------------------------------------
# Structured JSON logging
# ---------------------------------------------------------------------------
class JSONFormatter(logging.Formatter):
    """Emit log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
        }
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry, ensure_ascii=False)


_handler = logging.StreamHandler()
_handler.setFormatter(JSONFormatter())
logging.root.handlers = [_handler]
logging.root.setLevel(logging.INFO)

logger = logging.getLogger(__name__)

# Shared state for lazy-initialized singletons
_state = AppState()


def _get_state() -> AppState:
    return _state


async def _init_db_with_retry(settings) -> None:
    """Attempt DB init with up to 3 retries."""
    from src.database.init_db import init_database
    import asyncio as _asyncio

    db_url = settings.database.database_url
    for _attempt in range(3):
        try:
            await init_database(db_url)
            break
        except Exception as _db_err:
            if _attempt < 2:
                logger.warning("DB init attempt %d failed, retrying in 2s: %s", _attempt + 1, _db_err)
                await _asyncio.sleep(2)
            else:
                raise


def _create_repositories(state: AppState, session_factory, db_url: str):
    """Create all repository instances and store in state."""
    from src.database.repositories.kb_registry import KBRegistryRepository
    from src.database.repositories.glossary import GlossaryRepository
    from src.database.repositories.ownership import (
        DocumentOwnerRepository,
        TopicOwnerRepository,
        ErrorReportRepository,
    )
    from src.database.repositories.feedback import FeedbackRepository
    from src.database.repositories.ingestion_run import IngestionRunRepository
    from src.database.repositories.trust_score import TrustScoreRepository
    from src.database.repositories.lifecycle import DocumentLifecycleRepository
    from src.database.repositories.data_source import DataSourceRepository
    from src.database.repositories.traceability import ProvenanceRepository
    from src.database.repositories.category import CategoryRepository
    from src.database.repositories.search_group import SearchGroupRepository
    from src.database.repositories.usage_log import UsageLogRepository

    state["glossary_repo"] = GlossaryRepository(session_factory)
    state["doc_owner_repo"] = DocumentOwnerRepository(session_factory)
    state["topic_owner_repo"] = TopicOwnerRepository(session_factory)
    state["error_report_repo"] = ErrorReportRepository(session_factory)
    state["feedback_repo"] = FeedbackRepository(session_factory)
    state["ingestion_run_repo"] = IngestionRunRepository(session_factory)
    state["trust_score_repo"] = TrustScoreRepository(session_factory)
    state["lifecycle_repo"] = DocumentLifecycleRepository(session_factory)
    state["data_source_repo"] = DataSourceRepository(session_factory)
    state["provenance_repo"] = ProvenanceRepository(session_factory)
    state["category_repo"] = CategoryRepository(session_factory)
    state["search_group_repo"] = SearchGroupRepository(session_factory)
    state["usage_log_repo"] = UsageLogRepository(session_factory)
    state["_kb_registry_pending"] = KBRegistryRepository(db_url)


async def _init_database(state: AppState, settings) -> None:
    """Initialize PostgreSQL + all repositories + domain services."""
    from src.database.session import create_async_session_factory

    db_url = settings.database.database_url
    await _init_db_with_retry(settings)

    session_factory = create_async_session_factory(
        db_url,
        pool_size=settings.database.pool_size,
        max_overflow=settings.database.max_overflow,
        echo=settings.database.echo,
    )
    state["db_session_factory"] = session_factory

    # Initialize repositories
    _create_repositories(state, session_factory, db_url)

    # KB Registry uses its own engine (manages RegistryBase tables)
    kb_registry = state["_kb_registry_pending"]
    await kb_registry.initialize()
    state["kb_registry"] = kb_registry

    # Load L1 categories into ingestion pipeline cache
    try:
        cat_repo = state.get("category_repo")
        if cat_repo:
            l1_cats = await cat_repo.get_l1_categories()
            if l1_cats:
                from src.pipeline.ingestion import load_l1_categories_from_db
                load_l1_categories_from_db(l1_cats)
    except Exception as e:
        logger.warning("L1 category cache load failed (using defaults): %s", e)

    # Term extractor for ingestion
    try:
        from src.pipeline.term_extractor import TermExtractor
        state["term_extractor"] = TermExtractor(
            glossary_repo=state.get("glossary_repo"),
            embedder=state.get("embedder"),
        )
        logger.info("TermExtractor initialized")
    except Exception as e:
        logger.warning("TermExtractor init failed: %s", e)

    # Trust Score Service
    try:
        from src.search.trust_score_service import TrustScoreService
        state["trust_score_service"] = TrustScoreService(
            trust_score_repo=state.get("trust_score_repo"),
            feedback_repo=state.get("feedback_repo"),
        )
        logger.info("TrustScoreService initialized")
    except Exception as e:
        logger.warning("TrustScoreService init failed: %s", e)

    # Lifecycle State Machine
    try:
        from src.domain.lifecycle import LifecycleStateMachine
        state["lifecycle_service"] = LifecycleStateMachine(
            lifecycle_repo=state.get("lifecycle_repo"),
        )
        logger.info("LifecycleStateMachine initialized")
    except Exception as e:
        logger.warning("LifecycleStateMachine init failed: %s", e)

    # Freshness Predictor
    try:
        from src.search.freshness_predictor import FreshnessPredictor
        state["freshness_predictor"] = FreshnessPredictor()
        logger.info("FreshnessPredictor initialized")
    except Exception as e:
        logger.warning("FreshnessPredictor init failed: %s", e)

    logger.info("PostgreSQL database initialized: %s", db_url.split("@")[-1] if "@" in db_url else db_url)


async def _init_cache(state: AppState) -> None:
    """Initialize Redis caches + multi-layer cache."""
    await asyncio.sleep(0)

    # Redis cache (search cache + dedup cache + multi-layer cache)
    try:
        redis_url = os.getenv("REDIS_URL", _DEFAULT_REDIS_URL)
        from src.cache.redis_cache import SearchCache
        from src.cache.dedup_cache import DedupCache

        state["search_cache"] = SearchCache(redis_url=redis_url)
        state["dedup_cache"] = DedupCache(redis_url=redis_url)
        logger.info("Redis cache initialized: %s", redis_url)
    except Exception as e:
        logger.warning("Redis cache init failed (search/dedup cache disabled): %s", e)

    # Multi-Layer Cache (L1 memory + L2 Redis semantic)
    try:
        from src.cache.multi_layer_cache import MultiLayerCache
        from src.cache.l1_memory_cache import L1InMemoryCache
        from src.cache.l2_semantic_cache import L2SemanticCache
        from src.cache.idempotency_cache import IdempotencyCache
        from src.config_weights import weights as _cache_weights

        cache_cfg = _cache_weights.cache
        l1 = L1InMemoryCache(
            max_size=cache_cfg.l1_max_entries,
            ttl_seconds=cache_cfg.l1_ttl_seconds,
        )

        l2 = None
        if cache_cfg.enable_semantic_cache:
            _cache_redis_url = os.getenv("REDIS_URL", _DEFAULT_REDIS_URL)
            l2 = L2SemanticCache(
                redis_url=_cache_redis_url,
                embedding_provider=None,  # Set after embedder init below
                similarity_threshold=cache_cfg.l2_similarity_threshold,
                max_entries=cache_cfg.l2_max_entries,
                ttl_seconds=cache_cfg.l2_ttl_seconds,
            )

        multi_cache = MultiLayerCache(
            l1_cache=l1,
            l2_cache=l2,
            embedding_provider=None,  # Will be set after embedder init
        )
        state["multi_layer_cache"] = multi_cache

        # Idempotency cache
        _idemp_redis = None
        try:
            import redis.asyncio as _aioredis
            _idemp_redis = _aioredis.from_url(
                os.getenv("REDIS_URL", _DEFAULT_REDIS_URL),
                decode_responses=True,
            )
        except Exception as e:
            logger.warning("Failed to create idempotency Redis client: %s", e)
        state["idempotency_cache"] = IdempotencyCache(
            redis_client=_idemp_redis,
            ttl_seconds=cache_cfg.idempotency_ttl_seconds,
        )

        logger.info(
            "MultiLayerCache initialized (L1=%d entries, semantic=%s)",
            cache_cfg.l1_max_entries,
            cache_cfg.enable_semantic_cache,
        )
    except Exception as e:
        logger.warning("MultiLayerCache init failed: %s", e)


async def _init_dedup(state: AppState) -> None:
    """Initialize 4-stage dedup pipeline."""
    await asyncio.sleep(0)
    try:
        from src.pipeline.dedup import DedupPipeline, DedupResultTracker, RedisDedupIndex
        from src.pipeline.dedup.bloom_filter import BloomFilter
        from src.pipeline.dedup.conflict_detector import OllamaLLMClient
        from src.config_weights import weights as _w

        dedup_cfg = _w.dedup
        bloom = BloomFilter(
            expected_items=dedup_cfg.bloom_expected_items,
            fp_rate=dedup_cfg.bloom_false_positive_rate,
        )

        # Stage 4 LLM client: use Ollama if available
        stage4_llm = None
        if dedup_cfg.enable_stage4:
            try:
                from src.config import get_settings
                _s = get_settings()
                stage4_llm = OllamaLLMClient(
                    base_url=_s.ollama.base_url,
                    model=_s.ollama.model,
                )
            except Exception as e:
                logger.warning("Failed to create stage4 LLM client for dedup: %s", e)

        dedup_pipeline = DedupPipeline(
            bloom_filter=bloom,
            llm_client=stage4_llm,
            enable_stage4=dedup_cfg.enable_stage4,
            near_duplicate_threshold=dedup_cfg.near_duplicate_threshold,
            semantic_duplicate_threshold=dedup_cfg.semantic_duplicate_threshold,
            stage3_skip_threshold=dedup_cfg.stage3_skip_threshold,
        )
        state["dedup_pipeline"] = dedup_pipeline

        # Result tracker (requires Redis)
        redis_client = None
        try:
            import redis.asyncio as aioredis
            _redis_url = os.getenv("REDIS_URL", _DEFAULT_REDIS_URL)
            redis_client = aioredis.from_url(_redis_url, decode_responses=True)
        except Exception as e:
            logger.warning("Failed to create dedup Redis client: %s", e)
        state["dedup_result_tracker"] = DedupResultTracker(redis_client=redis_client)
        state["redis_dedup_index"] = RedisDedupIndex(redis_client=redis_client)

        logger.info(
            "DedupPipeline initialized (near=%.2f, semantic=%.2f, skip=%.2f, stage4=%s)",
            dedup_cfg.near_duplicate_threshold,
            dedup_cfg.semantic_duplicate_threshold,
            dedup_cfg.stage3_skip_threshold,
            dedup_cfg.enable_stage4,
        )
    except Exception as e:
        logger.warning("DedupPipeline init failed (using simple dedup cache): %s", e)


async def _init_vectordb(state: AppState, settings) -> None:
    """Initialize Qdrant client, collections, search engine, and store."""
    try:
        from src.vectordb.client import QdrantConfig, QdrantClientProvider

        config = QdrantConfig.from_env()
        provider = QdrantClientProvider(config)
        await provider.ensure_client()
        state["qdrant_provider"] = provider

        from src.vectordb.collections import QdrantCollectionManager
        from src.vectordb.search import QdrantSearchEngine
        from src.vectordb.store import QdrantStoreOperations

        cm = QdrantCollectionManager(provider)
        state["qdrant_collections"] = cm
        state["qdrant_search"] = QdrantSearchEngine(provider, cm)
        state["qdrant_store"] = QdrantStoreOperations(provider, cm)
        logger.info("Qdrant initialized: %s", settings.qdrant.url)
    except Exception as e:
        logger.warning("Qdrant init failed: %s", e)


async def _init_graph(state: AppState, settings) -> None:
    """Initialize Neo4j client, graph repo, expander, integrity checker, multi-hop."""
    if not settings.neo4j.enabled:
        return

    try:
        from src.graph.client import Neo4jClient

        neo4j = Neo4jClient(
            uri=settings.neo4j.uri,
            user=settings.neo4j.user,
            password=settings.neo4j.password,
            database=settings.neo4j.database,
        )
        await neo4j.connect()
        state["neo4j"] = neo4j

        from src.graph.repository import Neo4jGraphRepository

        state["graph_repo"] = Neo4jGraphRepository(neo4j)

        from src.search.graph_expander import GraphSearchExpander
        state["graph_expander"] = GraphSearchExpander(graph_repo=state["graph_repo"])

        # Ensure graph indexes (idempotent)
        try:
            from src.graph.indexer import ensure_indexes
            index_result = await ensure_indexes(neo4j)
            logger.info(
                "Graph indexes ensured: %d constraints, %d indexes, %d fulltext",
                index_result.get("constraints_created", 0),
                index_result.get("indexes_created", 0),
                index_result.get("fulltext_indexes_created", 0),
            )
        except Exception as _idx_err:
            logger.warning("Graph index creation failed (non-fatal): %s", _idx_err)

        # Initialize graph integrity checker and multi-hop searcher
        try:
            from src.graph.integrity import GraphIntegrityChecker
            from src.graph.multi_hop_searcher import MultiHopSearcher

            state["graph_integrity"] = GraphIntegrityChecker(
                neo4j_client=neo4j,
                graph_repository=state["graph_repo"],
            )
            state["multi_hop_searcher"] = MultiHopSearcher(
                neo4j_client=neo4j,
                graph_repository=state["graph_repo"],
            )
            logger.info("Graph integrity checker and multi-hop searcher initialized")
        except Exception as _graph_err:
            logger.warning("Graph advanced services init failed: %s", _graph_err)

        logger.info("Neo4j initialized: %s", settings.neo4j.uri)
    except Exception as e:
        logger.warning("Neo4j init failed: %s", e)


def _try_tei_embedding(_settings):
    """Try to initialize TEI embedding provider."""
    use_cloud = os.getenv("USE_CLOUD_EMBEDDING", "true").lower() in ("true", "1", "yes")
    if not use_cloud:
        logger.info("Cloud embedding disabled (USE_CLOUD_EMBEDDING=false), using local")
        return None
    try:
        from src.embedding.tei_provider import TEIEmbeddingProvider

        tei_url = os.getenv("BGE_TEI_URL", "http://localhost:8080")
        tei_embedder = TEIEmbeddingProvider(base_url=tei_url)
        if tei_embedder.is_ready():
            logger.info("TEI embedding initialized (cloud): %s", tei_url)
            return tei_embedder
    except Exception as e:
        logger.debug("TEI embedding not available: %s", e)
    return None


def _try_ollama_embedding(settings):
    """Try to initialize Ollama embedding provider."""
    try:
        from src.embedding.ollama_provider import OllamaEmbeddingProvider

        ollama_embedder = OllamaEmbeddingProvider(
            base_url=settings.ollama.base_url,
            model=settings.ollama.embedding_model,
        )
        if ollama_embedder.is_ready():
            logger.info("Ollama embedding initialized (Metal GPU): %s", settings.ollama.embedding_model)
            return ollama_embedder
    except Exception as e:
        logger.debug("Ollama embedding not available: %s", e)
    return None


def _try_onnx_embedding(settings):
    """Try to initialize ONNX embedding provider."""
    try:
        from src.embedding.onnx_provider import OnnxBgeEmbeddingProvider

        model_path = settings.embedding.onnx_model_path or os.getenv(
            "KNOWLEDGE_BGE_ONNX_MODEL_PATH", ""
        )
        onnx_embedder = OnnxBgeEmbeddingProvider(model_path=model_path)
        if onnx_embedder.is_ready():
            logger.info("BGE-M3 ONNX embedding initialized (CPU)")
            return onnx_embedder
        logger.warning("BGE-M3 ONNX model not ready (check model path)")
    except Exception as e:
        logger.warning("ONNX embedding init failed: %s", e)
    return None


def _wire_embedder_to_cache(state: AppState, embedder) -> None:
    """Wire embedder into MultiLayerCache for L2 semantic matching."""
    multi_cache = state.get("multi_layer_cache")
    if multi_cache is None:
        return
    multi_cache._embedding_provider = embedder
    if multi_cache._l2 is not None and hasattr(multi_cache._l2, "_embedding_provider"):
        multi_cache._l2._embedding_provider = embedder
    logger.info("MultiLayerCache embedder wired")


async def _init_embedding(state: AppState, settings) -> None:
    """Initialize embedding provider: TEI > Ollama > ONNX fallback, wire to cache."""
    await asyncio.sleep(0)
    embedder = (
        _try_tei_embedding(settings)
        or _try_ollama_embedding(settings)
        or _try_onnx_embedding(settings)
    )

    if embedder:
        state["embedder"] = embedder
        _wire_embedder_to_cache(state, embedder)
    else:
        logger.error("No embedding provider available. Search will not work.")


async def _init_llm(state: AppState, settings) -> None:
    """Initialize LLM client (Ollama or SageMaker) + GraphRAG extractor."""
    await asyncio.sleep(0)
    try:
        use_sagemaker = os.getenv("USE_SAGEMAKER_LLM", "false").lower() == "true"
        if use_sagemaker:
            from src.llm.sagemaker_client import SageMakerConfig, SageMakerLLMClient
            llm = SageMakerLLMClient(config=SageMakerConfig())
            state["llm"] = llm
            logger.info("SageMaker LLM initialized: %s", SageMakerConfig().endpoint_name)
        else:
            from src.llm.ollama_client import OllamaClient, OllamaConfig
            llm_config = OllamaConfig(
                base_url=settings.ollama.base_url,
                model=settings.ollama.model,
                context_length=settings.ollama.context_length,
            )
            llm = OllamaClient(config=llm_config)
            state["llm"] = llm
            logger.info("Ollama LLM initialized: %s (%s)", settings.ollama.base_url, settings.ollama.model)
    except Exception as e:
        logger.warning("LLM init failed: %s", e)

    # GraphRAG extractor
    if state.get("llm") and state.get("neo4j"):
        try:
            from src.pipeline.graphrag_extractor import GraphRAGExtractor
            state["graphrag_extractor"] = GraphRAGExtractor()
            logger.info("GraphRAGExtractor initialized")
        except Exception as e:
            logger.warning("GraphRAGExtractor init failed: %s", e)


async def _init_search_services(state: AppState) -> None:
    """Initialize all search services + RAG pipeline."""
    await asyncio.sleep(0)
    # QueryPreprocessor
    try:
        from src.search.query_preprocessor import QueryPreprocessor

        state["query_preprocessor"] = QueryPreprocessor()
        logger.info("QueryPreprocessor initialized")
    except Exception as e:
        logger.warning("QueryPreprocessor init failed: %s", e)

    # CompositeReranker
    try:
        from src.search.composite_reranker import CompositeReranker

        state["composite_reranker"] = CompositeReranker()
        logger.info("CompositeReranker initialized")
    except Exception as e:
        logger.warning("CompositeReranker init failed: %s", e)

    # Cross-encoder warmup (fire-and-forget background model load)
    try:
        from src.search.cross_encoder_reranker import warmup as ce_warmup
        ce_warmup()
        logger.info("Cross-encoder warmup started")
    except Exception as e:
        logger.warning("Cross-encoder warmup failed: %s", e)

    # QueryClassifier (cached singleton, P1-4 perf fix)
    try:
        from src.search.query_classifier import QueryClassifier

        state["query_classifier"] = QueryClassifier()
        logger.info("QueryClassifier initialized")
    except Exception as e:
        logger.warning("QueryClassifier init failed: %s", e)

    # TieredResponseGenerator
    if state.get("llm"):
        try:
            from src.search.tiered_response import TieredResponseGenerator

            state["tiered_response_generator"] = TieredResponseGenerator(
                llm_client=state["llm"],
            )
            logger.info("TieredResponseGenerator initialized")
        except Exception as e:
            logger.warning("TieredResponseGenerator init failed: %s", e)

    # AnswerService (singleton - avoid per-request lazy init race)
    if state.get("llm"):
        try:
            from src.search.answer_service import AnswerService

            state["answer_service"] = AnswerService(llm_client=state["llm"])
            logger.info("AnswerService initialized")
        except Exception as e:
            logger.warning("AnswerService init failed: %s", e)

    # CRAGRetrievalEvaluator (singleton, avoid per-request construction)
    try:
        from src.search.crag_evaluator import CRAGRetrievalEvaluator

        state["crag_evaluator"] = CRAGRetrievalEvaluator()
        logger.info("CRAGRetrievalEvaluator initialized")
    except Exception as e:
        logger.warning("CRAGRetrievalEvaluator init failed: %s", e)

    # QueryExpansionService
    try:
        from src.search.query_expansion import QueryExpansionService
        state["query_expander"] = QueryExpansionService(glossary_repository=state.get("glossary_repo"))
        logger.info("QueryExpansionService initialized")
    except Exception as e:
        logger.warning("QueryExpansionService init failed: %s", e)

    # RAG pipeline
    from src.search.rag_pipeline import KnowledgeRAGPipeline

    state["rag_pipeline"] = KnowledgeRAGPipeline(
        search_engine=state.get("qdrant_search"),
        llm_client=state.get("llm"),
        graph_client=state.get("neo4j"),
        embedder=state.get("embedder"),
        query_preprocessor=state.get("query_preprocessor"),
        query_expander=state.get("query_expander"),
    )

    missing = [k for k in ["qdrant_search", "embedder", "llm"] if k not in state]
    if missing:
        logger.warning("RAG pipeline initialized without: %s", missing)


async def _init_auth(state: AppState, settings) -> None:
    """Initialize auth provider + RBAC/ABAC engines."""
    try:
        import json as _json
        from src.auth.providers import create_auth_provider
        from src.auth.rbac import RBACEngine
        from src.auth.abac import ABACEngine, DEFAULT_ABAC_POLICIES
        from src.auth.service import AuthService

        auth_settings = settings.auth
        provider_kwargs: dict = {}
        if auth_settings.provider == "keycloak":
            provider_kwargs = {
                "server_url": auth_settings.keycloak_url,
                "realm": auth_settings.keycloak_realm,
                "client_id": auth_settings.keycloak_client_id,
                "client_secret": auth_settings.keycloak_client_secret,
            }
        elif auth_settings.provider == "azure_ad":
            provider_kwargs = {
                "tenant_id": auth_settings.azure_ad_tenant_id,
                "client_id": auth_settings.azure_ad_client_id,
            }
        elif auth_settings.provider == "internal":
            from src.auth.jwt_service import JWTService
            from src.auth.token_store import TokenStore

            if not auth_settings.jwt_secret:
                raise ValueError(
                    "AUTH_JWT_SECRET is required when AUTH_PROVIDER=internal. "
                    "Generate one with: openssl rand -hex 32"
                )

            jwt_svc = JWTService(
                secret_key=auth_settings.jwt_secret,
                algorithm=auth_settings.jwt_algorithm,
                access_token_expire_minutes=auth_settings.jwt_access_expire_minutes,
                refresh_token_expire_hours=auth_settings.jwt_refresh_expire_hours,
                issuer=auth_settings.jwt_issuer,
            )
            state["jwt_service"] = jwt_svc
            provider_kwargs = {"jwt_service": jwt_svc}
        else:
            try:
                api_keys = _json.loads(auth_settings.local_api_keys)
            except Exception:
                api_keys = {}
            provider_kwargs = {"api_keys": api_keys}

        state["auth_provider"] = create_auth_provider(auth_settings.provider, **provider_kwargs)
        state["rbac_engine"] = RBACEngine()
        state["abac_engine"] = ABACEngine(policies=DEFAULT_ABAC_POLICIES)

        # Auth service (DB operations)
        db_url = settings.database.database_url
        auth_service = AuthService(
            database_url=db_url,
            pool_size=settings.database.pool_size,
            max_overflow=settings.database.max_overflow,
        )
        state["auth_service"] = auth_service

        # Token store for internal auth (uses auth_service's DB)
        if auth_settings.provider == "internal":
            state["token_store"] = TokenStore(auth_service._session_factory)

        # Seed default roles & permissions
        try:
            await auth_service.seed_defaults()
        except Exception as e:
            logger.debug("Auth seed_defaults deferred: %s", e)

        logger.info("Auth initialized: provider=%s, enabled=%s", auth_settings.provider, auth_settings.enabled)
    except Exception as e:
        logger.warning("Auth init failed (running without auth): %s", e)


async def _init_services():
    """Orchestrate all service initialization in dependency order."""
    from src.config import get_settings
    settings = get_settings()

    try:
        await _init_database(_state, settings)
    except Exception as e:
        logger.warning("PostgreSQL init failed (repositories will use stubs): %s", e)

    await _init_cache(_state)
    await _init_dedup(_state)
    await _init_vectordb(_state, settings)
    await _init_graph(_state, settings)
    await _init_embedding(_state, settings)
    await _init_llm(_state, settings)
    await _init_search_services(_state)
    await _init_auth(_state, settings)


async def _close_caches(state: AppState) -> None:
    """Close Redis caches and multi-layer cache L2."""
    for key in ("search_cache", "dedup_cache"):
        cache = state.get(key)
        if cache:
            try:
                await cache.close()
                logger.info("Closed %s", key)
            except Exception as e:
                logger.debug("Error closing %s: %s", key, e)

    multi_cache = state.get("multi_layer_cache")
    if multi_cache and hasattr(multi_cache, "_l2") and multi_cache._l2 is not None:
        try:
            if hasattr(multi_cache._l2, "close"):
                await multi_cache._l2.close()
            logger.info("Closed multi_layer_cache L2")
        except Exception as e:
            logger.debug("Error closing multi_layer_cache L2: %s", e)


async def _close_connections(state: AppState) -> None:
    """Close Qdrant, Neo4j, PostgreSQL, and Auth connections."""
    for key, label, method in (
        ("qdrant_provider", "Qdrant", "close"),
        ("neo4j", "Neo4j", "close"),
        ("kb_registry", "KB registry", "shutdown"),
    ):
        svc = state.get(key)
        if not svc:
            continue
        try:
            await getattr(svc, method)()
            logger.info("Closed %s", label)
        except Exception as e:
            logger.debug("Error closing %s: %s", label, e)

    auth_svc = state.get("auth_service")
    if auth_svc:
        try:
            await auth_svc.close()
            logger.info("Closed Auth service")
        except Exception as e:
            logger.debug("Error closing Auth service: %s", e)


async def _shutdown_services():
    """Clean up on shutdown with graceful drain of active jobs."""
    import asyncio as _aio

    from src.api.routes.jobs import get_active_job_count

    # Set shutdown flag to prevent new ingestion jobs
    _state["_shutting_down"] = True
    logger.info("Shutting down... draining active tasks")

    # Wait up to 30 seconds for active ingestion jobs to complete
    deadline = 30
    poll_interval = 0.5
    elapsed = 0.0
    while elapsed < deadline:
        active_count = await get_active_job_count()
        if active_count == 0:
            break
        logger.info("Waiting for %d active job(s) to finish (%.0fs remaining)", active_count, deadline - elapsed)
        await _aio.sleep(poll_interval)
        elapsed += poll_interval

    active_remaining = await get_active_job_count()
    if active_remaining:
        logger.warning("Shutdown deadline reached with %d job(s) still active", active_remaining)

    await _close_caches(_state)
    await _close_connections(_state)

    logger.info("Shutdown complete")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    await _init_services()
    app.state._app_state = _state  # Expose for request.app.state access (no circular import)
    yield
    await _shutdown_services()


app = FastAPI(
    title="Knowledge Local",
    description="Standalone Knowledge Management System with RAG capabilities. "
    "Provides document ingestion, hybrid vector+graph search, and LLM-powered answers.",
    version="0.1.0",
    lifespan=lifespan,
    openapi_tags=[
        {"name": "Search", "description": "Hub Search — unified knowledge search with RAG pipeline"},
        {"name": "Knowledge", "description": "Knowledge base management — CRUD, ingestion, stats"},
        {"name": "Auth & Permissions", "description": "Authentication, user management, RBAC/ABAC, activity logs"},
        {"name": "Glossary", "description": "Domain glossary management — terms, synonyms, CSV import"},
        {"name": "Quality & Trust", "description": "KTS trust scores, evaluation, provenance, lineage"},
        {"name": "Feedback", "description": "User feedback and voting on knowledge entries"},
        {"name": "Ownership", "description": "Document/topic ownership and error reporting"},
        {"name": "Data Sources", "description": "External data source management"},
        {"name": "Search Groups", "description": "KB search group scoping"},
        {"name": "Jobs", "description": "Background ingestion job tracking"},
        {"name": "RAG", "description": "Direct RAG operations — file upload, JSONL reingest"},
        {"name": "Admin", "description": "Administrative operations — KB config, graph queries"},
    ],
    responses={
        400: {"description": "Bad Request", "model": _ErrorResponse},
        401: {"description": "Unauthorized"},
        403: {"description": "Forbidden"},
        404: {"description": "Not Found", "model": _ErrorResponse},
        500: {"description": "Internal Server Error", "model": _ErrorResponse},
        503: {"description": "Service Unavailable", "model": _ErrorResponse},
    },
)

# Global exception handlers — normalize all errors to standard JSON format
app.add_exception_handler(HTTPException, _http_exc_handler)
app.add_exception_handler(Exception, _unhandled_exc_handler)

# Rate limiter middleware (conditional on env var) — added before CORS
if os.getenv("RATE_LIMIT_ENABLED", "false").lower() == "true":
    from src.api.middleware.rate_limiter import RateLimiterMiddleware
    app.add_middleware(RateLimiterMiddleware)
    logger.info(
        "Rate limiter enabled: %s req / %s sec",
        os.getenv("RATE_LIMIT_REQUESTS", "100"),
        os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60"),
    )

# Register routes
from src.api.routes import (  # noqa: E402
    health, search, ingest, admin, kb,
    glossary, ownership, pipeline, quality,
    feedback, data_sources, search_analytics,
    rag,
)
from src.api.routes import metrics as metrics_route  # noqa: E402
from src.api.routes import jobs as jobs_route  # noqa: E402

app.include_router(health.router)
app.include_router(search.router)
app.include_router(ingest.router)
app.include_router(admin.router)
app.include_router(kb.router)
app.include_router(kb.admin_router)
app.include_router(glossary.router)
app.include_router(ownership.admin_router)
app.include_router(ownership.knowledge_router)
app.include_router(pipeline.router)
app.include_router(quality.router)
app.include_router(feedback.admin_router)
app.include_router(feedback.knowledge_router)
app.include_router(data_sources.router)
app.include_router(search_analytics.router)
app.include_router(rag.knowledge_router)
app.include_router(rag.rag_query_router)
app.include_router(metrics_route.router)
app.include_router(jobs_route.router)

from src.api.routes import search_groups  # noqa: E402
app.include_router(search_groups.router)

from src.api.routes import auth as auth_routes  # noqa: E402
app.include_router(auth_routes.router)

# Auth middleware (adds user context + activity logging)
from src.auth.middleware import AuthMiddleware  # noqa: E402
app.add_middleware(AuthMiddleware)

# CORSMiddleware MUST be added LAST (outermost = first to execute)
cors_origins = os.getenv("CORS_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True if cors_origins != ["*"] else False,
    allow_methods=["*"],
    allow_headers=["*"],
)
