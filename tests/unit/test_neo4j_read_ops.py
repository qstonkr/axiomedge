"""Unit tests for src/stores/neo4j/_read_ops.py — scope-sensitive Cypher paths.

Regression scope for the 2026-04-23 bug where ``search_section_titles`` placed
``WHERE ts.kb_id = $kb_id`` *before* ``WITH node AS ts`` — causing
``CypherSyntaxError: Variable 'ts' not defined`` whenever a ``kb_id`` was
supplied. The except tuple also missed ``neo4j.exceptions.Neo4jError`` so the
error escalated to a 500 instead of degrading gracefully.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest


def _make_mixin(execute_query_result: list[dict[str, Any]] | Exception):
    """Build a ReadOpsMixin subclass with a mock ``_client.execute_query``.

    Using subclassing (not plain instantiation) because ``ReadOpsMixin`` is
    designed to be mixed into a repository class.
    """
    from src.stores.neo4j._read_ops import ReadOpsMixin

    class _Host(ReadOpsMixin):
        def __init__(self):
            self._client = AsyncMock()
            if isinstance(execute_query_result, Exception):
                self._client.execute_query = AsyncMock(side_effect=execute_query_result)
            else:
                self._client.execute_query = AsyncMock(return_value=execute_query_result)

    return _Host()


class TestSearchSectionTitlesCypherScope:
    """Verify the Cypher string satisfies Neo4j scope rules."""

    @pytest.mark.asyncio
    async def test_where_is_after_with_when_kb_id_is_set(self):
        """Regression: ``ts.kb_id`` filter must come AFTER ``WITH node AS ts``.

        Prior bug: WHERE placed before WITH → ``Variable 'ts' not defined``
        whenever kb_id triggered the kb_filter branch.
        """
        mixin = _make_mixin([])
        await mixin.search_section_titles("query text", kb_id="g-espa")

        assert mixin._client.execute_query.await_count == 1
        cypher_arg = mixin._client.execute_query.await_args.args[0]
        # WITH ... ts 가 먼저 등장해야 하고, ts.kb_id 필터가 그 뒤에 와야 함
        with_idx = cypher_arg.index("WITH node AS ts")
        where_ts_idx = cypher_arg.index("ts.kb_id")
        assert with_idx < where_ts_idx, (
            "WITH 보다 WHERE 가 먼저 오면 ts 미정의 — "
            f"WITH idx={with_idx} vs WHERE idx={where_ts_idx}"
        )

    @pytest.mark.asyncio
    async def test_kb_id_passed_as_param(self):
        mixin = _make_mixin([])
        await mixin.search_section_titles("q", kb_id="g-espa", limit=5, min_score=0.4)

        call = mixin._client.execute_query.await_args
        params = call.args[1]
        assert params["kb_id"] == "g-espa"
        assert params["limit"] == 5
        assert params["min_score"] == 0.4
        assert params["query"] == "q"

    @pytest.mark.asyncio
    async def test_kb_id_omitted_when_none(self):
        """kb_id=None 이면 cypher 에 kb_filter 없고 params 에도 kb_id 없음."""
        mixin = _make_mixin([])
        await mixin.search_section_titles("q", kb_id=None)

        call = mixin._client.execute_query.await_args
        cypher_arg = call.args[0]
        params = call.args[1]
        assert "ts.kb_id" not in cypher_arg
        assert "kb_id" not in params


class TestSearchSectionTitlesExceptionHandling:
    """Verify neo4j.exceptions.Neo4jError subclasses degrade to [] (not 500)."""

    @pytest.mark.asyncio
    async def test_cypher_syntax_error_returns_empty(self):
        from neo4j.exceptions import CypherSyntaxError

        exc = CypherSyntaxError("Variable 'ts' not defined")
        mixin = _make_mixin(exc)

        result = await mixin.search_section_titles("q", kb_id="g-espa")
        assert result == []

    @pytest.mark.asyncio
    async def test_generic_runtime_error_returns_empty(self):
        mixin = _make_mixin(RuntimeError("connection dropped"))
        result = await mixin.search_section_titles("q")
        assert result == []

    @pytest.mark.asyncio
    async def test_happy_path_returns_payload(self):
        payload = [
            {
                "chunk_id": "c1",
                "section_title": "Intro",
                "section_path": "1/1",
                "score": 0.9,
            },
        ]
        mixin = _make_mixin(payload)
        result = await mixin.search_section_titles("q", kb_id="g-espa")
        assert result == payload
