"""Dense Term Index -- BGE-M3 ONNX 기반 벡터 인덱스

표준 용어 전체를 Dense 임베딩으로 변환하여 numpy 행렬로 캐시.
코사인 유사도 기반 Top-K 검색.

설계서: docs/design/GLOSSARY_SIMILARITY_MATCHING_DESIGN.md (3.2.3절)
Created: 2026-03-10
Extracted from: oreo-ecosystem (application/services/knowledge/dense_term_index.py)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import numpy as np

from src.config_weights import weights as _w

if TYPE_CHECKING:
    from src.embedding.onnx_provider import OnnxBgeEmbeddingProvider

logger = logging.getLogger(__name__)


class DenseTermIndex:
    """표준 용어 Dense 임베딩 인덱스.

    BGE-M3 ONNX (1024d)로 표준 용어를 임베딩하고,
    numpy matmul로 코사인 유사도 Top-K 검색.

    메모리: 39,929 x 1024 x 4bytes = ~156MB
    """

    def __init__(self, provider: OnnxBgeEmbeddingProvider) -> None:
        self._provider = provider
        self._matrix: np.ndarray | None = None  # (N, 1024) L2-normalized
        self._term_indices: list[int] = []

    @property
    def is_ready(self) -> bool:
        return self._matrix is not None and len(self._term_indices) > 0

    def build(self, precomputed_terms: list[Any], batch_size: int = _w.search.term_build_batch_size) -> None:
        """표준 용어 임베딩 행렬 구축.

        Args:
            precomputed_terms: _PrecomputedStd 리스트
            batch_size: 임베딩 배치 크기
        """
        if not self._provider.is_ready():
            logger.warning("ONNX provider not ready, skipping dense index build")
            return

        texts: list[str] = []
        indices: list[int] = []

        for idx, pc in enumerate(precomputed_terms):
            text = pc.term.term
            if pc.term.term_ko:
                text += " " + pc.term.term_ko
            if pc.term.definition:
                text += " " + pc.term.definition[:100]
            texts.append(text)
            indices.append(idx)

        if not texts:
            return

        # 배치 임베딩
        all_vecs: list[list[float]] = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            try:
                output = self._provider.encode(
                    batch, return_dense=True, return_sparse=False, return_colbert_vecs=False
                )
                vecs = output.get("dense_vecs", [])
                if vecs:
                    all_vecs.extend(vecs)
                else:
                    # 빈 벡터로 패딩
                    all_vecs.extend([[0.0] * _w.embedding.dimension for _ in batch])
            except Exception as e:  # noqa: BLE001
                logger.warning("Dense embedding batch %d failed: %s", i, e)
                all_vecs.extend([[0.0] * _w.embedding.dimension for _ in batch])

        if len(all_vecs) != len(texts):
            logger.error(
                "Dense index vector count mismatch: %d vecs vs %d texts",
                len(all_vecs), len(texts),
            )
            return

        matrix = np.array(all_vecs, dtype=np.float32)
        # L2 정규화 (cosine -> dot product)
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms = np.clip(norms, 1e-12, None)
        self._matrix = matrix / norms
        self._term_indices = indices

        logger.info(
            "Dense term index built: %d terms, shape=%s, memory=%.1fMB",
            len(indices),
            self._matrix.shape,
            self._matrix.nbytes / (1024 * 1024),
        )

    def search(self, query_text: str, top_k: int = _w.search.term_search_top_k) -> list[tuple[int, float]]:
        """코사인 유사도 기반 Top-K 검색.

        Args:
            query_text: 검색 쿼리 (term + term_ko + definition)
            top_k: 반환할 최대 건수

        Returns:
            (precomputed_index, cosine_score) 리스트
        """
        if self._matrix is None or not self._term_indices:
            return []

        try:
            output = self._provider.encode(
                [query_text], return_dense=True, return_sparse=False, return_colbert_vecs=False
            )
        except Exception:  # noqa: BLE001
            return []

        vecs = output.get("dense_vecs", [])
        if not vecs or not vecs[0]:
            return []

        q_vec = np.array(vecs[0], dtype=np.float32)
        q_norm = np.linalg.norm(q_vec)
        if q_norm < 1e-12:
            return []
        q_vec = q_vec / q_norm

        # dot product = cosine (both L2-normalized)
        scores = self._matrix @ q_vec

        actual_k = min(top_k, len(scores))
        if actual_k <= 0:
            return []

        top_indices = np.argpartition(scores, -actual_k)[-actual_k:]
        top_indices = top_indices[np.argsort(scores[top_indices])[::-1]]

        return [(self._term_indices[i], float(scores[i])) for i in top_indices]

    def search_batch(
        self, query_texts: list[str], top_k: int = _w.search.term_search_top_k, batch_size: int = _w.search.term_build_batch_size
    ) -> list[list[tuple[int, float]]]:
        """배치 코사인 유사도 검색.

        Args:
            query_texts: 검색 쿼리 리스트
            top_k: 각 쿼리당 Top-K
            batch_size: 임베딩 배치 크기

        Returns:
            각 쿼리에 대한 (precomputed_index, score) 리스트의 리스트
        """
        if self._matrix is None or not query_texts:
            return [[] for _ in query_texts]

        # 배치 임베딩
        all_vecs: list[list[float]] = []
        for i in range(0, len(query_texts), batch_size):
            batch = query_texts[i : i + batch_size]
            try:
                output = self._provider.encode(
                    batch, return_dense=True, return_sparse=False, return_colbert_vecs=False
                )
                all_vecs.extend(output.get("dense_vecs", []))
            except Exception:  # noqa: BLE001
                all_vecs.extend([[0.0] * _w.embedding.dimension for _ in batch])

        q_matrix = np.array(all_vecs, dtype=np.float32)
        norms = np.linalg.norm(q_matrix, axis=1, keepdims=True)
        norms = np.clip(norms, 1e-12, None)
        q_matrix = q_matrix / norms

        # 전체 유사도 행렬 (M, N)
        sim_matrix = q_matrix @ self._matrix.T

        results: list[list[tuple[int, float]]] = []
        actual_k = min(top_k, sim_matrix.shape[1])

        for i in range(sim_matrix.shape[0]):
            row = sim_matrix[i]
            if actual_k <= 0:
                results.append([])
                continue
            top_idx = np.argpartition(row, -actual_k)[-actual_k:]
            top_idx = top_idx[np.argsort(row[top_idx])[::-1]]
            results.append(
                [(self._term_indices[j], float(row[j])) for j in top_idx]
            )

        return results
