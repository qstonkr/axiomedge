"""Backfill tests for src/api/routes/search_helpers.py — targeting missed lines."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.api.routes.search_helpers import (
    _apply_token_boost,
    _collect_existing_docs,
    _compute_inject_score,
    _d1_date_in_range,
    _extract_identifiers,
    _parse_week_pattern,
    _process_week_point,
    _scroll_doc_with_filters,
    _scroll_identifier_chunks,
    date_filter_search,
    document_diversity,
    get_active_kb_ids,
    graph_expansion,
    identifier_search,
    keyword_boost,
    retrieve_chunks_by_ids,
    week_name_search,
)


# ── Fixtures ───────────────────────────────────────────────────────────────


def _chunk(
    cid: str = "c1",
    content: str = "hello",
    score: float = 0.5,
    kb_id: str = "kb1",
    doc_name: str = "doc1",
    **extra: Any,
) -> dict[str, Any]:
    return {
        "chunk_id": cid,
        "content": content,
        "score": score,
        "kb_id": kb_id,
        "document_name": doc_name,
        **extra,
    }


def _qdrant_response(
    status: int = 200,
    points: list[dict] | None = None,
) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = {
        "result": {"points": points or []},
    }
    return resp


# ── get_active_kb_ids ──────────────────────────────────────────────────────


class TestGetActiveKbIds:
    @pytest.fixture(autouse=True)
    def _clear_cache(self):
        from src.api.routes import search_helpers
        search_helpers._kb_registry_cache.clear()
        yield
        search_helpers._kb_registry_cache.clear()

    @pytest.mark.asyncio
    async def test_populates_cache(self):
        registry = AsyncMock()
        registry.list_all.return_value = [
            {"kb_id": "a", "status": "active"},
            {"kb_id": "b", "status": "inactive"},
            {"kb_id": "c", "status": "active"},
        ]
        result = await get_active_kb_ids(registry)
        assert result == {"a", "c"}
        registry.list_all.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_returns_cached(self):
        registry = AsyncMock()
        registry.list_all.return_value = [
            {"kb_id": "x", "status": "active"},
        ]
        r1 = await get_active_kb_ids(registry)
        r2 = await get_active_kb_ids(registry)
        assert r1 == r2 == {"x"}
        assert registry.list_all.await_count == 1

    @pytest.mark.asyncio
    async def test_cache_expired(self):
        from src.api.routes import search_helpers

        registry = AsyncMock()
        registry.list_all.return_value = [
            {"kb_id": "old", "status": "active"},
        ]
        await get_active_kb_ids(registry)

        key = str(id(registry))
        ts, ids = search_helpers._kb_registry_cache[key]
        search_helpers._kb_registry_cache[key] = (ts - 120.0, ids)

        registry.list_all.return_value = [
            {"kb_id": "new", "status": "active"},
        ]
        result = await get_active_kb_ids(registry)
        assert result == {"new"}


# ── _extract_identifiers ──────────────────────────────────────────────────


class TestExtractIdentifiers:
    def test_comma_numbers(self):
        ids = _extract_identifiers("금액 6,720,009원")
        assert "6,720,009" in ids

    def test_jira_keys(self):
        ids = _extract_identifiers("GRIT-12345 이슈")
        assert "GRIT-12345" in ids

    def test_store_codes(self):
        ids = _extract_identifiers("VL820 매장")
        assert "VL820" in ids

    def test_error_codes(self):
        ids = _extract_identifiers("에러 E-4001")
        assert "E-4001" in ids

    def test_filenames(self):
        ids = _extract_identifiers("config-test.yaml 파일")
        assert "config-test.yaml" in ids

    def test_camelcase(self):
        ids = _extract_identifiers("PwdFailCntLimitCache 조회")
        assert "PwdFailCntLimitCache" in ids

    def test_no_match(self):
        assert _extract_identifiers("일반 질문입니다") == []


# ── _scroll_identifier_chunks ─────────────────────────────────────────────


class TestScrollIdentifierChunks:
    @pytest.mark.asyncio
    async def test_success(self):
        client = AsyncMock()
        client.post.return_value = _qdrant_response(
            200,
            [
                {
                    "id": "p1",
                    "payload": {
                        "content": "abc GRIT-123 def",
                        "document_name": "d1",
                        "source_uri": "s1",
                    },
                }
            ],
        )
        existing: set[str] = set()
        result = await _scroll_identifier_chunks(
            client, "GRIT-123", "my-kb", "http://q:6333", existing,
        )
        assert len(result) == 1
        assert result[0]["_identifier_match"] is True
        assert result[0]["kb_id"] == "my-kb"
        assert "p1" in existing

    @pytest.mark.asyncio
    async def test_skip_existing(self):
        client = AsyncMock()
        client.post.return_value = _qdrant_response(
            200, [{"id": "p1", "payload": {"content": "x"}}],
        )
        existing = {"p1"}
        result = await _scroll_identifier_chunks(
            client, "X", "kb", "http://q:6333", existing,
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_non_200(self):
        client = AsyncMock()
        client.post.return_value = _qdrant_response(500)
        result = await _scroll_identifier_chunks(
            client, "X", "kb", "http://q:6333", set(),
        )
        assert result == []


# ── identifier_search ─────────────────────────────────────────────────────


class TestIdentifierSearch:
    @pytest.mark.asyncio
    async def test_no_identifiers(self):
        chunks = [_chunk()]
        result = await identifier_search("일반 질문", chunks, ["kb1"], "url")
        assert result is chunks

    @pytest.mark.asyncio
    async def test_empty_chunks(self):
        result = await identifier_search("GRIT-12345", [], ["kb1"], "url")
        assert result == []

    @pytest.mark.asyncio
    async def test_injects_chunks(self):
        resp = _qdrant_response(
            200,
            [{"id": "new1", "payload": {"content": "GRIT-12345 fix"}}],
        )

        async def _fake_post(*a, **kw):
            return resp

        mock_client = AsyncMock()
        mock_client.post = _fake_post
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            chunks = [_chunk(cid="c1")]
            result = await identifier_search(
                "GRIT-12345 관련", chunks, ["kb1"], "http://q:6333",
            )
        assert any(c.get("_identifier_match") for c in result)

    @pytest.mark.asyncio
    async def test_handles_exception(self):
        with patch("httpx.AsyncClient", side_effect=RuntimeError("boom")):
            chunks = [_chunk()]
            result = await identifier_search(
                "GRIT-12345", chunks, ["kb1"], "http://q:6333",
            )
        assert result == chunks


# ── keyword_boost ─────────────────────────────────────────────────────────


class TestKeywordBoost:
    def test_single_kb_no_tokens(self):
        chunks = [_chunk(score=0.8), _chunk(cid="c2", score=0.9)]
        result = keyword_boost(chunks, [], ["kb1"], 10, 0.3)
        assert result[0]["score"] >= result[1]["score"]

    def test_single_kb_with_tokens(self):
        chunks = [_chunk(content="hello world", score=0.5)]
        result = keyword_boost(chunks, ["hello"], ["kb1"], 10, 0.3)
        assert result[0]["score"] > 0.5

    def test_multi_kb_separates_keyword_matched(self):
        chunks = [
            _chunk(cid="a", content="foo bar", score=0.3),
            _chunk(cid="b", content="no match", score=0.9),
        ]
        result = keyword_boost(
            chunks, ["foo"], ["kb1", "kb2"], 10, 0.3,
        )
        assert result[0].get("_keyword_matched") is True

    def test_pool_size_limit(self):
        chunks = [_chunk(cid=f"c{i}", score=0.1 * i) for i in range(20)]
        result = keyword_boost(chunks, [], ["kb1"], 5, 0.3)
        assert len(result) == 5

    def test_other_chunks_follow_keyword(self):
        chunks = [
            _chunk(cid="a", content="xyz", score=0.9),
            _chunk(cid="b", content="hello", score=0.1),
        ]
        result = keyword_boost(
            chunks, ["hello"], ["kb1", "kb2"], 10, 0.5,
        )
        assert result[0]["chunk_id"] == "b"


# ── document_diversity ────────────────────────────────────────────────────


class TestDocumentDiversity:
    def test_limits_per_doc(self):
        chunks = [_chunk(cid=f"c{i}", doc_name="same") for i in range(10)]
        result = document_diversity(chunks, 20, max_chunks_per_doc=3)
        first_three = result[:3]
        assert all(c["document_name"] == "same" for c in first_three)
        assert len(result) == 10

    def test_pool_size_respected(self):
        chunks = [_chunk(cid=f"c{i}", doc_name="same") for i in range(10)]
        result = document_diversity(chunks, 5, max_chunks_per_doc=3)
        assert len(result) == 5

    def test_overflow_pushed_back(self):
        chunks = [
            _chunk(cid="a1", doc_name="A", score=0.9),
            _chunk(cid="a2", doc_name="A", score=0.8),
            _chunk(cid="b1", doc_name="B", score=0.7),
        ]
        result = document_diversity(chunks, 10, max_chunks_per_doc=1)
        assert result[0]["chunk_id"] == "a1"
        assert result[1]["chunk_id"] == "b1"
        assert result[2]["chunk_id"] == "a2"


# ── date_filter_search ────────────────────────────────────────────────────


class TestDateFilterSearch:
    @pytest.mark.asyncio
    async def test_no_date_in_query(self):
        chunks = [_chunk()]
        result = await date_filter_search("일반 질문", chunks, ["kb"], "url", 10)
        assert result is chunks

    @pytest.mark.asyncio
    async def test_korean_date_pattern(self):
        resp = _qdrant_response(
            200,
            [
                {
                    "id": "dp1",
                    "payload": {
                        "content": "월별 보고서",
                        "document_name": "2025-03 보고서",
                        "source_uri": "s",
                    },
                }
            ],
        )
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            chunks = [_chunk(doc_name="other_doc")]
            result = await date_filter_search(
                "2025년 3월 매출", chunks, ["kb1"], "http://q:6333", 20,
            )
        assert any(c.get("_date_filtered") for c in result)

    @pytest.mark.asyncio
    async def test_iso_date_pattern(self):
        resp = _qdrant_response(200, [])
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            chunks = [_chunk()]
            result = await date_filter_search(
                "2025-03 데이터", chunks, ["kb1"], "http://q:6333", 20,
            )
        assert len(result) >= 1

    @pytest.mark.asyncio
    async def test_skips_existing_docs(self):
        resp = _qdrant_response(
            200,
            [
                {
                    "id": "dp1",
                    "payload": {
                        "content": "x",
                        "document_name": "doc1",
                    },
                }
            ],
        )
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            chunks = [_chunk(doc_name="doc1")]
            result = await date_filter_search(
                "2025년 3월", chunks, ["kb1"], "http://q:6333", 20,
            )
        assert not any(c.get("_date_filtered") for c in result)

    @pytest.mark.asyncio
    async def test_handles_exception(self):
        with patch("httpx.AsyncClient", side_effect=RuntimeError("fail")):
            chunks = [_chunk()]
            result = await date_filter_search(
                "2025년 3월", chunks, ["kb1"], "http://q:6333", 20,
            )
        assert result == chunks


# ── _parse_week_pattern ───────────────────────────────────────────────────


class TestParseWeekPattern:
    def test_pattern_a(self):
        texts, label, m, d = _parse_week_pattern("3월 2주차 보고")
        assert "3월 2주차" in texts
        assert label.startswith("A:")
        assert m is None and d is None

    def test_pattern_e(self):
        texts, label, m, d = _parse_week_pattern("2025년 14주차")
        assert "2025_14" in texts
        assert label.startswith("E:")

    def test_pattern_d3_iso(self):
        texts, label, m, d = _parse_week_pattern("2025-03-15 일정")
        assert "2025-03-15" in texts
        assert label.startswith("D3:")

    def test_pattern_d3_korean(self):
        texts, label, m, d = _parse_week_pattern("2025년 3월 15일")
        assert "2025-03-15" in texts

    def test_pattern_d1(self):
        texts, label, mo, day = _parse_week_pattern("3월 15일 보고서")
        assert "03월" in texts
        assert mo == 3 and day == 15
        assert label.startswith("D1:")

    def test_no_match(self):
        texts, label, m, d = _parse_week_pattern("일반 질문")
        assert texts == []
        assert label == ""


# ── _d1_date_in_range ─────────────────────────────────────────────────────


class TestD1DateInRange:
    def test_in_range_same_month(self):
        assert _d1_date_in_range("보고서(03/10 ~ 03/16)", 3, 12) is True

    def test_out_of_range(self):
        assert _d1_date_in_range("보고서(03/10 ~ 03/16)", 3, 20) is False

    def test_cross_month_start(self):
        assert _d1_date_in_range("보고서(03/28 ~ 04/03)", 3, 30) is True

    def test_cross_month_end(self):
        assert _d1_date_in_range("보고서(03/28 ~ 04/03)", 4, 2) is True

    def test_cross_month_miss(self):
        assert _d1_date_in_range("보고서(03/28 ~ 04/03)", 4, 5) is False

    def test_no_range_with_week(self):
        assert _d1_date_in_range("3월 2주차 보고서", 3, 10) is True

    def test_no_range_no_week(self):
        assert _d1_date_in_range("일반 문서", 3, 10) is False


# ── _process_week_point ───────────────────────────────────────────────────


class TestProcessWeekPoint:
    def test_adds_chunk(self):
        all_chunks: list[dict] = []
        existing: set[str] = set()
        pt = {
            "id": "w1",
            "payload": {
                "content": "weekly report",
                "document_name": "3월 2주차",
                "source_uri": "uri",
            },
        }
        _process_week_point(pt, "kb1", existing, None, None, all_chunks)
        assert len(all_chunks) == 1
        assert all_chunks[0]["_week_matched"] is True
        assert "3월 2주차" in existing

    def test_skips_existing(self):
        all_chunks: list[dict] = []
        existing = {"3월 2주차"}
        pt = {"id": "w1", "payload": {"document_name": "3월 2주차"}}
        _process_week_point(pt, "kb1", existing, None, None, all_chunks)
        assert len(all_chunks) == 0

    def test_skips_empty_doc_name(self):
        all_chunks: list[dict] = []
        pt = {"id": "w1", "payload": {"document_name": ""}}
        _process_week_point(pt, "kb1", set(), None, None, all_chunks)
        assert len(all_chunks) == 0

    def test_d1_filter_rejects(self):
        all_chunks: list[dict] = []
        pt = {
            "id": "w1",
            "payload": {"document_name": "보고서(03/10 ~ 03/16)"},
        }
        _process_week_point(pt, "kb1", set(), 3, 20, all_chunks)
        assert len(all_chunks) == 0

    def test_d1_filter_accepts(self):
        all_chunks: list[dict] = []
        pt = {
            "id": "w1",
            "payload": {"document_name": "보고서(03/10 ~ 03/16)"},
        }
        _process_week_point(pt, "kb1", set(), 3, 12, all_chunks)
        assert len(all_chunks) == 1


# ── week_name_search ──────────────────────────────────────────────────────


class TestWeekNameSearch:
    @pytest.mark.asyncio
    async def test_no_pattern(self):
        chunks = [_chunk()]
        result = await week_name_search("일반", chunks, ["kb"], "url", 10)
        assert result is chunks

    @pytest.mark.asyncio
    async def test_empty_chunks(self):
        result = await week_name_search("3월 2주차", [], ["kb"], "url", 10)
        assert result == []

    @pytest.mark.asyncio
    async def test_injects_week_chunks(self):
        resp = _qdrant_response(
            200,
            [
                {
                    "id": "wk1",
                    "payload": {
                        "content": "주간 보고",
                        "document_name": "3월 2주차 보고서",
                        "source_uri": "s",
                    },
                }
            ],
        )
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            chunks = [_chunk(doc_name="other")]
            result = await week_name_search(
                "3월 2주차", chunks, ["kb1"], "http://q:6333", 20,
            )
        assert any(c.get("_week_matched") for c in result)

    @pytest.mark.asyncio
    async def test_handles_exception(self):
        with patch("httpx.AsyncClient", side_effect=RuntimeError("boom")):
            chunks = [_chunk()]
            result = await week_name_search(
                "3월 2주차", chunks, ["kb1"], "http://q:6333", 20,
            )
        assert result == chunks


# ── _compute_inject_score ─────────────────────────────────────────────────


class TestComputeInjectScore:
    def test_two_matches(self):
        assert _compute_inject_score("매장 보고서 분석", "매장 보고서") == 0.75

    def test_one_match(self):
        assert _compute_inject_score("매장 분석", "매장 데이터") == 0.55

    def test_no_match(self):
        assert _compute_inject_score("가나다라", "xyz abc") == 0.35


# ── _collect_existing_docs ────────────────────────────────────────────────


class TestCollectExistingDocs:
    def test_collects_names_and_uris(self):
        chunks = [
            _chunk(doc_name="docA", source_uri="uriA"),
            _chunk(doc_name="docB"),
        ]
        # add source_uri to second chunk
        chunks[1]["source_uri"] = "uriB"
        result = _collect_existing_docs(chunks)
        assert "docA" in result
        assert "uriA" in result
        assert "docB" in result
        assert "uriB" in result

    def test_skips_empty(self):
        chunks = [{"document_name": "", "source_uri": ""}]
        result = _collect_existing_docs(chunks)
        assert len(result) == 0


# ── _scroll_doc_with_filters ─────────────────────────────────────────────


class TestScrollDocWithFilters:
    @pytest.mark.asyncio
    async def test_returns_first_match(self):
        qc = AsyncMock()
        resp_empty = _qdrant_response(200, [])
        resp_hit = _qdrant_response(
            200, [{"id": "p1", "payload": {"content": "x"}}],
        )
        qc.post = AsyncMock(side_effect=[resp_empty, resp_hit])

        filters = [
            {"must": [{"key": "a", "match": {"value": "1"}}]},
            {"must": [{"key": "b", "match": {"value": "2"}}]},
        ]
        pts = await _scroll_doc_with_filters(
            qc, "http://q:6333", "kb_test", filters,
        )
        assert len(pts) == 1

    @pytest.mark.asyncio
    async def test_returns_empty_on_all_miss(self):
        qc = AsyncMock()
        qc.post = AsyncMock(return_value=_qdrant_response(200, []))
        pts = await _scroll_doc_with_filters(
            qc, "url", "coll",
            [{"must": [{"key": "a", "match": {"value": "1"}}]}],
        )
        assert pts == []

    @pytest.mark.asyncio
    async def test_skips_non_200(self):
        qc = AsyncMock()
        qc.post = AsyncMock(return_value=_qdrant_response(500))
        pts = await _scroll_doc_with_filters(
            qc, "url", "coll",
            [{"must": [{"key": "a", "match": {"value": "1"}}]}],
        )
        assert pts == []


# ── graph_expansion ───────────────────────────────────────────────────────


class TestGraphExpansion:
    @pytest.mark.asyncio
    async def test_no_expanded_uris(self):
        expander = AsyncMock()
        expander.expand_with_entities = AsyncMock(
            return_value=SimpleNamespace(
                expanded_source_uris=set(),
                graph_related_count=0,
            ),
        )
        chunks = [_chunk()]
        result = await graph_expansion(
            "질문", chunks, ["kb1"], expander, "url",
        )
        assert result == chunks

    @pytest.mark.asyncio
    async def test_boosts_and_injects(self):
        expander = MagicMock()
        expander.expand_with_entities = AsyncMock(
            return_value=SimpleNamespace(
                expanded_source_uris={"newdoc"},
                graph_related_count=1,
            ),
        )
        expander.boost_chunks = MagicMock(
            side_effect=lambda chunks, _: chunks,
        )

        resp = _qdrant_response(
            200,
            [
                {
                    "id": "g1",
                    "payload": {
                        "content": "graph content",
                        "document_name": "newdoc",
                        "source_uri": "newuri",
                    },
                }
            ],
        )
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            chunks = [_chunk(doc_name="existdoc")]
            result = await graph_expansion(
                "질문", chunks, ["kb1"], expander, "http://q:6333",
            )
        assert any(c.get("graph_injected") for c in result)

    @pytest.mark.asyncio
    async def test_uses_expand_fallback(self):
        """Uses .expand() when expand_with_entities missing."""
        expander = AsyncMock()
        del expander.expand_with_entities
        expander.expand = AsyncMock(
            return_value=SimpleNamespace(
                expanded_source_uris=set(),
                graph_related_count=0,
            ),
        )
        chunks = [_chunk()]
        result = await graph_expansion(
            "질문", chunks, ["kb1"], expander, "url",
        )
        expander.expand.assert_awaited_once()
        assert result == chunks

    @pytest.mark.asyncio
    async def test_handles_timeout(self):
        expander = MagicMock()

        async def slow(*a, **kw):
            await asyncio.sleep(10)

        expander.expand_with_entities = slow
        chunks = [_chunk()]
        result = await graph_expansion(
            "질문", chunks, ["kb1"], expander, "url",
        )
        assert result == chunks

    @pytest.mark.asyncio
    async def test_handles_exception(self):
        expander = MagicMock()
        expander.expand_with_entities = AsyncMock(
            side_effect=RuntimeError("boom"),
        )
        chunks = [_chunk()]
        result = await graph_expansion(
            "질문", chunks, ["kb1"], expander, "url",
        )
        assert result == chunks


# ── retrieve_chunks_by_ids ────────────────────────────────────────────────


class TestRetrieveChunksByIds:
    @pytest.mark.asyncio
    async def test_empty_inputs(self):
        assert await retrieve_chunks_by_ids(None, [], [], {}) == []
        assert await retrieve_chunks_by_ids(MagicMock(), ["c"], [], {}) == []

    @pytest.mark.asyncio
    async def test_retrieves_chunks(self):
        pt = SimpleNamespace(
            id="p1",
            payload={"content": "hello", "metadata": {"k": "v"}},
        )
        client = MagicMock()
        client.retrieve = MagicMock(return_value=[pt])

        result = await retrieve_chunks_by_ids(
            client,
            ["coll1"],
            ["p1"],
            {"p1": 0.9},
        )
        assert len(result) == 1
        assert result[0]["chunk_id"] == "p1"
        assert result[0]["score"] == 0.9
        assert result[0]["_tree_expanded"] is True

    @pytest.mark.asyncio
    async def test_default_score(self):
        pt = SimpleNamespace(
            id="p2",
            payload={"content": "x", "metadata": {}},
        )
        client = MagicMock()
        client.retrieve = MagicMock(return_value=[pt])

        result = await retrieve_chunks_by_ids(
            client, ["coll1"], ["p2"], {},
        )
        assert result[0]["score"] == 0.3

    @pytest.mark.asyncio
    async def test_handles_exception(self):
        client = MagicMock()
        client.retrieve = MagicMock(side_effect=RuntimeError("fail"))

        result = await retrieve_chunks_by_ids(
            client, ["coll1"], ["p1"], {},
        )
        assert result == []


# ── _apply_token_boost ────────────────────────────────────────────────────


class TestApplyTokenBoost:
    def test_boost_applied(self):
        chunk = _chunk(content="hello world test", score=0.5)
        matched = _apply_token_boost(chunk, ["hello", "world"], 0.3)
        assert matched == 2
        assert chunk["score"] > 0.5

    def test_no_match(self):
        chunk = _chunk(content="abc", score=0.5)
        matched = _apply_token_boost(chunk, ["xyz"], 0.3)
        assert matched == 0
        assert chunk["score"] == 0.5

    def test_partial_match(self):
        chunk = _chunk(content="hello xyz", score=0.5)
        matched = _apply_token_boost(chunk, ["hello", "world"], 0.3)
        assert matched == 1
        assert chunk["score"] == pytest.approx(0.65)
