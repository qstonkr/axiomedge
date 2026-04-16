# GraphRAG

**파일**: `src/pipeline/graphrag/extractor.py`, `src/pipeline/neo4j_loader.py`, `src/graph/`
**목적**: 문서에서 Entity + Relationship 추출 → Neo4j 저장 → 검색 시 graph expansion.

---

## 개요

일반 vector 검색은 "문장 유사도" 만 잡는다. GraphRAG 는 **엔티티 간 관계** 를
Neo4j 에 저장해서 "김철수는 강남점 점장이고, 강남점은 POS 시스템 장애가 있다"
같은 **연결 지식** 을 검색 결과에 보강한다.

```
인제스트:  chunk → LLM extraction → (Entity, Relationship) → Neo4j MERGE
검색:     query → entity 추출 → multi-hop traversal → graph context 첨부
```

---

## Entity 추출

### 과정

`src/pipeline/graphrag/extractor.py::GraphRAGExtractor`:

1. Chunk text 를 Teacher LLM (EXAONE SageMaker) 에 전달
2. `KOREAN_EXTRACTION_PROMPT` 로 JSON 구조화 추출 요청
3. 응답 파싱 → `(entity_name, entity_type, description)` + `(source, target, relation_type)`
4. Placeholder / corruption / non-person 필터 적용
5. Neo4j MERGE (idempotent)

### Entity Types (허용 목록)

| Type | 설명 | 예시 |
|---|---|---|
| `Person` | 사람 (이름) | 김철수, 이영희 |
| `Store` | 매장/점포 | 강남점, GN001 |
| `Team` | 팀/부서/본부 | 운영지원팀, PBU본부 |
| `System` | IT 시스템/플랫폼 | POS, ERP, 발주시스템 |
| `Product` | 상품/서비스 | 삼각김밥, 도시락 |
| `Concept` | 정책/규정/절차 | 폐기절차, 마감할인 |
| `Location` | 지역/주소 | 서울 강남구, 2층 |
| `Event` | 행사/사건 | 2024 신년행사 |

### Relationship Types (허용 목록)

```
MENTIONS, MANAGES, WORKS_AT, RELATED_TO,
HAS_ISSUE, CAUSED_BY, RESOLVED_BY, PART_OF
```

허용 목록 외 관계는 `RELATED_TO` 로 fallback.

---

## 필터링 규칙

LLM 이 생성하는 엔티티는 노이즈가 많아 다단계 필터를 거친다.

### Placeholder 필터

`_PLACEHOLDER_VALUES` — LLM 이 흔히 만드는 sentinel 값:

```python
{"미상", "불명", "unknown", "TBD", "N/A", "해당없음", "기타", ...}
```

→ 제거 (knowledge graph 에 무의미)

### Non-person Blocklist

`_NON_PERSON_BLOCKLIST` — Person 으로 오분류되는 조직/지역/시스템:

```python
{"카카오", "네이버", "제주", "서울", "GS", "LG", ...}
```

→ Person label 대신 적절한 타입으로 재분류 또는 제거

### Company Suffix 감지

카드, 보험, 은행 → Store/Organization (not Person)

### Location Suffix

`동`, `구`, `로`, `도` 으로 끝나는 → Location

### Team Suffix

`팀`, `본부`, `실`, `부서` → Team

### Tech/System 키워드

블록체인, 레디스, 클라우드, API → System

### OCR Corruption 필터

- Lone jamo (ㄱ, ㅂ 단독)
- 반복 음절 (가가가, ㅋㅋㅋ)
- 1자 한글 엔티티

→ 모두 제거 (OCR 오류 잔존물)

---

## Prompt 설계

`src/pipeline/graphrag/prompts.py::KOREAN_EXTRACTION_PROMPT`:

- Zero-shot 구조화 추출
- 한국어 도메인 컨텍스트 (GS Retail 편의점)
- JSON 출력 강제 (`entities: [{name, type, description}]`, `relationships: [{source, target, type}]`)
- 허용 entity type / relationship type 명시
- 최대 10 entities + 10 relationships per chunk

### Prompt injection 방어

PR1 (2026-04-16) 에서 chunk content 가 `<context>` 태그로 delimit + instruction 키워드 중화 적용. `src/llm/prompt_safety.py::safe_user_input` 참고.

---

## Neo4j 로딩

`src/pipeline/neo4j_loader.py`:

- **MERGE** 패턴 (idempotent) — 같은 entity 재인제스트해도 중복 노드 없음
- `entity_id` = `{kb_id}::{entity_name}` (KB 단위 격리)
- Relationship 도 MERGE — 빈도 카운트(`frequency`) 증가
- Batch size: `config_weights.pipeline.neo4j_batch_size = 5000`

### Cross-KB Entity

같은 이름의 entity 가 다른 KB 에 있으면 **별도 노드** (entity_id 에 kb_id 포함). Cross-KB 연결은 별도 `RELATED_TO` 관계로 명시적 linking 필요.

---

## 검색 시 Graph Expansion

`src/graph/multi_hop_searcher.py::MultiHopSearcher.find_related`:

1. Query 에서 entity 추출 (KiwiPy NER)
2. 추출된 entity 이름으로 Neo4j `find_related` — N-hop traversal
3. **PR3 에서 asyncio.gather 병렬화** (5개 seed 동시 쿼리)
4. 관련 엔티티의 context (definition, connected documents) → RAG 프롬프트에 첨부

### 파라미터

- `max_hops`: 기본 2 (`config_weights.graph.default_max_hops`)
- `max_results`: 기본 10
- `graph_distance_decay`: 0.3 (hop 수에 따른 relevance 감쇠)
- `entity_boost`: 1.15 (매칭 엔티티가 있는 chunk 에 score boost)

---

## Feature Flags

```python
# config_weights.py
weights.ingestion.enable_graphrag = True  # 인제스트 시 entity 추출
weights.graph.default_max_hops = 2        # 검색 시 hop 수
```

GraphRAG 비활성화 시: 인제스트에서 엔티티 추출 skip, 검색에서 graph expansion skip. Vector-only 검색으로 동작.

---

## 참고

- Entity/Relationship 추출: `src/pipeline/graphrag/extractor.py`
- Prompt: `src/pipeline/graphrag/prompts.py`
- Neo4j 로더: `src/pipeline/neo4j_loader.py`
- Multi-hop 검색: `src/graph/multi_hop_searcher.py`
- 도메인 용어: `docs/GLOSSARY.md`
- 데이터 모델 (Node/Relationship types): `docs/DATA_MODEL.md`
