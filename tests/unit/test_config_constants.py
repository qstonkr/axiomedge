"""Unit tests for config.py SSOT constants."""

from src.config import (
    DEFAULT_LLM_MODEL,
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_DATABASE_URL,
)


class TestConfigConstants:
    def test_llm_model_defined(self) -> None:
        assert DEFAULT_LLM_MODEL == "exaone3.5:7.8b"

    def test_embedding_model_defined(self) -> None:
        assert DEFAULT_EMBEDDING_MODEL == "bge-m3:latest"

    def test_database_url_defined(self) -> None:
        assert "postgresql" in DEFAULT_DATABASE_URL
        assert "knowledge" in DEFAULT_DATABASE_URL

    def test_init_db_uses_same_url(self) -> None:
        from src.database.init_db import DEFAULT_DATABASE_URL as init_url
        assert init_url == DEFAULT_DATABASE_URL
