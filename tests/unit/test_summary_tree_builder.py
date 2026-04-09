"""Unit tests for summary_tree_builder — RAPTOR식 클러스터링 → 요약 트리."""

from __future__ import annotations

import numpy as np
import pytest

from src.pipeline.summary_tree_builder import (
    _cluster_embeddings,
    build_summary_layer,
    build_summary_tree,
)


class FakeEmbedder:
    async def embed(self, text: str) -> list[float]:
        return [0.1] * 10

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[0.1 * (i + 1)] * 10 for i in range(len(texts))]


class FakeLLM:
    async def generate(self, prompt: str, *, max_tokens: int | None = None) -> str:
        return "테스트 요약 결과입니다."


class FakeFailLLM:
    async def generate(self, prompt: str, *, max_tokens: int | None = None) -> str:
        raise RuntimeError("LLM 호출 실패")


class TestClusterEmbeddings:
    """GMM 클러스터링 테스트."""

    def test_single_item(self):
        embeddings = np.array([[1.0, 2.0, 3.0]])
        clusters = _cluster_embeddings(embeddings, use_umap=False)
        assert len(clusters) == 1
        assert clusters[0] == [0]

    def test_two_items(self):
        embeddings = np.array([[1.0, 0.0], [0.0, 1.0]])
        clusters = _cluster_embeddings(embeddings, use_umap=False)
        total = sum(len(c) for c in clusters)
        assert total == 2

    def test_clear_clusters(self):
        # 두 그룹으로 명확히 나뉘는 데이터
        group_a = np.random.RandomState(42).randn(10, 5) + np.array([5, 0, 0, 0, 0])
        group_b = np.random.RandomState(42).randn(10, 5) + np.array([0, 5, 0, 0, 0])
        embeddings = np.vstack([group_a, group_b])
        clusters = _cluster_embeddings(embeddings, use_umap=False)
        assert len(clusters) >= 2
        total = sum(len(c) for c in clusters)
        assert total == 20

    def test_all_indices_present(self):
        embeddings = np.random.RandomState(42).randn(15, 5)
        clusters = _cluster_embeddings(embeddings, use_umap=False)
        all_indices = sorted(idx for c in clusters for idx in c)
        assert all_indices == list(range(15))


class TestBuildSummaryLayer:
    """단일 계층 요약 생성 테스트."""

    @pytest.mark.asyncio
    async def test_basic_layer(self):
        texts = [f"텍스트 {i}" for i in range(10)]
        embeddings = np.random.RandomState(42).randn(10, 10)
        results = await build_summary_layer(
            texts, embeddings, FakeEmbedder(), FakeLLM(),
            use_umap=False, min_chunks=3,
        )
        assert len(results) > 0
        for r in results:
            assert "text" in r
            assert "embedding" in r
            assert "source_indices" in r
            assert r["text"] == "테스트 요약 결과입니다."

    @pytest.mark.asyncio
    async def test_too_few_chunks_skipped(self):
        texts = ["a", "b"]
        embeddings = np.array([[1.0, 2.0], [3.0, 4.0]])
        results = await build_summary_layer(
            texts, embeddings, FakeEmbedder(), FakeLLM(),
            min_chunks=5,
        )
        assert results == []

    @pytest.mark.asyncio
    async def test_llm_failure_returns_empty(self):
        texts = [f"텍스트 {i}" for i in range(10)]
        embeddings = np.random.RandomState(42).randn(10, 10)
        results = await build_summary_layer(
            texts, embeddings, FakeEmbedder(), FakeFailLLM(),
            use_umap=False, min_chunks=3,
        )
        assert results == []


class TestBuildSummaryTree:
    """재귀적 요약 트리 테스트."""

    @pytest.mark.asyncio
    async def test_basic_tree(self):
        chunks = [
            {"text": f"청크 {i}", "embedding": list(np.random.RandomState(i).randn(10)), "chunk_id": f"c{i}"}
            for i in range(15)
        ]
        results = await build_summary_tree(
            chunks, FakeEmbedder(), FakeLLM(),
            max_layers=2, min_chunks=3, use_umap=False,
        )
        assert len(results) > 0
        for r in results:
            assert "layer" in r
            assert r["layer"] >= 1
            assert "source_chunk_ids" in r

    @pytest.mark.asyncio
    async def test_empty_input(self):
        results = await build_summary_tree(
            [], FakeEmbedder(), FakeLLM(),
        )
        assert results == []

    @pytest.mark.asyncio
    async def test_too_few_chunks(self):
        chunks = [
            {"text": "a", "embedding": [0.1] * 10, "chunk_id": "c0"},
            {"text": "b", "embedding": [0.2] * 10, "chunk_id": "c1"},
        ]
        results = await build_summary_tree(
            chunks, FakeEmbedder(), FakeLLM(),
            min_chunks=5,
        )
        assert results == []

    @pytest.mark.asyncio
    async def test_layer_numbers(self):
        chunks = [
            {"text": f"청크 {i}", "embedding": list(np.random.RandomState(i).randn(10)), "chunk_id": f"c{i}"}
            for i in range(20)
        ]
        results = await build_summary_tree(
            chunks, FakeEmbedder(), FakeLLM(),
            max_layers=3, min_chunks=3, use_umap=False,
        )
        layers = {r["layer"] for r in results}
        assert 1 in layers

    @pytest.mark.asyncio
    async def test_source_chunk_ids_tracked(self):
        chunks = [
            {"text": f"청크 {i}", "embedding": list(np.random.RandomState(i).randn(10)), "chunk_id": f"c{i}"}
            for i in range(10)
        ]
        results = await build_summary_tree(
            chunks, FakeEmbedder(), FakeLLM(),
            max_layers=1, min_chunks=3, use_umap=False,
        )
        for r in results:
            assert all(cid.startswith("c") for cid in r["source_chunk_ids"])
