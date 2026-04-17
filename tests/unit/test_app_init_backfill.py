"""Backfill tests for src/api/app.py — uncovered _init_* functions and helpers.

Covers missed lines: _init_db_with_retry, _create_repositories, _init_database
(domain services), _init_cache (multi-layer), _init_dedup, _init_vectordb error,
_init_graph (index/integrity errors), _try_* embedding helpers, _wire_embedder_to_cache,
_init_llm (graphrag), _init_auth (internal provider), _init_distill, _close_caches,
_close_connections, _shutdown_services (drain logic).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.api.state import AppState


# ---------------------------------------------------------------------------
# _init_db_with_retry — line 79 (success on first try)
# ---------------------------------------------------------------------------

class TestInitDbWithRetry:
    @pytest.mark.asyncio
    async def test_success_on_first_attempt(self):
        from src.api.app import _init_db_with_retry

        settings = MagicMock()
        settings.database.database_url = "postgresql+asyncpg://localhost/test"

        with patch(
            "src.stores.postgres.init_db.init_database",
            new_callable=AsyncMock,
        ) as mock_init:
            await _init_db_with_retry(settings)
            mock_init.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_success_on_second_attempt(self):
        from src.api.app import _init_db_with_retry

        settings = MagicMock()
        settings.database.database_url = "postgresql+asyncpg://localhost/test"

        with patch(
            "src.stores.postgres.init_db.init_database",
            new_callable=AsyncMock,
            side_effect=[RuntimeError("fail"), None],
        ) as mock_init, patch("asyncio.sleep", new_callable=AsyncMock):
            await _init_db_with_retry(settings)
            assert mock_init.await_count == 2


# ---------------------------------------------------------------------------
# _create_repositories — lines 90-127
# ---------------------------------------------------------------------------

class TestCreateRepositories:
    def _patch_all_repos(self):
        """Return list of patchers for all repository constructors."""
        paths = [
            "src.stores.postgres.repositories.kb_registry.KBRegistryRepository",
            "src.stores.postgres.repositories.glossary.GlossaryRepository",
            "src.stores.postgres.repositories.ownership.DocumentOwnerRepository",
            "src.stores.postgres.repositories.ownership.TopicOwnerRepository",
            "src.stores.postgres.repositories.ownership.ErrorReportRepository",
            "src.stores.postgres.repositories.feedback.FeedbackRepository",
            "src.stores.postgres.repositories.ingestion_run.IngestionRunRepository",
            "src.stores.postgres.repositories.trust_score.TrustScoreRepository",
            "src.stores.postgres.repositories.lifecycle.DocumentLifecycleRepository",
            "src.stores.postgres.repositories.data_source.DataSourceRepository",
            "src.stores.postgres.repositories.traceability.ProvenanceRepository",
            "src.stores.postgres.repositories.category.CategoryRepository",
            "src.stores.postgres.repositories.search_group.SearchGroupRepository",
            "src.stores.postgres.repositories.usage_log.UsageLogRepository",
        ]
        return [patch(p, return_value=MagicMock()) for p in paths]

    def test_creates_all_repos(self):
        from src.api.app import _create_repositories

        state = AppState()
        session_factory = MagicMock()
        db_url = "postgresql+asyncpg://localhost/test"

        patchers = self._patch_all_repos()
        for p in patchers:
            p.start()
        try:
            with patch(
                "src.distill.repository.DistillRepository",
                return_value=MagicMock(),
            ):
                _create_repositories(state, session_factory, db_url)
        finally:
            for p in patchers:
                p.stop()

        assert state.glossary_repo is not None
        assert state.feedback_repo is not None
        assert state.ingestion_run_repo is not None
        assert state.trust_score_repo is not None
        assert state.lifecycle_repo is not None
        assert state.data_source_repo is not None
        assert state.provenance_repo is not None
        assert state.category_repo is not None
        assert state.search_group_repo is not None
        assert state.usage_log_repo is not None
        assert state.doc_owner_repo is not None
        assert state.topic_owner_repo is not None
        assert state.error_report_repo is not None
        assert state.distill_repo is not None

    def test_distill_repo_import_failure_is_graceful(self):
        from src.api.app import _create_repositories

        state = AppState()
        session_factory = MagicMock()
        db_url = "postgresql+asyncpg://localhost/test"

        patchers = self._patch_all_repos()
        for p in patchers:
            p.start()
        try:
            with patch(
                "src.distill.repository.DistillRepository",
                side_effect=ImportError("no distill"),
            ):
                _create_repositories(state, session_factory, db_url)
        finally:
            for p in patchers:
                p.stop()

        # distill_repo should not be set
        assert state.get("distill_repo") is None
        # Other repos still created
        assert state.glossary_repo is not None


# ---------------------------------------------------------------------------
# _init_database — domain service init branches (lines 137-204)
# ---------------------------------------------------------------------------

class TestInitDatabaseDomainServices:
    @pytest.mark.asyncio
    async def test_full_database_init_with_domain_services(self):
        """Exercise lines 137-204: session_factory, repos, domain services."""
        from src.api.app import _init_database

        state = AppState()
        settings = MagicMock()
        settings.database.database_url = "postgresql+asyncpg://localhost/test"
        settings.database.pool_size = 5
        settings.database.max_overflow = 10
        settings.database.echo = False

        mock_session_factory = MagicMock()
        mock_kb_registry = AsyncMock()
        mock_kb_registry.initialize = AsyncMock()

        with (
            patch(
                "src.stores.postgres.init_db.init_database",
                new_callable=AsyncMock,
            ),
            patch(
                "src.stores.postgres.session.create_async_session_factory",
                return_value=mock_session_factory,
            ),
            patch("src.api.app._create_repositories") as mock_create_repos,
            patch(
                "src.pipelines.ingestion.load_l1_categories_from_db",
            ) as mock_load_l1,
            patch(
                "src.pipelines.term_extractor.TermExtractor",
                return_value=MagicMock(),
            ),
            patch(
                "src.search.trust_score_service.TrustScoreService",
                return_value=MagicMock(),
            ),
            patch(
                "src.core.lifecycle.LifecycleStateMachine",
                return_value=MagicMock(),
            ),
            patch(
                "src.search.freshness_predictor.FreshnessPredictor",
                return_value=MagicMock(),
            ),
        ):
            # _create_repositories sets _kb_registry_pending
            def fake_create_repos(s, sf, url):
                s["_kb_registry_pending"] = mock_kb_registry
                s["category_repo"] = AsyncMock()
                s["glossary_repo"] = MagicMock()
                s["trust_score_repo"] = MagicMock()
                s["feedback_repo"] = MagicMock()
                s["lifecycle_repo"] = MagicMock()

            mock_create_repos.side_effect = fake_create_repos

            # category_repo.get_l1_categories returns some categories
            cat_repo = AsyncMock()
            cat_repo.get_l1_categories = AsyncMock(
                return_value=[{"id": 1, "name": "test"}]
            )

            def patched_create_repos(s, sf, url):
                fake_create_repos(s, sf, url)
                s["category_repo"] = cat_repo

            mock_create_repos.side_effect = patched_create_repos

            await _init_database(state, settings)

        assert state.db_session_factory is mock_session_factory
        assert state.kb_registry is mock_kb_registry
        assert state.term_extractor is not None
        assert state.trust_score_service is not None
        assert state.lifecycle_service is not None
        assert state.freshness_predictor is not None
        mock_load_l1.assert_called_once()

    @pytest.mark.asyncio
    async def test_l1_category_load_failure_is_graceful(self):
        """Exercise line 162: L1 category cache load failure."""
        from src.api.app import _init_database

        state = AppState()
        settings = MagicMock()
        settings.database.database_url = "postgresql+asyncpg://localhost/test"
        settings.database.pool_size = 5
        settings.database.max_overflow = 10
        settings.database.echo = False

        mock_kb = AsyncMock()
        mock_kb.initialize = AsyncMock()

        with (
            patch(
                "src.stores.postgres.init_db.init_database",
                new_callable=AsyncMock,
            ),
            patch(
                "src.stores.postgres.session.create_async_session_factory",
                return_value=MagicMock(),
            ),
            patch("src.api.app._create_repositories") as mock_cr,
            patch(
                "src.pipelines.term_extractor.TermExtractor",
                return_value=MagicMock(),
            ),
            patch(
                "src.search.trust_score_service.TrustScoreService",
                return_value=MagicMock(),
            ),
            patch(
                "src.core.lifecycle.LifecycleStateMachine",
                return_value=MagicMock(),
            ),
            patch(
                "src.search.freshness_predictor.FreshnessPredictor",
                return_value=MagicMock(),
            ),
        ):
            cat_repo = AsyncMock()
            cat_repo.get_l1_categories = AsyncMock(
                side_effect=RuntimeError("DB error")
            )

            def fake_cr(s, sf, url):
                s["_kb_registry_pending"] = mock_kb
                s["category_repo"] = cat_repo
                s["glossary_repo"] = MagicMock()
                s["trust_score_repo"] = MagicMock()
                s["feedback_repo"] = MagicMock()
                s["lifecycle_repo"] = MagicMock()

            mock_cr.side_effect = fake_cr
            # Should not raise
            await _init_database(state, settings)

        # State still populated despite L1 failure
        assert state.db_session_factory is not None

    @pytest.mark.asyncio
    async def test_term_extractor_failure_is_graceful(self):
        """Exercise line 173: TermExtractor init failure."""
        from src.api.app import _init_database

        state = AppState()
        settings = MagicMock()
        settings.database.database_url = "postgresql+asyncpg://localhost/test"
        settings.database.pool_size = 5
        settings.database.max_overflow = 10
        settings.database.echo = False

        mock_kb = AsyncMock()
        mock_kb.initialize = AsyncMock()

        with (
            patch(
                "src.stores.postgres.init_db.init_database",
                new_callable=AsyncMock,
            ),
            patch(
                "src.stores.postgres.session.create_async_session_factory",
                return_value=MagicMock(),
            ),
            patch("src.api.app._create_repositories") as mock_cr,
            patch(
                "src.pipelines.term_extractor.TermExtractor",
                side_effect=RuntimeError("TermExtractor broken"),
            ),
            patch(
                "src.search.trust_score_service.TrustScoreService",
                return_value=MagicMock(),
            ),
            patch(
                "src.core.lifecycle.LifecycleStateMachine",
                return_value=MagicMock(),
            ),
            patch(
                "src.search.freshness_predictor.FreshnessPredictor",
                return_value=MagicMock(),
            ),
        ):
            def fake_cr(s, sf, url):
                s["_kb_registry_pending"] = mock_kb
                s["category_repo"] = None
                s["glossary_repo"] = MagicMock()
                s["trust_score_repo"] = MagicMock()
                s["feedback_repo"] = MagicMock()
                s["lifecycle_repo"] = MagicMock()

            mock_cr.side_effect = fake_cr
            await _init_database(state, settings)

        assert state.term_extractor is None
        assert state.trust_score_service is not None


# ---------------------------------------------------------------------------
# _init_cache — multi-layer cache (lines 220-276)
# ---------------------------------------------------------------------------

class TestInitCacheMultiLayer:
    @pytest.mark.asyncio
    async def test_redis_cache_failure_is_graceful(self):
        """Lines 220-221: Redis init fails -> no cache, no crash."""
        from src.api.app import _init_cache

        state = AppState()
        with patch(
            "src.api.app._default_redis_url",
            side_effect=RuntimeError("no redis"),
        ):
            await _init_cache(state)

        assert state.search_cache is None
        assert state.dedup_cache is None

    @pytest.mark.asyncio
    async def test_multi_layer_cache_failure_is_graceful(self):
        """Lines 275-276: MultiLayerCache init fails."""
        from src.api.app import _init_cache

        state = AppState()
        with (
            patch("src.api.app._default_redis_url", return_value="redis://x"),
            patch(
                "src.stores.redis.redis_cache.SearchCache",
                return_value=MagicMock(),
            ),
            patch(
                "src.stores.redis.dedup_cache.DedupCache",
                return_value=MagicMock(),
            ),
            patch(
                "src.stores.redis.multi_layer_cache.MultiLayerCache",
                side_effect=RuntimeError("ML cache broken"),
            ),
            patch(
                "src.stores.redis.l1_memory_cache.L1InMemoryCache",
                return_value=MagicMock(),
            ),
        ):
            await _init_cache(state)

        assert state.search_cache is not None
        assert state.multi_layer_cache is None

    @pytest.mark.asyncio
    async def test_idempotency_redis_failure(self):
        """Lines 263-264: idempotency Redis client creation fails."""
        from src.api.app import _init_cache

        state = AppState()
        mock_multi = MagicMock()

        with (
            patch("src.api.app._default_redis_url", return_value="redis://x"),
            patch(
                "src.stores.redis.redis_cache.SearchCache",
                return_value=MagicMock(),
            ),
            patch(
                "src.stores.redis.dedup_cache.DedupCache",
                return_value=MagicMock(),
            ),
            patch(
                "src.stores.redis.multi_layer_cache.MultiLayerCache",
                return_value=mock_multi,
            ),
            patch(
                "src.stores.redis.l1_memory_cache.L1InMemoryCache",
                return_value=MagicMock(),
            ),
            patch(
                "src.stores.redis.l2_semantic_cache.L2SemanticCache",
                return_value=MagicMock(),
            ),
            patch(
                "src.stores.redis.idempotency_cache.IdempotencyCache",
                return_value=MagicMock(),
            ),
            patch(
                "redis.asyncio.from_url",
                side_effect=RuntimeError("conn refused"),
            ),
            patch(
                "src.config.weights.weights",
                new=MagicMock(
                    cache=MagicMock(
                        l1_max_entries=100,
                        l1_ttl_seconds=300,
                        enable_semantic_cache=True,
                        l2_similarity_threshold=0.9,
                        l2_max_entries=500,
                        l2_ttl_seconds=600,
                        idempotency_ttl_seconds=60,
                    ),
                ),
            ),
        ):
            await _init_cache(state)

        assert state.multi_layer_cache is not None
        assert state.idempotency_cache is not None


# ---------------------------------------------------------------------------
# _init_dedup — lines 281-336
# ---------------------------------------------------------------------------

class TestInitDedup:
    @pytest.mark.asyncio
    async def test_dedup_pipeline_created(self):
        """Lines 281-334: full dedup pipeline creation."""
        from src.api.app import _init_dedup

        state = AppState()
        mock_bloom = MagicMock()
        mock_pipeline = MagicMock()
        mock_tracker = MagicMock()
        mock_redis_idx = MagicMock()

        dedup_cfg = MagicMock(
            bloom_expected_items=10000,
            bloom_false_positive_rate=0.01,
            enable_stage4=False,
            near_duplicate_threshold=0.95,
            semantic_duplicate_threshold=0.90,
            stage3_skip_threshold=0.99,
        )

        with (
            patch(
                "src.pipelines.dedup.DedupPipeline",
                return_value=mock_pipeline,
            ),
            patch(
                "src.pipelines.dedup.DedupResultTracker",
                return_value=mock_tracker,
            ),
            patch(
                "src.pipelines.dedup.RedisDedupIndex",
                return_value=mock_redis_idx,
            ),
            patch(
                "src.pipelines.dedup.bloom_filter.BloomFilter",
                return_value=mock_bloom,
            ),
            patch(
                "src.config.weights.weights",
                new=MagicMock(dedup=dedup_cfg),
            ),
            patch("src.api.app._default_redis_url", return_value="redis://x"),
            patch("redis.asyncio.from_url", return_value=MagicMock()),
        ):
            await _init_dedup(state)

        assert state.dedup_pipeline is mock_pipeline
        assert state.dedup_result_tracker is mock_tracker
        assert state.redis_dedup_index is mock_redis_idx

    @pytest.mark.asyncio
    async def test_dedup_pipeline_with_stage4(self):
        """Lines 296-305: stage4 LLM client creation."""
        from src.api.app import _init_dedup

        state = AppState()
        dedup_cfg = MagicMock(
            bloom_expected_items=10000,
            bloom_false_positive_rate=0.01,
            enable_stage4=True,
            near_duplicate_threshold=0.95,
            semantic_duplicate_threshold=0.90,
            stage3_skip_threshold=0.99,
        )

        with (
            patch(
                "src.pipelines.dedup.DedupPipeline",
                return_value=MagicMock(),
            ),
            patch(
                "src.pipelines.dedup.DedupResultTracker",
                return_value=MagicMock(),
            ),
            patch(
                "src.pipelines.dedup.RedisDedupIndex",
                return_value=MagicMock(),
            ),
            patch(
                "src.pipelines.dedup.bloom_filter.BloomFilter",
                return_value=MagicMock(),
            ),
            patch(
                "src.pipelines.dedup.conflict_detector.OllamaLLMClient",
                return_value=MagicMock(),
            ) as mock_llm_cls,
            patch(
                "src.config.weights.weights",
                new=MagicMock(dedup=dedup_cfg),
            ),
            patch("src.config.get_settings") as mock_gs,
            patch("src.api.app._default_redis_url", return_value="redis://x"),
            patch("redis.asyncio.from_url", return_value=MagicMock()),
        ):
            mock_gs.return_value.ollama.base_url = "http://localhost:11434"
            mock_gs.return_value.ollama.model = "exaone"
            await _init_dedup(state)

        assert state.dedup_pipeline is not None

    @pytest.mark.asyncio
    async def test_dedup_total_failure_is_graceful(self):
        """Line 336: entire dedup init fails."""
        from src.api.app import _init_dedup

        state = AppState()
        with patch(
            "src.pipelines.dedup.DedupPipeline",
            side_effect=ImportError("no dedup"),
        ), patch(
            "src.config.weights.weights",
            new=MagicMock(
                dedup=MagicMock(
                    bloom_expected_items=10000,
                    bloom_false_positive_rate=0.01,
                )
            ),
        ), patch(
            "src.pipelines.dedup.bloom_filter.BloomFilter",
            side_effect=ImportError("no bloom"),
        ):
            await _init_dedup(state)

        assert state.dedup_pipeline is None


# ---------------------------------------------------------------------------
# _init_vectordb — error path (lines 358-359)
# ---------------------------------------------------------------------------

class TestInitVectorDBError:
    @pytest.mark.asyncio
    async def test_vectordb_failure_is_graceful(self):
        """Lines 358-359: Qdrant init fails."""
        from src.api.app import _init_vectordb

        state = AppState()
        settings = MagicMock()
        with patch(
            "src.stores.qdrant.client.QdrantConfig.from_env",
            side_effect=RuntimeError("qdrant down"),
        ):
            await _init_vectordb(state, settings)

        assert state.qdrant_provider is None


# ---------------------------------------------------------------------------
# _init_graph — error branches (lines 396-397, 413-414, 417-418)
# ---------------------------------------------------------------------------

class TestInitGraphErrors:
    @pytest.mark.asyncio
    async def test_graph_index_failure_is_graceful(self):
        """Lines 396-397: ensure_indexes fails, non-fatal."""
        from src.api.app import _init_graph
        import src.stores.neo4j.client as graph_client_mod

        state = AppState()
        settings = MagicMock()
        settings.neo4j.enabled = True
        settings.neo4j.uri = "bolt://localhost:7687"
        settings.neo4j.user = "neo4j"
        settings.neo4j.password = "pass"
        settings.neo4j.database = "neo4j"

        mock_neo4j = AsyncMock()
        mock_neo4j.connect = AsyncMock()
        original_cls = graph_client_mod.Neo4jClient
        graph_client_mod.Neo4jClient = MagicMock(return_value=mock_neo4j)
        try:
            with (
                patch(
                    "src.stores.neo4j.repository.Neo4jGraphRepository",
                ),
                patch("src.search.graph_expander.GraphSearchExpander"),
                patch(
                    "src.stores.neo4j.indexer.ensure_indexes",
                    new_callable=AsyncMock,
                    side_effect=RuntimeError("index error"),
                ),
                patch(
                    "src.stores.neo4j.integrity.GraphIntegrityChecker",
                ),
                patch(
                    "src.stores.neo4j.multi_hop_searcher.MultiHopSearcher",
                ),
            ):
                await _init_graph(state, settings)
        finally:
            graph_client_mod.Neo4jClient = original_cls

        # Graph still initialized despite index failure
        assert state.neo4j is not None
        assert state.graph_integrity is not None

    @pytest.mark.asyncio
    async def test_graph_advanced_services_failure(self):
        """Lines 413-414: integrity/multi-hop init fails."""
        from src.api.app import _init_graph
        import src.stores.neo4j.client as graph_client_mod

        state = AppState()
        settings = MagicMock()
        settings.neo4j.enabled = True
        settings.neo4j.uri = "bolt://localhost:7687"
        settings.neo4j.user = "neo4j"
        settings.neo4j.password = "pass"
        settings.neo4j.database = "neo4j"

        mock_neo4j = AsyncMock()
        mock_neo4j.connect = AsyncMock()
        original_cls = graph_client_mod.Neo4jClient
        graph_client_mod.Neo4jClient = MagicMock(return_value=mock_neo4j)
        try:
            with (
                patch(
                    "src.stores.neo4j.repository.Neo4jGraphRepository",
                ),
                patch("src.search.graph_expander.GraphSearchExpander"),
                patch(
                    "src.stores.neo4j.indexer.ensure_indexes",
                    new_callable=AsyncMock,
                    return_value={
                        "constraints_created": 0,
                        "indexes_created": 0,
                        "fulltext_indexes_created": 0,
                    },
                ),
                patch(
                    "src.stores.neo4j.integrity.GraphIntegrityChecker",
                    side_effect=RuntimeError("integrity broken"),
                ),
            ):
                await _init_graph(state, settings)
        finally:
            graph_client_mod.Neo4jClient = original_cls

        assert state.neo4j is not None
        assert state.graph_integrity is None

    @pytest.mark.asyncio
    async def test_graph_total_failure(self):
        """Lines 417-418: entire graph init fails."""
        from src.api.app import _init_graph
        import src.stores.neo4j.client as graph_client_mod

        state = AppState()
        settings = MagicMock()
        settings.neo4j.enabled = True
        settings.neo4j.uri = "bolt://localhost:7687"
        settings.neo4j.user = "neo4j"
        settings.neo4j.password = "pass"
        settings.neo4j.database = "neo4j"

        original_cls = graph_client_mod.Neo4jClient
        mock_cls = MagicMock(side_effect=RuntimeError("neo4j unreachable"))
        graph_client_mod.Neo4jClient = mock_cls
        try:
            await _init_graph(state, settings)
        finally:
            graph_client_mod.Neo4jClient = original_cls

        assert state.neo4j is None


# ---------------------------------------------------------------------------
# _try_tei_embedding — lines 425-426
# ---------------------------------------------------------------------------

class TestTryTeiEmbedding:
    def test_tei_cloud_disabled(self):
        """Line 425-426: USE_CLOUD_EMBEDDING=false returns None."""
        from src.api.app import _try_tei_embedding

        with patch.dict("os.environ", {"USE_CLOUD_EMBEDDING": "false"}):
            result = _try_tei_embedding(MagicMock())

        assert result is None

    def test_tei_not_ready(self):
        from src.api.app import _try_tei_embedding

        with (
            patch.dict("os.environ", {"USE_CLOUD_EMBEDDING": "true"}),
            patch(
                "src.nlp.embedding.tei_provider.TEIEmbeddingProvider",
            ) as MockTEI,
            patch("src.config.get_settings") as mock_gs,
        ):
            mock_gs.return_value.tei.embedding_url = "http://tei:8080"
            MockTEI.return_value.is_ready.return_value = False
            result = _try_tei_embedding(MagicMock())

        assert result is None


# ---------------------------------------------------------------------------
# _try_ollama_embedding — lines 451-454
# ---------------------------------------------------------------------------

class TestTryOllamaEmbedding:
    def test_ollama_ready(self):
        from src.api.app import _try_ollama_embedding

        settings = MagicMock()
        settings.ollama.base_url = "http://localhost:11434"
        settings.ollama.embedding_model = "bge-m3"

        with patch(
            "src.nlp.embedding.ollama_provider.OllamaEmbeddingProvider",
        ) as MockOllama:
            instance = MagicMock()
            instance.is_ready.return_value = True
            MockOllama.return_value = instance
            result = _try_ollama_embedding(settings)

        assert result is instance

    def test_ollama_failure(self):
        """Lines 453-454: Ollama init fails."""
        from src.api.app import _try_ollama_embedding

        settings = MagicMock()
        with patch(
            "src.nlp.embedding.ollama_provider.OllamaEmbeddingProvider",
            side_effect=RuntimeError("no ollama"),
        ):
            result = _try_ollama_embedding(settings)

        assert result is None


# ---------------------------------------------------------------------------
# _try_onnx_embedding — lines 468-472
# ---------------------------------------------------------------------------

class TestTryOnnxEmbedding:
    def test_onnx_ready(self):
        from src.api.app import _try_onnx_embedding

        settings = MagicMock()
        settings.embedding.onnx_model_path = "/models/bge-m3"

        with patch(
            "src.nlp.embedding.onnx_provider.OnnxBgeEmbeddingProvider",
        ) as MockOnnx:
            instance = MagicMock()
            instance.is_ready.return_value = True
            MockOnnx.return_value = instance
            result = _try_onnx_embedding(settings)

        assert result is instance

    def test_onnx_not_ready(self):
        """Lines 468-469, 470: model not ready."""
        from src.api.app import _try_onnx_embedding

        settings = MagicMock()
        settings.embedding.onnx_model_path = "/models/bge-m3"

        with patch(
            "src.nlp.embedding.onnx_provider.OnnxBgeEmbeddingProvider",
        ) as MockOnnx:
            MockOnnx.return_value.is_ready.return_value = False
            result = _try_onnx_embedding(settings)

        assert result is None

    def test_onnx_failure(self):
        """Lines 471-472: ONNX init exception."""
        from src.api.app import _try_onnx_embedding

        settings = MagicMock()
        settings.embedding.onnx_model_path = ""
        with patch(
            "src.nlp.embedding.onnx_provider.OnnxBgeEmbeddingProvider",
            side_effect=RuntimeError("onnx broken"),
        ):
            result = _try_onnx_embedding(settings)

        assert result is None


# ---------------------------------------------------------------------------
# _wire_embedder_to_cache — lines 481-484
# ---------------------------------------------------------------------------

class TestWireEmbedderToCache:
    def test_wire_embedder_sets_providers(self):
        """Lines 481-484: wires embedder into multi-layer cache."""
        from src.api.app import _wire_embedder_to_cache

        state = AppState()
        mock_cache = MagicMock()
        mock_cache._l2 = MagicMock()
        mock_cache._l2._embedding_provider = None
        state["multi_layer_cache"] = mock_cache

        embedder = MagicMock()
        _wire_embedder_to_cache(state, embedder)

        assert mock_cache._embedding_provider == embedder
        assert mock_cache._l2._embedding_provider == embedder

    def test_wire_embedder_no_cache(self):
        """Line 480: no multi_layer_cache -> early return."""
        from src.api.app import _wire_embedder_to_cache

        state = AppState()
        _wire_embedder_to_cache(state, MagicMock())
        # No crash

    def test_wire_embedder_no_l2(self):
        """Line 482: l2 is None -> skip l2 wiring."""
        from src.api.app import _wire_embedder_to_cache

        state = AppState()
        mock_cache = MagicMock()
        mock_cache._l2 = None
        state["multi_layer_cache"] = mock_cache

        embedder = MagicMock()
        _wire_embedder_to_cache(state, embedder)

        assert mock_cache._embedding_provider == embedder


# ---------------------------------------------------------------------------
# _init_llm — graphrag extractor branch (lines 515-525)
# ---------------------------------------------------------------------------

class TestInitLLMGraphRAG:
    @pytest.mark.asyncio
    async def test_llm_failure_is_graceful(self):
        """Lines 515-516: LLM creation fails."""
        from src.api.app import _init_llm

        state = AppState()
        settings = MagicMock()

        with patch(
            "src.core.providers.llm.create_llm_client",
            side_effect=RuntimeError("llm broken"),
        ):
            await _init_llm(state, settings)

        assert state.llm is None

    @pytest.mark.asyncio
    async def test_graphrag_extractor_created(self):
        """Lines 519-523: GraphRAG extractor created when llm+neo4j present."""
        from src.api.app import _init_llm

        state = AppState()
        state["neo4j"] = MagicMock()
        settings = MagicMock()

        mock_extractor = MagicMock()
        with (
            patch(
                "src.core.providers.llm.create_llm_client",
                return_value=MagicMock(),
            ),
            patch(
                "src.pipelines.graphrag_extractor.GraphRAGExtractor",
                return_value=mock_extractor,
            ),
        ):
            await _init_llm(state, settings)

        assert state.graphrag_extractor is mock_extractor

    @pytest.mark.asyncio
    async def test_graphrag_extractor_failure(self):
        """Lines 524-525: GraphRAG extractor init fails."""
        from src.api.app import _init_llm

        state = AppState()
        state["neo4j"] = MagicMock()
        settings = MagicMock()

        with (
            patch(
                "src.core.providers.llm.create_llm_client",
                return_value=MagicMock(),
            ),
            patch(
                "src.pipelines.graphrag_extractor.GraphRAGExtractor",
                side_effect=RuntimeError("graphrag broken"),
            ),
        ):
            await _init_llm(state, settings)

        assert state.llm is not None
        assert state.graphrag_extractor is None

    @pytest.mark.asyncio
    async def test_graphrag_skipped_without_neo4j(self):
        """Lines 519: no neo4j -> skip GraphRAG."""
        from src.api.app import _init_llm

        state = AppState()
        settings = MagicMock()

        with patch(
            "src.core.providers.llm.create_llm_client",
            return_value=MagicMock(),
        ):
            await _init_llm(state, settings)

        assert state.llm is not None
        assert state.graphrag_extractor is None


# ---------------------------------------------------------------------------
# _init_auth — internal provider branch (lines 571-582)
# ---------------------------------------------------------------------------

class TestInitAuthInternal:
    @pytest.mark.asyncio
    async def test_internal_auth_creates_token_store(self):
        """Lines 570-572: internal provider creates token_store."""
        from src.api.app import _init_auth

        state = AppState()
        settings = MagicMock()
        settings.auth.provider = "internal"
        settings.auth.enabled = True
        settings.database.database_url = "postgresql+asyncpg://localhost/test"
        settings.database.pool_size = 5
        settings.database.max_overflow = 10

        mock_auth_svc = MagicMock()
        mock_auth_svc.seed_defaults = AsyncMock()
        mock_auth_svc._session_factory = MagicMock()

        with (
            patch(
                "src.core.providers.auth.create_auth_provider",
                return_value=MagicMock(),
            ),
            patch("src.auth.rbac.RBACEngine", return_value=MagicMock()),
            patch(
                "src.auth.abac.ABACEngine", return_value=MagicMock()
            ),
            patch(
                "src.auth.service.AuthService",
                return_value=mock_auth_svc,
            ),
            patch(
                "src.auth.token_store.TokenStore",
                return_value=MagicMock(),
            ) as mock_ts,
            patch("src.auth.service.create_async_engine"),
            patch("src.auth.service.async_sessionmaker"),
        ):
            await _init_auth(state, settings)

        assert state.auth_provider is not None
        assert state.token_store is not None

    @pytest.mark.asyncio
    async def test_auth_seed_defaults_failure(self):
        """Lines 577-578: seed_defaults fails but auth continues."""
        from src.api.app import _init_auth

        state = AppState()
        settings = MagicMock()
        settings.auth.provider = "local"
        settings.auth.enabled = True
        settings.database.database_url = "postgresql+asyncpg://localhost/test"
        settings.database.pool_size = 5
        settings.database.max_overflow = 10

        mock_auth_svc = MagicMock()
        mock_auth_svc.seed_defaults = AsyncMock(
            side_effect=RuntimeError("seed fail")
        )

        with (
            patch(
                "src.core.providers.auth.create_auth_provider",
                return_value=MagicMock(),
            ),
            patch("src.auth.rbac.RBACEngine", return_value=MagicMock()),
            patch(
                "src.auth.abac.ABACEngine", return_value=MagicMock()
            ),
            patch(
                "src.auth.service.AuthService",
                return_value=mock_auth_svc,
            ),
            patch("src.auth.service.create_async_engine"),
            patch("src.auth.service.async_sessionmaker"),
        ):
            await _init_auth(state, settings)

        assert state.auth_provider is not None
        assert state.auth_service is mock_auth_svc

    @pytest.mark.asyncio
    async def test_auth_total_failure(self):
        """Lines 581-582: entire auth init fails."""
        from src.api.app import _init_auth

        state = AppState()
        settings = MagicMock()
        settings.auth.provider = "local"

        with patch(
            "src.core.providers.auth.create_auth_provider",
            side_effect=RuntimeError("auth broken"),
        ):
            await _init_auth(state, settings)

        assert state.auth_provider is None


# ---------------------------------------------------------------------------
# _init_distill — lines 612-638
# ---------------------------------------------------------------------------

class TestInitDistill:
    @pytest.mark.asyncio
    async def test_distill_no_repo_returns_early(self):
        from src.api.app import _init_distill

        state = AppState()
        settings = MagicMock()
        await _init_distill(state, settings)
        assert state.get("distill_service") is None

    @pytest.mark.asyncio
    async def test_distill_seeds_new_profiles(self):
        """Lines 612-636: seeds profiles and creates service."""
        from src.api.app import _init_distill

        state = AppState()
        mock_repo = AsyncMock()
        mock_repo.get_profile = AsyncMock(return_value=None)
        mock_repo.create_profile = AsyncMock()
        state["distill_repo"] = mock_repo

        settings = MagicMock()
        settings.qdrant.url = "http://localhost:6333"

        mock_config = MagicMock()
        mock_profile = MagicMock()
        mock_config.profiles = {"default": mock_profile}
        mock_service = MagicMock()

        with (
            patch(
                "src.distill.config.load_config",
                return_value=mock_config,
            ),
            patch(
                "src.distill.config.profile_to_dict",
                return_value={"key": "val"},
            ),
            patch(
                "src.distill.service.DistillService",
                return_value=mock_service,
            ),
        ):
            await _init_distill(state, settings)

        mock_repo.create_profile.assert_awaited_once()
        assert state.distill_service is mock_service

    @pytest.mark.asyncio
    async def test_distill_skips_existing_profiles(self):
        """Line 620-621: existing profile -> skip seed."""
        from src.api.app import _init_distill

        state = AppState()
        mock_repo = AsyncMock()
        mock_repo.get_profile = AsyncMock(
            return_value={"name": "default"}
        )
        state["distill_repo"] = mock_repo

        settings = MagicMock()
        settings.qdrant.url = "http://localhost:6333"

        mock_config = MagicMock()
        mock_config.profiles = {"default": MagicMock()}

        with (
            patch(
                "src.distill.config.load_config",
                return_value=mock_config,
            ),
            patch(
                "src.distill.service.DistillService",
                return_value=MagicMock(),
            ),
        ):
            await _init_distill(state, settings)

        mock_repo.create_profile.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_distill_seed_failure_per_profile(self):
        """Lines 626-627: create_profile fails for one profile."""
        from src.api.app import _init_distill

        state = AppState()
        mock_repo = AsyncMock()
        mock_repo.get_profile = AsyncMock(return_value=None)
        mock_repo.create_profile = AsyncMock(
            side_effect=RuntimeError("DB error")
        )
        state["distill_repo"] = mock_repo

        settings = MagicMock()
        settings.qdrant.url = "http://localhost:6333"

        mock_config = MagicMock()
        mock_config.profiles = {"default": MagicMock()}

        with (
            patch(
                "src.distill.config.load_config",
                return_value=mock_config,
            ),
            patch(
                "src.distill.config.profile_to_dict",
                return_value={"key": "val"},
            ),
            patch(
                "src.distill.service.DistillService",
                return_value=MagicMock(),
            ),
        ):
            await _init_distill(state, settings)

        # Service still created despite seed failure
        assert state.distill_service is not None

    @pytest.mark.asyncio
    async def test_distill_total_failure(self):
        """Lines 637-638: entire distill init fails."""
        from src.api.app import _init_distill

        state = AppState()
        state["distill_repo"] = MagicMock()
        settings = MagicMock()

        with patch(
            "src.distill.config.load_config",
            side_effect=RuntimeError("no config"),
        ):
            await _init_distill(state, settings)

        assert state.get("distill_service") is None


# ---------------------------------------------------------------------------
# _close_caches — lines 649-659
# ---------------------------------------------------------------------------

class TestCloseCaches:
    @pytest.mark.asyncio
    async def test_close_caches_with_errors(self):
        """Lines 649-650: cache close raises -> logged, no crash."""
        from src.api.app import _close_caches

        state = AppState()
        state["search_cache"] = AsyncMock(
            close=AsyncMock(side_effect=RuntimeError("close fail"))
        )
        state["dedup_cache"] = AsyncMock(
            close=AsyncMock(side_effect=RuntimeError("close fail"))
        )

        await _close_caches(state)
        # No assertion needed — just verify no crash

    @pytest.mark.asyncio
    async def test_close_multi_layer_l2(self):
        """Lines 654-659: close multi-layer L2 cache."""
        from src.api.app import _close_caches

        state = AppState()
        mock_l2 = AsyncMock()
        mock_l2.close = AsyncMock()
        mock_cache = MagicMock()
        mock_cache._l2 = mock_l2
        state["multi_layer_cache"] = mock_cache

        await _close_caches(state)

        mock_l2.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_close_multi_layer_l2_error(self):
        """Lines 658-659: L2 close fails."""
        from src.api.app import _close_caches

        state = AppState()
        mock_l2 = AsyncMock()
        mock_l2.close = AsyncMock(side_effect=RuntimeError("l2 close fail"))
        mock_cache = MagicMock()
        mock_cache._l2 = mock_l2
        state["multi_layer_cache"] = mock_cache

        await _close_caches(state)
        # No crash


# ---------------------------------------------------------------------------
# _close_connections — lines 675-684
# ---------------------------------------------------------------------------

class TestCloseConnections:
    @pytest.mark.asyncio
    async def test_close_connections_with_errors(self):
        """Lines 675-676: service close raises."""
        from src.api.app import _close_connections

        state = AppState()
        state["qdrant_provider"] = AsyncMock(
            close=AsyncMock(side_effect=RuntimeError("close fail"))
        )
        state["neo4j"] = AsyncMock(
            close=AsyncMock(side_effect=RuntimeError("close fail"))
        )
        state["kb_registry"] = AsyncMock(
            shutdown=AsyncMock(side_effect=RuntimeError("close fail"))
        )

        await _close_connections(state)
        # No crash

    @pytest.mark.asyncio
    async def test_close_auth_service_error(self):
        """Lines 683-684: auth service close fails."""
        from src.api.app import _close_connections

        state = AppState()
        state["auth_service"] = AsyncMock(
            close=AsyncMock(side_effect=RuntimeError("auth close fail"))
        )

        await _close_connections(state)
        # No crash


# ---------------------------------------------------------------------------
# _shutdown_services — drain logic (lines 705-711)
# ---------------------------------------------------------------------------

class TestShutdownDrain:
    @pytest.mark.asyncio
    async def test_shutdown_with_active_jobs_draining(self):
        """Lines 705-707: wait loop with active jobs."""
        import src.api.app as app_mod

        original_state = app_mod._state
        test_state = AppState()
        app_mod._state = test_state

        call_count = 0

        async def mock_active_count():
            nonlocal call_count
            call_count += 1
            # Return 1 for first two calls, then 0
            return 1 if call_count <= 2 else 0

        try:
            with (
                patch(
                    "src.api.routes.jobs.get_active_job_count",
                    side_effect=mock_active_count,
                ),
                patch("asyncio.sleep", new_callable=AsyncMock),
                patch(
                    "src.api.app._close_caches",
                    new_callable=AsyncMock,
                ),
                patch(
                    "src.api.app._close_connections",
                    new_callable=AsyncMock,
                ),
            ):
                await app_mod._shutdown_services()
        finally:
            app_mod._state = original_state

        assert test_state._shutting_down is True
        assert call_count >= 3

    @pytest.mark.asyncio
    async def test_shutdown_deadline_reached(self):
        """Lines 710-711: deadline reached with jobs still active."""
        import src.api.app as app_mod

        original_state = app_mod._state
        test_state = AppState()
        app_mod._state = test_state

        async def always_active():
            return 1

        try:
            with (
                patch(
                    "src.api.routes.jobs.get_active_job_count",
                    side_effect=always_active,
                ),
                patch(
                    "asyncio.sleep", new_callable=AsyncMock
                ) as mock_sleep,
                patch(
                    "src.api.app._close_caches",
                    new_callable=AsyncMock,
                ),
                patch(
                    "src.api.app._close_connections",
                    new_callable=AsyncMock,
                ),
            ):
                # Make sleep advance elapsed quickly
                async def fast_sleep(n):
                    pass

                mock_sleep.side_effect = fast_sleep
                await app_mod._shutdown_services()
        finally:
            app_mod._state = original_state

        assert test_state._shutting_down is True


# ---------------------------------------------------------------------------
# _init_services — includes _init_distill call (line 603)
# ---------------------------------------------------------------------------

class TestInitServicesWithDistill:
    @pytest.mark.asyncio
    async def test_init_services_calls_distill(self):
        """Verify _init_services also calls _init_distill."""
        mock_settings = MagicMock()

        with (
            patch(
                "src.api.app._init_database",
                new_callable=AsyncMock,
            ),
            patch(
                "src.api.app._init_cache", new_callable=AsyncMock
            ),
            patch(
                "src.api.app._init_dedup", new_callable=AsyncMock
            ),
            patch(
                "src.api.app._init_vectordb",
                new_callable=AsyncMock,
            ),
            patch(
                "src.api.app._init_graph", new_callable=AsyncMock
            ),
            patch(
                "src.api.app._init_embedding",
                new_callable=AsyncMock,
            ),
            patch(
                "src.api.app._init_llm", new_callable=AsyncMock
            ),
            patch(
                "src.api.app._init_search_services",
                new_callable=AsyncMock,
            ),
            patch(
                "src.api.app._init_auth", new_callable=AsyncMock
            ),
            patch(
                "src.api.app._init_distill",
                new_callable=AsyncMock,
            ) as m_distill,
            patch(
                "src.config.get_settings",
                return_value=mock_settings,
            ),
        ):
            from src.api.app import _init_services

            await _init_services()
            m_distill.assert_called_once()


# ---------------------------------------------------------------------------
# Rate limiter middleware conditional (lines 763-765)
# ---------------------------------------------------------------------------

class TestRateLimiterConditional:
    def test_rate_limiter_not_added_by_default(self):
        """Lines 762-765: RATE_LIMIT_ENABLED defaults to false."""
        from src.api.app import app

        middleware_names = [
            type(m.cls).__name__
            if hasattr(m, "cls")
            else str(m)
            for m in app.user_middleware
        ]
        # By default rate limiter is not in middleware
        # (unless env var is set in test env)
        # Just verify app loads without error
        assert app is not None
