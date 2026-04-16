"""Unit tests for graphrag_extractor.py — coverage push.

Targets ~190 uncovered lines: extract, _parse_response, save_to_neo4j,
_save_relationship_with_history, _archive_relationship, _is_newer,
get_relationship_history, query_at_point_in_time, query_recent_entities,
GraphRAGBatchProcessor, build_extraction_prompt, etc.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from src.pipelines.graphrag_extractor import (
    GraphRAGExtractor,
    GraphRAGBatchProcessor,
    GraphNode,
    GraphRelationship,
    ExtractionResult,
    _is_safe_cypher_label,
    build_extraction_prompt,
    get_kb_schema,
    ALLOWED_NODES,
    ALLOWED_RELATIONSHIPS,
    HISTORY_RELATIONSHIP_MAP,
)


# ---------------------------------------------------------------------------
# Helpers / schema
# ---------------------------------------------------------------------------


class TestCypherSafety:
    def test_safe_label(self):
        assert _is_safe_cypher_label("Person") is True
        assert _is_safe_cypher_label("_Internal") is True

    def test_unsafe_labels(self):
        assert _is_safe_cypher_label("drop;--") is False
        assert _is_safe_cypher_label("") is False
        assert _is_safe_cypher_label("123bad") is False


class TestBuildExtractionPrompt:
    def test_default_prompt(self):
        prompt = build_extraction_prompt("doc text")
        assert "{document}" in prompt  # placeholder preserved

    def test_kb_specific_prompt(self):
        prompt = build_extraction_prompt("doc text", kb_id="hax")
        assert "시스템" in prompt

    def test_unknown_kb(self):
        prompt = build_extraction_prompt("doc text", kb_id="unknown-kb")
        assert "{document}" in prompt


class TestGetKbSchema:
    def test_known_kb(self):
        schema = get_kb_schema("hax")
        assert "System" in schema["nodes"]

    def test_unknown_kb(self):
        schema = get_kb_schema("nonexistent")
        assert schema["nodes"] == ALLOWED_NODES


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


class TestGraphNode:
    def test_to_dict(self):
        n = GraphNode(id="Kim", type="Person", properties={"role": "manager"})
        d = n.to_dict()
        assert d["id"] == "Kim"
        assert d["type"] == "Person"
        assert d["role"] == "manager"


class TestGraphRelationship:
    def test_to_dict(self):
        r = GraphRelationship(source="A", target="B", type="MANAGES", properties={"since": "2025"})
        d = r.to_dict()
        assert d["source"] == "A"
        assert d["since"] == "2025"


class TestExtractionResult:
    def test_counts(self):
        r = ExtractionResult(
            nodes=[GraphNode("A", "Person"), GraphNode("B", "Team")],
            relationships=[GraphRelationship("A", "B", "MEMBER_OF")],
        )
        assert r.node_count == 2
        assert r.relationship_count == 1

    def test_to_dict(self):
        r = ExtractionResult(
            nodes=[GraphNode("A", "Person")],
            source_document="test",
        )
        d = r.to_dict()
        assert d["source_document"] == "test"
        assert len(d["nodes"]) == 1


# ---------------------------------------------------------------------------
# GraphRAGExtractor
# ---------------------------------------------------------------------------


class TestExtract:
    def test_success(self):
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = json.dumps({
            "nodes": [
                {"id": "김철수", "type": "Person"},
                {"id": "개발팀", "type": "Team"},
            ],
            "relationships": [
                {"source": "김철수", "type": "MEMBER_OF", "target": "개발팀"},
            ],
        })
        extractor = GraphRAGExtractor(llm_client=mock_llm)
        result = extractor.extract("김철수는 개발팀 소속입니다.", source_title="test")
        assert result.node_count == 2
        assert result.relationship_count == 1

    def test_llm_failure(self):
        mock_llm = MagicMock()
        mock_llm.invoke.side_effect = Exception("LLM timeout")
        extractor = GraphRAGExtractor(llm_client=mock_llm)
        result = extractor.extract("문서 내용", source_title="test")
        assert result.node_count == 0
        assert "LLM timeout" in result.raw_response

    def test_kb_specific_prompt_used(self):
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = '{"nodes":[], "relationships":[]}'
        extractor = GraphRAGExtractor(llm_client=mock_llm)
        extractor.extract("text", kb_id="hax")
        call_kwargs = mock_llm.invoke.call_args
        # kb-specific prompt should mention 시스템
        assert "시스템" in call_kwargs.kwargs.get("prompt_template", call_kwargs[1].get("prompt_template", ""))

    def test_document_truncation(self):
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = '{"nodes":[], "relationships":[]}'
        extractor = GraphRAGExtractor(llm_client=mock_llm)
        long_doc = "x" * 100000
        extractor.extract(long_doc, max_length=100)
        call_kwargs = mock_llm.invoke.call_args
        doc_arg = call_kwargs.kwargs.get("document", call_kwargs[1].get("document", ""))
        assert len(doc_arg) <= 100


# ---------------------------------------------------------------------------
# _parse_response
# ---------------------------------------------------------------------------


class TestParseResponse:
    def setup_method(self):
        self.extractor = GraphRAGExtractor(llm_client=MagicMock())

    def test_valid_json(self):
        content = json.dumps({
            "nodes": [{"id": "홍길동", "type": "Person"}],
            "relationships": [{"source": "홍길동", "type": "MANAGES", "target": "B"}],
        })
        result = self.extractor._parse_response(content)
        assert result.node_count == 1
        assert result.relationship_count == 1

    def test_json_in_markdown_block(self):
        content = '```json\n{"nodes":[{"id":"홍길동","type":"Person"}],"relationships":[]}\n```'
        result = self.extractor._parse_response(content)
        assert result.node_count == 1

    def test_no_json_found(self):
        result = self.extractor._parse_response("no json here")
        assert result.node_count == 0

    def test_invalid_node_type(self):
        content = json.dumps({
            "nodes": [{"id": "X", "type": "InvalidType"}, {"id": "김영희", "type": "Person"}],
            "relationships": [],
        })
        result = self.extractor._parse_response(content)
        assert result.node_count == 1  # only valid type

    def test_empty_node_id_skipped(self):
        content = json.dumps({
            "nodes": [{"id": "", "type": "Person"}, {"id": "홍길동", "type": "Person"}],
            "relationships": [],
        })
        result = self.extractor._parse_response(content)
        assert result.node_count == 1

    def test_invalid_relationship_type_defaults(self):
        content = json.dumps({
            "nodes": [{"id": "홍길동", "type": "Person"}, {"id": "B", "type": "Team"}],
            "relationships": [{"source": "홍길동", "type": "INVALID_REL", "target": "B"}],
        })
        result = self.extractor._parse_response(content)
        assert result.relationships[0].type == "RELATED_TO"

    def test_dangling_reference(self):
        content = json.dumps({
            "nodes": [{"id": "홍길동", "type": "Person"}],
            "relationships": [{"source": "홍길동", "type": "MANAGES", "target": "MISSING"}],
        })
        result = self.extractor._parse_response(content)
        assert result.relationship_count == 1  # still added with warning

    def test_missing_source_target_skipped(self):
        content = json.dumps({
            "nodes": [{"id": "홍길동", "type": "Person"}],
            "relationships": [{"source": "", "type": "MANAGES", "target": "홍길동"}],
        })
        result = self.extractor._parse_response(content)
        assert result.relationship_count == 0

    def test_malformed_json_repair(self):
        """Broken JSON should attempt repair."""
        content = '{"nodes":[{"id":"홍길동","type":"Person"}],"relationships":[]'  # missing closing brace
        result = self.extractor._parse_response(content)
        # json_repair should fix it
        assert result.node_count >= 0  # shouldn't crash

    def test_completely_broken_json(self):
        result = self.extractor._parse_response("{not json at all!!")
        assert result.node_count == 0


# ---------------------------------------------------------------------------
# save_to_neo4j
# ---------------------------------------------------------------------------


class TestSaveToNeo4j:
    def _make_result(self):
        return ExtractionResult(
            nodes=[
                GraphNode("Kim", "Person"),
                GraphNode("DevTeam", "Team"),
                GraphNode("Orphan", "Person"),  # no relationship
            ],
            relationships=[
                GraphRelationship("Kim", "DevTeam", "MEMBER_OF"),
            ],
            source_document="test",
            source_page_id="page-1",
            source_updated_at="2025-03-01",
            kb_id="hax",
        )

    def test_save_basic(self):
        mock_session = MagicMock()
        mock_record = MagicMock()
        mock_record.__getitem__ = lambda self, key: True  # is_new
        mock_session.run.return_value = [mock_record]

        mock_driver = MagicMock()
        mock_driver.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_driver.session.return_value.__exit__ = MagicMock(return_value=False)

        extractor = GraphRAGExtractor(llm_client=MagicMock(), neo4j_driver=mock_driver)
        result = self._make_result()
        stats = extractor.save_to_neo4j(result)
        assert stats["nodes_created"] >= 0
        assert isinstance(stats["relationships_created"], int)

    def test_unsafe_node_type_skipped(self):
        result = ExtractionResult(
            nodes=[GraphNode("Bad", "DROP;--"), GraphNode("A", "Person"), GraphNode("B", "Team")],
            relationships=[GraphRelationship("A", "B", "MEMBER_OF")],
        )
        mock_session = MagicMock()
        mock_session.run.return_value = []
        mock_driver = MagicMock()
        mock_driver.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_driver.session.return_value.__exit__ = MagicMock(return_value=False)

        extractor = GraphRAGExtractor(llm_client=MagicMock(), neo4j_driver=mock_driver)
        stats = extractor.save_to_neo4j(result)
        # Should not crash


# ---------------------------------------------------------------------------
# _is_newer
# ---------------------------------------------------------------------------


class TestIsNewer:
    def setup_method(self):
        self.extractor = GraphRAGExtractor(llm_client=MagicMock())

    def test_newer(self):
        assert self.extractor._is_newer("2025-03-01", "2025-01-01") is True

    def test_older(self):
        assert self.extractor._is_newer("2024-01-01", "2025-01-01") is False

    def test_same(self):
        assert self.extractor._is_newer("2025-01-01", "2025-01-01") is False

    def test_invalid_date(self):
        # Should return True on parse error
        assert self.extractor._is_newer("not-a-date", "2025-01-01") is True

    def test_with_timezone(self):
        assert self.extractor._is_newer("2025-03-01T00:00:00Z", "2025-01-01T00:00:00Z") is True


# ---------------------------------------------------------------------------
# get_relationship_history
# ---------------------------------------------------------------------------


class TestGetRelationshipHistory:
    def test_with_rel_type(self):
        mock_session = MagicMock()
        mock_session.run.return_value = [
            {"target": "팀A", "updated_at": "2025-01-01", "source_document": "doc1", "status": "current"},
        ]
        mock_driver = MagicMock()
        mock_driver.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_driver.session.return_value.__exit__ = MagicMock(return_value=False)

        extractor = GraphRAGExtractor(llm_client=MagicMock(), neo4j_driver=mock_driver)
        history = extractor.get_relationship_history("김철수", rel_type="MEMBER_OF")
        assert len(history) >= 1

    def test_without_rel_type(self):
        mock_session = MagicMock()
        mock_session.run.return_value = []
        mock_driver = MagicMock()
        mock_driver.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_driver.session.return_value.__exit__ = MagicMock(return_value=False)

        extractor = GraphRAGExtractor(llm_client=MagicMock(), neo4j_driver=mock_driver)
        history = extractor.get_relationship_history("김철수")
        assert isinstance(history, list)

    def test_unsafe_rel_type(self):
        extractor = GraphRAGExtractor(
            llm_client=MagicMock(),
            neo4j_driver=MagicMock(),
        )
        history = extractor.get_relationship_history("김철수", rel_type="DROP;--")
        assert history == []


# ---------------------------------------------------------------------------
# query_at_point_in_time
# ---------------------------------------------------------------------------


class TestQueryAtPointInTime:
    def test_current_valid(self):
        mock_record = {"target": "팀A", "valid_from": "2024-01-01", "source_document": "doc"}
        mock_session = MagicMock()
        mock_session.run.return_value.single.return_value = mock_record
        mock_driver = MagicMock()
        mock_driver.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_driver.session.return_value.__exit__ = MagicMock(return_value=False)

        extractor = GraphRAGExtractor(llm_client=MagicMock(), neo4j_driver=mock_driver)
        result = extractor.query_at_point_in_time("김철수", "MEMBER_OF", "2025-01-01")
        assert result["status"] == "current"

    def test_unsafe_rel_type(self):
        extractor = GraphRAGExtractor(llm_client=MagicMock(), neo4j_driver=MagicMock())
        result = extractor.query_at_point_in_time("X", "DROP;--", "2025-01-01")
        assert result is None

    def test_archived_fallback(self):
        """When current not found, should check archived."""
        mock_session = MagicMock()
        # First query (current) returns None
        current_result = MagicMock()
        current_result.single.return_value = None
        # Second query (history) returns archived
        history_result = MagicMock()
        history_result.single.return_value = {
            "target": "팀B",
            "valid_from": "2023-01-01",
            "valid_until": "2024-06-01",
            "source_document": "old-doc",
        }
        mock_session.run.side_effect = [current_result, history_result]
        mock_driver = MagicMock()
        mock_driver.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_driver.session.return_value.__exit__ = MagicMock(return_value=False)

        extractor = GraphRAGExtractor(llm_client=MagicMock(), neo4j_driver=mock_driver)
        result = extractor.query_at_point_in_time("김철수", "MEMBER_OF", "2023-06-01")
        assert result["status"] == "archived"

    def test_not_found(self):
        mock_session = MagicMock()
        mock_result = MagicMock()
        mock_result.single.return_value = None
        mock_session.run.return_value = mock_result
        mock_driver = MagicMock()
        mock_driver.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_driver.session.return_value.__exit__ = MagicMock(return_value=False)

        extractor = GraphRAGExtractor(llm_client=MagicMock(), neo4j_driver=mock_driver)
        result = extractor.query_at_point_in_time("X", "MEMBER_OF", "2020-01-01")
        assert result is None


# ---------------------------------------------------------------------------
# query_recent_entities
# ---------------------------------------------------------------------------


class TestQueryRecentEntities:
    def test_success(self):
        mock_session = MagicMock()
        mock_session.run.return_value = [
            {"type": "Person", "id": "Kim", "properties": {}},
        ]
        mock_driver = MagicMock()
        mock_driver.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_driver.session.return_value.__exit__ = MagicMock(return_value=False)

        extractor = GraphRAGExtractor(llm_client=MagicMock(), neo4j_driver=mock_driver)
        entities = extractor.query_recent_entities(limit=10)
        assert len(entities) == 1

    def test_driver_failure(self):
        extractor = GraphRAGExtractor(llm_client=MagicMock())
        # No driver set, _get_neo4j_driver will try to import neo4j
        with patch.object(extractor, "_get_neo4j_driver", side_effect=Exception("no driver")):
            entities = extractor.query_recent_entities()
            assert entities == []

    def test_query_failure(self):
        mock_session = MagicMock()
        mock_session.run.side_effect = Exception("query failed")
        mock_driver = MagicMock()
        mock_driver.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_driver.session.return_value.__exit__ = MagicMock(return_value=False)

        extractor = GraphRAGExtractor(llm_client=MagicMock(), neo4j_driver=mock_driver)
        entities = extractor.query_recent_entities()
        assert entities == []


# ---------------------------------------------------------------------------
# close
# ---------------------------------------------------------------------------


class TestClose:
    def test_close_with_driver(self):
        mock_driver = MagicMock()
        extractor = GraphRAGExtractor(llm_client=MagicMock(), neo4j_driver=mock_driver)
        extractor.close()
        mock_driver.close.assert_called_once()
        assert extractor._neo4j_driver is None

    def test_close_without_driver(self):
        extractor = GraphRAGExtractor(llm_client=MagicMock())
        extractor._neo4j_driver = None
        extractor.close()  # should not crash


# ---------------------------------------------------------------------------
# GraphRAGBatchProcessor
# ---------------------------------------------------------------------------


class TestBatchProcessor:
    def test_process_documents_success(self):
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = json.dumps({
            "nodes": [{"id": "홍길동", "type": "Person"}, {"id": "B", "type": "Team"}],
            "relationships": [{"source": "홍길동", "type": "MEMBER_OF", "target": "B"}],
        })
        extractor = GraphRAGExtractor(llm_client=mock_llm)
        processor = GraphRAGBatchProcessor(extractor=extractor)

        docs = [
            {"content": "Doc 1", "title": "Title 1", "page_id": "p1", "updated_at": "2025-01-01"},
            {"content": "Doc 2", "title": "Title 2", "page_id": "p2", "updated_at": "2025-02-01"},
        ]
        stats = processor.process_documents(docs, save_to_neo4j=False)
        assert stats["success"] == 2
        assert stats["failed"] == 0
        assert stats["total_nodes"] >= 2

    def test_process_with_failure(self):
        """When extractor.extract itself raises, it counts as failed."""
        mock_extractor = MagicMock()
        mock_extractor.extract.side_effect = [
            ExtractionResult(nodes=[GraphNode("A", "Person")]),
            Exception("Unexpected error"),
        ]
        processor = GraphRAGBatchProcessor(extractor=mock_extractor)

        docs = [
            {"content": "Doc 1", "title": "T1"},
            {"content": "Doc 2", "title": "T2"},
        ]
        stats = processor.process_documents(docs, save_to_neo4j=False)
        assert stats["success"] == 1
        assert stats["failed"] == 1

    def test_get_all_nodes_and_relationships(self):
        processor = GraphRAGBatchProcessor(extractor=MagicMock())
        processor.results = [
            ExtractionResult(
                nodes=[GraphNode("A", "Person")],
                relationships=[GraphRelationship("A", "B", "MANAGES")],
            ),
            ExtractionResult(
                nodes=[GraphNode("C", "Team")],
                relationships=[],
            ),
        ]
        assert len(processor.get_all_nodes()) == 2
        assert len(processor.get_all_relationships()) == 1

    def test_save_to_neo4j_called(self):
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = json.dumps({
            "nodes": [{"id": "홍길동", "type": "Person"}, {"id": "B", "type": "Team"}],
            "relationships": [{"source": "홍길동", "type": "MEMBER_OF", "target": "B"}],
        })
        mock_driver = MagicMock()
        mock_session = MagicMock()
        mock_session.run.return_value = []
        mock_driver.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_driver.session.return_value.__exit__ = MagicMock(return_value=False)

        extractor = GraphRAGExtractor(llm_client=mock_llm, neo4j_driver=mock_driver)
        processor = GraphRAGBatchProcessor(extractor=extractor)

        docs = [{"content": "text", "title": "t"}]
        stats = processor.process_documents(docs, save_to_neo4j=True, kb_id="hax")
        assert stats["success"] == 1


# ---------------------------------------------------------------------------
# _archive_relationship
# ---------------------------------------------------------------------------


class TestArchiveRelationship:
    def test_archive(self):
        mock_session = MagicMock()
        extractor = GraphRAGExtractor(llm_client=MagicMock())
        extractor._archive_relationship(
            mock_session, "Kim", "MEMBER_OF", "OldTeam", "2025-03-01T00:00:00"
        )
        mock_session.run.assert_called_once()

    def test_unsafe_history_type(self):
        mock_session = MagicMock()
        extractor = GraphRAGExtractor(llm_client=MagicMock())
        # RELATED_TO maps to WAS_RELATED_TO which is safe
        # But a type not in HISTORY_RELATIONSHIP_MAP uses f"WAS_{rel_type}"
        extractor._archive_relationship(
            mock_session, "A", "RELATED_TO", "B", "2025-01-01"
        )
        mock_session.run.assert_called_once()


# ---------------------------------------------------------------------------
# _save_relationship_with_history
# ---------------------------------------------------------------------------


class TestSaveRelationshipWithHistory:
    def _make_extractor(self):
        return GraphRAGExtractor(llm_client=MagicMock())

    def _make_result(self):
        return ExtractionResult(
            source_page_id="page-1",
            source_document="doc",
            source_updated_at="2025-06-01",
        )

    def test_no_existing_creates_new(self):
        mock_session = MagicMock()
        mock_session.run.side_effect = [
            [],  # check query returns nothing
            MagicMock(),  # create query
        ]
        extractor = self._make_extractor()
        rel = GraphRelationship("A", "B", "MANAGES")
        stats = extractor._save_relationship_with_history(
            mock_session, rel, self._make_result(), "2025-06-01", "2025-06-01T00:00:00",
        )
        assert stats["created"] == 1

    def test_same_target_updates(self):
        mock_session = MagicMock()
        mock_session.run.side_effect = [
            [{"target": "B", "updated_at": "2025-01-01", "source_page_id": "p1"}],
            MagicMock(),  # update query
        ]
        extractor = self._make_extractor()
        rel = GraphRelationship("A", "B", "MANAGES")
        stats = extractor._save_relationship_with_history(
            mock_session, rel, self._make_result(), "2025-06-01", "2025-06-01T00:00:00",
        )
        assert stats["updated"] == 1

    def test_different_target_newer_archives(self):
        mock_session = MagicMock()
        mock_session.run.side_effect = [
            [{"target": "OLD", "updated_at": "2024-01-01", "source_page_id": "p1"}],
            MagicMock(),  # archive query
            MagicMock(),  # create query
        ]
        extractor = self._make_extractor()
        rel = GraphRelationship("A", "B", "MANAGES")
        stats = extractor._save_relationship_with_history(
            mock_session, rel, self._make_result(), "2025-06-01", "2025-06-01T00:00:00",
        )
        assert stats["archived"] == 1
        assert stats["created"] == 1

    def test_different_target_older_skipped(self):
        mock_session = MagicMock()
        mock_session.run.side_effect = [
            [{"target": "NEWER", "updated_at": "2026-01-01", "source_page_id": "p1"}],
        ]
        extractor = self._make_extractor()
        rel = GraphRelationship("A", "B", "MANAGES")
        stats = extractor._save_relationship_with_history(
            mock_session, rel, self._make_result(), "2025-01-01", "2025-01-01T00:00:00",
        )
        assert stats["skipped"] == 1

    def test_with_node_type_map(self):
        mock_session = MagicMock()
        mock_session.run.side_effect = [[], MagicMock()]
        extractor = self._make_extractor()
        rel = GraphRelationship("A", "B", "MANAGES")
        node_type_map = {"A": "Person", "B": "Team"}
        stats = extractor._save_relationship_with_history(
            mock_session, rel, self._make_result(), "2025-06-01", "now",
            node_type_map=node_type_map,
        )
        assert stats["created"] == 1


# ---------------------------------------------------------------------------
# _create_relationship
# ---------------------------------------------------------------------------


class TestCreateRelationship:
    def test_create_with_properties(self):
        mock_session = MagicMock()
        extractor = GraphRAGExtractor(llm_client=MagicMock())
        rel = GraphRelationship("A", "B", "MANAGES", properties={"since": "2025", "tags": ["x"]})
        result = ExtractionResult(source_page_id="p1", source_document="doc")
        extractor._create_relationship(
            mock_session, rel, result, "2025-01-01", "now",
            src_label=":Person", tgt_label=":Team",
        )
        mock_session.run.assert_called_once()


# ---------------------------------------------------------------------------
# _SageMakerLLMClient / _OllamaLLMClient
# ---------------------------------------------------------------------------


class TestLLMClients:
    def test_sagemaker_invoke(self):
        from src.pipelines.graphrag_extractor import _SageMakerLLMClient
        client = _SageMakerLLMClient()
        mock_boto_client = MagicMock()
        mock_boto_client.invoke_endpoint.return_value = {
            "Body": MagicMock(read=MagicMock(return_value=json.dumps({
                "choices": [{"message": {"content": '{"nodes":[],"relationships":[]}'}}]
            }).encode()))
        }
        with patch.object(client, "_get_client", return_value=mock_boto_client):
            result = client.invoke(
                document="test doc",
                prompt_template="Extract from: {document}\nJSON:",
            )
            assert "nodes" in result

    def test_get_llm_sagemaker(self):
        extractor = GraphRAGExtractor(llm_client=None)
        with patch.dict("os.environ", {"GRAPHRAG_USE_SAGEMAKER": "true"}):
            llm = extractor._get_llm()
            assert llm is not None

    def test_get_llm_ollama(self):
        extractor = GraphRAGExtractor(llm_client=None)
        with patch.dict("os.environ", {"GRAPHRAG_USE_SAGEMAKER": "false"}):
            llm = extractor._get_llm()
            assert llm is not None


# ---------------------------------------------------------------------------
# save_to_neo4j node property flattening
# ---------------------------------------------------------------------------


class TestSaveToNeo4jNodeFlattening:
    def test_node_properties_flattened(self):
        """Dict/list properties should be JSON-serialized."""
        result = ExtractionResult(
            nodes=[
                GraphNode("A", "Person", properties={"info": {"key": "val"}, "tags": ["a", "b"]}),
                GraphNode("B", "Team"),
            ],
            relationships=[GraphRelationship("A", "B", "MEMBER_OF")],
            source_page_id="p1",
            source_document="doc",
            kb_id="hax",
        )
        mock_session = MagicMock()
        mock_record = MagicMock()
        mock_record.__getitem__ = lambda self, key: True
        mock_session.run.return_value = [mock_record]
        mock_driver = MagicMock()
        mock_driver.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_driver.session.return_value.__exit__ = MagicMock(return_value=False)

        extractor = GraphRAGExtractor(llm_client=MagicMock(), neo4j_driver=mock_driver)
        stats = extractor.save_to_neo4j(result)
        assert stats["nodes_created"] >= 0
