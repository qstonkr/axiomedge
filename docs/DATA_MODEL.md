# Data Model

DB 스키마 요약 — PostgreSQL / Qdrant / Neo4j / Redis.
신규 쿼리 작성, 마이그레이션, 캐시 invalidation 시 참고.

---

## PostgreSQL

### Core Tables (`KnowledgeBase`)

| 테이블 | PK | 역할 |
|---|---|---|
| `kb_configs` | `kb_id` | KB 등록/설정 (search_group, categories, tier, status) |
| `knowledge_documents` | `id` | 문서 메타 (document_id, kb_id, hash, source_uri, owner) |
| `glossary_terms` | `id` | 용어사전 (term, synonyms, abbreviations, definition, kb_id) |
| `knowledge_usage_logs` | `id` | 검색/답변 로그 (query, answer, crag_confidence, latency_ms) |
| `knowledge_trust_scores` | `id` | 신뢰도 점수 (composite, model, citation, recency) |
| `rag_golden_set` | `id` | 평가 셋 (261 Q&A, faithfulness/relevancy 메트릭) |
| `document_owners` | `id` | 문서 소유권 (user_email, kb_id, document_id) |
| `topic_owners` | `id` | 주제 소유권 (user_email, topic, kb_id) |
| `document_error_reports` | `id` | 오류 리포트 (user, document, error_type, resolution) |
| `knowledge_data_sources` | `id` | 데이터 소스 레지스트리 (Confluence/Git/S3 연동 설정) |
| `knowledge_lifecycle_transitions` | `id` | 문서 상태 머신 이력 (from_state, to_state, reason) |
| `contributor_reputations` | `id` | 기여자 평판 (gamification) |
| `search_groups` | `id` | 검색 그룹 (name, description, kb_ids list) |
| `knowledge_categories` | `id` | KB 내 문서 분류 (L1/L2 카테고리) |

### Auth Tables (`KnowledgeBase`)

| 테이블 | PK | 역할 |
|---|---|---|
| `auth_users` | `id` | 사용자 (email, hashed_password, display_name) |
| `auth_roles` | `id` | 역할 (admin/curator/analyst/viewer) |
| `auth_user_roles` | `user_id + role_id` | 사용자-역할 매핑 |
| `auth_permissions` | `id` | 권한 (kb:read, kb:write, admin:manage 등) |
| `auth_role_permissions` | `role_id + permission_id` | 역할-권한 매핑 |
| `auth_activity_logs` | `id` | 접근 기록 (user, action, resource, timestamp) |
| `auth_refresh_tokens` | `id` | Refresh token 저장 (internal provider) |

### Distill Tables (`DistillBase`)

| 테이블 | PK | 역할 |
|---|---|---|
| `distill_profiles` | `name` | 빌드 프로필 (search_group, base_model, config JSON) |
| `distill_builds` | `id` (uuid) | 빌드 실행 이력 (status, metrics, gguf_sha256) |
| `distill_training_data` | `id` (uuid) | 학습 QA 쌍 (question, answer, source_type, status) |
| `distill_edge_logs` | `id` (uuid) | 엣지 서버 사용 로그 (query, answer, latency) |
| `distill_edge_servers` | `id` (uuid) | 등록된 엣지 서버 (store_id, status, heartbeat) |
| `distill_base_models` | `hf_id` | 베이스 모델 레지스트리 (license, verified, commercial_use) |

### Index 요약

주요 인덱스 (쿼리 성능):

```sql
-- distill
idx_distill_build_profile (profile_name)
idx_distill_build_status (status)
idx_train_data_profile (profile_name)
idx_train_data_source (source_type)
idx_train_data_status (status)
idx_train_data_batch (generation_batch_id)
idx_edge_server_store (store_id) UNIQUE
idx_edge_server_profile (profile_name)
idx_edge_log_profile (profile_name)
```

### 스키마 변경 방법

Alembic 없음 — `src/database/init_db.py::init_database()` 에서 `metadata.create_all()` 패턴.

1. `src/database/models.py` (core) 또는 `src/distill/models.py` (distill) 수정
2. 새 column 은 **반드시 `nullable=True`** 또는 `default` 부여 (기존 row 보호)
3. `docker exec postgres psql -U knowledge -d knowledge_db` 로 `ALTER TABLE` 수동 실행
4. 다음 앱 재시작 시 `create_all()` 이 새 테이블/column 자동 생성 (기존 유지)
5. Backfill 필요 시 별도 `UPDATE` SQL 작성

**주의**: `DROP COLUMN` / `RENAME TABLE` 은 `create_all()` 이 지원하지 않으므로 수동 SQL 필수.

