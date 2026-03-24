"""Knowledge Local - FastAPI Application.

Standalone knowledge management API server.
All oreo framework dependencies removed.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()

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

        logger.info("PostgreSQL database initialized: %s", db_url.split("@")[-1] if "@" in db_url else db_url)
    except Exception as e:
        logger.warning("PostgreSQL init failed (repositories will use stubs): %s", e)

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
            logger.info("Neo4j initialized: %s", settings.neo4j.uri)
        except Exception as e:
            logger.warning("Neo4j init failed: %s", e)

    # Embedding provider: prefer Ollama (Metal GPU) > ONNX (CPU)
    embedder = None
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
    else:
        logger.error("No embedding provider available. Search will not work.")

    # LLM client
    try:
        from src.llm.ollama_client import OllamaClient, OllamaConfig

        llm_config = OllamaConfig(
            base_url=settings.ollama.base_url,
            model=settings.ollama.model,
        )
        llm = OllamaClient(config=llm_config)
        _state["llm"] = llm
        logger.info("Ollama LLM initialized: %s (%s)", settings.ollama.base_url, settings.ollama.model)
    except Exception as e:
        logger.warning("LLM init failed: %s", e)

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

    # RAG pipeline
    from src.search.rag_pipeline import KnowledgeRAGPipeline

    _state["rag_pipeline"] = KnowledgeRAGPipeline(
        search_engine=_state.get("qdrant_search"),
        llm_client=_state.get("llm"),
        graph_client=_state.get("neo4j"),
        embedder=_state.get("embedder"),
        query_preprocessor=_state.get("query_preprocessor"),
    )


async def _shutdown_services():
    """Clean up on shutdown."""
    if "kb_registry" in _state:
        await _state["kb_registry"].shutdown()
    if "neo4j" in _state:
        await _state["neo4j"].close()
    if "qdrant_provider" in _state:
        await _state["qdrant_provider"].close()


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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routes
from src.api.routes import (  # noqa: E402
    health, search, ingest, admin, kb,
    glossary, ownership, pipeline, quality,
    feedback, data_sources, search_analytics,
    whitelist, rag,
)

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
app.include_router(whitelist.router)
app.include_router(rag.knowledge_router)
app.include_router(rag.intelligent_router)

from src.api.routes import search_groups  # noqa: E402
app.include_router(search_groups.router)
