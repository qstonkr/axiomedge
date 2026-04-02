"""Unit tests for AppState typed container."""

from src.api.state import AppState


class TestAppState:
    """Test AppState dict-style backward compatibility."""

    def setup_method(self) -> None:
        self.state = AppState()

    def test_default_fields_are_none(self) -> None:
        assert self.state.embedder is None
        assert self.state.llm is None
        assert self.state.qdrant_search is None

    def test_shutting_down_default_false(self) -> None:
        assert self.state._shutting_down is False

    # --- Dict-style access ---

    def test_setitem_and_getitem(self) -> None:
        self.state["embedder"] = "fake_embedder"
        assert self.state["embedder"] == "fake_embedder"
        assert self.state.embedder == "fake_embedder"

    def test_getitem_raises_keyerror_for_unknown(self) -> None:
        import pytest
        with pytest.raises(KeyError):
            self.state["nonexistent_field_xyz"]

    def test_get_returns_default(self) -> None:
        assert self.state.get("embedder") is None
        assert self.state.get("embedder", "fallback") is None  # field exists, value is None
        assert self.state.get("nonexistent_xyz", "fallback") == "fallback"

    def test_contains_none_means_not_present(self) -> None:
        assert "embedder" not in self.state  # None → not present
        self.state["embedder"] = "test"
        assert "embedder" in self.state

    def test_contains_shutting_down_false_is_present(self) -> None:
        # _shutting_down defaults to False (not None), so it IS present
        assert "_shutting_down" in self.state

    def test_setitem_shutting_down(self) -> None:
        self.state["_shutting_down"] = True
        assert self.state._shutting_down is True

    # --- All service fields exist ---

    def test_all_service_fields_exist(self) -> None:
        expected = [
            "db_session_factory", "kb_registry", "glossary_repo",
            "embedder", "llm", "neo4j", "graph_repo",
            "qdrant_search", "qdrant_store", "rag_pipeline",
            "search_cache", "dedup_pipeline", "auth_provider",
        ]
        for field_name in expected:
            assert hasattr(self.state, field_name), f"Missing field: {field_name}"
