"""Extractor-side schema integration — signature + hallucination drop."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.pipelines.graphrag.extractor import GraphRAGExtractor
from src.pipelines.graphrag.schema_resolver import invalidate_cache
from src.pipelines.graphrag.schema_types import SchemaProfile


@pytest.fixture(autouse=True)
def _clean_cache():
    invalidate_cache()
    yield
    invalidate_cache()


class TestExtractorSignature:
    def test_extract_accepts_source_type_kwarg(self):
        import inspect
        sig = inspect.signature(GraphRAGExtractor.extract)
        assert "source_type" in sig.parameters
        assert "schema" in sig.parameters

    def test_source_type_defaults_to_none(self):
        import inspect
        sig = inspect.signature(GraphRAGExtractor.extract)
        assert sig.parameters["source_type"].default is None
        assert sig.parameters["schema"].default is None


class TestHallucinationDrop:
    """LLM 이 schema outside 의 label 을 뽑으면 silent drop 되어야 한다."""

    def test_out_of_schema_nodes_dropped(self, monkeypatch):
        extractor = GraphRAGExtractor()
        schema = SchemaProfile(
            nodes=("Person", "Team"),
            relationships=("MEMBER_OF",),
            prompt_focus="x",
        )
        fake_json = (
            '{"nodes":['
            '{"id":"Alice","type":"Person"},'
            '{"id":"Intruder","type":"FakeType"}'
            '],"relationships":[]}'
        )

        mock_llm = MagicMock()
        mock_llm.invoke = MagicMock(return_value=fake_json)
        monkeypatch.setattr(extractor, "_get_llm", lambda: mock_llm)

        result = extractor.extract(
            document="doc",
            source_title="t",
            source_page_id="p",
            source_updated_at=None,
            kb_id=None,
            schema=schema,
        )

        types = {n.type for n in result.nodes}
        assert "Person" in types
        assert "FakeType" not in types, "schema-outside label must be dropped"

    def test_out_of_schema_relationships_dropped(self, monkeypatch):
        extractor = GraphRAGExtractor()
        schema = SchemaProfile(
            nodes=("Person", "Team"),
            relationships=("MEMBER_OF",),
            prompt_focus="x",
        )
        fake_json = (
            '{"nodes":['
            '{"id":"Alice","type":"Person"},'
            '{"id":"Red","type":"Team"}'
            '],"relationships":['
            '{"source":"Alice","type":"MEMBER_OF","target":"Red"},'
            '{"source":"Alice","type":"BELONGS_TO","target":"Red"}'
            ']}'
        )
        mock_llm = MagicMock()
        mock_llm.invoke = MagicMock(return_value=fake_json)
        monkeypatch.setattr(extractor, "_get_llm", lambda: mock_llm)

        result = extractor.extract(
            document="doc",
            source_title="t",
            source_page_id="p",
            source_updated_at=None,
            kb_id=None,
            schema=schema,
        )
        rel_types = {r.type for r in result.relationships}
        assert "MEMBER_OF" in rel_types
        assert "BELONGS_TO" not in rel_types

    def test_schema_preserves_allowed_types(self, monkeypatch):
        """Schema-valid types pass through unchanged."""
        extractor = GraphRAGExtractor()
        schema = SchemaProfile(
            nodes=("Person", "Team"),
            relationships=("MEMBER_OF",),
            prompt_focus="x",
        )
        fake_json = (
            '{"nodes":['
            '{"id":"Alice","type":"Person"},'
            '{"id":"Red","type":"Team"}'
            '],"relationships":['
            '{"source":"Alice","type":"MEMBER_OF","target":"Red"}'
            ']}'
        )
        mock_llm = MagicMock()
        mock_llm.invoke = MagicMock(return_value=fake_json)
        monkeypatch.setattr(extractor, "_get_llm", lambda: mock_llm)

        result = extractor.extract(
            document="doc",
            source_title="t",
            source_page_id="p",
            source_updated_at=None,
            kb_id=None,
            schema=schema,
        )
        types = {n.type for n in result.nodes}
        assert types == {"Person", "Team"}
        rel_types = {r.type for r in result.relationships}
        assert rel_types == {"MEMBER_OF"}
