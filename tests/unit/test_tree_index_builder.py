"""Unit tests for tree_index_builder — heading_path 기반 Neo4j 트리 구축."""

from __future__ import annotations

import pytest

from src.pipeline.tree_index_builder import (
    parse_heading_path,
    build_tree_from_chunks,
    _path_hash,
)


class TestParseHeadingPath:
    """heading_path 문자열 파싱 테스트."""

    def test_simple_path(self):
        result = parse_heading_path("설치 가이드 > 사전 요구사항 > Python 설정")
        assert len(result) == 3
        assert result[0]["level"] == 1
        assert result[0]["title"] == "설치 가이드"
        assert result[1]["level"] == 2
        assert result[1]["title"] == "사전 요구사항"
        assert result[2]["level"] == 3
        assert result[2]["title"] == "Python 설정"

    def test_single_level(self):
        result = parse_heading_path("개요")
        assert len(result) == 1
        assert result[0]["level"] == 1
        assert result[0]["title"] == "개요"
        assert result[0]["full_path"] == "개요"

    def test_empty_string(self):
        assert parse_heading_path("") == []
        assert parse_heading_path("   ") == []

    def test_none_like(self):
        assert parse_heading_path(None) == []

    def test_full_path_construction(self):
        result = parse_heading_path("A > B > C")
        assert result[0]["full_path"] == "A"
        assert result[1]["full_path"] == "A > B"
        assert result[2]["full_path"] == "A > B > C"

    def test_path_hash_deterministic(self):
        r1 = parse_heading_path("A > B")
        r2 = parse_heading_path("A > B")
        assert r1[1]["path_hash"] == r2[1]["path_hash"]

    def test_path_hash_unique_per_path(self):
        r1 = parse_heading_path("A > B")
        r2 = parse_heading_path("A > C")
        assert r1[1]["path_hash"] != r2[1]["path_hash"]

    def test_whitespace_handling(self):
        result = parse_heading_path("  A  >  B  >  C  ")
        assert result[0]["title"] == "A"
        assert result[1]["title"] == "B"
        assert result[2]["title"] == "C"


