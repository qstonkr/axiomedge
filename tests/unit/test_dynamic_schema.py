"""Unit tests for ensure_dynamic_constraints."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.pipelines.graphrag.schema_types import IndexSpec, SchemaProfile
from src.stores.neo4j.dynamic_schema import (
    ensure_dynamic_constraints,
    reset_session_cache,
)


@pytest.fixture(autouse=True)
def _clean_cache():
    reset_session_cache()
    yield
    reset_session_cache()


@pytest.fixture
def mock_client():
    client = AsyncMock()
    client.execute_write = AsyncMock(return_value={"nodes_created": 0})
    return client


class TestSafeLabelRejection:
    @pytest.mark.asyncio
    async def test_injection_label_rejected(self, mock_client):
        schema = SchemaProfile(
            nodes=("Meeting; DROP DATABASE",),
            relationships=(),
            prompt_focus="",
        )
        stats = await ensure_dynamic_constraints(mock_client, schema)
        assert stats["failed"] == 1
        assert stats["created"] == 0
        mock_client.execute_write.assert_not_called()

    @pytest.mark.asyncio
    async def test_non_cypher_identifier_rejected(self, mock_client):
        schema = SchemaProfile(
            nodes=("123InvalidStart",),
            relationships=(),
            prompt_focus="",
        )
        stats = await ensure_dynamic_constraints(mock_client, schema)
        assert stats["failed"] == 1
        mock_client.execute_write.assert_not_called()


class TestTier1Skip:
    @pytest.mark.asyncio
    async def test_existing_tier1_label_skipped(self, mock_client):
        # Document is in node_registry.NODE_LABELS (Tier 1)
        schema = SchemaProfile(
            nodes=("Document",),
            relationships=(),
            prompt_focus="",
        )
        stats = await ensure_dynamic_constraints(mock_client, schema)
        assert stats["skipped"] == 1
        assert stats["created"] == 0
        mock_client.execute_write.assert_not_called()


class TestCreate:
    @pytest.mark.asyncio
    async def test_new_label_creates_unique_constraint(self, mock_client):
        schema = SchemaProfile(
            nodes=("TestGadget",),
            relationships=(),
            prompt_focus="",
        )
        stats = await ensure_dynamic_constraints(mock_client, schema)
        assert stats["created"] == 1
        mock_client.execute_write.assert_awaited_once()
        call_cypher = mock_client.execute_write.await_args.args[0]
        assert "CREATE CONSTRAINT testgadget_id_unique" in call_cypher
        assert "REQUIRE n.id IS UNIQUE" in call_cypher

    @pytest.mark.asyncio
    async def test_idempotent_within_session(self, mock_client):
        schema = SchemaProfile(
            nodes=("TestGadget",),
            relationships=(),
            prompt_focus="",
        )
        await ensure_dynamic_constraints(mock_client, schema)
        mock_client.execute_write.reset_mock()
        # Second call should short-circuit via session cache
        stats2 = await ensure_dynamic_constraints(mock_client, schema)
        assert stats2["created"] == 0
        assert stats2["skipped"] == 1
        mock_client.execute_write.assert_not_called()

    @pytest.mark.asyncio
    async def test_custom_btree_index_emitted(self, mock_client):
        schema = SchemaProfile(
            nodes=("TestGadget",),
            relationships=(),
            prompt_focus="",
            indexes={"TestGadget": (IndexSpec(property="scheduled_at"),)},
        )
        await ensure_dynamic_constraints(mock_client, schema)
        assert mock_client.execute_write.await_count == 2
        idx_cypher = mock_client.execute_write.await_args_list[1].args[0]
        assert "CREATE INDEX testgadget_scheduled_at_idx" in idx_cypher
        assert "ON (n.scheduled_at)" in idx_cypher

    @pytest.mark.asyncio
    async def test_fulltext_index_emitted(self, mock_client):
        schema = SchemaProfile(
            nodes=("TestGadget",),
            relationships=(),
            prompt_focus="",
            indexes={
                "TestGadget": (IndexSpec(property="title", index_type="fulltext"),),
            },
        )
        await ensure_dynamic_constraints(mock_client, schema)
        idx_cypher = mock_client.execute_write.await_args_list[1].args[0]
        assert "CREATE FULLTEXT INDEX testgadget_title_ft" in idx_cypher
        assert "ON EACH [n.title]" in idx_cypher


class TestFailOpen:
    @pytest.mark.asyncio
    async def test_per_label_failure_does_not_abort_others(self, mock_client):
        from neo4j.exceptions import Neo4jError

        call_count = {"n": 0}

        async def flaky_write(cypher, *args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise Neo4jError("fake constraint collision")
            return {"nodes_created": 0}

        mock_client.execute_write = flaky_write
        schema = SchemaProfile(
            nodes=("TestGadget", "TestWidget"),
            relationships=(),
            prompt_focus="",
        )
        stats = await ensure_dynamic_constraints(mock_client, schema)
        assert stats["failed"] == 1
        assert stats["created"] == 1
