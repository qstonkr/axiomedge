"""Knowledge Local - FastAPI Application.

Standalone knowledge management API server.
All oreo framework dependencies removed.
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncIterator

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()


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
_state: dict = {}


def _get_state():
    return _state


async def _init_services():
    """Initialize all services on startup."""
    from src.config import get_settings

    settings = get_settings()

    # PostgreSQL database
    try:
        from src.database.session import create_async_session_factory
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

        session_factory = create_async_session_factory(
            db_url,
            pool_size=settings.database.pool_size,
            max_overflow=settings.database.max_overflow,
            echo=settings.database.echo,
        )
        _state["db_session_factory"] = session_factory

        # Initialize repositories
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

        # KB Registry uses its own engine (manages RegistryBase tables)
        kb_registry = KBRegistryRepository(db_url)
        await kb_registry.initialize()
        _state["kb_registry"] = kb_registry

        _state["glossary_repo"] = GlossaryRepository(session_factory)
        _state["doc_owner_repo"] = DocumentOwnerRepository(session_factory)
        _state["topic_owner_repo"] = TopicOwnerRepository(session_factory)
        _state["error_report_repo"] = ErrorReportRepository(session_factory)
        _state["feedback_repo"] = FeedbackRepository(session_factory)
        _state["ingestion_run_repo"] = IngestionRunRepository(session_factory)
        _state["trust_score_repo"] = TrustScoreRepository(session_factory)
        _state["lifecycle_repo"] = DocumentLifecycleRepository(session_factory)
        _state["data_source_repo"] = DataSourceRepository(session_factory)
        _state["provenance_repo"] = ProvenanceRepository(session_factory)
        _state["category_repo"] = CategoryRepository(session_factory)
        _state["search_group_repo"] = SearchGroupRepository(session_factory)
        _state["usage_log_repo"] = UsageLogRepository(session_factory)

        # Term extractor for ingestion
        try:
            from src.pipeline.term_extractor import TermExtractor
            _state["term_extractor"] = TermExtractor(glossary_repo=_state.get("glossary_repo"))
            logger.info("TermExtractor initialized")
        except Exception as e:
            logger.warning("TermExtractor init failed: %s", e)

        # Trust Score Service
        try:
            from src.search.trust_score_service import TrustScoreService
            _state["trust_score_service"] = TrustScoreService(
                trust_score_repo=_state["trust_score_repo"],
                feedback_repo=_state.get("feedback_repo"),
            )
            logger.info("TrustScoreService initialized")
        except Exception as e:
            logger.warning("TrustScoreService init failed: %s", e)

        # Lifecycle State Machine
        try:
            from src.domain.lifecycle import LifecycleStateMachine
            _state["lifecycle_service"] = LifecycleStateMachine(
                lifecycle_repo=_state["lifecycle_repo"],
            )
            logger.info("LifecycleStateMachine initialized")
        except Exception as e:
            logger.warning("LifecycleStateMachine init failed: %s", e)

        # Freshness Predictor
        try:
            from src.search.freshness_predictor import FreshnessPredictor
            _state["freshness_predictor"] = FreshnessPredictor()
            logger.info("FreshnessPredictor initialized")
        except Exception as e:
            logger.warning("FreshnessPredictor init failed: %s", e)

        logger.info("PostgreSQL database initialized: %s", db_url.split("@")[-1] if "@" in db_url else db_url)
    except Exception as e:
        logger.warning("PostgreSQL init failed (repositories will use stubs): %s", e)

    # Redis cache (search cache + dedup cache + multi-layer cache)
    try:
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
        from src.cache.redis_cache import SearchCache
        from src.cache.dedup_cache import DedupCache

        _state["search_cache"] = SearchCache(redis_url=redis_url)
        _state["dedup_cache"] = DedupCache(redis_url=redis_url)
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
            _cache_redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
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
        _state["multi_layer_cache"] = multi_cache

        # Idempotency cache
        _idemp_redis = None
        try:
            import redis.asyncio as _aioredis
            _idemp_redis = _aioredis.from_url(
                os.getenv("REDIS_URL", "redis://localhost:6379"),
                decode_responses=True,
            )
        except Exception:
            pass
        _state["idempotency_cache"] = IdempotencyCache(
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

    # 4-Stage Dedup Pipeline
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
            except Exception:
                pass

        dedup_pipeline = DedupPipeline(
            bloom_filter=bloom,
            llm_client=stage4_llm,
            enable_stage4=dedup_cfg.enable_stage4,
            near_duplicate_threshold=dedup_cfg.near_duplicate_threshold,
            semantic_duplicate_threshold=dedup_cfg.semantic_duplicate_threshold,
            stage3_skip_threshold=dedup_cfg.stage3_skip_threshold,
        )
        _state["dedup_pipeline"] = dedup_pipeline

        # Result tracker (requires Redis)
        redis_client = None
        try:
            import redis.asyncio as aioredis
            _redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
            redis_client = aioredis.from_url(_redis_url, decode_responses=True)
        except Exception:
            pass
        _state["dedup_result_tracker"] = DedupResultTracker(redis_client=redis_client)
        _state["redis_dedup_index"] = RedisDedupIndex(redis_client=redis_client)

        logger.info(
            "DedupPipeline initialized (near=%.2f, semantic=%.2f, skip=%.2f, stage4=%s)",
            dedup_cfg.near_duplicate_threshold,
            dedup_cfg.semantic_duplicate_threshold,
            dedup_cfg.stage3_skip_threshold,
            dedup_cfg.enable_stage4,
        )
    except Exception as e:
        logger.warning("DedupPipeline init failed (using simple dedup cache): %s", e)

    # Qdrant client
    try:
        from src.vectordb.client import QdrantConfig, QdrantClientProvider

        config = QdrantConfig.from_env()
        provider = QdrantClientProvider(config)
        await provider.ensure_client()
        _state["qdrant_provider"] = provider

        from src.vectordb.collections import QdrantCollectionManager
        from src.vectordb.search import QdrantSearchEngine
        from src.vectordb.store import QdrantStoreOperations

        cm = QdrantCollectionManager(provider)
        _state["qdrant_collections"] = cm
        _state["qdrant_search"] = QdrantSearchEngine(provider, cm)
        _state["qdrant_store"] = QdrantStoreOperations(provider, cm)
        logger.info("Qdrant initialized: %s", settings.qdrant.url)
    except Exception as e:
        logger.warning("Qdrant init failed: %s", e)

    # Neo4j client
    if settings.neo4j.enabled:
        try:
            from src.graph.client import Neo4jClient

            neo4j = Neo4jClient(
                uri=settings.neo4j.uri,
                user=settings.neo4j.user,
                password=settings.neo4j.password,
                database=settings.neo4j.database,
            )
            await neo4j.connect()
            _state["neo4j"] = neo4j

            from src.graph.repository import Neo4jGraphRepository

            _state["graph_repo"] = Neo4jGraphRepository(neo4j)

            from src.search.graph_expander import GraphSearchExpander
            _state["graph_expander"] = GraphSearchExpander(graph_repo=_state["graph_repo"])

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

                _state["graph_integrity"] = GraphIntegrityChecker(
                    neo4j_client=neo4j,
                    graph_repository=_state["graph_repo"],
                )
                _state["multi_hop_searcher"] = MultiHopSearcher(
                    neo4j_client=neo4j,
                    graph_repository=_state["graph_repo"],
                )
                logger.info("Graph integrity checker and multi-hop searcher initialized")
            except Exception as _graph_err:
                logger.warning("Graph advanced services init failed: %s", _graph_err)

            logger.info("Neo4j initialized: %s", settings.neo4j.uri)
        except Exception as e:
            logger.warning("Neo4j init failed: %s", e)

    # Embedding provider: prefer TEI (fastest) > Ollama (GPU) > ONNX (CPU)
    embedder = None

    # 1st: HuggingFace TEI (dedicated embedding server, fastest)
    try:
        from src.embedding.tei_provider import TEIEmbeddingProvider

        tei_url = os.getenv("BGE_TEI_URL", "http://localhost:8080")
        tei_embedder = TEIEmbeddingProvider(base_url=tei_url)
        if tei_embedder.is_ready():
            embedder = tei_embedder
            logger.info("TEI embedding initialized (dedicated server): %s", tei_url)
    except Exception as e:
        logger.debug("TEI embedding not available: %s", e)

    # 2nd: Ollama (Metal GPU on Apple Silicon)
    if embedder is None:
        try:
            from src.embedding.ollama_provider import OllamaEmbeddingProvider

            ollama_embedder = OllamaEmbeddingProvider(
                base_url=settings.ollama.base_url,
                model=settings.ollama.embedding_model,
            )
            if ollama_embedder.is_ready():
                embedder = ollama_embedder
                logger.info("Ollama embedding initialized (Metal GPU): %s", settings.ollama.embedding_model)
        except Exception as e:
            logger.debug("Ollama embedding not available: %s", e)

    if embedder is None:
        try:
            from src.embedding.onnx_provider import OnnxBgeEmbeddingProvider

            model_path = settings.embedding.onnx_model_path or os.getenv(
                "KNOWLEDGE_BGE_ONNX_MODEL_PATH", ""
            )
            onnx_embedder = OnnxBgeEmbeddingProvider(model_path=model_path)
            if onnx_embedder.is_ready():
                embedder = onnx_embedder
                logger.info("BGE-M3 ONNX embedding initialized (CPU)")
            else:
                logger.warning("BGE-M3 ONNX model not ready (check model path)")
        except Exception as e:
            logger.warning("ONNX embedding init failed: %s", e)

    if embedder:
        _state["embedder"] = embedder

        # Wire embedder into MultiLayerCache for L2 semantic matching
        multi_cache = _state.get("multi_layer_cache")
        if multi_cache is not None:
            multi_cache._embedding_provider = embedder
            if multi_cache._l2 is not None and hasattr(multi_cache._l2, "_embedding_provider"):
                multi_cache._l2._embedding_provider = embedder
            logger.info("MultiLayerCache embedder wired")
    else:
        logger.error("No embedding provider available. Search will not work.")

    # LLM client
    try:
        from src.llm.ollama_client import OllamaClient, OllamaConfig

        llm_config = OllamaConfig(
            base_url=settings.ollama.base_url,
            model=settings.ollama.model,
            context_length=settings.ollama.context_length,
        )
        llm = OllamaClient(config=llm_config)
        _state["llm"] = llm
        logger.info("Ollama LLM initialized: %s (%s)", settings.ollama.base_url, settings.ollama.model)
    except Exception as e:
        logger.warning("LLM init failed: %s", e)

    # GraphRAG extractor
    if _state.get("llm") and _state.get("neo4j"):
        try:
            from src.pipeline.graphrag_extractor import GraphRAGExtractor
            _state["graphrag_extractor"] = GraphRAGExtractor()
            logger.info("GraphRAGExtractor initialized")
        except Exception as e:
            logger.warning("GraphRAGExtractor init failed: %s", e)

    # QueryPreprocessor
    try:
        from src.search.query_preprocessor import QueryPreprocessor

        _state["query_preprocessor"] = QueryPreprocessor()
        logger.info("QueryPreprocessor initialized")
    except Exception as e:
        logger.warning("QueryPreprocessor init failed: %s", e)

    # CompositeReranker
    try:
        from src.search.composite_reranker import CompositeReranker

        _state["composite_reranker"] = CompositeReranker()
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

        _state["query_classifier"] = QueryClassifier()
        logger.info("QueryClassifier initialized")
    except Exception as e:
        logger.warning("QueryClassifier init failed: %s", e)

    # TieredResponseGenerator
    if _state.get("llm"):
        try:
            from src.search.tiered_response import TieredResponseGenerator

            _state["tiered_response_generator"] = TieredResponseGenerator(
                llm_client=_state["llm"],
            )
            logger.info("TieredResponseGenerator initialized")
        except Exception as e:
            logger.warning("TieredResponseGenerator init failed: %s", e)

    # AnswerService (singleton - avoid per-request lazy init race)
    if _state.get("llm"):
        try:
            from src.search.answer_service import AnswerService

            _state["answer_service"] = AnswerService(llm_client=_state["llm"])
            logger.info("AnswerService initialized")
        except Exception as e:
            logger.warning("AnswerService init failed: %s", e)

    # CRAGRetrievalEvaluator (singleton, avoid per-request construction)
    try:
        from src.search.crag_evaluator import CRAGRetrievalEvaluator

        _state["crag_evaluator"] = CRAGRetrievalEvaluator()
        logger.info("CRAGRetrievalEvaluator initialized")
    except Exception as e:
        logger.warning("CRAGRetrievalEvaluator init failed: %s", e)

    # QueryExpansionService
    try:
        from src.search.query_expansion import QueryExpansionService
        _state["query_expander"] = QueryExpansionService(glossary_repository=_state.get("glossary_repo"))
        logger.info("QueryExpansionService initialized")
    except Exception as e:
        logger.warning("QueryExpansionService init failed: %s", e)

    # RAG pipeline
    from src.search.rag_pipeline import KnowledgeRAGPipeline

    _state["rag_pipeline"] = KnowledgeRAGPipeline(
        search_engine=_state.get("qdrant_search"),
        llm_client=_state.get("llm"),
        graph_client=_state.get("neo4j"),
        embedder=_state.get("embedder"),
        query_preprocessor=_state.get("query_preprocessor"),
        query_expander=_state.get("query_expander"),
    )

    missing = [k for k in ["qdrant_search", "embedder", "llm"] if k not in _state]
    if missing:
        logger.warning("RAG pipeline initialized without: %s", missing)

    # Auth provider + RBAC/ABAC engines
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
        else:
            try:
                api_keys = _json.loads(auth_settings.local_api_keys)
            except Exception:
                api_keys = {}
            provider_kwargs = {"api_keys": api_keys}

        _state["auth_provider"] = create_auth_provider(auth_settings.provider, **provider_kwargs)
        _state["rbac_engine"] = RBACEngine()
        _state["abac_engine"] = ABACEngine(policies=DEFAULT_ABAC_POLICIES)

        # Auth service (DB operations)
        db_url = settings.database.database_url
        auth_service = AuthService(database_url=db_url)
        _state["auth_service"] = auth_service

        # Seed default roles & permissions
        try:
            await auth_service.seed_defaults()
        except Exception as e:
            logger.debug("Auth seed_defaults deferred: %s", e)

        logger.info("Auth initialized: provider=%s, enabled=%s", auth_settings.provider, auth_settings.enabled)
    except Exception as e:
        logger.warning("Auth init failed (running without auth): %s", e)


async def _shutdown_services():
    """Clean up on shutdown with graceful drain of active jobs."""
    import asyncio as _aio

    from src.api.routes.jobs import _jobs

    # Set shutdown flag to prevent new ingestion jobs
    _state["_shutting_down"] = True
    logger.info("Shutting down... draining active tasks")

    # Wait up to 30 seconds for active ingestion jobs to complete
    deadline = 30
    poll_interval = 0.5
    elapsed = 0.0
    while elapsed < deadline:
        active = [j for j in _jobs.values() if j.get("status") == "processing"]
        if not active:
            break
        logger.info("Waiting for %d active job(s) to finish (%.0fs remaining)", len(active), deadline - elapsed)
        await _aio.sleep(poll_interval)
        elapsed += poll_interval

    active_remaining = [j for j in _jobs.values() if j.get("status") == "processing"]
    if active_remaining:
        logger.warning("Shutdown deadline reached with %d job(s) still active", len(active_remaining))

    # Close connections in order: Redis, Qdrant, Neo4j, PostgreSQL, Auth
    # 1. Redis + multi-layer cache
    for key in ("search_cache", "dedup_cache"):
        cache = _state.get(key)
        if cache:
            try:
                await cache.close()
                logger.info("Closed %s", key)
            except Exception:
                pass

    # Close L2 semantic cache Redis connection
    multi_cache = _state.get("multi_layer_cache")
    if multi_cache and hasattr(multi_cache, "_l2") and multi_cache._l2 is not None:
        try:
            if hasattr(multi_cache._l2, "close"):
                await multi_cache._l2.close()
            logger.info("Closed multi_layer_cache L2")
        except Exception:
            pass

    # 2. Qdrant
    if "qdrant_provider" in _state:
        try:
            await _state["qdrant_provider"].close()
            logger.info("Closed Qdrant")
        except Exception:
            pass

    # 3. Neo4j
    if "neo4j" in _state:
        try:
            await _state["neo4j"].close()
            logger.info("Closed Neo4j")
        except Exception:
            pass

    # 4. PostgreSQL (kb_registry has its own engine)
    if "kb_registry" in _state:
        try:
            await _state["kb_registry"].shutdown()
            logger.info("Closed KB registry")
        except Exception:
            pass

    # 5. Auth
    auth_svc = _state.get("auth_service")
    if auth_svc:
        try:
            await auth_svc.close()
            logger.info("Closed Auth service")
        except Exception:
            pass

    logger.info("Shutdown complete")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    await _init_services()
    yield
    await _shutdown_services()


app = FastAPI(
    title="Knowledge Local",
    description="Standalone Knowledge Management System",
    version="0.1.0",
    lifespan=lifespan,
)

cors_origins = os.getenv("CORS_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True if cors_origins != ["*"] else False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Rate limiter middleware (after CORS, conditional on env var)
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
