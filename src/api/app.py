"""axiomedge — FastAPI application.

Standalone knowledge management API server.
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

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

def _default_redis_url() -> str:
    from src.config import get_settings
    return get_settings().redis.url


# ---------------------------------------------------------------------------
# Structured JSON logging — delegated to src.core.logging (PR-7 G)
# ---------------------------------------------------------------------------
from src.core.logging import configure_logging, JsonFormatter as _JsonFormatter  # noqa: E402

configure_logging(service="axiomedge-api")
logger = logging.getLogger(__name__)


# F4 — DEPRECATED alias.
#
# 다음 release (≥ v1.5) 에서 제거 예정. 신규 코드는 항상
# ``from src.core.logging import JsonFormatter`` 를 사용해야 한다. 본 alias
# 는 PR-7 (G) 통합 로깅 마이그레이션 직후 작성된 외부 테스트 호환성용이며,
# 의존하는 위치를 모두 갱신한 시점에 삭제한다.
#
# 제거 plan:
#   1. grep -rn "from src.api.app import JSONFormatter" 로 호출처 수집
#   2. 각 호출처를 ``from src.core.logging import JsonFormatter`` 로 교체
#   3. 본 라인 + DeprecationWarning 삭제
#
# 의존하는 코드가 import 시점에 경고를 인지하도록 module-level
# DeprecationWarning 발행. ``-W error::DeprecationWarning`` 환경에서는 실패
# → 호출처 마이그레이션 강제.
import warnings as _warnings  # noqa: E402


class _DeprecatedJSONFormatter(_JsonFormatter):
    """Deprecated alias — emits a warning when subclassed/instantiated."""

    def __init__(self, *args, **kwargs):
        _warnings.warn(
            "src.api.app.JSONFormatter is deprecated; "
            "use src.core.logging.JsonFormatter instead. "
            "This alias will be removed in v1.5.",
            DeprecationWarning,
            stacklevel=2,
        )
        super().__init__(*args, **kwargs)


JSONFormatter = _DeprecatedJSONFormatter

# Shared state for lazy-initialized singletons
_state = AppState()


def _get_state() -> AppState:
    return _state


async def _init_db_with_retry(settings) -> None:
    """Attempt DB init with up to 3 retries."""
    from src.stores.postgres.init_db import init_database
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


def _create_repositories(state: AppState, session_factory, db_url: str) -> None:
    """Create all repository instances and store in state."""
    from src.stores.postgres.repositories.kb_registry import KBRegistryRepository
    from src.stores.postgres.repositories.glossary import GlossaryRepository
    from src.stores.postgres.repositories.ownership import (
        DocumentOwnerRepository,
        TopicOwnerRepository,
        ErrorReportRepository,
    )
    from src.stores.postgres.repositories.feedback import FeedbackRepository
    from src.stores.postgres.repositories.ingestion_run import IngestionRunRepository
    from src.stores.postgres.repositories.ingestion_failures import IngestionFailureRepository
    from src.stores.postgres.repositories.audit_log import AuditLogRepository
    from src.stores.postgres.repositories.feature_flags import FeatureFlagRepository
    from src.stores.postgres.repositories.trust_score import TrustScoreRepository
    from src.stores.postgres.repositories.lifecycle import DocumentLifecycleRepository
    from src.stores.postgres.repositories.bulk_upload import BulkUploadRepository
    from src.stores.postgres.repositories.data_source import DataSourceRepository
    from src.stores.postgres.repositories.traceability import ProvenanceRepository
    from src.stores.postgres.repositories.category import CategoryRepository
    from src.stores.postgres.repositories.search_group import SearchGroupRepository
    from src.stores.postgres.repositories.usage_log import UsageLogRepository

    state["glossary_repo"] = GlossaryRepository(session_factory)
    state["doc_owner_repo"] = DocumentOwnerRepository(session_factory)
    state["topic_owner_repo"] = TopicOwnerRepository(session_factory)
    state["error_report_repo"] = ErrorReportRepository(session_factory)
    state["feedback_repo"] = FeedbackRepository(session_factory)
    state["ingestion_run_repo"] = IngestionRunRepository(session_factory)
    state["ingestion_failure_repo"] = IngestionFailureRepository(session_factory)
    state["audit_log_repo"] = AuditLogRepository(session_factory)
    state["feature_flag_repo"] = FeatureFlagRepository(session_factory)
    state["trust_score_repo"] = TrustScoreRepository(session_factory)
    state["lifecycle_repo"] = DocumentLifecycleRepository(session_factory)
    state["data_source_repo"] = DataSourceRepository(session_factory)
    state["bulk_upload_repo"] = BulkUploadRepository(session_factory)
    state["provenance_repo"] = ProvenanceRepository(session_factory)
    state["category_repo"] = CategoryRepository(session_factory)
    state["search_group_repo"] = SearchGroupRepository(session_factory)
    state["usage_log_repo"] = UsageLogRepository(session_factory)
    state["_kb_registry_pending"] = KBRegistryRepository(db_url)

    # Distill plugin repo (sync 등록만, 시드/서비스 초기화는 _init_distill에서)
    try:
        from src.distill.repository import DistillRepository
        state["distill_repo"] = DistillRepository(session_factory)
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError, ImportError) as e:
        logger.warning("Distill repo init skipped: %s", e)


async def _init_database(state: AppState, settings) -> None:
    """Initialize PostgreSQL + all repositories + domain services."""
    from src.stores.postgres.session import create_async_session_factory

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
                from src.pipelines.ingestion import load_l1_categories_from_db
                load_l1_categories_from_db(l1_cats)
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError, ImportError) as e:
        logger.warning("L1 category cache load failed (using defaults): %s", e)

    # Term extractor for ingestion
    try:
        from src.pipelines.term_extractor import TermExtractor
        state["term_extractor"] = TermExtractor(
            glossary_repo=state.get("glossary_repo"),
            embedder=state.get("embedder"),
        )
        logger.info("TermExtractor initialized")
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError, ImportError) as e:
        logger.warning("TermExtractor init failed: %s", e)

    # Trust Score Service
    try:
        from src.search.trust_score_service import TrustScoreService
        state["trust_score_service"] = TrustScoreService(
            trust_score_repo=state.get("trust_score_repo"),
            feedback_repo=state.get("feedback_repo"),
        )
        logger.info("TrustScoreService initialized")
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError, ImportError) as e:
        logger.warning("TrustScoreService init failed: %s", e)

    # Lifecycle State Machine
    try:
        from src.core.lifecycle import LifecycleStateMachine
        state["lifecycle_service"] = LifecycleStateMachine(
            lifecycle_repo=state.get("lifecycle_repo"),
        )
        logger.info("LifecycleStateMachine initialized")
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError, ImportError) as e:
        logger.warning("LifecycleStateMachine init failed: %s", e)

    # Freshness Predictor
    try:
        from src.search.freshness_predictor import FreshnessPredictor
        state["freshness_predictor"] = FreshnessPredictor()
        logger.info("FreshnessPredictor initialized")
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError, ImportError) as e:
        logger.warning("FreshnessPredictor init failed: %s", e)

    logger.info("PostgreSQL database initialized: %s", db_url.split("@")[-1] if "@" in db_url else db_url)


async def _init_cache(state: AppState) -> None:
    """Initialize Redis caches + multi-layer cache."""
    await asyncio.sleep(0)

    # Redis cache (search cache + dedup cache + multi-layer cache)
    try:
        redis_url = _default_redis_url()
        from src.stores.redis.redis_cache import SearchCache
        from src.stores.redis.dedup_cache import DedupCache

        state["search_cache"] = SearchCache(redis_url=redis_url)
        state["dedup_cache"] = DedupCache(redis_url=redis_url)
        logger.info("Redis cache initialized: %s", redis_url)
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError, ImportError) as e:
        logger.warning("Redis cache init failed (search/dedup cache disabled): %s", e)

    # Multi-Layer Cache (L1 memory + L2 Redis semantic)
    try:
        from src.stores.redis.multi_layer_cache import MultiLayerCache
        from src.stores.redis.l1_memory_cache import L1InMemoryCache
        from src.stores.redis.l2_semantic_cache import L2SemanticCache
        from src.stores.redis.idempotency_cache import IdempotencyCache
        from src.config.weights import weights as _cache_weights

        cache_cfg = _cache_weights.cache
        l1 = L1InMemoryCache(
            max_size=cache_cfg.l1_max_entries,
            ttl_seconds=cache_cfg.l1_ttl_seconds,
        )

        l2 = None
        if cache_cfg.enable_semantic_cache:
            _cache_redis_url = _default_redis_url()
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
                _default_redis_url(),
                decode_responses=True,
            )
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError, ImportError) as e:
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
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError, ImportError) as e:
        logger.warning("MultiLayerCache init failed: %s", e)


async def _init_dedup(state: AppState) -> None:
    """Initialize 4-stage dedup pipeline."""
    await asyncio.sleep(0)
    try:
        from src.pipelines.dedup import DedupPipeline, DedupResultTracker, RedisDedupIndex
        from src.pipelines.dedup.bloom_filter import BloomFilter
        from src.pipelines.dedup.conflict_detector import OllamaLLMClient
        from src.config.weights import weights as _w

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
            except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError, ImportError) as e:
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
            _redis_url = _default_redis_url()
            redis_client = aioredis.from_url(_redis_url, decode_responses=True)
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError, ImportError) as e:
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
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError, ImportError) as e:
        logger.warning("DedupPipeline init failed (using simple dedup cache): %s", e)


async def _init_vectordb(state: AppState, settings) -> None:
    """Initialize Qdrant client, collections, search engine, and store."""
    try:
        from src.stores.qdrant.client import QdrantConfig, QdrantClientProvider

        config = QdrantConfig.from_env()
        provider = QdrantClientProvider(config)
        await provider.ensure_client()
        state["qdrant_provider"] = provider

        from src.stores.qdrant.collections import QdrantCollectionManager
        from src.stores.qdrant.search import QdrantSearchEngine
        from src.stores.qdrant.store import QdrantStoreOperations

        cm = QdrantCollectionManager(provider)
        state["qdrant_collections"] = cm
        state["qdrant_search"] = QdrantSearchEngine(provider, cm)
        state["qdrant_store"] = QdrantStoreOperations(provider, cm)
        logger.info("Qdrant initialized: %s", settings.qdrant.url)
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError, ImportError) as e:
        logger.warning("Qdrant init failed: %s", e)


async def _init_graph(state: AppState, settings) -> None:
    """Initialize Neo4j client, graph repo, expander, integrity checker, multi-hop."""
    if not settings.neo4j.enabled:
        return

    try:
        from src.stores.neo4j.client import Neo4jClient

        neo4j = Neo4jClient(
            uri=settings.neo4j.uri,
            user=settings.neo4j.user,
            password=settings.neo4j.password,
            database=settings.neo4j.database,
        )
        await neo4j.connect()
        state["neo4j"] = neo4j

        from src.stores.neo4j.repository import Neo4jGraphRepository

        state["graph_repo"] = Neo4jGraphRepository(neo4j)

        from src.search.graph_expander import GraphSearchExpander
        state["graph_expander"] = GraphSearchExpander(graph_repo=state["graph_repo"])

        # Ensure graph indexes (idempotent)
        try:
            from src.stores.neo4j.indexer import ensure_indexes
            index_result = await ensure_indexes(neo4j)
            logger.info(
                "Graph indexes ensured: %d constraints, %d indexes, %d fulltext",
                index_result.get("constraints_created", 0),
                index_result.get("indexes_created", 0),
                index_result.get("fulltext_indexes_created", 0),
            )
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError, ImportError) as _idx_err:
            logger.warning("Graph index creation failed (non-fatal): %s", _idx_err)

        # Initialize graph integrity checker and multi-hop searcher
        try:
            from src.stores.neo4j.integrity import GraphIntegrityChecker
            from src.stores.neo4j.multi_hop_searcher import MultiHopSearcher

            state["graph_integrity"] = GraphIntegrityChecker(
                neo4j_client=neo4j,
                graph_repository=state["graph_repo"],
            )
            state["multi_hop_searcher"] = MultiHopSearcher(
                neo4j_client=neo4j,
                graph_repository=state["graph_repo"],
            )
            logger.info("Graph integrity checker and multi-hop searcher initialized")
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError, ImportError) as _graph_err:
            logger.warning("Graph advanced services init failed: %s", _graph_err)

        logger.info("Neo4j initialized: %s", settings.neo4j.uri)
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError, ImportError) as e:
        logger.warning("Neo4j init failed: %s", e)


def _try_tei_embedding(_settings) -> Any:
    """Try to initialize TEI embedding provider."""
    use_cloud = os.getenv("USE_CLOUD_EMBEDDING", "true").lower() in ("true", "1", "yes")
    if not use_cloud:
        logger.info("Cloud embedding disabled (USE_CLOUD_EMBEDDING=false), using local")
        return None
    try:
        from src.nlp.embedding.tei_provider import TEIEmbeddingProvider

        from src.config import get_settings as _gs
        tei_url = _gs().tei.embedding_url
        tei_embedder = TEIEmbeddingProvider(base_url=tei_url)
        if tei_embedder.is_ready():
            logger.info("TEI embedding initialized (cloud): %s", tei_url)
            return tei_embedder
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError, ImportError) as e:
        logger.debug("TEI embedding not available: %s", e)
    return None


def _try_ollama_embedding(settings) -> Any:
    """Try to initialize Ollama embedding provider."""
    try:
        from src.nlp.embedding.ollama_provider import OllamaEmbeddingProvider

        ollama_embedder = OllamaEmbeddingProvider(
            base_url=settings.ollama.base_url,
            model=settings.ollama.embedding_model,
        )
        if ollama_embedder.is_ready():
            logger.info("Ollama embedding initialized (Metal GPU): %s", settings.ollama.embedding_model)
            return ollama_embedder
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError, ImportError) as e:
        logger.debug("Ollama embedding not available: %s", e)
    return None


def _try_onnx_embedding(settings) -> Any:
    """Try to initialize ONNX embedding provider."""
    try:
        from src.nlp.embedding.onnx_provider import OnnxBgeEmbeddingProvider

        model_path = settings.embedding.onnx_model_path or os.getenv(
            "KNOWLEDGE_BGE_ONNX_MODEL_PATH", ""
        )
        onnx_embedder = OnnxBgeEmbeddingProvider(model_path=model_path)
        if onnx_embedder.is_ready():
            logger.info("BGE-M3 ONNX embedding initialized (CPU)")
            return onnx_embedder
        logger.warning("BGE-M3 ONNX model not ready (check model path)")
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError, ImportError) as e:
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
    """Initialize LLM client via provider registry + GraphRAG extractor.

    선택 우선순위는 `src/providers/llm.py::_resolve_provider_name`:
      1. `LLM_PROVIDER` env var ("ollama" / "sagemaker" / ...)
      2. 레거시 `USE_SAGEMAKER_LLM=true` → "sagemaker" 로 매핑
      3. 기본값 "ollama"
    """
    await asyncio.sleep(0)
    try:
        from src.core.providers.llm import create_llm_client
        state["llm"] = create_llm_client(settings=settings)
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError, ImportError) as e:
        logger.warning("LLM init failed: %s", e)

    # GraphRAG extractor
    if state.get("llm") and state.get("neo4j"):
        try:
            from src.pipelines.graphrag_extractor import GraphRAGExtractor
            state["graphrag_extractor"] = GraphRAGExtractor()
            logger.info("GraphRAGExtractor initialized")
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError, ImportError) as e:
            logger.warning("GraphRAGExtractor init failed: %s", e)


async def _init_search_services(state: AppState) -> None:
    """Initialize all search services + RAG pipeline.

    Delegates to ``SearchServicesFactory`` for testability.
    """
    await asyncio.sleep(0)
    from src.api.search_services_factory import SearchServicesFactory
    await SearchServicesFactory(state).initialize()


async def _init_auth(state: AppState, settings) -> None:
    """Initialize auth provider via registry + RBAC/ABAC engines.

    Provider 선택 + 초기화 로직은 `src/providers/auth.py` 의 registry 로
    단일화됨. 이전에는 여기 `_init_auth` 와 `src/auth/providers.py` 양쪽에
    if-elif 체인이 중복돼 있었으나, registry 패턴으로 통합.
    """
    try:
        from src.auth.abac import DEFAULT_ABAC_POLICIES, ABACEngine
        from src.auth.rbac import RBACEngine
        from src.auth.service import AuthService
        from src.core.providers.auth import create_auth_provider

        auth_settings = settings.auth

        # Registry-based provider creation — internal 은 jwt_service 를 state 에 저장.
        state["auth_provider"] = create_auth_provider(
            auth_settings.provider, settings, state,
        )
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
            from src.auth.token_store import TokenStore
            state["token_store"] = TokenStore(auth_service._session_factory)

        # Seed default roles & permissions
        try:
            await auth_service.seed_defaults()
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError, ImportError) as e:
            logger.debug("Auth seed_defaults deferred: %s", e)

        logger.info("Auth initialized: provider=%s, enabled=%s", auth_settings.provider, auth_settings.enabled)
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError, ImportError) as e:
        logger.warning("Auth init failed (running without auth): %s", e)


async def _init_services() -> None:
    """Orchestrate all service initialization in dependency order."""
    from src.config import get_settings
    settings = get_settings()

    try:
        await _init_database(_state, settings)
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError, ImportError) as e:
        logger.warning("PostgreSQL init failed (repositories will use stubs): %s", e)

    await _init_cache(_state)
    await _init_dedup(_state)
    await _init_vectordb(_state, settings)
    await _init_graph(_state, settings)
    await _init_embedding(_state, settings)
    await _init_llm(_state, settings)
    await _init_search_services(_state)
    await _init_auth(_state, settings)
    await _init_distill(_state, settings)


async def _init_distill(state: AppState, settings) -> None:
    """Distill 플러그인 초기화: yaml → DB 시드 + 서비스 등록."""
    distill_repo = state.get("distill_repo")
    if not distill_repo:
        return

    try:
        from src.distill.config import load_config, profile_to_dict
        from src.distill.service import DistillService

        distill_config = load_config()

        # distill.yaml → DB 시드 (DB에 없는 프로필만 자동 insert)
        for name, profile in distill_config.profiles.items():
            existing = await distill_repo.get_profile(name)
            if not existing:
                data = {"name": name, **profile_to_dict(profile)}
                try:
                    await distill_repo.create_profile(data)
                    logger.info("Distill profile seeded from yaml: %s", name)
                except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError, ImportError) as e:
                    logger.warning("Distill profile seed failed for %s: %s", name, e)

        state["distill_service"] = DistillService(
            config=distill_config,
            session_factory=state.get("db_session_factory"),
            sagemaker_client=state.get("llm"),
            embedder=state.get("embedder"),
            qdrant_url=settings.qdrant.url,
        )
        logger.info("Distill plugin initialized: %d profiles", len(distill_config.profiles))
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError, ImportError) as e:
        logger.warning("Distill plugin init skipped: %s", e)


async def _close_caches(state: AppState) -> None:
    """Close Redis caches and multi-layer cache L2."""
    for key in ("search_cache", "dedup_cache"):
        cache = state.get(key)
        if cache:
            try:
                await cache.close()
                logger.info("Closed %s", key)
            except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError, ImportError) as e:
                logger.debug("Error closing %s: %s", key, e)

    multi_cache = state.get("multi_layer_cache")
    if multi_cache and hasattr(multi_cache, "_l2") and multi_cache._l2 is not None:
        try:
            if hasattr(multi_cache._l2, "close"):
                await multi_cache._l2.close()
            logger.info("Closed multi_layer_cache L2")
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError, ImportError) as e:
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
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError, ImportError) as e:
            logger.debug("Error closing %s: %s", label, e)

    auth_svc = state.get("auth_service")
    if auth_svc:
        try:
            await auth_svc.close()
            logger.info("Closed Auth service")
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError, ImportError) as e:
            logger.debug("Error closing Auth service: %s", e)


async def _shutdown_services() -> None:
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
    # Sentry init early so any startup errors are captured
    from src.core.observability.sentry import init_sentry
    from src.core.observability.tracing import init_tracing
    init_sentry()
    init_tracing(app)
    await _init_services()
    app.state._app_state = _state  # Expose for request.app.state access (no circular import)

    # P0-W2 — FeatureFlag invalidation listener spawn (Redis pub/sub).
    app.state._ff_listener_task = None
    try:
        from src.core.feature_flags import invalidation_listener
        from src.jobs.queue import get_pool
        redis = await get_pool()
        if redis is not None:
            app.state._ff_listener_task = asyncio.create_task(
                invalidation_listener(redis),
                name="feature_flag_invalidation_listener",
            )
            logger.info("FeatureFlag invalidation listener spawned")
    except (ImportError, RuntimeError, OSError, AttributeError) as e:
        logger.warning(
            "FeatureFlag listener not started (Redis unavailable): %s", e,
        )

    try:
        yield
    finally:
        # P1-W3 — drain audit middleware fire-and-forget tasks.
        bg_audit = getattr(app.state, "_audit_bg_tasks", None)
        if bg_audit:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*bg_audit, return_exceptions=True),
                    timeout=5.0,
                )
                logger.info(
                    "Audit bg tasks drained: %d", len(bg_audit),
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "Audit bg drain timed out — %d tasks still running",
                    len(bg_audit),
                )

        # Cancel FF invalidation listener gracefully
        ff_task = getattr(app.state, "_ff_listener_task", None)
        if ff_task and not ff_task.done():
            ff_task.cancel()
            try:
                await asyncio.wait_for(ff_task, timeout=2.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass

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

# Production-safe defaults: APP_ENV=production tightens rate limiter + CORS
_app_env = os.getenv("APP_ENV", "development").lower()
_is_prod = _app_env == "production"

# Rate limiter middleware — added before CORS. Default ON in production.
if os.getenv("RATE_LIMIT_ENABLED", "true" if _is_prod else "false").lower() == "true":
    from src.api.middleware.rate_limiter import RateLimiterMiddleware
    app.add_middleware(RateLimiterMiddleware)
    logger.info(
        "Rate limiter enabled: %s req / %s sec",
        os.getenv("RATE_LIMIT_REQUESTS", "100"),
        os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60"),
    )

# Register routes — auto-discover all modules in src.api.routes/
from src.api.route_discovery import discover_and_register_routes  # noqa: E402
discover_and_register_routes(app)

# Request-ID middleware (PR-7 G) — must be inner so that trace_id ContextVar
# is set before downstream middleware/handler logging.
from src.api.middleware.request_id import RequestIDMiddleware  # noqa: E402
app.add_middleware(RequestIDMiddleware)

# Audit-log middleware (PR-12 J) — must run AFTER auth middleware so that
# request.state.user 는 이미 세팅되어 actor 식별 가능. Starlette 의
# add_middleware 는 LIFO 순서로 wrapping — 따라서 Auth 보다 먼저 등록해야
# Auth 가 inner (먼저 실행) 가 됨.
from src.api.middleware.audit_log import AuditLogMiddleware  # noqa: E402
app.add_middleware(AuditLogMiddleware)

# Auth middleware (adds user context + activity logging)
from src.auth.middleware import AuthMiddleware  # noqa: E402
app.add_middleware(AuthMiddleware)

# Security headers (defense-in-depth)
from src.api.middleware.security_headers import SecurityHeadersMiddleware  # noqa: E402
app.add_middleware(SecurityHeadersMiddleware)

# API deprecation headers (Sunset/Deprecation per RFC 8594) — registers via
# src.api.middleware.api_version.deprecate(...) calls, no-op when registry empty
from starlette.middleware.base import BaseHTTPMiddleware  # noqa: E402
from src.api.middleware.api_version import add_deprecation_headers  # noqa: E402
app.add_middleware(BaseHTTPMiddleware, dispatch=add_deprecation_headers)

# CORSMiddleware MUST be added LAST (outermost = first to execute).
# In production, CORS_ORIGINS must be explicitly set (no wildcard).
_cors_default = "" if _is_prod else "*"
cors_origins = os.getenv("CORS_ORIGINS", _cors_default).split(",")
cors_origins = [o.strip() for o in cors_origins if o.strip()]
if _is_prod and (not cors_origins or "*" in cors_origins):
    raise RuntimeError(
        "APP_ENV=production requires explicit CORS_ORIGINS (comma-separated, no wildcard). "
        "Example: CORS_ORIGINS=https://app.example.com,https://admin.example.com"
    )
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins or ["*"],
    allow_credentials=True if cors_origins and "*" not in cors_origins else False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID", "X-Requested-With"],
    expose_headers=["X-Request-ID"],
)
