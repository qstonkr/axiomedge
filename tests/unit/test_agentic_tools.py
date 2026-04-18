"""Tests for agentic tools — registry + 5 tool implementations."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agentic.protocols import Tool, ToolResult
from src.agentic.tools import (
    DEFAULT_TOOLS,
    GlossaryLookupTool,
    GraphQueryTool,
    KBListerTool,
    QdrantSearchTool,
    TimeResolverTool,
    ToolRegistry,
    build_default_registry,
)


# =============================================================================
# Registry
# =============================================================================


def test_default_registry_has_5_tools() -> None:
    reg = build_default_registry()
    assert len(reg.names()) == 5
    assert reg.names() == sorted([
        "qdrant_search", "graph_query", "glossary_lookup", "time_resolver", "kb_list",
    ])


def test_registry_get_unknown_raises() -> None:
    reg = build_default_registry()
    with pytest.raises(KeyError, match="Unknown tool"):
        reg.get("nonexistent")


def test_registry_specs_returns_metadata_for_each() -> None:
    reg = build_default_registry()
    specs = reg.specs()
    assert len(specs) == 5
    for s in specs:
        assert s.name and s.description and s.args_schema


def test_registry_duplicate_name_rejected() -> None:
    class _A(Tool):
        name = "x"
        description = ""
        args_schema = {}
        async def execute(self, args: dict[str, Any], state: dict[str, Any]) -> ToolResult:
            return ToolResult(success=True, data=None)
    class _B(Tool):
        name = "x"
        description = ""
        args_schema = {}
        async def execute(self, args: dict[str, Any], state: dict[str, Any]) -> ToolResult:
            return ToolResult(success=True, data=None)
    with pytest.raises(ValueError, match="Duplicate"):
        ToolRegistry([_A(), _B()])


def test_registry_contains_operator() -> None:
    reg = build_default_registry()
    assert "qdrant_search" in reg
    assert "nonexistent" not in reg


def test_default_tools_constant_size() -> None:
    assert len(DEFAULT_TOOLS) == 5


# =============================================================================
# QdrantSearchTool
# =============================================================================


@pytest.mark.asyncio
async def test_qdrant_search_missing_query() -> None:
    tool = QdrantSearchTool()
    result = await tool.execute({}, {})
    assert not result.success
    assert "query is required" in (result.error or "")


@pytest.mark.asyncio
async def test_qdrant_search_missing_dependencies() -> None:
    tool = QdrantSearchTool()
    result = await tool.execute({"query": "test"}, {})
    assert not result.success
    assert "qdrant_search or embedder" in (result.error or "")


@pytest.mark.asyncio
async def test_qdrant_search_happy_path() -> None:
    tool = QdrantSearchTool()
    embedder = MagicMock()
    embedder.encode = MagicMock(return_value={"dense_vecs": [[0.1] * 1024], "lexical_weights": [None]})
    chunk = MagicMock(point_id="c1", content="hello", score=0.9, metadata={"k": "v"})
    search_engine = MagicMock()
    search_engine.search = AsyncMock(return_value=[chunk])
    state = {"embedder": embedder, "qdrant_search": search_engine}

    result = await tool.execute({"query": "test", "kb_ids": ["g-espa"], "top_k": 3}, state)
    assert result.success
    assert isinstance(result.data, list) and len(result.data) == 1
    assert result.data[0]["chunk_id"] == "c1"
    assert result.metadata["kb_ids"] == ["g-espa"]


# =============================================================================
# GraphQueryTool
# =============================================================================


@pytest.mark.asyncio
async def test_graph_query_no_repo() -> None:
    tool = GraphQueryTool()
    result = await tool.execute({"mode": "related_chunks"}, {})
    assert not result.success
    assert "graph_repo not initialized" in (result.error or "")


@pytest.mark.asyncio
async def test_graph_query_unknown_mode() -> None:
    tool = GraphQueryTool()
    result = await tool.execute({"mode": "wat"}, {"graph_repo": MagicMock()})
    assert not result.success
    assert "unknown mode" in (result.error or "")


@pytest.mark.asyncio
async def test_graph_query_related_chunks_requires_entity_names() -> None:
    tool = GraphQueryTool()
    result = await tool.execute(
        {"mode": "related_chunks"},
        {"graph_repo": MagicMock()},
    )
    assert not result.success
    assert "entity_names required" in (result.error or "")


@pytest.mark.asyncio
async def test_graph_query_related_chunks_happy_path() -> None:
    tool = GraphQueryTool()
    repo = MagicMock()
    repo.find_related_chunks = AsyncMock(return_value={"uri-1", "uri-2"})
    result = await tool.execute(
        {"mode": "related_chunks", "entity_names": ["신촌점"], "max_hops": 2},
        {"graph_repo": repo},
    )
    assert result.success
    assert result.data == ["uri-1", "uri-2"]
    assert result.metadata["entity_count"] == 1
    assert result.metadata["source_count"] == 2


@pytest.mark.asyncio
async def test_graph_query_entities_happy_path() -> None:
    tool = GraphQueryTool()
    repo = MagicMock()
    repo.search_entities = AsyncMock(return_value=[{"name": "신촌점", "type": "Store"}])
    result = await tool.execute(
        {"mode": "entities", "keywords": ["신촌"]},
        {"graph_repo": repo},
    )
    assert result.success
    assert len(result.data) == 1
    assert result.data[0]["name"] == "신촌점"


# =============================================================================
# GlossaryLookupTool
# =============================================================================


@pytest.mark.asyncio
async def test_glossary_lookup_no_repo() -> None:
    tool = GlossaryLookupTool()
    result = await tool.execute({"term": "PBU"}, {})
    assert not result.success
    assert "glossary repository not initialized" in (result.error or "")


@pytest.mark.asyncio
async def test_glossary_lookup_missing_term() -> None:
    tool = GlossaryLookupTool()
    result = await tool.execute({}, {"glossary": MagicMock()})
    assert not result.success
    assert "term is required" in (result.error or "")


@pytest.mark.asyncio
async def test_glossary_lookup_found() -> None:
    tool = GlossaryLookupTool()
    glossary = MagicMock()
    glossary.get_by_term = AsyncMock(return_value={"term": "PBU", "definition": "x"})
    result = await tool.execute({"term": "PBU"}, {"glossary": glossary})
    assert result.success
    assert result.data["term"] == "PBU"
    assert result.metadata["found"] is True


@pytest.mark.asyncio
async def test_glossary_lookup_not_found() -> None:
    tool = GlossaryLookupTool()
    glossary = MagicMock()
    glossary.get_by_term = AsyncMock(return_value=None)
    result = await tool.execute({"term": "X"}, {"glossary": glossary})
    assert result.success
    assert result.data is None
    assert result.metadata["found"] is False


# =============================================================================
# TimeResolverTool
# =============================================================================


@pytest.mark.asyncio
async def test_time_resolver_missing_expression() -> None:
    tool = TimeResolverTool()
    result = await tool.execute({}, {})
    assert not result.success


@pytest.mark.asyncio
async def test_time_resolver_known_keyword() -> None:
    tool = TimeResolverTool()
    result = await tool.execute({"expression": "차주 회의"}, {})
    assert result.success
    assert "주차" in result.data["resolved"]
    assert result.data["rule"] == "차주"


@pytest.mark.asyncio
async def test_time_resolver_n_days_ago() -> None:
    tool = TimeResolverTool()
    result = await tool.execute({"expression": "3일 전 보고서"}, {})
    assert result.success
    assert "일" in result.data["resolved"]
    assert "3일 전" in result.data["rule"]


@pytest.mark.asyncio
async def test_time_resolver_no_match_returns_passthrough() -> None:
    tool = TimeResolverTool()
    result = await tool.execute({"expression": "고객 응대"}, {})
    assert result.success
    assert result.data["resolved"] == "고객 응대"
    assert result.metadata["matched"] is False


# =============================================================================
# KBListerTool
# =============================================================================


@pytest.mark.asyncio
async def test_kb_list_no_registry() -> None:
    tool = KBListerTool()
    result = await tool.execute({}, {})
    assert not result.success


@pytest.mark.asyncio
async def test_kb_list_returns_active_only() -> None:
    tool = KBListerTool()
    registry = MagicMock()
    registry.list_all = AsyncMock(return_value=[
        {"id": "kb1", "name": "K1", "tier": "global", "status": "active", "document_count": 100},
        {"id": "kb2", "name": "K2", "tier": "team", "status": "pending", "document_count": 0},
        {"id": "kb3", "name": "K3", "tier": "global", "status": "active", "document_count": 50},
    ])
    result = await tool.execute({}, {"kb_registry": registry})
    assert result.success
    assert len(result.data) == 2  # only active
    assert all(k["kb_id"] in ("kb1", "kb3") for k in result.data)


@pytest.mark.asyncio
async def test_kb_list_with_tier_filter() -> None:
    tool = KBListerTool()
    registry = MagicMock()
    registry.list_all = AsyncMock(return_value=[
        {"id": "kb1", "name": "K1", "tier": "global", "status": "active"},
        {"id": "kb2", "name": "K2", "tier": "team", "status": "active"},
    ])
    result = await tool.execute({"tier": "global"}, {"kb_registry": registry})
    assert result.success
    assert len(result.data) == 1
    assert result.data[0]["kb_id"] == "kb1"
