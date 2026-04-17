# 가중치 튜닝 가이드

검색/인제스트/캐시 하이퍼파라미터의 근거와 조정 방법.

**SSOT**: `src/config/weights/` (7 서브모듈). 코드에서는 `from src.config.weights import weights`.

---

## 검색 가중치 (RerankerWeights)

### Composite Reranker Score Fusion

```
composite = model_weight × model_score      # cross-encoder (주 신호)
          + base_weight  × base_score       # Qdrant RRF 원점수
          + source_weight × source_prior    # 출처 사전확률
          + position_weight × pos_decay     # 순위 감쇠
          + graph_bonus + keyword_bonus + entity_bonus
```

| 가중치 | 기본값 | 근거 |
|---|---|---|
| `model_weight` | 0.6 | cross-encoder가 가장 정확. 전체 점수의 60% |
| `base_weight` | 0.3 | Qdrant RRF는 recall용. 30% |
| `source_weight` | 0.1 | 출처 사전확률은 보조 신호. 10% |
| `position_weight` | 0.0 | 기본 비활성 (cross-encoder가 순위를 재정렬) |
| `faq_boost` | 1.2 | FAQ는 검증된 답변이므로 source_prior에 20% 가산 |
| `mmr_lambda` | 0.7 | 0=다양성, 1=관련성. 0.7은 관련성 우선 + 적당한 분산 |
| `entity_bonus` | 0.12 | 매장명/인명 일치 시 고정 보너스. 0.5 이상이면 relevance 압도 |

### 조정 시나리오

- **FAQ 답변이 너무 높게 나옴**: `faq_boost` 1.2 → 1.0
- **중복 결과가 많음**: `mmr_lambda` 0.7 → 0.5 (다양성 강화)
- **키워드 정확 매칭이 중요**: `keyword_boost_weight` 0.3 → 0.5
- **그래프 관계가 중요**: `graph_distance_weight` 0.15 → 0.25

---

## Hybrid Search (HybridSearchWeights)

| 가중치 | 기본값 | 근거 |
|---|---|---|
| `dense_weight` | 0.35 | 의미 유사도 (BGE-M3 dense) |
| `sparse_weight` | 0.35 | 키워드 매칭 (BM25/sparse) |
| `colbert_weight` | 0.30 | 토큰 레벨 매칭 (ColBERT) |

쿼리 유형별 자동 조정:
- 개념 질문: dense 0.45, sparse 0.25
- 절차 질문: dense 0.25, sparse 0.45
- 날짜 포함: dense 0.25, sparse 0.45

---

## 유사도 매칭 (SimilarityThresholds)

3-Layer decision zone:

| 구간 | CE 임계값 | Fallback 임계값 | 의미 |
|---|---|---|---|
| AUTO_MATCH | ≥ 0.85 | ≥ 0.90 | 자동 매칭 |
| REVIEW | ≥ 0.50 | ≥ 0.60 | 수동 검토 |
| NEW_TERM | < 0.50 | < 0.60 | 신규 용어 |

Fallback이 더 엄격한 이유: cross-encoder 없이 RRF만으로는 확신이 낮음.

---

## 캐시 (CacheConfig)

| 설정 | 기본값 | 근거 |
|---|---|---|
| `l1_max_entries` | 10,000 | 메모리 LRU. ~50MB |
| `l1_ttl_seconds` | 300 | 5분. 세션 내 반복 쿼리 |
| `l2_similarity_threshold` | 0.92 | Redis semantic. 92% 이상이면 같은 의도 |
| `l2_ttl_seconds` | 3600 | 1시간. 문서 변경 주기 고려 |

도메인별 TTL:
| 도메인 | TTL | 이유 |
|---|---|---|
| policy | 30분 | 정책은 최신이어야 |
| code | 30분 | 코드 변경 빈번 |
| kb_search | 1시간 | 균형 |
| general | 2시간 | 덜 민감 |

---

## Dedup (DedupConfig)

4-Stage 파이프라인:

| Stage | 임계값 | 메트릭 | 근거 |
|---|---|---|---|
| 1. Bloom filter | — | 해시 존재 | O(1) 빠른 필터 |
| 2. Jaccard | 0.80 | 단어 겹침 | 80% 이상 겹치면 유사 |
| 3. Cosine | 0.90 | 임베딩 유사 | 90% 이상이면 의미 동일 |
| 4. LLM conflict | — | 충돌 감지 | 최종 판정 |

`stage3_skip_threshold` (0.85): Jaccard가 이미 충분히 높으면 Stage 3 skip.

---

## 조정 방법

1. `src/config/weights/` 해당 파일 수정
2. 핫 리로드: `POST /api/v1/admin/config/weights` API로 런타임 변경
3. `weights.update_from_dict({"reranker.model_weight": 0.7})` 코드 호출
4. 변경 후 `scripts/distill/run_rag_evaluation.py`로 골든셋 평가 재실행

---

## 평가 기준

현재 스코어 (261 골든셋):
- Faithfulness: 0.62
- Relevancy: 0.78
- Completeness: 0.66
- Source Recall: 85%

가중치 변경 시 이 메트릭이 **하락하면 롤백**.
