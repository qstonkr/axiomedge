# Migration Guide

DB 스키마 변경, 데이터 마이그레이션, 환경 전환 절차.

**Alembic 적용 (2026-04 ~)** — 신규 변경은 `alembic revision` 으로 관리.
기존 DB 는 baseline (`0001_baseline`) 으로 stamp 됨 (`init_db.py` 가 자동 처리).

> **Production 운영 시 가장 중요한 것**: zero-downtime — `migrations/PATTERNS.md` 의
> "위험 변경 → 2-step 분리" 패턴을 반드시 따르세요.

---

## 빠른 명령

```bash
make db-init                              # 신규 DB: create_all + alembic stamp head
make db-upgrade                           # 최신 head 까지 적용
make db-revision MSG="add user_prefs"     # autogenerate 새 migration
make db-history                           # migration 히스토리
make db-current                           # 현재 적용된 revision
make db-check FILE=migrations/versions/0002_xxx.py  # zero-downtime 안전성 사전 검사
```

---

## 스키마 변경 절차

### 권장: Alembic migration

```bash
# 1. Model 수정 (nullable=True 또는 default 권장)
# src/stores/postgres/models.py 또는 src/distill/models.py
new_field = Column(String(100), nullable=True)

# 2. autogenerate (metadata diff 로 새 migration 파일 생성)
make db-revision MSG="add new_field to my_table"

# 3. 생성된 migrations/versions/XXXX_<msg>.py 검토
#    - 데이터 손실 가능 변경(drop column/table) 은 특히 주의
#    - 필요하면 backfill SQL 을 upgrade() 끝에 op.execute("UPDATE ...") 추가

# 4. 적용
make db-upgrade
```

### Hotfix (Alembic 우회 — 비추천)

`init_db.py` 의 `create_all()` 은 여전히 동작하므로 모델만 수정 후 앱 재시작해도 신규 컬럼/테이블은 만들어집니다. 단, **다른 환경 DB 와 schema drift 가 발생하므로 즉시 baseline migration 을 만들어 stamp 하세요**.

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

## B-0: Multi-tenant + RBAC 마이그레이션

자세한 모델 + 매트릭스는 `docs/RBAC.md`. 여기서는 기존 환경 → B-0 전환
체크리스트만.

### 사전 조건

1. `make stop && make start` — Postgres / Qdrant / Neo4j / Redis 기동
2. 백업 (point-of-no-return 가드)
   ```bash
   docker exec knowledge-local-postgres-1 \
     pg_dump -U knowledge knowledge_db > backup-pre-b0.sql
   ```

### 적용 순서

```bash
# 1. 스키마 — alembic 0002 → 0003 → 0004 순차
uv run alembic upgrade head

# 2. KB 백필 (idempotent, dry-run 기본)
uv run python scripts/backfill_org_id.py            # 카운트 표시
uv run python scripts/backfill_org_id.py --apply    # 실제 update

# 3. 앱 기동 — startup 시 seed_defaults() 가 자동 실행:
#    - canonical role 4개 + legacy role 5개 (is_legacy=True) 시드
#    - 모든 active user 를 default-org 멤버로 등록
make api
```

### Role 매핑 (legacy → canonical)

| Legacy role | 자동 매핑 | 비고 |
|---|---|---|
| `admin` | `ADMIN` | DB 시드만 유지, 신규 부여 X |
| `kb_manager` | `ADMIN` (또는 KB-scoped `MEMBER`) | scope 필요시 명시 |
| `editor` | `MEMBER` | document/glossary write 동일 |
| `contributor` | `MEMBER` | |
| `viewer` | `VIEWER` | 동일 |

자동 reassign 스크립트는 제공하지 않음 — admin user 가 신규 user 부여 시
canonical role 사용. 기존 legacy 부여는 작동 유지 (RBAC engine 이 호환).

### Org switcher 활성화 (선택)

다중 org 멤버 user 를 위한 `POST /api/v1/auth/switch-org` 또는
`X-Organization-Id` 헤더는 백엔드 측 구현 완료. 프론트는 B-1 이후 노출.

### 롤백 절차

```bash
uv run alembic downgrade 0003_rbac_b0   # FK + NOT NULL 제거 (0004 → 0003)
# 또는 더 위로:
uv run alembic downgrade 0001_baseline  # auth_roles.is_legacy + organizations 제거
```

DB dump 가 있다면 `psql -U knowledge knowledge_db < backup-pre-b0.sql`.

### 검증

```bash
# 1. 모든 KB 가 default-org 에 매핑
docker exec knowledge-local-postgres-1 psql -U knowledge -d knowledge_db -tAc \
  "SELECT count(*) FROM kb_configs WHERE organization_id IS NULL;"
# → 0

# 2. role 시드 9개
docker exec knowledge-local-postgres-1 psql -U knowledge -d knowledge_db -tAc \
  "SELECT count(*) FROM auth_roles;"
# → 9 (4 canonical + 5 legacy)

# 3. cross-tenant 테스트
uv run pytest tests/unit/test_cross_tenant_unit.py -v --no-cov
```

---

## 참고

- 데이터 모델: `docs/DATA_MODEL.md`
- 배포: `docs/DEPLOYMENT.md`
- 설정 변수: `docs/CONFIGURATION.md`
- Distill 모델: `src/distill/models.py`
- Core 모델: `src/database/models.py`
- RBAC 가이드: `docs/RBAC.md`