class TestBuildTreeFromChunks:
    """build_tree_from_chunks 트리 구축 테스트."""

    def _make_chunks(self, paths: list[str]) -> list[dict]:
        return [
            {"chunk_id": f"chunk-{i}", "heading_path": p, "chunk_index": i}
            for i, p in enumerate(paths)
        ]

    def test_basic_tree(self):
        chunks = self._make_chunks([
            "설치 > 리눅스",
            "설치 > 리눅스",
            "설치 > 윈도우",
            "운영 > 모니터링",
        ])
        result = build_tree_from_chunks("kb1", "doc1", chunks)

        assert result["root"]["node_id"] == "kb1:doc1"
        assert result["root"]["doc_id"] == "doc1"

        # 섹션: 설치, 리눅스, 윈도우, 운영, 모니터링 = 5
        sections = result["sections"]
        titles = [s["title"] for s in sections]
        assert "설치" in titles
        assert "리눅스" in titles
        assert "윈도우" in titles
        assert "운영" in titles
        assert "모니터링" in titles

        # 페이지: 4개
        assert len(result["pages"]) == 4

    def test_empty_chunks(self):
        result = build_tree_from_chunks("kb1", "doc1", [])
        assert result["sections"] == []
        assert result["pages"] == []

    def test_no_heading_path_chunks(self):
        chunks = self._make_chunks(["", "", ""])
        result = build_tree_from_chunks("kb1", "doc1", chunks)

        # flat 섹션 1개에 모든 청크 할당
        assert len(result["sections"]) == 1
        assert result["sections"][0]["title"] == "(본문)"
        assert len(result["pages"]) == 3

    def test_mixed_heading_and_no_heading(self):
        chunks = self._make_chunks(["A > B", "", "A > C"])
        result = build_tree_from_chunks("kb1", "doc1", chunks)

        titles = [s["title"] for s in result["sections"]]
        assert "(본문)" in titles
        assert "A" in titles
        assert "B" in titles
        assert "C" in titles

    def test_edges_root_to_level1(self):
        chunks = self._make_chunks(["A", "B"])
        result = build_tree_from_chunks("kb1", "doc1", chunks)

        root_edges = [e for e in result["edges"]
                      if e["source"] == "kb1:doc1" and e["type"] == "HAS_TREE_SECTION"]
        assert len(root_edges) == 2

    def test_edges_section_to_subsection(self):
        chunks = self._make_chunks(["A > B", "A > C"])
        result = build_tree_from_chunks("kb1", "doc1", chunks)

        parent_section_id = None
        for s in result["sections"]:
            if s["title"] == "A":
                parent_section_id = s["node_id"]
                break

        child_edges = [e for e in result["edges"]
                       if e["source"] == parent_section_id and e["type"] == "HAS_TREE_SECTION"]
        assert len(child_edges) == 2  # B, C

    def test_edges_section_to_page(self):
        chunks = self._make_chunks(["A"])
        result = build_tree_from_chunks("kb1", "doc1", chunks)

        page_edges = [e for e in result["edges"] if e["type"] == "HAS_TREE_PAGE"]
        assert len(page_edges) == 1

    def test_sibling_edges(self):
        chunks = self._make_chunks(["A", "A", "A"])
        result = build_tree_from_chunks("kb1", "doc1", chunks)

        sibling_edges = [e for e in result["edges"] if e["type"] == "TREE_NEXT_SIBLING"]
        # 3 pages in same section → 2 sibling edges
        page_siblings = [e for e in sibling_edges
                         if "page:" in e["source"] and "page:" in e["target"]]
        assert len(page_siblings) == 2

    def test_section_sibling_edges(self):
        chunks = self._make_chunks(["A", "B", "C"])
        result = build_tree_from_chunks("kb1", "doc1", chunks)

        sibling_edges = [e for e in result["edges"]
                         if e["type"] == "TREE_NEXT_SIBLING"
                         and "section:" in e["source"]]
        # A, B, C are level-1 siblings → 2 sibling edges
        assert len(sibling_edges) == 2

    def test_document_to_root_edge(self):
        chunks = self._make_chunks(["A"])
        result = build_tree_from_chunks("kb1", "doc1", chunks)

        root_edge = [e for e in result["edges"] if e["type"] == "HAS_TREE_ROOT"]
        assert len(root_edge) == 1
        assert root_edge[0]["source"] == "doc1"
        assert root_edge[0]["target"] == "kb1:doc1"

    def test_page_node_ids(self):
        chunks = self._make_chunks(["A", "B"])
        result = build_tree_from_chunks("kb1", "doc1", chunks)

        page_ids = [p["node_id"] for p in result["pages"]]
        assert "kb1:doc1:page:0" in page_ids
        assert "kb1:doc1:page:1" in page_ids

    def test_chunk_id_preserved(self):
        chunks = [{"chunk_id": "my-uuid-123", "heading_path": "A", "chunk_index": 0}]
        result = build_tree_from_chunks("kb1", "doc1", chunks)
        assert result["pages"][0]["chunk_id"] == "my-uuid-123"

    def test_deep_nesting(self):
        chunks = self._make_chunks(["A > B > C > D"])
        result = build_tree_from_chunks("kb1", "doc1", chunks)

        assert len(result["sections"]) == 4
        levels = {s["title"]: s["level"] for s in result["sections"]}
        assert levels["A"] == 1
        assert levels["B"] == 2
        assert levels["C"] == 3
        assert levels["D"] == 4

    def test_duplicate_heading_paths(self):
        """같은 heading_path의 여러 청크는 같은 섹션에 할당."""
        chunks = self._make_chunks(["A > B", "A > B", "A > B"])
        result = build_tree_from_chunks("kb1", "doc1", chunks)

        # A, B 섹션만 (중복 없음)
        assert len(result["sections"]) == 2
        # 3 페이지 모두 같은 섹션
        section_ids = {p["section_id"] for p in result["pages"]}
        assert len(section_ids) == 1


class TestPathHash:
    """_path_hash 유틸리티 테스트."""

    def test_deterministic(self):
        assert _path_hash("A > B") == _path_hash("A > B")

    def test_different_paths(self):
        assert _path_hash("A > B") != _path_hash("A > C")

    def test_length(self):
        assert len(_path_hash("test")) == 12
