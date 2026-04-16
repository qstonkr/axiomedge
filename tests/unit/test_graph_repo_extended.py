"""Extended unit tests for src/graph/repository.py — 173 uncovered lines."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.stores.neo4j.repository import Neo4jGraphRepository, NoOpNeo4jGraphRepository


def _run(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


def _make_repo(records=None, write_result=None):
    client = AsyncMock()
    client.execute_query = AsyncMock(return_value=records or [])
    client.execute_write = AsyncMock(return_value=write_result or {"nodes_created": 1})
    client.execute_unwind_batch = AsyncMock(return_value=[])
    client.health_check = AsyncMock(return_value=True)
    return Neo4jGraphRepository(client), client


# ---------------------------------------------------------------------------
# Write Methods
# ---------------------------------------------------------------------------

class TestUpsertDocument:
    def test_basic(self):
        repo, client = _make_repo()
        result = _run(repo.upsert_document("doc1", title="Test", kb_id="kb1"))
        assert result == {"nodes_created": 1}
        client.execute_write.assert_called_once()

    def test_with_extra_properties(self):
        repo, client = _make_repo()
        result = _run(repo.upsert_document("doc1", title="Test", extra_properties={"custom": "value"}))
        assert result == {"nodes_created": 1}
        call_args = client.execute_write.call_args
        assert "extra_props" in call_args[0][1] or "extra_props" in call_args.kwargs.get("params", call_args[0][1])


class TestUpsertEntity:
    def test_supported_type(self):
        repo, client = _make_repo()
        result = _run(repo.upsert_entity("Person", "p1", name="Alice"))
        assert result == {"nodes_created": 1}

    def test_unsupported_type_falls_back(self):
        repo, client = _make_repo()
        result = _run(repo.upsert_entity("UnknownType", "e1", name="X"))
        assert result == {"nodes_created": 1}
        # Should fall back to Entity
        cypher = client.execute_write.call_args[0][0]
        assert "Entity" in cypher


class TestCreateRelationship:
    def test_basic(self):
        repo, client = _make_repo()
        result = _run(repo.create_relationship("s1", "t1", "MENTIONS"))
        assert result == {"nodes_created": 1}

    def test_unsupported_rel_type(self):
        repo, client = _make_repo()
        result = _run(repo.create_relationship("s1", "t1", "UNKNOWN_REL"))
        assert result == {"nodes_created": 1}
        cypher = client.execute_write.call_args[0][0]
        assert "RELATED_TO" in cypher

    def test_with_properties(self):
        repo, client = _make_repo()
        result = _run(repo.create_relationship("s1", "t1", "OWNS", properties={"since": "2024"}))
        assert result == {"nodes_created": 1}


class TestBatchUpsertNodes:
    def test_batch(self):
        repo, client = _make_repo()
        nodes = [{"node_id": "n1", "title": "T1", "properties": {}}]
        result = _run(repo.batch_upsert_nodes("Document", nodes))
        client.execute_unwind_batch.assert_called_once()


class TestBatchUpsertEdges:
    def test_batch(self):
        repo, client = _make_repo()
        edges = [{"source": "s1", "target": "t1", "properties": {}}]
        result = _run(repo.batch_upsert_edges("MENTIONS", edges))
        client.execute_unwind_batch.assert_called_once()


class TestUpsertDocumentLineage:
    def test_lineage(self):
        repo, client = _make_repo()
        result = _run(repo.upsert_document_lineage(
            "doc1", kb_id="kb1",
            provenance={"source_type": "confluence", "source_url": "http://x"},
        ))
        assert client.execute_write.call_count == 2  # doc merge + KB link


# ---------------------------------------------------------------------------
# Read Methods
# ---------------------------------------------------------------------------

class TestFindRelatedChunks:
    def test_success(self):
        records = [{"source_uri": "http://a"}, {"source_uri": "http://b"}]
        repo, _ = _make_repo(records=records)
        result = _run(repo.find_related_chunks(["entity1"]))
        assert result == {"http://a", "http://b"}

    def test_empty_names(self):
        repo, _ = _make_repo()
        result = _run(repo.find_related_chunks([]))
        assert result == set()

    def test_with_scope(self):
        records = [{"source_uri": "http://a"}]
        repo, client = _make_repo(records=records)
        result = _run(repo.find_related_chunks(["entity1"], scope_kb_ids=["kb1"]))
        assert "http://a" in result
        cypher = client.execute_query.call_args[0][0]
        assert "scope_kb_ids" in cypher

    def test_failure_returns_empty(self):
        repo, client = _make_repo()
        client.execute_query = AsyncMock(side_effect=Exception("neo4j error"))
        result = _run(repo.find_related_chunks(["entity1"]))
        assert result == set()


class TestSearchEntities:
    def test_success(self):
        records = [{"name": "Server", "node_type": "System"}]
        repo, _ = _make_repo(records=records)
        result = _run(repo.search_entities(["server"]))
        assert len(result) >= 1

    def test_empty_keywords(self):
        repo, client = _make_repo()
        # Empty keywords should fall back to CONTAINS search
        result = _run(repo.search_entities([]))
        assert isinstance(result, list)

    def test_graphrag_failure_falls_through(self):
        repo, client = _make_repo()
        # First call (GraphRAG) fails, second (wiki) succeeds
        call_count = 0
        async def _side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("graphrag index missing")
            return [{"name": "X", "node_type": "Entity"}]

        client.execute_query = AsyncMock(side_effect=_side_effect)
        result = _run(repo.search_entities(["test"]))
        # Should still get results from wiki search
        assert isinstance(result, list)


class TestFindExperts:
    def test_success(self):
        records = [{"name": "Alice", "person_id": "p1", "doc_count": 5, "related": ["doc1"], "source": "document_owner"}]
        repo, _ = _make_repo(records=records)
        result = _run(repo.find_experts("topic1"))
        assert len(result) >= 1
        assert result[0]["name"] == "Alice"

    def test_dedup_merges(self):
        # find_experts runs 3 separate queries; mock returns for all 3
        records = [
            {"name": "Alice", "person_id": "p1", "doc_count": 5, "related": ["d1"], "source": "doc_owner"},
        ]
        repo, client = _make_repo()
        # Each of the 3 paths returns one Alice record
        client.execute_query = AsyncMock(side_effect=[
            [{"name": "Alice", "person_id": "p1", "doc_count": 5, "related": ["d1"], "source": "doc_owner"}],
            [{"name": "Alice", "person_id": "p1", "doc_count": 3, "related": ["d2"], "source": "entity_expert"}],
            [],
        ])
        result = _run(repo.find_experts("topic1"))
        assert len(result) == 1
        assert result[0]["doc_count"] == 8  # merged


class TestSearchRelatedNodes:
    def test_success(self):
        repo, _ = _make_repo(records=[{"id": "d2", "name": "Related"}])
        result = _run(repo.search_related_nodes("doc1"))
        assert len(result) == 1

    def test_failure(self):
        repo, client = _make_repo()
        client.execute_query = AsyncMock(side_effect=Exception("fail"))
        result = _run(repo.search_related_nodes("doc1"))
        assert result == []


class TestGetEntityNeighbors:
    def test_success(self):
        repo, _ = _make_repo(records=[{"name": "Bob", "type": "Person", "id": "p2"}])
        result = _run(repo.get_entity_neighbors("Alice", "Person"))
        assert len(result) == 1

    def test_failure(self):
        repo, client = _make_repo()
        client.execute_query = AsyncMock(side_effect=Exception("fail"))
        result = _run(repo.get_entity_neighbors("Alice", "Person"))
        assert result == []


class TestGetKnowledgePath:
    def test_success(self):
        repo, _ = _make_repo(records=[{"path_length": 2}])
        result = _run(repo.get_knowledge_path("d1", "d2"))
        assert len(result) == 1

    def test_failure(self):
        repo, client = _make_repo()
        client.execute_query = AsyncMock(side_effect=Exception("fail"))
        result = _run(repo.get_knowledge_path("d1", "d2"))
        assert result == []


class TestFindCommonEntities:
    def test_too_few_docs(self):
        repo, _ = _make_repo()
        result = _run(repo.find_common_entities(["d1"]))
        assert result == []

    def test_success(self):
        repo, _ = _make_repo(records=[{"name": "topic", "type": "Topic"}])
        result = _run(repo.find_common_entities(["d1", "d2"]))
        assert len(result) == 1


class TestFindSimilarDocuments:
    def test_success(self):
        repo, _ = _make_repo(records=[{"id": "d2", "title": "Similar"}])
        result = _run(repo.find_similar_documents("d1"))
        assert len(result) == 1


class TestQueryProcessChain:
    def test_success(self):
        repo, _ = _make_repo(records=[{"step_number": 1, "action": "Start"}])
        result = _run(repo.query_process_chain("my process"))
        assert len(result) == 1

    def test_empty_keyword(self):
        repo, _ = _make_repo()
        result = _run(repo.query_process_chain(""))
        assert result == []


class TestFindStepContext:
    def test_success(self):
        repo, _ = _make_repo(records=[{"step_number": 2, "action": "Step 2"}])
        result = _run(repo.find_step_context("step keyword"))
        assert result == {"step_number": 2, "action": "Step 2"}

    def test_empty_keyword(self):
        repo, _ = _make_repo()
        result = _run(repo.find_step_context(""))
        assert result == {}

    def test_no_results(self):
        repo, _ = _make_repo(records=[])
        result = _run(repo.find_step_context("unknown"))
        assert result == {}


# ---------------------------------------------------------------------------
# Stats / Health
# ---------------------------------------------------------------------------

class TestStats:
    def test_get_entity_count(self):
        repo, _ = _make_repo(records=[{"count": 42}])
        result = _run(repo.get_entity_count())
        assert result == 42

    def test_get_entity_count_failure(self):
        repo, client = _make_repo()
        client.execute_query = AsyncMock(side_effect=Exception("fail"))
        result = _run(repo.get_entity_count())
        assert result == 0

    def test_get_document_count(self):
        repo, _ = _make_repo(records=[{"count": 100}])
        result = _run(repo.get_document_count())
        assert result == 100

    def test_get_stats(self):
        repo, client = _make_repo()
        client.execute_query = AsyncMock(side_effect=[
            [{"label": "Document", "count": 10}],
            [{"type": "MENTIONS", "count": 5}],
        ])
        result = _run(repo.get_stats())
        assert result["node_types"]["Document"] == 10
        assert result["edge_types"]["MENTIONS"] == 5

    def test_health_check(self):
        repo, _ = _make_repo()
        result = _run(repo.health_check())
        assert result is True


# ---------------------------------------------------------------------------
# Resolve helpers
# ---------------------------------------------------------------------------

class TestResolveHelpers:
    def test_resolve_node_type_supported(self):
        repo, _ = _make_repo()
        assert repo._resolve_node_type("Document") == "Document"

    def test_resolve_node_type_case_insensitive(self):
        repo, _ = _make_repo()
        assert repo._resolve_node_type("document") == "Document"

    def test_resolve_node_type_unknown(self):
        repo, _ = _make_repo()
        assert repo._resolve_node_type("Alien") == "Entity"

    def test_resolve_relation_type_supported(self):
        repo, _ = _make_repo()
        assert repo._resolve_relation_type("MENTIONS") == "MENTIONS"

    def test_resolve_relation_type_unknown(self):
        repo, _ = _make_repo()
        assert repo._resolve_relation_type("UNKNOWN") == "RELATED_TO"


# ---------------------------------------------------------------------------
# NoOp
# ---------------------------------------------------------------------------

class TestNoOp:
    def test_all_methods_return_defaults(self):
        noop = NoOpNeo4jGraphRepository()
        assert _run(noop.upsert_document("d1")) == {"nodes_created": 0, "properties_set": 0}
        assert _run(noop.upsert_entity("Person", "p1")) == {"nodes_created": 0, "properties_set": 0}
        assert _run(noop.create_relationship("s", "t", "R")) == {"nodes_created": 0, "relationships_created": 0}
        assert _run(noop.batch_upsert_nodes("X", [])) == []
        assert _run(noop.batch_upsert_edges("X", [])) == []
        assert _run(noop.find_related_chunks([])) == set()
        assert _run(noop.search_entities([])) == []
        assert _run(noop.find_experts("x")) == []
        assert _run(noop.health_check()) is True
        assert _run(noop.get_entity_count()) == 0
        assert _run(noop.get_document_count()) == 0
        stats = _run(noop.get_stats())
        assert stats == {"node_types": {}, "edge_types": {}}
