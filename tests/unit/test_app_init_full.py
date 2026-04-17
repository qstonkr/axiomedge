"""Comprehensive tests for src/api/app.py — init/shutdown/formatter/middleware."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.api.app import JSONFormatter, _get_state
from src.api.state import AppState


# ---------------------------------------------------------------------------
# JSONFormatter
# ---------------------------------------------------------------------------

class TestJSONFormatter:
    def setup_method(self):
        self.formatter = JSONFormatter()

    def test_format_basic_record(self):
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="hello world", args=None, exc_info=None,
        )
        output = self.formatter.format(record)
        parsed = json.loads(output)
        assert parsed["level"] == "INFO"
        assert parsed["message"] == "hello world"
        assert "timestamp" in parsed
        assert "module" in parsed
        assert "function" in parsed

    def test_format_with_exception(self):
        try:
            raise ValueError("test error")
        except ValueError:
            import sys
            exc_info = sys.exc_info()

        record = logging.LogRecord(
            name="test", level=logging.ERROR, pathname="", lineno=0,
            msg="failed", args=None, exc_info=exc_info,
        )
        output = self.formatter.format(record)
        parsed = json.loads(output)
        assert "exception" in parsed
        assert "ValueError" in parsed["exception"]

    def test_format_ensure_ascii_false(self):
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="한국어 메시지", args=None, exc_info=None,
        )
        output = self.formatter.format(record)
        assert "한국어 메시지" in output
        parsed = json.loads(output)
        assert parsed["message"] == "한국어 메시지"

    def test_format_with_args(self):
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="count: %d", args=(42,), exc_info=None,
        )
        output = self.formatter.format(record)
        parsed = json.loads(output)
        assert parsed["message"] == "count: 42"


# ---------------------------------------------------------------------------
# _get_state
# ---------------------------------------------------------------------------

class TestGetState:
    def test_returns_app_state_instance(self):
        state = _get_state()
        assert isinstance(state, AppState)

    def test_returns_same_instance(self):
        s1 = _get_state()
        s2 = _get_state()
        assert s1 is s2


# ---------------------------------------------------------------------------
# _init_cache (patches at source module level)
# ---------------------------------------------------------------------------

class TestInitCache:
    async def test_init_cache_creates_caches(self):
        from src.api.app import _init_cache

        state = AppState()
        with patch("src.stores.redis.redis_cache.aioredis") as mock_aioredis, \
             patch("src.stores.redis.dedup_cache.aioredis") as mock_aioredis2:
            mock_aioredis.from_url.return_value = MagicMock()
            mock_aioredis2.from_url.return_value = MagicMock()
            await _init_cache(state)
        assert state.search_cache is not None
        assert state.dedup_cache is not None


# ---------------------------------------------------------------------------
# _init_vectordb
# ---------------------------------------------------------------------------

class TestInitVectorDB:
    async def test_init_vectordb_creates_search_engine(self):
        from src.api.app import _init_vectordb

        state = AppState()
        settings = MagicMock()
        settings.qdrant.url = "http://localhost:6333"

        with patch("src.stores.qdrant.client.QdrantConfig") as MockConfig, \
             patch("src.stores.qdrant.client.QdrantClientProvider") as MockProvider, \
             patch("src.stores.qdrant.collections.QdrantCollectionManager") as MockCM, \
             patch("src.stores.qdrant.search.QdrantSearchEngine") as MockSearch, \
             patch("src.stores.qdrant.store.QdrantStoreOperations") as MockStore:
            provider = AsyncMock()
            MockProvider.return_value = provider
            MockConfig.from_env.return_value = MagicMock()
            await _init_vectordb(state, settings)

        assert state.qdrant_provider is not None
        assert state.qdrant_search is not None
        assert state.qdrant_store is not None


# ---------------------------------------------------------------------------
# _init_graph
# ---------------------------------------------------------------------------

class TestInitGraph:
    async def test_init_graph_disabled(self):
        from src.api.app import _init_graph

        state = AppState()
        settings = MagicMock()
        settings.neo4j.enabled = False
        await _init_graph(state, settings)
        assert state.neo4j is None

    async def test_init_graph_creates_client(self):
        """Test _init_graph with Neo4j mocked at the source module level."""
        from src.api.app import _init_graph
        import src.stores.neo4j.client as graph_client_mod

        state = AppState()
        settings = MagicMock()
        settings.neo4j.enabled = True
        settings.neo4j.uri = "bolt://localhost:7687"
        settings.neo4j.user = "neo4j"
        settings.neo4j.password = "pass"
        settings.neo4j.database = "neo4j"

        mock_neo4j_instance = AsyncMock()
        mock_neo4j_instance.connect = AsyncMock()
        MockNeo4jClient = MagicMock(return_value=mock_neo4j_instance)

        original_cls = graph_client_mod.Neo4jClient
        graph_client_mod.Neo4jClient = MockNeo4jClient
        try:
            with patch("src.stores.neo4j.repository.Neo4jGraphRepository"), \
                 patch("src.search.graph_expander.GraphSearchExpander"), \
                 patch("src.stores.neo4j.indexer.ensure_indexes", new_callable=AsyncMock) as mock_idx, \
                 patch("src.stores.neo4j.integrity.GraphIntegrityChecker"), \
                 patch("src.stores.neo4j.multi_hop_searcher.MultiHopSearcher"):
                mock_idx.return_value = {"constraints_created": 0, "indexes_created": 0, "fulltext_indexes_created": 0}
                await _init_graph(state, settings)
        finally:
            graph_client_mod.Neo4jClient = original_cls

        assert state.neo4j is not None
        assert state.graph_repo is not None


# ---------------------------------------------------------------------------
# _init_embedding
# ---------------------------------------------------------------------------

class TestInitEmbedding:
    async def test_init_embedding_tei(self):
        from src.api.app import _init_embedding

        state = AppState()
        settings = MagicMock()

        with patch("src.nlp.embedding.tei_provider.TEIEmbeddingProvider") as MockTEI:
            instance = MagicMock()
            instance.is_ready.return_value = True
            MockTEI.return_value = instance
            await _init_embedding(state, settings)

        assert state.embedder is instance

    async def test_init_embedding_no_provider(self):
        from src.api.app import _init_embedding

        state = AppState()
        settings = MagicMock()
        settings.ollama.base_url = "http://localhost:11434"
        settings.ollama.embedding_model = "bge-m3"
        settings.embedding.onnx_model_path = ""

        with patch("src.nlp.embedding.tei_provider.TEIEmbeddingProvider", side_effect=RuntimeError("no tei")), \
             patch("src.nlp.embedding.ollama_provider.OllamaEmbeddingProvider") as MockOllama, \
             patch("src.nlp.embedding.onnx_provider.OnnxBgeEmbeddingProvider") as MockOnnx:
            MockOllama.return_value.is_ready.return_value = False
            MockOnnx.return_value.is_ready.return_value = False
            await _init_embedding(state, settings)

        assert state.embedder is None


# ---------------------------------------------------------------------------
# _init_llm
# ---------------------------------------------------------------------------

class TestInitLLM:
    async def test_init_llm_ollama(self):
        from src.api.app import _init_llm

        state = AppState()
        settings = MagicMock()
        settings.ollama.base_url = "http://localhost:11434"
        settings.ollama.model = "exaone"
        settings.ollama.context_length = 8192

        with patch.dict("os.environ", {"USE_SAGEMAKER_LLM": "false"}), \
             patch("src.nlp.llm.ollama_client.OllamaClient") as MockClient, \
             patch("src.nlp.llm.ollama_client.OllamaConfig"):
            MockClient.return_value = MagicMock()
            await _init_llm(state, settings)

        assert state.llm is not None

    async def test_init_llm_sagemaker(self):
        from src.api.app import _init_llm

        state = AppState()
        settings = MagicMock()

        with patch.dict("os.environ", {"USE_SAGEMAKER_LLM": "true"}), \
             patch("src.nlp.llm.sagemaker_client.SageMakerLLMClient") as MockSM, \
             patch("src.nlp.llm.sagemaker_client.SageMakerConfig"):
            MockSM.return_value = MagicMock()
            await _init_llm(state, settings)

        assert state.llm is not None


# ---------------------------------------------------------------------------
# _init_search_services
# ---------------------------------------------------------------------------

class TestInitSearchServices:
    async def test_init_search_services_creates_pipeline(self):
        from src.api.app import _init_search_services

        state = AppState()
        state.qdrant_search = MagicMock()
        state.embedder = MagicMock()
        state.llm = MagicMock()
        state.glossary_repo = MagicMock()

        with patch("src.search.query_preprocessor.QueryPreprocessor"), \
             patch("src.search.composite_reranker.CompositeReranker"), \
             patch("src.search.cross_encoder_reranker.warmup"), \
             patch("src.search.query_classifier.QueryClassifier"), \
             patch("src.search.tiered_response.TieredResponseGenerator"), \
             patch("src.search.answer_service.AnswerService"), \
             patch("src.search.crag_evaluator.CRAGRetrievalEvaluator"), \
             patch("src.search.query_expansion.QueryExpansionService"), \
             patch("src.search.rag_pipeline.KnowledgeRAGPipeline"):
            await _init_search_services(state)

        assert state.rag_pipeline is not None


# ---------------------------------------------------------------------------
# _shutdown_services
# ---------------------------------------------------------------------------

class TestShutdownServices:
    async def test_shutdown_closes_connections(self):
        import src.api.app as app_mod
        import src.api.routes.jobs as jobs_mod

        original_state = app_mod._state
        test_state = AppState()
        app_mod._state = test_state

        search_cache = AsyncMock()
        dedup_cache = AsyncMock()
        qdrant = AsyncMock()
        neo4j = AsyncMock()
        kb_reg = AsyncMock()
        auth_svc = AsyncMock()

        test_state.search_cache = search_cache
        test_state.dedup_cache = dedup_cache
        test_state.multi_layer_cache = AsyncMock()
        test_state.idempotency_cache = AsyncMock()
        test_state.qdrant_provider = qdrant
        test_state.neo4j = neo4j
        test_state.kb_registry = kb_reg
        test_state.auth_service = auth_svc

        # _jobs was migrated to Redis; provide a mock dict for the import
        jobs_mod._jobs = {}
        try:
            await app_mod._shutdown_services()
        finally:
            if hasattr(jobs_mod, "_jobs"):
                delattr(jobs_mod, "_jobs")
            app_mod._state = original_state

        search_cache.close.assert_awaited_once()
        dedup_cache.close.assert_awaited_once()
        qdrant.close.assert_awaited_once()
        neo4j.close.assert_awaited_once()
        kb_reg.shutdown.assert_awaited_once()
        auth_svc.close.assert_awaited_once()

    async def test_shutdown_waits_for_active_jobs(self):
        import src.api.app as app_mod
        import src.api.routes.jobs as jobs_mod

        original_state = app_mod._state
        test_state = AppState()
        test_state.multi_layer_cache = AsyncMock()
        test_state.idempotency_cache = AsyncMock()
        app_mod._state = test_state

        jobs_mod._jobs = {"job1": {"status": "completed"}}
        try:
            await app_mod._shutdown_services()
        finally:
            if hasattr(jobs_mod, "_jobs"):
                delattr(jobs_mod, "_jobs")
            app_mod._state = original_state


# ---------------------------------------------------------------------------
# _init_auth
# ---------------------------------------------------------------------------

class TestInitAuth:
    async def test_init_auth_local_provider(self):
        from src.api.app import _init_auth

        state = AppState()
        settings = MagicMock()
        settings.auth.provider = "local"
        settings.auth.local_api_keys = '{"key1": "admin"}'
        settings.auth.enabled = True
        settings.database.database_url = "postgresql+asyncpg://localhost/test"
        settings.database.pool_size = 5
        settings.database.max_overflow = 10

        with patch("src.auth.providers.create_auth_provider") as mock_create, \
             patch("src.auth.rbac.RBACEngine"), \
             patch("src.auth.abac.ABACEngine"), \
             patch("src.auth.service.create_async_engine"), \
             patch("src.auth.service.async_sessionmaker") as mock_maker:
            mock_maker.return_value = MagicMock()
            mock_auth_svc_instance = MagicMock()
            mock_auth_svc_instance.seed_defaults = AsyncMock()

            with patch("src.auth.service.AuthService", return_value=mock_auth_svc_instance):
                await _init_auth(state, settings)

        assert state.auth_provider is not None
        assert state.rbac_engine is not None


# ---------------------------------------------------------------------------
# _init_database (integration-style: test error handling path)
# ---------------------------------------------------------------------------

class TestInitDatabase:
    async def test_init_database_retries_and_raises(self):
        from src.api.app import _init_database

        state = AppState()
        settings = MagicMock()
        settings.database.database_url = "postgresql+asyncpg://localhost/test"

        with patch("src.stores.postgres.init_db.init_database", new_callable=AsyncMock) as mock_init:
            mock_init.side_effect = RuntimeError("connection refused")
            with pytest.raises(Exception, match="connection refused"):
                await _init_database(state, settings)
            assert mock_init.await_count == 3  # 3 retries


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

class TestLifespan:
    async def test_lifespan_context_manager(self):
        from src.api.app import lifespan

        mock_app = MagicMock()

        with patch("src.api.app._init_services", new_callable=AsyncMock) as mock_init, \
             patch("src.api.app._shutdown_services", new_callable=AsyncMock) as mock_shutdown:
            async with lifespan(mock_app):
                mock_init.assert_awaited_once()
            mock_shutdown.assert_awaited_once()
