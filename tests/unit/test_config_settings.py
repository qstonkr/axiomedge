"""Unit tests for config.py Settings classes and get_settings/reset_settings."""

from __future__ import annotations

from src.config import (
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_LLM_MODEL,
    ApiSettings,
    AuthSettings,
    DashboardSettings,
    DatabaseSettings,
    EmbeddingSettings,
    Neo4jSettings,
    OllamaSettings,
    PipelineSettings,
    QdrantSettings,
    QualitySettings,
    Settings,
    get_settings,
    reset_settings,
)


# ---------------------------------------------------------------------------
# Individual Settings classes — defaults
# ---------------------------------------------------------------------------


class TestDatabaseSettings:
    def test_default_url(self):
        s = DatabaseSettings()
        assert "postgresql" in s.database_url

    def test_default_pool_size(self):
        s = DatabaseSettings()
        assert s.pool_size == 5
        assert s.max_overflow == 10
        assert s.echo is False


class TestQdrantSettings:
    def test_defaults(self):
        s = QdrantSettings()
        assert s.url == "http://localhost:6333"
        assert s.collection_name == "knowledge"
        assert s.entity_collection_name == "knowledge_entities"
        assert s.timeout == 30
        # NOTE: dense_dimension / dense_vector_name / sparse_vector_name 는 PR5
        # 에서 dead fields 로 제거. SSOT 는 config_weights.weights.embedding.dimension
        # 과 vectordb.client.DEFAULT_{DENSE,SPARSE}_VECTOR_NAME. 해당 검증은
        # tests/unit/test_config_drift.py 로 이동.


class TestNeo4jSettings:
    def test_defaults(self):
        s = Neo4jSettings()
        assert s.enabled is True
        assert s.uri == "bolt://localhost:7687"
        assert s.user == "neo4j"
        assert s.database == "neo4j"


class TestOllamaSettings:
    def test_defaults(self):
        s = OllamaSettings()
        assert s.base_url == "http://localhost:11434"
        assert s.model == DEFAULT_LLM_MODEL
        assert s.embedding_model == DEFAULT_EMBEDDING_MODEL
        assert s.timeout == 60
        assert s.context_length == 32768

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_BASE_URL", "http://gpu-server:11434")
        monkeypatch.setenv("OLLAMA_TIMEOUT", "120")
        s = OllamaSettings()
        assert s.base_url == "http://gpu-server:11434"
        assert s.timeout == 120


class TestEmbeddingSettings:
    def test_defaults(self, monkeypatch):
        monkeypatch.delenv("KNOWLEDGE_BGE_ONNX_MODEL_PATH", raising=False)
        s = EmbeddingSettings()
        assert s.max_length == 512
        assert s.colbert_max_tokens == 128
        assert s.onnx_model_path == ""


class TestQualitySettings:
    def test_defaults(self):
        s = QualitySettings()
        assert s.min_content_length == 50
        assert s.stale_threshold_days == 730
        assert s.stale_weight == 0.7


class TestPipelineSettings:
    def test_defaults(self):
        s = PipelineSettings()
        assert s.max_workers == 4
        assert s.ingest_batch_size == 50
        assert s.incremental_mode is True
        assert s.force_rebuild is False

    def test_output_dir_property(self):
        s = PipelineSettings()
        assert "pipeline" in s.output_dir

    def test_crawl_dir_property(self):
        s = PipelineSettings()
        assert "full_crawl" in s.crawl_dir

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("KNOWLEDGE_PIPELINE_MAX_WORKERS", "8")
        s = PipelineSettings()
        assert s.max_workers == 8


class TestAuthSettings:
    def test_defaults(self, monkeypatch):
        # .env 에서 AUTH_* 가 process env 로 들어와 있을 수 있어 default 검증
        # 전 env 비움 — pydantic-settings 는 priority 가 env > default.
        for key in [
            "AUTH_ENABLED", "AUTH_PROVIDER", "AUTH_JWT_ALGORITHM",
            "AUTH_JWT_ACCESS_EXPIRE_MINUTES", "AUTH_JWT_REFRESH_EXPIRE_HOURS",
            "AUTH_JWT_ISSUER", "AUTH_COOKIE_SECURE",
        ]:
            monkeypatch.delenv(key, raising=False)
        s = AuthSettings()
        assert s.enabled is False
        assert s.provider == "local"
        assert s.jwt_algorithm == "HS256"
        assert s.jwt_access_expire_minutes == 60
        assert s.jwt_refresh_expire_hours == 8
        assert s.jwt_issuer == "axiomedge-api"
        assert s.cookie_secure is False

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("AUTH_ENABLED", "true")
        monkeypatch.setenv("AUTH_PROVIDER", "internal")
        monkeypatch.setenv("AUTH_JWT_SECRET", "test-secret")
        s = AuthSettings()
        assert s.enabled is True
        assert s.provider == "internal"
        assert s.jwt_secret == "test-secret"


class TestApiSettings:
    def test_defaults(self):
        s = ApiSettings()
        assert s.host == "0.0.0.0"
        assert s.port == 8000


class TestDashboardSettings:
    def test_defaults(self):
        s = DashboardSettings()
        assert s.api_url == "http://localhost:8000"
        assert s.api_timeout == 30
        assert s.search_timeout == 60


# ---------------------------------------------------------------------------
# Aggregated Settings
# ---------------------------------------------------------------------------


class TestSettings:
    def test_aggregated_settings_has_all_sub_settings(self):
        s = Settings()
        assert isinstance(s.database, DatabaseSettings)
        assert isinstance(s.qdrant, QdrantSettings)
        assert isinstance(s.neo4j, Neo4jSettings)
        assert isinstance(s.ollama, OllamaSettings)
        assert isinstance(s.embedding, EmbeddingSettings)
        assert isinstance(s.quality, QualitySettings)
        assert isinstance(s.pipeline, PipelineSettings)
        assert isinstance(s.auth, AuthSettings)
        assert isinstance(s.api, ApiSettings)
        assert isinstance(s.dashboard, DashboardSettings)


# ---------------------------------------------------------------------------
# get_settings / reset_settings
# ---------------------------------------------------------------------------


class TestGetSettings:
    def test_returns_settings_instance(self):
        reset_settings()
        s = get_settings()
        assert isinstance(s, Settings)

    def test_cached_returns_same_instance(self):
        reset_settings()
        s1 = get_settings()
        s2 = get_settings()
        assert s1 is s2

    def test_reset_clears_cache(self):
        reset_settings()
        s1 = get_settings()
        reset_settings()
        s2 = get_settings()
        # After reset, a new instance is created
        assert s1 is not s2
