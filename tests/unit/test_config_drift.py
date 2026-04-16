"""Config SSOT drift fixes (PR5)."""

from __future__ import annotations



class TestQdrantSettingsNoDeadFields:
    """`src/config.py::QdrantSettings` — dead fields removed.

    dense_dimension / dense_vector_name / sparse_vector_name 는 어디에서도
    `settings.qdrant.X` 로 읽히지 않았고, 실제 SSOT 는 각각:
      - dimension → config_weights.weights.embedding.dimension
      - *_vector_name → vectordb.client.DEFAULT_*_VECTOR_NAME
    PR5 에서 제거.
    """

    def test_qdrant_settings_has_no_dense_dimension(self):
        from src.config import QdrantSettings
        qs = QdrantSettings()
        assert not hasattr(qs, "dense_dimension"), (
            "QdrantSettings.dense_dimension is dead — use "
            "config_weights.weights.embedding.dimension"
        )

    def test_qdrant_settings_has_no_dense_vector_name(self):
        from src.config import QdrantSettings
        qs = QdrantSettings()
        assert not hasattr(qs, "dense_vector_name"), (
            "QdrantSettings.dense_vector_name is dead — use "
            "vectordb.client.DEFAULT_DENSE_VECTOR_NAME"
        )

    def test_qdrant_settings_has_no_sparse_vector_name(self):
        from src.config import QdrantSettings
        qs = QdrantSettings()
        assert not hasattr(qs, "sparse_vector_name"), (
            "QdrantSettings.sparse_vector_name is dead — use "
            "vectordb.client.DEFAULT_SPARSE_VECTOR_NAME"
        )

    def test_qdrant_settings_keeps_live_fields(self):
        """실사용 필드는 유지."""
        from src.config import QdrantSettings
        qs = QdrantSettings()
        assert qs.url.startswith("http")
        assert qs.collection_name == "knowledge"
        assert qs.entity_collection_name == "knowledge_entities"
        assert qs.timeout >= 1
        assert qs.search_timeout_ms >= 100


class TestEmbeddingDimensionSSOT:
    """config_weights.weights.embedding.dimension 이 단일 SSOT."""

    def test_weights_embedding_dimension_is_1024(self):
        from src.config_weights import weights
        assert weights.embedding.dimension == 1024

    def test_vectordb_client_references_weights_embedding_dimension(self):
        """QdrantConfig default 가 weights SSOT 를 사용."""
        from src.config_weights import weights
        from src.vectordb.client import QdrantConfig
        cfg = QdrantConfig()
        assert cfg.dense_dimension == weights.embedding.dimension


class TestVectorNameSSOT:
    """vectordb.client.DEFAULT_*_VECTOR_NAME 이 단일 SSOT."""

    def test_defaults_are_bge_dense_sparse(self):
        from src.vectordb.client import (
            DEFAULT_DENSE_VECTOR_NAME,
            DEFAULT_SPARSE_VECTOR_NAME,
        )
        assert DEFAULT_DENSE_VECTOR_NAME == "bge_dense"
        assert DEFAULT_SPARSE_VECTOR_NAME == "bge_sparse"

    def test_provider_config_uses_defaults(self):
        from src.vectordb.client import (
            DEFAULT_DENSE_VECTOR_NAME,
            DEFAULT_SPARSE_VECTOR_NAME,
            QdrantConfig,
        )
        cfg = QdrantConfig()
        assert cfg.dense_vector_name == DEFAULT_DENSE_VECTOR_NAME
        assert cfg.sparse_vector_name == DEFAULT_SPARSE_VECTOR_NAME
