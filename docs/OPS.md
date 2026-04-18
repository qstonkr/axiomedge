# Operations Runbook

장애 대응, 롤백, DB 작업, 캐시 관리, 모니터링 절차.

---

## 서비스 상태 확인

```bash
# API health
curl -sS http://localhost:8000/health | jq

# Docker services
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"

# 개별 서비스 로그
docker logs knowledge-local-postgres-1 --tail 50
docker logs knowledge-local-qdrant-1 --tail 50
```

### `/health` 응답 해석

| 필드 | true 조건 | false 시 액션 |
|---|---|---|
| `qdrant` | `get_collections()` 성공 | `docker restart knowledge-local-qdrant-1` |
| `neo4j` | `health_check()` 성공 | `docker restart knowledge-local-neo4j-1` |
| `embedding` | TEI / Ollama / ONNX 중 하나 ready | TEI URL 확인 → Ollama 상태 확인 |
| `llm` | Ollama `/api/version` 200 | `docker restart knowledge-local-ollama-1` |
| `redis` | `ping()` 성공 | `docker restart knowledge-local-redis-1` |
| `database` | session factory 존재 | PostgreSQL 로그 확인 |
| `paddleocr` | `/health` 200 | EC2 띄우기 또는 ignore (선택적) |

---

## 일반 장애 대응

### API 500 에러

```bash
# 1. 로그 확인
tail -100 logs/api.log | grep ERROR

# 2. Stack trace 분석
grep -A 10 "Traceback" logs/api.log | tail -30

# 3. 서비스 상태
curl http://localhost:8000/health | jq
```

### Search timeout

```bash
# Qdrant 상태
curl http://localhost:6333/collections | jq '.result.collections[].name'

# Collection 크기
curl http://localhost:6333/collections/kb_pbu_store | jq '.result.points_count'

# HNSW 최적화 (대규모 인제스트 후)
curl -X POST http://localhost:6333/collections/kb_pbu_store/index
```

### OOM (메모리 부족)

```bash
# Docker 메모리 사용
docker stats --no-stream

# 대응
# 1. Worker 수 줄이기: KNOWLEDGE_PIPELINE_MAX_WORKERS=2
# 2. Ollama 모델 크기 줄이기: exaone3.5:2.4b 대신 작은 모델
# 3. Redis maxmemory 조정: docker-compose.yml
```

---

## DB 작업

### 백업 (자동화)

| Store | 스크립트 | 보관 |
|---|---|---|
| PostgreSQL | `scripts/ops/backup_db.sh` | 기본 7개 (`KEEP_COUNT` 환경변수) |
| Qdrant | `scripts/ops/backup_qdrant.sh` | 기본 7개 (collection 별) |
| Neo4j | `scripts/ops/backup_neo4j.sh` | 기본 7개 (offline dump — 짧은 downtime) |

```bash
# 한 번에 — make 사용
make backup-all

# 개별
make backup-pg
make backup-qdrant
make backup-neo4j

# cron 권장 (예: 매일 03:00)
0 3 * * * cd /opt/axiomedge && make backup-all >> /var/log/axiomedge_backup.log 2>&1
```

복구:
- PostgreSQL: `docker exec -i <pg> psql -U knowledge knowledge_db < backups/knowledge_db_*.sql.gz` (gunzip 선행)
- Qdrant: snapshot recover API — `POST /collections/{name}/snapshots/upload` 또는 storage 디렉토리 교체
- Neo4j: `docker exec <neo4j> neo4j-admin database load <db> --from-path=/backups --overwrite-destination`

### 스키마 변경 (Alembic)

```bash
# 1. models.py 수정 (nullable=True default 권장)
# 2. autogenerate migration
make db-revision MSG="add new_field to distill_profiles"
# 3. migrations/versions/XXXX_*.py 검토
# 4. 적용
make db-upgrade
```

상세: [docs/MIGRATION_GUIDE.md](MIGRATION_GUIDE.md)

### 주요 조회 쿼리

```sql
-- 최근 검색 로그
SELECT query, answer, crag_confidence, latency_ms, created_at
FROM knowledge_usage_logs ORDER BY created_at DESC LIMIT 20;

-- 프로필별 학습 데이터 통계
SELECT profile_name, status, COUNT(*) FROM distill_training_data
GROUP BY profile_name, status ORDER BY profile_name, status;

-- 빌드 이력
SELECT id, profile_name, status, version, train_loss, created_at
FROM distill_builds ORDER BY created_at DESC LIMIT 10;

-- 엣지 서버 상태
SELECT store_id, status, last_heartbeat, model_version
FROM distill_edge_servers ORDER BY last_heartbeat DESC;

-- 베이스 모델 레지스트리
SELECT hf_id, verified, commercial_use, enabled FROM distill_base_models
ORDER BY sort_order;
```

