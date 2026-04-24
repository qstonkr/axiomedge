"""Comprehensive tests for src/graph/ — client, entity_resolver, multi_hop, schema."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch


from src.stores.neo4j.client import (
    Neo4jClient,
    NoOpNeo4jClient,
    NoOpResult,
    NoOpSession,
    NoOpTransaction,
)
from src.stores.neo4j.entity_resolver import (
    EntityResolver,
    EntityType,
    ResolutionStage,
    _basic_normalize,
)
from src.stores.neo4j.multi_hop_searcher import (
    Expert,
    KnowledgePath,
    MultiHopSearcher,
    RelatedNode,
)


# ===========================================================================
# Neo4jClient
# ===========================================================================

class TestNeo4jClient:
    def test_init_defaults(self):
        client = Neo4jClient()
        assert client.uri == "bolt://localhost:7687"
        assert client.user == "neo4j"
        assert client.database == "neo4j"

    def test_init_custom(self):
        client = Neo4jClient(uri="bolt://host:7687", user="admin", password="secret")
        assert client.uri == "bolt://host:7687"
        assert client.user == "admin"

    def test_auth_disabled_from_env(self):
        with patch.dict("os.environ", {"NEO4J_AUTH": "none"}):
            client = Neo4jClient()
            assert client._auth_disabled is True

    async def test_close_no_driver(self):
        client = Neo4jClient()
        await client.close()  # Should not raise

    async def test_close_with_driver(self):
        client = Neo4jClient()
        client._driver = AsyncMock()
        await client.close()
        client._driver is None

    async def test_health_check_no_driver(self):
        client = Neo4jClient()
        client._driver = None
        # connect will fail
        with patch.object(client, "connect", side_effect=RuntimeError("no neo4j")):
            result = await client.health_check()
        assert result is False

    async def test_execute_query(self):
        client = Neo4jClient()
        mock_session = AsyncMock()
        mock_result = AsyncMock()

        # Mock async iterator for records
        record1 = MagicMock()
        record1.data.return_value = {"n": 1}
        mock_result.__aiter__ = MagicMock(return_value=iter([record1]))

        async def fake_aiter(self):
            yield record1

        mock_result.__aiter__ = fake_aiter
        mock_session.run.return_value = mock_result
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        client._driver = MagicMock()
        client._driver.session.return_value = mock_session

        records = await client.execute_query("RETURN 1 as n")
        assert len(records) == 1
        assert records[0]["n"] == 1

    async def test_execute_write(self):
        """Neo4jClient.execute_write 가 session.execute_write(callable) 를
        쓰는지 검증 (PR#2 / a4f5ddd — bare session.run → managed tx).

        ``_work(tx)`` 안에서 ``tx.run`` 을 호출하고 summary 를 consume 한 뒤
        counter dict 를 반환한다. 테스트는 실제 callable 을 fake tx 로 실행해
        결과를 그대로 돌려준다.
        """
        client = Neo4jClient()
        mock_tx = AsyncMock()
        mock_result = AsyncMock()
        mock_summary = MagicMock()
        mock_summary.counters.nodes_created = 5
        mock_summary.counters.nodes_deleted = 0
        mock_summary.counters.relationships_created = 3
        mock_summary.counters.relationships_deleted = 0
        mock_summary.counters.properties_set = 10
        mock_result.consume.return_value = mock_summary
        mock_tx.run.return_value = mock_result

        async def _fake_execute_write(callable_):
            # Driver managed tx 동작 모사: callable 을 _fake tx 로 1회 호출.
            return await callable_(mock_tx)

        mock_session = AsyncMock()
        mock_session.execute_write = _fake_execute_write
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        client._driver = MagicMock()
        client._driver.session.return_value = mock_session

        result = await client.execute_write("CREATE (n:Test)", {"k": "v"})
        assert result["nodes_created"] == 5
        assert result["relationships_created"] == 3
        # tx.run 이 실제 cypher + params 를 받았는지 (callable 경로 검증)
        mock_tx.run.assert_awaited_once()
        args = mock_tx.run.call_args.args
        assert args[0] == "CREATE (n:Test)"
        assert args[1] == {"k": "v"}


# ===========================================================================
# NoOp Client
# ===========================================================================

class TestNoOpNeo4jClient:
    async def test_connect(self):
        client = NoOpNeo4jClient()
        await client.connect()  # No-op

    async def test_close(self):
        client = NoOpNeo4jClient()
        await client.close()

    async def test_execute_query(self):
        client = NoOpNeo4jClient()
        result = await client.execute_query("RETURN 1")
        assert result == []

    async def test_execute_write(self):
        client = NoOpNeo4jClient()
        result = await client.execute_write("CREATE (n:Test)")
        assert result["nodes_created"] == 0

    async def test_execute_batch(self):
        client = NoOpNeo4jClient()
        result = await client.execute_batch([("Q1", None), ("Q2", None)])
        assert len(result) == 2

    async def test_health_check(self):
        client = NoOpNeo4jClient()
        assert await client.health_check() is True

    async def test_execute_unwind_batch_empty(self):
        client = NoOpNeo4jClient()
        result = await client.execute_unwind_batch("Q", param_name="items", items=[])
        assert result == []

    async def test_execute_unwind_batch_nonempty(self):
        client = NoOpNeo4jClient()
        result = await client.execute_unwind_batch("Q", param_name="items", items=[{"a": 1}])
        assert len(result) == 1


class TestNoOpSession:
    async def test_run(self):
        session = NoOpSession()
        result = await session.run("RETURN 1")
        assert isinstance(result, NoOpResult)

    async def test_begin_transaction(self):
        session = NoOpSession()
        tx = await session.begin_transaction()
        assert isinstance(tx, NoOpTransaction)


class TestNoOpResult:
    async def test_consume(self):
        result = NoOpResult()
        summary = await result.consume()
        assert summary.counters.nodes_created == 0

    async def test_async_iteration(self):
        result = NoOpResult()
        items = []
        async for item in result:
            items.append(item)
        assert items == []


# ===========================================================================
# EntityResolver
# ===========================================================================

class TestBasicNormalize:
    def test_known_abbreviation(self):
        assert _basic_normalize("k8s") == "Kubernetes"
        assert _basic_normalize("pg") == "PostgreSQL"

    def test_unknown_returns_original(self):
        assert _basic_normalize("unknown_term") == "unknown_term"

    def test_case_insensitive(self):
        assert _basic_normalize("K8S") == "Kubernetes"


class TestEntityResolver:
    def setup_method(self):
        self.resolver = EntityResolver()

    async def test_resolve_known_abbreviation(self):
        result = await self.resolver.resolve("k8s", "kb1")
        assert result.canonical_name == "Kubernetes"
        assert result.confidence == 1.0

    async def test_resolve_unknown_returns_original(self):
        result = await self.resolver.resolve("my_custom_term", "kb1")
        assert result.canonical_name == "my_custom_term"
        assert result.resolution_stage == ResolutionStage.RULE_BASED
        assert result.confidence == 0.5

    async def test_resolve_rule_based(self):
        result = await self.resolver.resolve("pg", "kb1")
        assert result.canonical_name == "PostgreSQL"

    async def test_resolve_with_glossary(self):
        glossary = AsyncMock()
        glossary.get_by_term = AsyncMock(return_value=None)
        glossary.list_by_kb = AsyncMock(return_value=[])
        resolver = EntityResolver(glossary_repo=glossary)

        result = await resolver.resolve("unknown", "kb1")
        assert result.canonical_name == "unknown"

    async def test_resolve_with_glossary_match(self):
        term = MagicMock()
        term.term = "Kubernetes"
        term.id = "t1"
        glossary = AsyncMock()
        glossary.get_by_term = AsyncMock(return_value=term)
        resolver = EntityResolver(glossary_repo=glossary)

        result = await resolver.resolve("kubernetes", "kb1")
        assert result.canonical_name == "Kubernetes"
        assert result.resolution_stage == ResolutionStage.GLOSSARY

    async def test_resolve_batch(self):
        result = await self.resolver.resolve_batch(
            [("k8s", EntityType.SYSTEM), ("pg", EntityType.SYSTEM)],
            kb_id="kb1",
        )
        assert len(result) == 2
        assert result[0].canonical_name == "Kubernetes"
        assert result[1].canonical_name == "PostgreSQL"


# ===========================================================================
# MultiHopSearcher
# ===========================================================================

class TestMultiHopSearcher:
    async def test_find_related_no_client(self):
        searcher = MultiHopSearcher()
        result = await searcher.find_related(["test"])
        assert result == []

    async def test_find_related_with_neo4j(self):
        neo4j = AsyncMock()
        neo4j.execute_query.return_value = [
            {
                "id": "doc1", "name": "Doc 1", "type": "Document",
                "distance": 1, "relation_types": ["REFERENCES"], "properties": {},
            },
        ]
        searcher = MultiHopSearcher(neo4j_client=neo4j)

        result = await searcher.find_related(["seed"])
        assert len(result) == 1
        assert result[0].id == "doc1"

    async def test_find_related_with_repository(self):
        repo = AsyncMock()
        repo.find_related_chunks.return_value = {"http://doc1", "http://doc2"}
        searcher = MultiHopSearcher(graph_repository=repo)

        with patch("src.stores.neo4j.lucene_utils.build_lucene_or_query", return_value="test"):
            result = await searcher.find_related(["test"])
        assert len(result) == 2

    async def test_find_experts_no_client(self):
        searcher = MultiHopSearcher()
        result = await searcher.find_experts("k8s")
        assert result == []

    async def test_find_experts_with_neo4j(self):
        neo4j = AsyncMock()
        neo4j.execute_query.return_value = [
            {"name": "Alice", "email": "a@b.com", "doc_count": 5, "topics": ["k8s"], "departments": ["SRE"]},
        ]
        searcher = MultiHopSearcher(neo4j_client=neo4j)

        result = await searcher.find_experts("k8s")
        assert len(result) == 1
        assert result[0].name == "Alice"

    async def test_search_related_no_client(self):
        searcher = MultiHopSearcher()
        result = await searcher.search_related("doc1")
        assert result == []

    async def test_get_knowledge_path_no_client(self):
        searcher = MultiHopSearcher()
        result = await searcher.get_knowledge_path("doc1", "doc2")
        assert result is None

    async def test_get_knowledge_path_found(self):
        neo4j = AsyncMock()
        neo4j.execute_query.return_value = [
            {"path_length": 2, "nodes": [{"id": "d1"}, {"id": "d2"}], "relationships": ["REFERENCES"]},
        ]
        searcher = MultiHopSearcher(neo4j_client=neo4j)

        result = await searcher.get_knowledge_path("d1", "d2")
        assert result is not None
        assert result.path_length == 2

    async def test_find_similar_documents_no_client(self):
        searcher = MultiHopSearcher()
        result = await searcher.find_similar_documents("doc1")
        assert result == []

    async def test_find_similar_documents_with_neo4j(self):
        neo4j = AsyncMock()
        neo4j.execute_query.return_value = [
            {"id": "doc2", "title": "Doc 2", "kb_id": "kb1", "shared_topics": ["k8s"], "overlap_count": 3},
        ]
        searcher = MultiHopSearcher(neo4j_client=neo4j)

        result = await searcher.find_similar_documents("doc1")
        assert len(result) == 1
        assert result[0]["overlap_count"] == 3


# ===========================================================================
# Graph Schema
# ===========================================================================

class TestGraphSchema:
    def test_schema_constants_exist(self):
        from src.stores.neo4j.schema import (
            NODE_TYPES,
            RELATION_TYPES,
            GRAPH_CONSTRAINTS,
            GRAPH_FULLTEXT_INDEXES,
            CARDINALITY_RULES,
        )
        assert isinstance(NODE_TYPES, dict)
        assert isinstance(RELATION_TYPES, dict)
        assert isinstance(GRAPH_CONSTRAINTS, list)
        assert isinstance(GRAPH_FULLTEXT_INDEXES, list)
        assert len(GRAPH_FULLTEXT_INDEXES) > 0
        assert "OWNED_BY" in CARDINALITY_RULES

    async def test_apply_schema(self):
        from src.stores.neo4j.schema import apply_schema

        client = AsyncMock()
        client.execute_write.return_value = {"nodes_created": 0}

        result = await apply_schema(client)
        assert "constraints_created" in result
        assert "indexes_created" in result
        assert "fulltext_indexes_created" in result
        assert client.execute_write.await_count > 0

    async def test_apply_schema_handles_already_exists(self):
        from src.stores.neo4j.schema import apply_schema

        client = AsyncMock()
        client.execute_write.side_effect = RuntimeError("already exists")

        result = await apply_schema(client)
        # Should not raise, and counts should be 0
        assert result["constraints_created"] == 0


class TestRelatedNodeDataclass:
    def test_creation(self):
        node = RelatedNode(
            id="n1", name="Node 1", type="Document",
            distance=1, relation_types=["REFERENCES"],
            properties={}, relevance_score=0.9,
        )
        assert node.id == "n1"
        assert node.relevance_score == 0.9


class TestExpertDataclass:
    def test_creation(self):
        expert = Expert(
            name="Alice", email="a@b.com",
            document_count=5, topics=["k8s"],
            departments=["SRE"],
        )
        assert expert.name == "Alice"


class TestKnowledgePathDataclass:
    def test_creation(self):
        path = KnowledgePath(
            from_doc_id="d1", to_doc_id="d2",
            path_length=2, nodes=[], relationships=[],
        )
        assert path.path_length == 2
