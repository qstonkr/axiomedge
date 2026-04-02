"""Comprehensive unit tests for graphrag_extractor.py — maximizing line coverage.

Tests data classes, schema helpers, prompt building, response parsing,
date comparison, Cypher safety, and batch processor.
No external services required (Neo4j/Ollama/SageMaker mocked).
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from src.pipeline.graphrag_extractor import (
    ALLOWED_NODES,
    ALLOWED_RELATIONSHIPS,
    DEFAULT_SCHEMA_PROFILE,
    ExtractionResult,
    GraphNode,
    GraphRAGBatchProcessor,
    GraphRAGExtractor,
    GraphRelationship,
    HISTORY_RELATIONSHIP_MAP,
    KB_SCHEMA_PROFILES,
    KOREAN_EXTRACTION_PROMPT,
    _is_safe_cypher_label,
    build_extraction_prompt,
    get_kb_schema,
)


# =========================================================================
# Cypher Safety
# =========================================================================

class TestIsSafeCypherLabel:
    def test_valid_labels(self):
        assert _is_safe_cypher_label("Person") is True
        assert _is_safe_cypher_label("MEMBER_OF") is True
        assert _is_safe_cypher_label("_private") is True
        assert _is_safe_cypher_label("A") is True

    def test_invalid_labels(self):
        assert _is_safe_cypher_label("") is False
        assert _is_safe_cypher_label("123abc") is False
        assert _is_safe_cypher_label("has space") is False
        assert _is_safe_cypher_label("semi;colon") is False
        assert _is_safe_cypher_label("drop()") is False


# =========================================================================
# Schema
# =========================================================================

class TestSchema:
    def test_allowed_nodes_non_empty(self):
        assert len(ALLOWED_NODES) > 0
        assert "Person" in ALLOWED_NODES
        assert "System" in ALLOWED_NODES

    def test_allowed_relationships_non_empty(self):
        assert len(ALLOWED_RELATIONSHIPS) > 0
        assert "MEMBER_OF" in ALLOWED_RELATIONSHIPS

    def test_history_mapping(self):
        assert HISTORY_RELATIONSHIP_MAP["MEMBER_OF"] == "WAS_MEMBER_OF"
        assert HISTORY_RELATIONSHIP_MAP["MANAGES"] == "PREVIOUSLY_MANAGED"

    def test_get_kb_schema_known(self):
        for kb_id in KB_SCHEMA_PROFILES:
            schema = get_kb_schema(kb_id)
            assert "nodes" in schema
            assert "relationships" in schema
            assert "prompt_focus" in schema

    def test_get_kb_schema_unknown(self):
        schema = get_kb_schema("unknown-kb")
        assert schema == DEFAULT_SCHEMA_PROFILE


# =========================================================================
# build_extraction_prompt
# =========================================================================

class TestBuildExtractionPrompt:
    def test_default_prompt(self):
        prompt = build_extraction_prompt("some document")
        assert "엔티티" in prompt
        assert "관계" in prompt
        assert "{document}" in prompt  # should have the placeholder

    def test_kb_specific_prompt(self):
        prompt = build_extraction_prompt("doc text", kb_id="a-ari")
        assert "점포" in prompt or "Store" in prompt

    def test_unknown_kb_uses_default(self):
        prompt = build_extraction_prompt("doc", kb_id="nonexistent")
        assert "엔티티" in prompt

    def test_prompt_has_format_placeholder(self):
        prompt = build_extraction_prompt("test doc")
        # The prompt should be formattable with document=...
        formatted = prompt.format(document="My document text")
        assert "My document text" in formatted

    def test_all_kb_profiles(self):
        for kb_id in KB_SCHEMA_PROFILES:
            prompt = build_extraction_prompt("test", kb_id=kb_id)
            assert len(prompt) > 100


# =========================================================================
# GraphNode
# =========================================================================

class TestGraphNode:
    def test_basic(self):
        n = GraphNode(id="홍길동", type="Person")
        assert n.id == "홍길동"
        assert n.type == "Person"
        assert n.properties == {}

    def test_with_properties(self):
        n = GraphNode(id="시스템A", type="System", properties={"role": "main"})
        assert n.properties["role"] == "main"

    def test_to_dict(self):
        n = GraphNode(id="팀A", type="Team", properties={"dept": "IT"})
        d = n.to_dict()
        assert d["id"] == "팀A"
        assert d["type"] == "Team"
        assert d["dept"] == "IT"


# =========================================================================
# GraphRelationship
# =========================================================================

class TestGraphRelationship:
    def test_basic(self):
        r = GraphRelationship(source="A", target="B", type="MEMBER_OF")
        assert r.source == "A"
        assert r.target == "B"
        assert r.type == "MEMBER_OF"

    def test_to_dict(self):
        r = GraphRelationship(
            source="홍길동", target="개발팀", type="MEMBER_OF",
            properties={"since": "2024"},
        )
        d = r.to_dict()
        assert d["source"] == "홍길동"
        assert d["target"] == "개발팀"
        assert d["type"] == "MEMBER_OF"
        assert d["since"] == "2024"


# =========================================================================
# ExtractionResult
# =========================================================================

class TestExtractionResult:
    def test_defaults(self):
        r = ExtractionResult()
        assert r.nodes == []
        assert r.relationships == []
        assert r.node_count == 0
        assert r.relationship_count == 0
        assert r.source_document is None
        assert r.source_page_id is None
        assert r.source_updated_at is None
        assert r.kb_id is None
        assert r.raw_response is None

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
            relationships=[GraphRelationship("A", "B", "MANAGES")],
            source_document="Test Doc",
            source_page_id="p1",
            source_updated_at="2024-01-01",
        )
        d = r.to_dict()
        assert len(d["nodes"]) == 1
        assert len(d["relationships"]) == 1
        assert d["source_document"] == "Test Doc"


# =========================================================================
# GraphRAGExtractor
# =========================================================================

class TestGraphRAGExtractor:
    def test_init_defaults(self):
        with patch.dict("os.environ", {}, clear=False):
            ext = GraphRAGExtractor()
            assert ext.ollama_base_url is not None
            assert ext.ollama_model is not None
            assert ext._llm is None
            assert ext._neo4j_driver is None

    def test_init_custom(self):
        ext = GraphRAGExtractor(
            ollama_base_url="http://custom:11434",
            ollama_model="custom-model",
            neo4j_uri="bolt://custom:7687",
            neo4j_user="user",
            neo4j_password="pass",
        )
        assert ext.ollama_base_url == "http://custom:11434"
        assert ext.ollama_model == "custom-model"

    def test_init_with_injected_clients(self):
        mock_llm = MagicMock()
        mock_driver = MagicMock()
        ext = GraphRAGExtractor(llm_client=mock_llm, neo4j_driver=mock_driver)
        assert ext._llm is mock_llm
        assert ext._neo4j_driver is mock_driver


class TestGraphRAGExtractorParseResponse:
    def setup_method(self):
        self.ext = GraphRAGExtractor(llm_client=MagicMock(), neo4j_driver=MagicMock())

    def test_valid_json(self):
        content = json.dumps({
            "nodes": [
                {"id": "홍길동", "type": "Person"},
                {"id": "개발팀", "type": "Team"},
            ],
            "relationships": [
                {"source": "홍길동", "type": "MEMBER_OF", "target": "개발팀"},
            ],
        })
        result = self.ext._parse_response(content)
        assert result.node_count == 2
        assert result.relationship_count == 1

    def test_json_in_code_block(self):
        content = '```json\n{"nodes": [{"id": "A", "type": "Person"}], "relationships": []}\n```'
        result = self.ext._parse_response(content)
        assert result.node_count == 1

    def test_no_json(self):
        result = self.ext._parse_response("No JSON here at all")
        assert result.node_count == 0
        assert result.relationship_count == 0

    def test_invalid_json(self):
        result = self.ext._parse_response("{broken json here}")
        assert result.node_count == 0

    def test_unknown_node_type_filtered(self):
        content = json.dumps({
            "nodes": [{"id": "x", "type": "UnknownType"}],
            "relationships": [],
        })
        result = self.ext._parse_response(content)
        assert result.node_count == 0

    def test_empty_node_id_filtered(self):
        content = json.dumps({
            "nodes": [{"id": "", "type": "Person"}],
            "relationships": [],
        })
        result = self.ext._parse_response(content)
        assert result.node_count == 0

    def test_invalid_relationship_type_defaults(self):
        content = json.dumps({
            "nodes": [{"id": "A", "type": "Person"}, {"id": "B", "type": "Team"}],
            "relationships": [{"source": "A", "type": "INVALID_TYPE", "target": "B"}],
        })
        result = self.ext._parse_response(content)
        assert result.relationship_count == 1
        assert result.relationships[0].type == "RELATED_TO"

    def test_dangling_reference(self):
        """Relationships referencing non-existent nodes should still be added with a warning."""
        content = json.dumps({
            "nodes": [{"id": "A", "type": "Person"}],
            "relationships": [{"source": "A", "type": "MANAGES", "target": "NonExistent"}],
        })
        result = self.ext._parse_response(content)
        assert result.relationship_count == 1

    def test_empty_source_or_target_filtered(self):
        content = json.dumps({
            "nodes": [{"id": "A", "type": "Person"}],
            "relationships": [{"source": "", "type": "MANAGES", "target": "A"}],
        })
        result = self.ext._parse_response(content)
        assert result.relationship_count == 0

    def test_node_properties_preserved(self):
        content = json.dumps({
            "nodes": [{"id": "A", "type": "Person", "role": "manager"}],
            "relationships": [],
        })
        result = self.ext._parse_response(content)
        assert result.nodes[0].properties.get("role") == "manager"

    def test_relationship_properties_preserved(self):
        content = json.dumps({
            "nodes": [{"id": "A", "type": "Person"}, {"id": "B", "type": "Team"}],
            "relationships": [{"source": "A", "type": "MEMBER_OF", "target": "B", "since": "2024"}],
        })
        result = self.ext._parse_response(content)
        assert result.relationships[0].properties.get("since") == "2024"


class TestGraphRAGExtractorExtract:
    def test_extract_success(self):
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = json.dumps({
            "nodes": [{"id": "A", "type": "Person"}],
            "relationships": [],
        })
        ext = GraphRAGExtractor(llm_client=mock_llm, neo4j_driver=MagicMock())
        result = ext.extract("document text", source_title="Test")
        assert result.node_count == 1
        assert result.source_document == "Test"

    def test_extract_llm_failure(self):
        mock_llm = MagicMock()
        mock_llm.invoke.side_effect = Exception("LLM timeout")
        ext = GraphRAGExtractor(llm_client=mock_llm, neo4j_driver=MagicMock())
        result = ext.extract("text", source_title="T")
        assert result.node_count == 0
        assert "LLM timeout" in result.raw_response

    def test_extract_truncates_long_document(self):
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = '{"nodes":[], "relationships":[]}'
        ext = GraphRAGExtractor(llm_client=mock_llm, neo4j_driver=MagicMock())
        long_doc = "x" * 100000
        ext.extract(long_doc, max_length=1000)
        # Check that the document was truncated
        call_kwargs = mock_llm.invoke.call_args
        assert len(call_kwargs.kwargs.get("document", "")) <= 1000

    def test_extract_with_kb_id(self):
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = '{"nodes":[], "relationships":[]}'
        ext = GraphRAGExtractor(llm_client=mock_llm, neo4j_driver=MagicMock())
        result = ext.extract("text", kb_id="a-ari")
        assert result.kb_id == "a-ari"


class TestGraphRAGExtractorIsNewer:
    def setup_method(self):
        self.ext = GraphRAGExtractor(llm_client=MagicMock(), neo4j_driver=MagicMock())

    def test_newer(self):
        assert self.ext._is_newer("2024-06-01", "2024-01-01") is True

    def test_older(self):
        assert self.ext._is_newer("2024-01-01", "2024-06-01") is False

    def test_same(self):
        assert self.ext._is_newer("2024-01-01", "2024-01-01") is False

    def test_with_time(self):
        assert self.ext._is_newer("2024-01-01T12:00:00", "2024-01-01T06:00:00") is True

    def test_invalid_date(self):
        # Should return True (favor new document)
        assert self.ext._is_newer("invalid", "2024-01-01") is True

    def test_with_z_suffix(self):
        assert self.ext._is_newer("2024-06-01T00:00:00Z", "2024-01-01T00:00:00Z") is True


class TestGraphRAGExtractorClose:
    def test_close_with_driver(self):
        mock_driver = MagicMock()
        ext = GraphRAGExtractor(llm_client=MagicMock(), neo4j_driver=mock_driver)
        ext.close()
        mock_driver.close.assert_called_once()
        assert ext._neo4j_driver is None

    def test_close_without_driver(self):
        ext = GraphRAGExtractor(llm_client=MagicMock())
        ext.close()  # Should not raise


class TestGraphRAGExtractorGetLlm:
    def test_ollama_by_default(self):
        ext = GraphRAGExtractor()
        with patch.dict("os.environ", {"GRAPHRAG_USE_SAGEMAKER": "false"}):
            with patch("src.pipeline.graphrag_extractor._OllamaLLMClient") as mock_cls:
                mock_cls.return_value = MagicMock()
                llm = ext._get_llm()
                assert llm is not None

    def test_sagemaker_when_enabled(self):
        ext = GraphRAGExtractor()
        with patch.dict("os.environ", {"GRAPHRAG_USE_SAGEMAKER": "true"}):
            with patch("src.pipeline.graphrag_extractor._SageMakerLLMClient") as mock_cls:
                mock_cls.return_value = MagicMock()
                llm = ext._get_llm()
                assert llm is not None

    def test_cached_llm(self):
        mock_llm = MagicMock()
        ext = GraphRAGExtractor(llm_client=mock_llm)
        assert ext._get_llm() is mock_llm


# =========================================================================
# GraphRAGBatchProcessor
# =========================================================================

class TestGraphRAGBatchProcessor:
    def test_init_default(self):
        with patch.object(GraphRAGExtractor, "__init__", return_value=None):
            bp = GraphRAGBatchProcessor()
            assert bp.results == []

    def test_init_custom(self):
        mock_ext = MagicMock(spec=GraphRAGExtractor)
        bp = GraphRAGBatchProcessor(extractor=mock_ext)
        assert bp.extractor is mock_ext

    def test_process_documents_success(self):
        mock_ext = MagicMock(spec=GraphRAGExtractor)
        mock_ext.extract.return_value = ExtractionResult(
            nodes=[GraphNode("A", "Person")],
            relationships=[GraphRelationship("A", "B", "MANAGES")],
        )
        mock_ext.save_to_neo4j.return_value = {
            "relationships_archived": 0,
        }
        bp = GraphRAGBatchProcessor(extractor=mock_ext)

        docs = [{"content": "text", "title": "T", "page_id": "1"}]
        stats = bp.process_documents(docs)
        assert stats["success"] == 1
        assert stats["total_nodes"] == 1

    def test_process_documents_failure(self):
        mock_ext = MagicMock(spec=GraphRAGExtractor)
        mock_ext.extract.side_effect = Exception("fail")
        bp = GraphRAGBatchProcessor(extractor=mock_ext)

        stats = bp.process_documents([{"content": "x"}])
        assert stats["failed"] == 1

    def test_process_without_neo4j(self):
        mock_ext = MagicMock(spec=GraphRAGExtractor)
        mock_ext.extract.return_value = ExtractionResult(
            nodes=[GraphNode("A", "Person")],
            relationships=[],
        )
        bp = GraphRAGBatchProcessor(extractor=mock_ext)

        stats = bp.process_documents([{"content": "x"}], save_to_neo4j=False)
        assert stats["success"] == 1
        mock_ext.save_to_neo4j.assert_not_called()

    def test_process_skip_save_when_no_nodes(self):
        mock_ext = MagicMock(spec=GraphRAGExtractor)
        mock_ext.extract.return_value = ExtractionResult()  # empty
        bp = GraphRAGBatchProcessor(extractor=mock_ext)

        stats = bp.process_documents([{"content": "x"}], save_to_neo4j=True)
        assert stats["success"] == 1
        mock_ext.save_to_neo4j.assert_not_called()

    def test_get_all_nodes(self):
        mock_ext = MagicMock(spec=GraphRAGExtractor)
        bp = GraphRAGBatchProcessor(extractor=mock_ext)
        bp.results = [
            ExtractionResult(nodes=[GraphNode("A", "Person"), GraphNode("B", "Team")]),
            ExtractionResult(nodes=[GraphNode("C", "System")]),
        ]
        nodes = bp.get_all_nodes()
        assert len(nodes) == 3

    def test_get_all_relationships(self):
        mock_ext = MagicMock(spec=GraphRAGExtractor)
        bp = GraphRAGBatchProcessor(extractor=mock_ext)
        bp.results = [
            ExtractionResult(relationships=[
                GraphRelationship("A", "B", "MEMBER_OF"),
            ]),
            ExtractionResult(relationships=[
                GraphRelationship("C", "D", "MANAGES"),
                GraphRelationship("E", "F", "OWNS"),
            ]),
        ]
        rels = bp.get_all_relationships()
        assert len(rels) == 3

    def test_process_with_kb_id(self):
        mock_ext = MagicMock(spec=GraphRAGExtractor)
        mock_ext.extract.return_value = ExtractionResult()
        bp = GraphRAGBatchProcessor(extractor=mock_ext)
        bp.process_documents([{"content": "x"}], kb_id="test-kb", save_to_neo4j=False)
        call_kwargs = mock_ext.extract.call_args
        assert call_kwargs.kwargs.get("kb_id") == "test-kb"