---

## 캐시 관리

```bash
# Redis 전체 초기화 (개발용)
docker exec knowledge-local-redis-1 redis-cli FLUSHDB

# 특정 KB 캐시 제거
docker exec knowledge-local-redis-1 redis-cli KEYS "cache:*pbu*" | \
  xargs docker exec -i knowledge-local-redis-1 redis-cli DEL

# Redis 상태
docker exec knowledge-local-redis-1 redis-cli INFO stats | grep hits
docker exec knowledge-local-redis-1 redis-cli DBSIZE
```

---

## 모델 / Embedding 작업

```bash
# Ollama 모델 목록
docker exec knowledge-local-ollama-1 ollama list

# 모델 다운로드
docker exec knowledge-local-ollama-1 ollama pull exaone3.5:7.8b

# 모델 삭제 (디스크 확보)
docker exec knowledge-local-ollama-1 ollama rm exaone3.5:7.8b
```

### TEI (SageMaker) 연결 확인

```bash
# TEI health
curl -sS $BGE_TEI_URL/health | jq

# Embedding 테스트
curl -sS -X POST $BGE_TEI_URL/embed \
  -H "Content-Type: application/json" \
  -d '{"inputs": "테스트 문장"}' | jq '.[0][:5]'
```

---

## Edge 서버 관리

### Heartbeat 확인

```sql
-- 오프라인 서버 (24시간 이상 미응답)
SELECT store_id, last_heartbeat, status
FROM distill_edge_servers
WHERE last_heartbeat < NOW() - INTERVAL '24 hours'
ORDER BY last_heartbeat;
```

### 모델 업데이트 요청

```bash
# 단일 서버
curl -X POST http://localhost:8000/api/v1/distill/edge-servers/STORE001/request-update \
  -H "Content-Type: application/json" \
  -d '{"update_type": "model"}'

# 전체 프로필 서버
curl -X POST http://localhost:8000/api/v1/distill/edge-servers/bulk-request-update \
  -H "Content-Type: application/json" \
  -d '{"profile_name": "pbu-store", "update_type": "both"}'
```

---

## 롤백

### API 코드 롤백

```bash
git log --oneline -10          # 돌아갈 커밋 확인
git revert <commit_hash>       # revert commit 생성
# Docker rebuild + K8s rolling update
make docker-build && make k8s-deploy
```

### DB 스키마 롤백

```bash
# 백업에서 복구 (전체)
docker exec -i knowledge-local-postgres-1 \
  psql -U knowledge knowledge_db < backup_before_migration.sql

# 특정 column 만 되돌리기
docker exec knowledge-local-postgres-1 psql -U knowledge -d knowledge_db \
  -c "ALTER TABLE distill_profiles DROP COLUMN IF EXISTS new_field;"
```

### Distill 모델 롤백

```bash
# 이전 빌드로 롤백 (API)
curl -X POST http://localhost:8000/api/v1/distill/builds/<build_id>/rollback

# 또는 S3 에서 이전 manifest 복원
aws s3 cp s3://oreo-dev-ml-artifacts/models/edge/pbu-store/manifest.json.bak \
  s3://oreo-dev-ml-artifacts/models/edge/pbu-store/manifest.json
```

---

## 모니터링 체크리스트 (일일)

- [ ] `/health` 모든 항목 true
- [ ] `knowledge_usage_logs` 에 오늘 날짜 row 있음 (사용자 활동)
- [ ] Edge server heartbeat 24h 이내 (`distill_edge_servers`)
- [ ] Redis memory < maxmemory 70%
- [ ] Qdrant points_count 가 어제 대비 급감하지 않음
- [ ] API error rate < 1% (로그 기반)

---

## 참고

- 데이터 모델: `docs/DATA_MODEL.md`
- 배포: `docs/DEPLOYMENT.md`
- 설정 변수: `docs/CONFIGURATION.md`
- 문제 해결: `docs/TROUBLESHOOTING.md`
- 보안: `docs/SECURITY.md`
