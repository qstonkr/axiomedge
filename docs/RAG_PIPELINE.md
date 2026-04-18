# RAG Search Pipeline

**파일**: `src/api/routes/search.py` + `src/api/routes/search_helpers.py` + `src/search/*`
**Entry point**: `POST /api/v1/search/hub` (hub_search)
**목적**: 사용자 질의 → 검색 + 재정렬 + LLM 답변 생성을 단일 파이프라인으로.

이 문서는 9단계(+ 서브 단계) 파이프라인의 **입출력 스키마, 튜닝 파라미터, 캐시 정책, 가중치 근거** 를 설명한다. `CLAUDE.md` 의 요약만으로는 검색 튜닝을 할 수 없어서 이 문서가 있다.

---

## 목차

1. [전체 흐름](#전체-흐름)
2. [단계별 상세](#단계별-상세)
3. [가중치 / 튜닝 파라미터](#가중치--튜닝-파라미터)
4. [캐시 계층](#캐시-계층)
5. [응답 스키마](#응답-스키마)
6. [실패 처리 / fallback](#실패-처리--fallback)
7. [관찰성 / 디버깅](#관찰성--디버깅)

---

## 전체 흐름

```
사용자 질의 (HubSearchRequest)
   │
   ├─ 0  L1 cache check → hit 시 즉시 반환
   │
   ├─ 1  Resolve collections (kb_ids / group_id / group_name → KB list)
   │        └─ filter: KB registry active_kb_ids (60s TTL 캐시)
   │
   ├─ 2  Query preprocess (typo fix / temporal resolve / normalize)
   │        └─ 2b  Query expansion (LLM or glossary)
   │        └─ 2.5 Query classify (factual/procedural/comparative)
   │
   ├─ 3  Embed query (dense + sparse + colbert)
   │
   ├─ 4  Qdrant hybrid search (per KB) + RRF merge
   │        ├─ 4.3  Keyword fallback (Qdrant scroll, parallel gather)
   │        ├─ 4.35 Identifier search (JIRA, filename, CamelCase)
   │        ├─ 4.4  Keyword boost
   │        ├─ 4.42 Document diversity (max 5 / doc, intra-doc Jaccard)
   │        ├─ 4.45 Date filter (doc_date metadata)
   │        └─ 4.46 Week-name search (N월 N주차 doc name)
   │
   ├─ 4.5  Passage cleaning (boilerplate / citation strip)
   ├─ 4.6  Cross-encoder rerank (TEI or local)
   │
   ├─ 5   Composite rerank (model 0.6 + base 0.3 + source 0.1 + entity boost)
   │        └─ 5.5  Tree context expansion (상위 summary 노드)
   │        └─ 5.6  Trust / freshness score
   │
   ├─ 6   GraphRAG expansion (Neo4j entity/relation enrichment)
   │
   ├─ 7   CRAG evaluation (retrieval quality → confidence)
   │
   ├─ 8   LLM answer generation (tiered: factual/procedural/opinion)
   │        ├─ 8b  Conflict detection (cross-KB 답변 상충)
   │        ├─ 8c  Follow-up question generation
   │        └─ 8d  Transparency block (출처 표시)
   │
   ├─ 9   Answer guard (hallucination detection)
   │
   └─ 9b  Usage log (PostgreSQL) + cache store (L1 + L2)
```

전체 코드: `src/api/routes/search.py::hub_search` (약 140 줄의 오케스트레이터, 각 단계는 `_step_*` helper 함수로 추출돼 있음). **SRP 리팩터 대상** — PR9 (Phase B) 에서 `SearchPipeline` 클래스로 분리 예정.

---

## 단계별 상세

### 0. L1 Cache check

- **구현**: `_step_cache_check` in `search.py`
- **위치**: Request → (hash) → Redis `cache:query:{hash}`
- **TTL**: L1 5분 (`src/cache/redis_cache.py::RedisCache.__init__(ttl=3600)` 전역; 실제 L1 은 더 짧을 수 있음)
- **L1/L2 구분**:
  - L1: 정확 일치 (쿼리 + KB set 해시)
  - L2: semantic 유사도 ≥ 0.95 (embedding 기반)
- **Miss 시**: 다음 단계로. `_step_log_usage` 가 응답 끝에 결과를 저장.

**튜닝 포인트**:
- `src/config.py::CacheSettings.ttl` (L1)
- `src/config_weights.py::CacheConfig.semantic_threshold` (L2 유사도)

---

### 1. Resolve collections

- **구현**: `_step_resolve_collections` + `_filter_by_kb_registry`
- **입력 우선순위**:
  1. `request.kb_ids: list[str]` — 명시
  2. `request.kb_filter.kb_ids` — 구조화 필터
  3. `request.group_id / group_name` — SearchGroup 테이블에서 resolve
  4. Fallback: Qdrant 의 모든 collection (기본 `["knowledge"]`)
- **KB registry 필터**: `status=active` 인 KB 만 통과
- **Cache**: `search_helpers.get_active_kb_ids()` — 60s TTL 메모리 캐시 (PR3 에서 추가)

**튜닝 포인트**:
- `src/api/routes/search_helpers.py::_KB_REGISTRY_CACHE_TTL_S = 60.0`

---

### 2. Query preprocess

- **구현**: `src/search/query_preprocessor.py::QueryPreprocessor`
- **단계**:
  - **Typo correction**: `src/ocr/domain_dict.py` 로 OCR 사전 기반 유사 단어 매칭 (choseong fuzzy)
  - **Temporal resolution**: "금주" / "이번 주" → `2026-04-14 ~ 2026-04-20` 구체 날짜
  - **Normalize**: 특수문자 정리, 공백 정규화
- **출력**: `preprocessed_query` (정규화된 문자열)

**관련 파일**: `src/search/query_preprocessor.py`, `src/ocr/ocr_corrector.py`

### 2b. Query expansion

- **구현**: `src/search/query_expander.py::QueryExpander`
- **전략**: glossary 기반 synonym + (선택) LLM paraphrase
- **출력**: `expanded_terms: list[str]` — 검색 시 keyword boost 에 사용
- **주의**: LLM 호출이라 latency 영향 — KB 단위 cache 권장 (Phase B)

### 2.5. Query classify

- **구현**: `src/search/query_classifier.py::QueryClassifier`
- **카테고리**: `factual` / `procedural` / `comparative` / `unknown`
- **효과**: 단계 4 의 dense/sparse 가중치 결정, 단계 8 의 LLM 프롬프트 템플릿 선택

---

### 3. Embed query

- **구현**: `src/embedding/provider_factory.py` → TEI / Ollama / ONNX 중 하나
- **결과**: `{dense_vecs, sparse_vecs, colbert_vecs}` dict
- **차원**: `src/config_weights.py::EmbeddingConfig.dimension = 1024` (BGE-M3 고정, SSOT)
- **Batch size** (forward pass): `EmbeddingConfig.batch_size = 32`
- **Cache**: embedder 자체 LRU (`EmbeddingConfig.cache_size = 512`)

**튜닝 포인트**:
- `USE_CLOUD_EMBEDDING=true` → TEI (SageMaker) 사용
- TEI fallback: Ollama → ONNX
- 상세: `docs/CONFIGURATION.md`

---

### 4. Qdrant hybrid search

- **구현**: `src/vectordb/search.py::QdrantSearchEngine`
- **방식**: RRF (Reciprocal Rank Fusion) — dense + sparse 결합
- **가중치**: `src/config_weights.py::HybridSearchConfig.dense_weight / sparse_weight`
- **Top-k 확장**: `effective_top_k = top_k × weights.retrieval.top_k_multiplier` (단계 2.5 classify 기반)

### 4.3. Keyword fallback

- **구현**: `search.py::_step_keyword_fallback`
- **발동 조건**: 초기 검색 결과가 query keyword 를 포함하지 않을 때
- **동작**: 각 collection 에 Qdrant scroll (morphemes + content field match) — **PR3 에서 `asyncio.gather()` 로 병렬화**
- **Timeout**: 3.0s (httpx timeout)

### 4.35. Identifier search

- **구현**: `search_helpers.py::_extract_identifiers`
- **패턴**: 금액 (`6,720,009`), JIRA key (`GRIT-12345`), filename, CamelCase
- **효과**: 정확 일치 chunk 를 상단 boost

### 4.4 ~ 4.46. Post-filter 단계

- **Keyword boost** (`4.4`): expansion 단계 output term 매칭 chunk boost
- **Document diversity** (`4.42`): 같은 문서에서 5개 이상 선정 금지, intra-doc Jaccard 유사도로 중복 chunk dedupe
- **Date filter** (`4.45`): `doc_date` metadata 가 query 의 temporal range 와 매칭되는 chunk 만
- **Week-name search** (`4.46`): "4월 3주차" 같은 패턴 → document name 매칭

---

### 4.5 ~ 4.6. Passage cleaning & Cross-encoder rerank

- **Passage cleaning**: `src/search/passage_cleaner.py::clean_chunks` — boilerplate, citation 태그 제거
- **Cross-encoder rerank**: `src/search/cross_encoder_reranker.py` — TEI (`bge-reranker-v2-m3`) 또는 local cross-encoder. 배치 크기 32.

---

### 5. Composite rerank

- **구현**: `src/search/composite_reranker.py::CompositeReranker`
- **공식** (기본값, `config_weights.py::RerankerWeights`):

```
final_score = model_weight   × cross_encoder_score    # 0.6
            + base_weight    × base_score             # 0.3 (original BM25/RRF)
            + source_weight  × source_credibility     # 0.1 (source type prior)
            + entity_boost   × entity_match_factor    # 추가
```

- **가중치 근거**:
  - `model_weight=0.6`: fine-tuned cross encoder 가 가장 강한 relevance signal
  - `base_weight=0.3`: lexical base 로 fine-tuned 가 완전히 놓치는 경우 방지
  - `source_weight=0.1`: KB 별 신뢰도 prior (`qdrant` > `graph` > `faq`)
- **Entity boost**: query 에서 추출한 entity (store/person/system) 가 chunk 에 매칭되면 score × 1.15

**튜닝 포인트** (전부 `config_weights.py` 에 있음):
- `RerankerWeights.model_weight / base_weight / source_weight`
- `RerankerWeights.faq_boost = 1.2`
- `RerankerWeights.mmr_lambda = 0.7` (diversity vs relevance tradeoff)
- Source credibility: `source_qdrant=1.0`, `source_web=0.95`, `source_graph=0.98`

### 5.5. Tree context expansion

- **구현**: `src/search/tree_context_expander.py`
- **역할**: Top-k chunk 의 **parent summary node** (summary tree) 를 추가 맥락으로 공급
- **발동 조건**: `query_type in ("comparative", "procedural")` 일 때만

### 5.6. Trust / freshness score

- **구현**: `src/search/trust_score_service.py`
- **입력**: chunk 의 `created_at`, `updated_at`, `trust_score` metadata
- **공식**:
  - `freshness = 2 ** (-age_days / half_life)` (half_life 180일 기본)
  - `trust_composite = model × citation × recency` (가중 합산)
- **반영**: chunk score 에 blending

---

### 6. Graph expansion

- **구현**: `src/graph/multi_hop_searcher.py::MultiHopSearcher`
- **동작**:
  1. Query 에서 entity 추출 (KiwiPy NER)
  2. Neo4j `find_related` — `MENTIONS` / `WORKS_AT` / `RELATED_TO` 관계로 N-hop 탐색
  3. 관련 엔티티의 추가 context (definitions, connected documents) 를 응답에 첨부
- **병렬화**: PR3 에서 `asyncio.gather()` 로 5개 entity 동시 쿼리
- **Max hops**: 기본 2 (`weights.graph.default_max_hops`)

---

### 7. CRAG evaluation

- **구현**: `src/search/crag_evaluator.py::CragEvaluator`
- **목적**: 검색 결과 자체의 품질 평가 → `confidence` 레이블 (높음/중간/낮음) 결정
- **공식**:

```
crag_score = 0.4 × relevance      # top-k chunk mean similarity
           + 0.3 × completeness   # query coverage
           + 0.3 × freshness      # age decay
```

- **Action**:
  - `correct` (>= 0.8): 바로 LLM 생성
  - `ambiguous` (0.5 ~ 0.8): LLM + hedge 문구
  - `incorrect` (< 0.5): "정보 부족" 응답 + 후속 질문만

**튜닝 포인트**: `config_weights.py::CragConfig.threshold_*`

---

### 8. LLM answer generation

- **구현**: `src/search/tiered_response.py::TieredResponseGenerator`
- **Tier** (`QueryType` 기반):
  - **Factual** → `FACTUAL_PROMPT` (한국어, 사실 기반)
  - **Procedural** → `PROCEDURAL_PROMPT` (단계별)
  - **Comparative** → `COMPARATIVE_PROMPT` (비교 표 유도)
  - **Default** → general
- **Prompt injection 방어**: chunk content + metadata 가 `src/llm/prompt_safety.py::safe_user_input` 로 XML delimit + neutralize (PR1)
- **LLM client**: `src/llm/ollama_client.py::OllamaClient` (기본) 또는 `sagemaker_client.py` (USE_SAGEMAKER_LLM=true)

### 8b. Conflict detection

- **구현**: `search.py::_check_kb_pair_conflict`
- **발동 조건**: 여러 KB 결과 혼합 시
- **동작**: 동일 주제에 대해 KB 간 답변이 상충하면 `conflicts: [{kb_a, kb_b, reason}]` 응답에 첨부

### 8c. Follow-up question generation

- **구현**: LLM 1회 추가 호출 (`prompt_safety.safe_user_input` 적용)
- **출력**: `followups: [str, str, str]` — UI 에 제안 버튼으로 노출

### 8d. Transparency block

- **구현**: `src/search/transparency_formatter.py::TransparencyFormatter`
- **출력**: 답변 아래 "출처:" 섹션 — 사용된 chunk 의 document 이름 + rank + score

---

### 9. Answer guard (hallucination detection)

- **구현**: `src/search/answer_guard.py::AnswerGuard`
- **방식**: 답변과 출처 chunk 간 embedding 유사도 ≥ threshold 확인
- **실패 시**: 답변을 "정보 부족" 문구로 대체

**튜닝 포인트**: `AnswerGuard` threshold (기본 0.8)

---

### 9b. Usage log + cache store

- **구현**: `_step_log_usage` + Redis L1 store
- **Usage log**: PostgreSQL `knowledge_usage_logs` 테이블에 `query / answer / chunks_used / crag_confidence / latency_ms` 등 기록
- **Cache store**: 성공 응답만 (confidence 낮음/no-result 는 캐시 안 함)

---

## 가중치 / 튜닝 파라미터

### 위치

모든 가중치는 **`src/config_weights.py`** SSOT. 파일 열어서 dataclass 별로 찾기:

| Dataclass | 주요 파라미터 | 영향 |
|---|---|---|
| `HybridSearchConfig` | `dense_weight`, `sparse_weight` | RRF 병합 비율 |
| `RerankerWeights` | `model_weight`, `base_weight`, `source_weight`, `faq_boost`, `mmr_lambda` | Composite rerank |
| `RetrievalConfig` | `top_k_multiplier`, `max_chunks_per_document` | 확장 배수 / diversity cap |
| `CragConfig` | `threshold_correct`, `threshold_ambiguous` | CRAG action 결정 |
| `GraphConfig` | `default_max_hops`, `graph_distance_decay` | 그래프 확장 |
| `EmbeddingConfig` | `dimension`, `batch_size`, `cache_size` | 임베더 기본 |
| `CacheConfig` | `l1_ttl`, `l2_semantic_threshold` | 캐시 정책 |

### 변경 절차

1. `config_weights.py` 수정
2. `uv run pytest tests/unit/ -k "config or weights"` 로 회귀 확인
3. 평가: `AWS_PROFILE=... uv run python scripts/run_rag_evaluation.py` (golden set)
4. 지표 비교: faithfulness / relevancy / completeness / source recall

### 현재 스코어 (2026-04-16 기준)

- Faithfulness: 0.62
- Relevancy: 0.78
- Completeness: 0.66
- Source recall: 85%

`docs/CONFIGURATION.md` 에 env var 오버라이드 방법 참고.

---

## 캐시 계층

| 계층 | 위치 | TTL | Key |
|---|---|---|---|
| L1 (exact) | Redis | 5 min ~ 1 hour | hash(query + kb_ids) |
| L2 (semantic) | Redis | 24 hour | embedding cluster |
| KB registry | Memory | 60s | registry instance id |
| Embedder | Memory (LRU) | session | text hash |
| Glossary | Memory | 5 min | kb_id |

**Invalidation**:
- KB 업데이트 (ingest 완료) → KB-level invalidation (`cache:kb:{kb_id}:*` 삭제)
- 전체 초기화: `redis-cli FLUSHDB`

---

## 응답 스키마

### Request

```python
class HubSearchRequest(BaseModel):
    query: str
    kb_ids: list[str] | None = None
    kb_filter: KbFilter | None = None
    group_id: int | None = None
    group_name: str | None = None
    top_k: int = 10
    include_answer: bool = True
    document_filter: list[str] | None = None
    # ... etc (상세: src/api/routes/search.py)
```

### Response (요약)

```python
{
    "query": "폐기 절차",
    "answer": "상품 폐기는 POS 에 등록한 뒤...",
    "confidence": "높음",  # 높음 / 중간 / 낮음
    "crag_action": "correct",
    "chunks": [
        {
            "chunk_id": "...",
            "document_name": "운영지침.pdf",
            "kb_id": "pbu-store",
            "content": "...",
            "score": 0.87,
            "metadata": {...}
        }
    ],
    "sources": [  # transparency block
        {"rank": 1, "document": "운영지침.pdf", "score": 0.87}
    ],
    "conflicts": [],        # cross-KB 상충 시
    "followups": [          # 제안 질문
        "폐기 박스 회수 일정은?",
        "이의 제기 절차는?",
        "POS 등록 방법은?"
    ],
    "transparency": "...",  # 출처 block markdown
    "latency_ms": 823,
    "cache_hit": false
}
```

상세: `docs/API.md` 의 `/api/v1/search/hub` 섹션.

---

## 실패 처리 / fallback

| 단계 | 실패 시 동작 |
|---|---|
| Cache | 다음 단계 진행 (graceful) |
| Collections resolve | KB 없으면 400 반환 |
| Embed query | TEI 실패 → Ollama → ONNX (provider chain) |
| Qdrant search | 단계 4.3 keyword fallback 활성화 |
| Rerank | cross-encoder 실패 → base_score 만 사용 |
| Graph expansion | Neo4j 실패 → warning 로그, chunks 만으로 진행 |
| CRAG | 실패 → `confidence="낮음"` 처리 |
| LLM generate | 실패 → extractive 응답 (top chunk content 그대로) |
| Answer guard | hallucination 감지 → "정보 부족" 응답 |
| Usage log | 실패 → warning 로그 (PR2 에서 silent 제거) |

**Bare except 금지** (PR2): 모든 실패는 `logger.warning(..., exc_info=...)` 호출.

---

## 관찰성 / 디버깅

### 로그

```bash
# API 서버 로그 tail
make api  # foreground, Ctrl+C 로 정지

# 또는 파일 로그
tail -f logs/api.log | grep hub_search
```

각 단계는 `logger.info` / `logger.debug` 로 주요 이벤트 기록.

### 메트릭

- **Usage log**: `SELECT * FROM knowledge_usage_logs ORDER BY created_at DESC LIMIT 10;`
- **Latency**: response `latency_ms` 필드
- **Cache hit rate**: Redis `INFO stats` 또는 `_step_cache_check` 로그 카운트

### 평가 셋 재실행

```bash
AWS_PROFILE=$AWS_PROFILE uv run python scripts/run_rag_evaluation.py          # 전체 (261 문항)
AWS_PROFILE=$AWS_PROFILE uv run python scripts/run_rag_evaluation.py g-espa   # 단일 KB
```

### 개별 단계 디버깅

각 step 은 `_step_*` 함수로 추출돼 있어 단위 테스트 + 개별 실행 가능:

```python
# python REPL 에서
from src.api.routes.search import _step_resolve_collections
import asyncio
asyncio.run(_step_resolve_collections(request, state))
```

---

## 향후 리팩터 (계획)

- **PR9 (Phase B)**: `hub_search` 13 단계를 `SearchPipeline` 클래스 + `SearchStage` Protocol 로 분리. 각 단계 독립 테스트 가능.
- **Plugin auto-discover**: 검색 단계 추가 시 `register_stage()` 데코레이터만으로 파이프라인 삽입.

상세: `docs/IMPROVEMENT_PLAN.md` Phase B 섹션.
