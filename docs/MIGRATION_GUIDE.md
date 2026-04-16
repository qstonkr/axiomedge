# Migration Guide

DB 스키마 변경, 데이터 마이그레이션, 환경 전환 절차.
**Alembic 미사용** — `create_all()` + 수동 SQL 패턴.

---

## 스키마 변경 절차

### 새 Column 추가

```bash
# 1. Model 수정 (반드시 nullable=True 또는 default)
# src/database/models.py 또는 src/distill/models.py
new_field = Column(String(100), nullable=True)

# 2. 수동 ALTER TABLE (현재 DB 에 즉시 반영)
docker exec knowledge-local-postgres-1 psql -U knowledge -d knowledge_db \
  -c "ALTER TABLE my_table ADD COLUMN new_field VARCHAR(100);"

# 3. 앱 재시작 (create_all() 이 column 인식 — 이미 있으면 skip)

# 4. Backfill (필요 시)
docker exec knowledge-local-postgres-1 psql -U knowledge -d knowledge_db \
  -c "UPDATE my_table SET new_field = 'default_value' WHERE new_field IS NULL;"
```

### 새 Table 추가

```bash
# 1. Model class 추가 (해당 Base 에 연결)
# 2. 앱 재시작 — create_all() 이 자동 생성
# 3. Seed 데이터 필요 시: init_db.py 에 seed 함수 추가 (insert-if-missing 패턴)
```

### Column 제거

```bash
# 1. 코드에서 해당 column 참조 모두 제거
# 2. Model 에서 Column 정의 삭제
# 3. 수동 DROP (create_all() 은 DROP 하지 않음)
docker exec knowledge-local-postgres-1 psql -U knowledge -d knowledge_db \
  -c "ALTER TABLE my_table DROP COLUMN IF EXISTS old_field;"
# 4. 앱 재시작
```

### Table 이름 변경

```bash
# 1. 새 model class 생성 (__tablename__ = 새 이름)
# 2. 수동 RENAME
docker exec knowledge-local-postgres-1 psql -U knowledge -d knowledge_db \
  -c "ALTER TABLE old_name RENAME TO new_name;"
# 3. 기존 model class 제거
# 4. 모든 import / 참조 업데이트
```

---

## 안전 수칙

| 규칙 | 이유 |
|---|---|
| **항상 백업 먼저** | `pg_dump > backup_before.sql` |
| **nullable=True 로 column 추가** | 기존 row 에 NOT NULL 위반 방지 |
| **NOT NULL 은 2단계** | 1) nullable 추가 + backfill 2) ALTER SET NOT NULL |
| **DROP 은 코드 제거 확인 후** | 아직 참조하는 코드가 있으면 런타임 에러 |
| **테스트 환경에서 먼저** | 로컬 docker DB 에서 검증 후 프로덕션 적용 |
| **유지보수 창 (야간)** | 대규모 ALTER/backfill 은 서비스 부하 낮을 때 |

---

## Distill 스키마 특이사항

### DistillBase 분리

Distill 테이블은 `DistillBase` (독립 metadata) 로 core `KnowledgeBase` 와 분리됨. 각각 `create_all()` 별도 호출.

### Seed 데이터 (insert-if-missing)

```python
# src/distill/seed.py
async def seed_base_models(repo):
    for row in DEFAULT_BASE_MODELS:
        await repo.insert_base_model_if_missing(row)  # 있으면 skip
```

Admin 이 UI 에서 편집한 값은 seed 가 덮어쓰지 않음 (`INSERT ... ON CONFLICT DO NOTHING`).

### Config JSON Column

`distill_profiles.config` 는 TEXT column 에 JSON 저장:
```json
{"lora": {"r": 16, ...}, "training": {...}, "qa_style": {...}, "data_quality": {...}, "deploy": {...}}
```

읽기: `json.loads(model.config)` → dict
쓰기: `json.dumps(config_dict, ensure_ascii=False)` → TEXT

---

## Qdrant Collection 마이그레이션

### 벡터 차원 변경 시

차원이 바뀌면 기존 collection 은 사용 불가 — 삭제 후 재인제스트.

```bash
# 1. Collection 삭제
curl -X DELETE "http://localhost:6333/collections/kb_pbu_store"

# 2. 재인제스트
make ingest ARGS="--source /data/pbu --kb-id pbu-store"
```

### Collection 이름 변경

Qdrant 는 rename 미지원. 새 collection 생성 → 데이터 복사 → 구 collection 삭제.

---

## 환경 전환 (로컬 → 프로덕션)

| 항목 | 로컬 | 프로덕션 |
|---|---|---|
| DB | `docker compose` PG | AWS RDS |
| Qdrant | `docker compose` | K8s StatefulSet |
| Neo4j | `docker compose`, `NEO4J_AUTH=none` | K8s, `NEO4J_AUTH=neo4j/PASSWORD` |
| Redis | `docker compose`, 256MB | ElastiCache, 2GB+ |
| LLM | Ollama | SageMaker (`USE_SAGEMAKER_LLM=true`) |
| Embedding | Ollama / ONNX | TEI (`USE_CLOUD_EMBEDDING=true`) |
| OCR | 로컬 PaddleOCR | EC2 on-demand (`PADDLEOCR_INSTANCE_ID`) |
| Auth | `AUTH_ENABLED=false` | `AUTH_PROVIDER=keycloak` |

전환 시 `docs/CONFIGURATION.md` 참고해 env var 만 바꾸면 됨.

---

## 참고

- 데이터 모델: `docs/DATA_MODEL.md`
- 배포: `docs/DEPLOYMENT.md`
- 설정 변수: `docs/CONFIGURATION.md`
- Distill 모델: `src/distill/models.py`
- Core 모델: `src/database/models.py`
