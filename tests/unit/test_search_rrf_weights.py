"""RRF fusion weight 검증 — 1글자 차이 한국어 단어 혼동 완화."""

from __future__ import annotations

from src.config.weights.search import SimilarityThresholds


def test_rrf_weights_sum_to_one():
    """RRF 채널 가중치 합산 = 1.0 (정규화)."""
    c = SimilarityThresholds()
    total = c.rrf_edit_weight + c.rrf_sparse_weight + c.rrf_dense_weight
    assert abs(total - 1.0) < 1e-9


def test_lexical_weight_dominates_dense():
    """lexical (edit + sparse) 합 ≥ dense — 1글자 차이 한국어 구분력 확보."""
    c = SimilarityThresholds()
    lexical = c.rrf_edit_weight + c.rrf_sparse_weight
    assert lexical >= c.rrf_dense_weight, (
        f"lexical={lexical} should match or exceed dense={c.rrf_dense_weight} "
        "for Korean 1-char-diff disambiguation"
    )


def test_no_single_channel_majority():
    """단일 채널이 50% 이상이면 안 됨 — RRF fusion 의 의미 약화."""
    c = SimilarityThresholds()
    weights = [c.rrf_edit_weight, c.rrf_sparse_weight, c.rrf_dense_weight]
    assert max(weights) < 0.50, f"max channel weight {max(weights)} >= 0.50"


def test_dense_min_weight_kept():
    """dense 가중치 최소 0.35 이상 — 의미 유사 검색 capacity 보수적 보호."""
    c = SimilarityThresholds()
    assert c.rrf_dense_weight >= 0.35