---

## Qdrant (Vector Store)

### Collections

| Collection | Vector | Dim | 역할 |
|---|---|---|---|
| `kb_<kb_id>` (예: `kb_pbu_store`) | `bge_dense` (float32) + `bge_sparse` (ColBERT) | 1024 | KB 별 메인 검색 collection |
| `knowledge_entities` | `bge_dense` | 1024 | GraphRAG 엔티티 임베딩 |

### Payload 구조 (chunk)

```json
{
  "content": "상품 폐기는 POS 에 등록 후...",
  "document_name": "운영지침.pdf",
  "kb_id": "pbu-store",
  "source_uri": "confluence://page/12345",
  "content_hash": "sha256:abc123...",
  "morphemes": ["폐기", "POS", "등록"],
  "doc_date": "2026-03-01",
  "creator_name": "김철수",
  "creator_team": "운영지원팀",
  "embedding_model": "bge-m3",
  "embedding_dimension": 1024,
  "indexed_at": "2026-04-15T10:30:00Z"
}
```

### ID 생성

Chunk ID 는 `content_hash` 기반 deterministic UUID — 같은 내용 재인제스트 시 upsert (중복 없음).

### Collection 생성

`src/vectordb/collections.py::QdrantCollectionManager.ensure_collection()`:
- HNSW: `m=16`, `ef_construct=256`
- Dense: `bge_dense` named vector, cosine distance
- Sparse: `bge_sparse` named vector, dot product

---

## Neo4j (Graph Store)

### Node Types

| Label | 속성 | 예시 |
|---|---|---|
| `Person` | name, entity_id, kb_id, frequency | "김철수" (영업팀장) |
| `Store` | name, store_code, kb_id | "강남점" |
| `Team` | name, kb_id | "운영지원팀" |
| `System` | name, kb_id | "POS 시스템" |
| `Product` | name, kb_id | "삼각김밥" |
| `Concept` | name, kb_id, definition | "폐기 절차" |
| `Location` | name, kb_id | "서울시 강남구" |
| `Document` | name, document_id, kb_id | "운영지침.pdf" |

### Relationship Types

| Type | 방향 | 의미 |
|---|---|---|
| `MENTIONS` | Document → Entity | 문서가 엔티티를 언급 |
| `WORKS_AT` | Person → Store/Team | 소속 |
| `MANAGES` | Person → Store/Team | 관리 |
| `RELATED_TO` | Entity → Entity | 일반 관련 |
| `HAS_ISSUE` | Store/System → Concept | 이슈 보유 |
| `CAUSED_BY` | Concept → Concept | 인과 |
| `RESOLVED_BY` | Concept → Person/System | 해결 주체 |
| `PART_OF` | Entity → Entity | 소속/부분 |

### Cypher 예시

```cypher
-- 엔티티 주변 2-hop 탐색
MATCH path = (start:Entity {name: $entity_name})-[*1..2]-(related)
RETURN DISTINCT related.name, related.entity_id, length(path) as distance
ORDER BY distance LIMIT 10

-- 문서별 엔티티 목록
MATCH (d:Document {document_id: $doc_id})-[:MENTIONS]->(e)
RETURN e.name, labels(e), e.frequency
ORDER BY e.frequency DESC
```

---

## Redis (Cache)

### Key Patterns

| Pattern | TTL | 역할 |
|---|---|---|
| `cache:query:{hash}` | 5 min ~ 1 hour | L1 exact match cache (query + kb_ids hash) |
| `cache:semantic:{cluster}` | 24 hour | L2 semantic cache (embedding 유사도 ≥ 0.95) |
| `idempotency:{request_id}` | 10 min | 중복 요청 방지 |

### Invalidation

- KB 업데이트 (인제스트 완료) → `cache:query:*` 중 해당 KB key 삭제
- 전체 초기화: `redis-cli FLUSHDB`
- KB 단위: `redis-cli KEYS "cache:*:<kb_id>:*" | xargs redis-cli DEL` (패턴 매칭)

### 설정

```
maxmemory 256mb (개발) / 2GB+ (프로덕션)
maxmemory-policy allkeys-lru
appendonly yes
```

---

## 참고

- 스키마 코드: `src/database/models.py` (core), `src/distill/models.py` (distill)
- Collection 관리: `src/vectordb/collections.py`
- GraphRAG: `src/pipeline/graphrag/extractor.py`
- 캐시: `src/cache/redis_cache.py`
- 마이그레이션 가이드: `docs/MIGRATION_GUIDE.md` (Phase B PR12 에서 추가 예정)
