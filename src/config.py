"""Knowledge Local - Unified Configuration.

All settings centralized here. Override via .env or environment variables.
"""

from __future__ import annotations

import os
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_RUNTIME_BASE_DIR = os.getenv(
    "KNOWLEDGE_PIPELINE_RUNTIME_BASE_DIR", "/tmp/knowledge-local"
)


class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="")

    database_url: str = Field(
        default="postgresql+asyncpg://knowledge:knowledge@localhost:5432/knowledge_db",
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
    dense_dimension: int = Field(default=1024)
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
    model: str = Field(default="exaone3.5:7.8b")
    embedding_model: str = Field(default="bge-m3:latest")
    timeout: int = Field(default=60, ge=10, le=300)
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


class ApiSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="API_")

    host: str = Field(default="0.0.0.0")
    port: int = Field(default=8000)


class DashboardSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DASHBOARD_")

    api_url: str = Field(default="http://localhost:8000")
    api_timeout: int = Field(default=30)
    search_timeout: int = Field(default=60)


class Settings(BaseSettings):
    """Top-level aggregated settings."""

    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    qdrant: QdrantSettings = Field(default_factory=QdrantSettings)
    neo4j: Neo4jSettings = Field(default_factory=Neo4jSettings)
    ollama: OllamaSettings = Field(default_factory=OllamaSettings)
    embedding: EmbeddingSettings = Field(default_factory=EmbeddingSettings)
    quality: QualitySettings = Field(default_factory=QualitySettings)
    pipeline: PipelineSettings = Field(default_factory=PipelineSettings)
    api: ApiSettings = Field(default_factory=ApiSettings)
    dashboard: DashboardSettings = Field(default_factory=DashboardSettings)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings() -> None:
    get_settings.cache_clear()
