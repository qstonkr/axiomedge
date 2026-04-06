"""Knowledge Local - Unified Configuration.

All settings centralized here. Override via .env or environment variables.
"""

from __future__ import annotations

import os
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Model name defaults — SSOT for all fallback references
DEFAULT_LLM_MODEL = "exaone3.5:7.8b"
DEFAULT_EMBEDDING_MODEL = "bge-m3:latest"  # Ollama tag
DEFAULT_EMBEDDING_MODEL_HF = "BAAI/bge-m3"  # HuggingFace ID (ONNX provider)

_DB_FALLBACK = "postgresql+asyncpg://{}:{}@localhost:5432/knowledge_db".format(  # noqa: S106
    os.getenv("PGUSER", "knowledge"), os.getenv("PGPASSWORD", "knowledge"),
)
DEFAULT_DATABASE_URL = os.getenv("DATABASE_URL", _DB_FALLBACK)

DEFAULT_RUNTIME_BASE_DIR = os.getenv(
    "KNOWLEDGE_PIPELINE_RUNTIME_BASE_DIR", "/tmp/knowledge-local"
)


class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="")

    database_url: str = Field(
        default=DEFAULT_DATABASE_URL,
        alias="DATABASE_URL",
    )
    pool_size: int = Field(default=5)
    max_overflow: int = Field(default=10)
    echo: bool = Field(default=False)


class QdrantSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="QDRANT_")

    url: str = Field(default="http://localhost:6333")
    collection_name: str = Field(default="knowledge")
    entity_collection_name: str = Field(default="knowledge_entities")
    # NOTE: Keep in sync with config_weights.EmbeddingConfig.dimension (BGE-M3 fixed)
    dense_dimension: int = Field(default=1024)
    # NOTE: Keep in sync with vectordb.client.DEFAULT_DENSE/SPARSE_VECTOR_NAME
    dense_vector_name: str = Field(default="bge_dense")
    sparse_vector_name: str = Field(default="bge_sparse")
    timeout: int = Field(default=30)
    search_timeout_ms: int = Field(default=5000)


class Neo4jSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NEO4J_")

    enabled: bool = Field(default=True)
    uri: str = Field(default="bolt://localhost:7687")
    user: str = Field(default="neo4j")
    password: str = Field(default="")
    database: str = Field(default="neo4j")


class OllamaSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OLLAMA_")

    base_url: str = Field(default="http://localhost:11434")
    model: str = Field(default=DEFAULT_LLM_MODEL)
    embedding_model: str = Field(default=DEFAULT_EMBEDDING_MODEL)
    timeout: int = Field(default=60, ge=10, le=300)
    context_length: int = Field(default=32768, ge=1024, le=32768)
    max_content_length: int = Field(default=4000, ge=100, le=32000)


class EmbeddingSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="KNOWLEDGE_BGE_")

    onnx_model_path: str = Field(default="")
    max_length: int = Field(default=512)
    colbert_max_tokens: int = Field(default=128)


class QualitySettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="KNOWLEDGE_QUALITY_")

    min_content_length: int = Field(default=50, ge=10, le=1000)
    stale_threshold_days: int = Field(default=730)
    stale_weight: float = Field(default=0.7, ge=0.0, le=1.0)


class PipelineSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="KNOWLEDGE_PIPELINE_")

    runtime_base_dir: str = Field(default=DEFAULT_RUNTIME_BASE_DIR)
    max_workers: int = Field(default=4, ge=1, le=16)
    batch_size: int = Field(default=50, ge=10, le=500)
    incremental_mode: bool = Field(default=True)
    force_rebuild: bool = Field(default=False)

    @property
    def output_dir(self) -> str:
        return os.path.join(self.runtime_base_dir, "pipeline")

    @property
    def crawl_dir(self) -> str:
        return os.path.join(self.runtime_base_dir, "full_crawl")


class AuthSettings(BaseSettings):
    """Auth configuration. Set AUTH_ENABLED=true to activate."""

    model_config = SettingsConfigDict(env_prefix="AUTH_")

    enabled: bool = Field(default=False)
    provider: str = Field(default="local")  # local | keycloak | azure_ad | internal

    # Internal JWT (for AUTH_PROVIDER=internal)
    jwt_secret: str = Field(default="")  # Required for internal provider
    jwt_algorithm: str = Field(default="HS256")
    jwt_access_expire_minutes: int = Field(default=60)
    jwt_refresh_expire_hours: int = Field(default=8)
    jwt_issuer: str = Field(default="oreo-internal-api")
    cookie_secure: bool = Field(default=False)  # True in production (HTTPS)

    # Keycloak
    keycloak_url: str = Field(default="")
    keycloak_realm: str = Field(default="knowledge")
    keycloak_client_id: str = Field(default="knowledge-local")
    keycloak_client_secret: str = Field(default="")

    # Azure AD
    azure_ad_tenant_id: str = Field(default="")
    azure_ad_client_id: str = Field(default="")

    # Local dev API keys (JSON: {"key": {"email": "...", "name": "...", "roles": [...]}})
    local_api_keys: str = Field(default="{}")


class ApiSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="API_")

    host: str = Field(default="0.0.0.0")
    port: int = Field(default=8000)


class DashboardSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DASHBOARD_")

    api_url: str = Field(default="http://localhost:8000")
    api_timeout: int = Field(default=30)
    search_timeout: int = Field(default=60)


class DistillSettings(BaseSettings):
    """Distill Plugin 인프라 설정. 프로필(학습 파라미터)은 distill.yaml/DB에서 관리."""

    model_config = SettingsConfigDict(env_prefix="DISTILL_")

    enabled: bool = Field(default=True, description="Distill 플러그인 활성화")
    config_path: str = Field(default="distill.yaml", description="프로필 YAML 경로")
    work_dir: str = Field(default="/tmp/distill", description="빌드 작업 디렉토리")
    llm_concurrency: int = Field(default=3, ge=1, le=10, description="Teacher LLM 동시 호출 수")
    llm_timeout_sec: int = Field(default=120, ge=10, description="Teacher LLM 호출 타임아웃")
    build_timeout_sec: int = Field(default=7200, ge=300, description="빌드 전체 타임아웃")
    log_full_context: bool = Field(default=False, description="usage_log에 answer+chunks 저장")
    rag_api_url: str = Field(default="http://localhost:8000", description="재학습 시 Teacher RAG URL")


class Settings(BaseSettings):
    """Top-level aggregated settings."""

    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    qdrant: QdrantSettings = Field(default_factory=QdrantSettings)
    neo4j: Neo4jSettings = Field(default_factory=Neo4jSettings)
    ollama: OllamaSettings = Field(default_factory=OllamaSettings)
    embedding: EmbeddingSettings = Field(default_factory=EmbeddingSettings)
    quality: QualitySettings = Field(default_factory=QualitySettings)
    pipeline: PipelineSettings = Field(default_factory=PipelineSettings)
    auth: AuthSettings = Field(default_factory=AuthSettings)
    api: ApiSettings = Field(default_factory=ApiSettings)
    dashboard: DashboardSettings = Field(default_factory=DashboardSettings)
    distill: DistillSettings = Field(default_factory=DistillSettings)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings() -> None:
    get_settings.cache_clear()
