"""Comprehensive tests for src/graph/repository.py — NoOpNeo4jGraphRepository."""

from __future__ import annotations

import pytest

from src.graph.repository import NoOpNeo4jGraphRepository


class TestNoOpNeo4jGraphRepository:
    """Every method of NoOpNeo4jGraphRepository returns empty/zero defaults."""

    @pytest.fixture
    def repo(self):
        return NoOpNeo4jGraphRepository()

    @pytest.mark.asyncio
    async def test_upsert_document(self, repo):
        result = await repo.upsert_document("doc1", title="T", kb_id="kb")
        assert result == {"nodes_created": 0, "properties_set": 0}

    @pytest.mark.asyncio
    async def test_upsert_entity(self, repo):
        result = await repo.upsert_entity("Person", "e1", name="Alice")
        assert result == {"nodes_created": 0, "properties_set": 0}

    @pytest.mark.asyncio
    async def test_create_relationship(self, repo):
        result = await repo.create_relationship("s1", "t1", "RELATED_TO")
        assert result == {"nodes_created": 0, "relationships_created": 0}

    @pytest.mark.asyncio
    async def test_batch_upsert_nodes(self, repo):
        result = await repo.batch_upsert_nodes("Entity", [{"id": "1"}])
        assert result == []

    @pytest.mark.asyncio
    async def test_batch_upsert_edges(self, repo):
        result = await repo.batch_upsert_edges("RELATED_TO", [{"source": "a", "target": "b"}])
        assert result == []

    @pytest.mark.asyncio
    async def test_upsert_document_lineage(self, repo):
        result = await repo.upsert_document_lineage("doc1", kb_id="kb")
        assert result == {"nodes_created": 0, "properties_set": 0}

    @pytest.mark.asyncio
    async def test_find_related_chunks(self, repo):
        result = await repo.find_related_chunks(["entity1"])
        assert result == set()

    @pytest.mark.asyncio
    async def test_search_entities(self, repo):
        result = await repo.search_entities(["keyword"])
        assert result == []

    @pytest.mark.asyncio
    async def test_find_experts(self, repo):
        result = await repo.find_experts("topic")
        assert result == []

    @pytest.mark.asyncio
    async def test_search_related_nodes(self, repo):
        result = await repo.search_related_nodes("doc1")
        assert result == []

    @pytest.mark.asyncio
    async def test_get_entity_neighbors(self, repo):
        result = await repo.get_entity_neighbors("name", "Person")
        assert result == []

    @pytest.mark.asyncio
    async def test_get_knowledge_path(self, repo):
        result = await repo.get_knowledge_path("s1", "t1")
        assert result == []

    @pytest.mark.asyncio
    async def test_find_common_entities(self, repo):
        result = await repo.find_common_entities(["d1", "d2"])
        assert result == []

    @pytest.mark.asyncio
    async def test_find_similar_documents(self, repo):
        result = await repo.find_similar_documents("doc1")
        assert result == []

    @pytest.mark.asyncio
    async def test_query_process_chain(self, repo):
        result = await repo.query_process_chain("process")
        assert result == []

    @pytest.mark.asyncio
    async def test_find_step_context(self, repo):
        result = await repo.find_step_context("step")
        assert result == {}

    @pytest.mark.asyncio
    async def test_get_entity_count(self, repo):
        assert await repo.get_entity_count() == 0

    @pytest.mark.asyncio
    async def test_get_document_count(self, repo):
        assert await repo.get_document_count() == 0

    @pytest.mark.asyncio
    async def test_get_stats(self, repo):
        result = await repo.get_stats()
        assert result == {"node_types": {}, "edge_types": {}}

    @pytest.mark.asyncio
    async def test_health_check(self, repo):
        assert await repo.health_check() is True


# ---------------------------------------------------------------------------
# Neo4jGraphRepository helper methods (no Neo4j connection needed)
# ---------------------------------------------------------------------------


class TestNeo4jGraphRepositoryHelpers:
    """Test private helper methods that don't require a Neo4j connection."""

    def _make_repo(self):
        from src.graph.repository import Neo4jGraphRepository
        mock_client = object()  # Unused for helper tests
        return Neo4jGraphRepository(mock_client)

    def test_resolve_node_type_supported(self):
        repo = self._make_repo()
        assert repo._resolve_node_type("Document") == "Document"
        assert repo._resolve_node_type("Person") == "Person"

    def test_resolve_node_type_case_insensitive(self):
        repo = self._make_repo()
        result = repo._resolve_node_type("document")
        assert result == "Document"

    def test_resolve_node_type_unsupported(self):
        repo = self._make_repo()
        result = repo._resolve_node_type("NonExistentType")
        assert result == "Entity"

    def test_resolve_relation_type_supported(self):
        repo = self._make_repo()
        assert repo._resolve_relation_type("BELONGS_TO") == "BELONGS_TO"

    def test_resolve_relation_type_case_insensitive(self):
        repo = self._make_repo()
        result = repo._resolve_relation_type("belongs_to")
        assert result == "BELONGS_TO"

    def test_resolve_relation_type_unsupported(self):
        repo = self._make_repo()
        result = repo._resolve_relation_type("UNKNOWN_REL")
        assert result == "RELATED_TO"

    def test_fact_relation_whitelist(self):
        from src.graph.repository import Neo4jGraphRepository
        wl = Neo4jGraphRepository._FACT_RELATION_WHITELIST
        assert "RESPONSIBLE_FOR" in wl
        assert "BELONGS_TO" in wl
        assert "NEXT_STEP" in wl

    def test_fulltext_index_names(self):
        from src.graph.repository import Neo4jGraphRepository
        assert Neo4jGraphRepository._FULLTEXT_INDEX == "entity_name_title"
        assert Neo4jGraphRepository._FULLTEXT_INDEX_GRAPHRAG == "entity_search"
