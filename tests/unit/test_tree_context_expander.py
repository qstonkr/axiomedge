"""Unit tests for tree_context_expander — 형제 청크 확장 + 섹션 제목 검색."""

from __future__ import annotations

import pytest

from src.search.tree_context_expander import (
    expand_siblings,
    search_by_section_titles,
    get_section_bonus_map,
    ExpandedChunk,
)


class FakeGraphRepo:
    """테스트용 가짜 그래프 레포지토리."""

    def __init__(
        self,
        siblings: dict[str, list[dict]] | None = None,
        section_results: list[dict] | None = None,
        section_paths: dict[str, str] | None = None,
    ):
        self._siblings = siblings or {}
        self._section_results = section_results or []
        self._section_paths = section_paths or {}

    async def find_tree_siblings_batch(
        self, chunk_ids: list[str], *, window: int = 2,
    ) -> dict[str, list[dict]]:
        return {cid: self._siblings.get(cid, []) for cid in chunk_ids}

    async def search_section_titles(
        self, query: str, *, kb_id: str | None = None, limit: int = 10,
    ) -> list[dict]:
        return self._section_results[:limit]

    async def get_chunk_section_paths_batch(
        self, chunk_ids: list[str],
    ) -> dict[str, str]:
        return {cid: self._section_paths[cid] for cid in chunk_ids if cid in self._section_paths}


class TestExpandSiblings:
    """형제 청크 확장 테스트."""

    @pytest.mark.asyncio
    async def test_basic_expansion(self):
        repo = FakeGraphRepo(siblings={
            "c1": [
                {"chunk_id": "c0", "chunk_index": 0, "section_title": "A", "section_path": "A"},
                {"chunk_id": "c2", "chunk_index": 2, "section_title": "A", "section_path": "A"},
            ],
        })
        result = await expand_siblings(
            ["c1"], {"c1": 0.9}, repo, window=2, max_per_hit=4, score_decay=0.85,
        )
        assert len(result) == 2
        assert result[0].chunk_id in ("c0", "c2")
        assert result[0].source == "sibling"
        assert result[0].score == pytest.approx(0.9 * 0.85)

    @pytest.mark.asyncio
    async def test_no_duplicates_with_existing(self):
        repo = FakeGraphRepo(siblings={
            "c1": [
                {"chunk_id": "c2", "chunk_index": 2, "section_title": "A", "section_path": "A"},
                {"chunk_id": "c3", "chunk_index": 3, "section_title": "A", "section_path": "A"},
            ],
        })
        # c2 is already a hit
        result = await expand_siblings(
            ["c1", "c2"], {"c1": 0.9, "c2": 0.8}, repo,
        )
        # c2 should not be in expanded (already in hits)
        expanded_ids = [r.chunk_id for r in result]
        assert "c2" not in expanded_ids
        assert "c3" in expanded_ids

    @pytest.mark.asyncio
    async def test_max_per_hit_limit(self):
        repo = FakeGraphRepo(siblings={
            "c1": [
                {"chunk_id": f"s{i}", "chunk_index": i, "section_title": "A", "section_path": "A"}
                for i in range(10)
            ],
        })
        result = await expand_siblings(
            ["c1"], {"c1": 0.9}, repo, max_per_hit=3,
        )
        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_empty_input(self):
        repo = FakeGraphRepo()
        result = await expand_siblings([], {}, repo)
        assert result == []

    @pytest.mark.asyncio
    async def test_no_siblings_found(self):
        repo = FakeGraphRepo(siblings={})
        result = await expand_siblings(["c1"], {"c1": 0.9}, repo)
        assert result == []

    @pytest.mark.asyncio
    async def test_score_always_below_original(self):
        repo = FakeGraphRepo(siblings={
            "c1": [
                {"chunk_id": "c2", "chunk_index": 2, "section_title": "A", "section_path": "A"},
            ],
        })
        result = await expand_siblings(
            ["c1"], {"c1": 0.9}, repo, score_decay=0.85,
        )
        assert result[0].score < 0.9


class TestSearchBySectionTitles:
    """섹션 제목 검색 테스트."""

    @pytest.mark.asyncio
    async def test_basic_search(self):
        repo = FakeGraphRepo(section_results=[
            {"chunk_id": "c10", "section_title": "임대차 조정", "section_path": "계약 > 임대차 조정", "score": 0.8},
            {"chunk_id": "c11", "section_title": "임대료 변경", "section_path": "계약 > 임대료 변경", "score": 0.7},
        ])
        result = await search_by_section_titles("임대료 조정", repo)
        assert len(result) == 2
        assert result[0].source == "section_title_search"

    @pytest.mark.asyncio
    async def test_excludes_existing(self):
        repo = FakeGraphRepo(section_results=[
            {"chunk_id": "c1", "section_title": "A", "section_path": "A", "score": 0.8},
            {"chunk_id": "c2", "section_title": "B", "section_path": "B", "score": 0.7},
        ])
        result = await search_by_section_titles(
            "test", repo, existing_chunk_ids={"c1"},
        )
        assert len(result) == 1
        assert result[0].chunk_id == "c2"

    @pytest.mark.asyncio
    async def test_limit(self):
        repo = FakeGraphRepo(section_results=[
            {"chunk_id": f"c{i}", "section_title": f"S{i}", "section_path": f"S{i}", "score": 0.5}
            for i in range(20)
        ])
        result = await search_by_section_titles("test", repo, limit=5)
        assert len(result) == 5

    @pytest.mark.asyncio
    async def test_dedup_chunk_ids(self):
        repo = FakeGraphRepo(section_results=[
            {"chunk_id": "c1", "section_title": "A", "section_path": "A", "score": 0.8},
            {"chunk_id": "c1", "section_title": "A", "section_path": "A", "score": 0.7},
        ])
        result = await search_by_section_titles("test", repo)
        assert len(result) == 1


class TestGetSectionBonusMap:
    """섹션 보너스 맵 테스트."""

    @pytest.mark.asyncio
    async def test_bonus_for_multi_hit_section(self):
        repo = FakeGraphRepo(section_paths={
            "c1": "설치 > 리눅스",
            "c2": "설치 > 윈도우",
            "c3": "운영 > 모니터링",
        })
        result = await get_section_bonus_map(["c1", "c2", "c3"], repo)
        # c1, c2 둘 다 "설치" 섹션 → 보너스
        assert result.get("c1") == 1.0
        assert result.get("c2") == 1.0
        # c3은 "운영" 섹션에 혼자 → 보너스 없음
        assert "c3" not in result

    @pytest.mark.asyncio
    async def test_no_bonus_single_hit(self):
        repo = FakeGraphRepo(section_paths={
            "c1": "A > B",
            "c2": "C > D",
        })
        result = await get_section_bonus_map(["c1", "c2"], repo)
        assert result == {}

    @pytest.mark.asyncio
    async def test_empty_input(self):
        repo = FakeGraphRepo()
        result = await get_section_bonus_map([], repo)
        assert result == {}

    @pytest.mark.asyncio
    async def test_three_hits_same_section(self):
        repo = FakeGraphRepo(section_paths={
            "c1": "설치 > A",
            "c2": "설치 > B",
            "c3": "설치 > C",
        })
        result = await get_section_bonus_map(["c1", "c2", "c3"], repo)
        assert len(result) == 3
