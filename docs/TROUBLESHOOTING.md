# Troubleshooting

## 서비스 시작 문제

### API 서버가 503 반환

API는 백엔드 미준비 시 degraded 모드로 시작됩니다.

```bash
# 어떤 서비스가 실패했는지 확인
curl http://localhost:8000/health
# {"status": "degraded", "checks": {"qdrant": false, ...}}

# 개별 서비스 상태 확인
docker compose ps
docker compose logs qdrant
docker compose logs neo4j
```

**원인**: Qdrant 또는 임베딩 서비스 미시작
**해결**: `make start`로 인프라 먼저 시작 후 `make api`

### Ollama 모델 미로드

```
Error: model 'exaone3.5:7.8b' not found
```

```bash
# 모델 확인
docker exec -it $(docker ps -q -f name=ollama) ollama list

# 모델 다운로드
docker exec -it $(docker ps -q -f name=ollama) ollama pull exaone3.5:7.8b
```

### Qdrant 연결 실패

```
ConnectionError: Connection refused at http://localhost:6333
```

```bash
docker compose logs qdrant
# 포트 충돌 확인
lsof -i :6333
```

### Neo4j 연결 실패

```
ServiceUnavailable: Unable to retrieve routing information
```

```bash
docker compose logs neo4j
# NEO4J_AUTH=none 설정 확인 (.env)
```

Neo4j가 비활성화되어도 시스템은 정상 작동합니다 (`NEO4J_ENABLED=false`). 그래프 기능만 비활성화됩니다.

### PostgreSQL 연결 실패

```
asyncpg.InvalidCatalogNameError: database "knowledge_db" does not exist
```

```bash
# DB 자동 생성 확인
docker compose exec postgres psql -U knowledge -l

# 수동 생성 필요 시
docker compose exec postgres createdb -U knowledge knowledge_db
```

---

## OCR 문제

### PaddleOCR 500 에러

```
POST /ocr → 500 Internal Server Error
```

**주요 원인 분류**:

1. **ConvertPirAttribute2RuntimeAttribute 에러**
   - 원인: PaddlePaddle이 OneDNN 포함 빌드됨
   - 해결: 소스에서 `-DWITH_MKLDNN=OFF`로 재빌드 (CLAUDE.md 참조)

2. **메모리 부족 (OOM)**
   - 원인: 대용량 이미지 처리
   - 해결: `OCR_MAX_IMAGE_DIMENSION=2048` 제한 확인

3. **Segfault (angle classifier)**
   - 원인: `use_angle_cls=True` 설정
   - 해결: 반드시 `use_angle_cls=False`로 설정

```bash
# PaddleOCR 로그 확인
docker compose logs paddleocr

# 컨테이너 재시작 횟수 확인
docker inspect --format='{{.RestartCount}}' paddleocr

# 헬스 체크
curl http://localhost:8866/health
```

### OCR 정확도 낮음

- 모델: `korean_PP-OCRv5_mobile_rec` (88% 정확도) 사용 확인
- DPI: PDF 렌더링 시 300 DPI 확인
- 너무 작은 이미지 (20x20px 미만)는 자동 필터링됨

### Broken CMap 폰트 (PowerPoint PDF)

PowerPoint에서 내보낸 PDF의 경우 ToUnicode CMap이 누락되어 텍스트 추출이 깨집니다. 시스템이 자동 감지하여 OCR로 라우팅하지만, 로그에서 확인 가능:

```
INFO: Broken CMap fonts detected on page X, routing to OCR
```

---

## 검색 품질 문제

### 유사 단어 혼동 ("폐기" vs "폐점")

한국어 유사 형태소가 혼동되는 경우:

1. **용어집에 등록**: Dashboard > Glossary에서 정확한 용어 정의
2. **Sparse 가중치 조정**: 키워드 매칭 강화
   ```bash
   curl -X PUT http://localhost:8000/api/v1/admin/config/weights \
     -H "Content-Type: application/json" \
     -d '{"hybrid_search.sparse_weight": 0.45, "hybrid_search.dense_weight": 0.25}'
   ```

### 검색 결과 관련성 낮음

```bash
# 현재 가중치 확인
curl http://localhost:8000/api/v1/admin/config/weights

# Reranker 가중치 조정
curl -X PUT http://localhost:8000/api/v1/admin/config/weights \
  -H "Content-Type: application/json" \
  -d '{"reranker.model_weight": 0.7, "reranker.base_weight": 0.2}'

# 기본값으로 리셋
curl -X POST http://localhost:8000/api/v1/admin/config/weights/reset
```

### RAG 응답이 "정보를 찾을 수 없습니다"

1. CRAG 신뢰도가 차단 임계값 미만 (`crag_block_threshold: 0.3`)
2. 검색된 청크의 관련성이 낮음
3. 해당 KB에 관련 문서가 없음

```bash
# 검색만 테스트 (RAG 없이)
curl -X POST http://localhost:8000/api/v1/search/hub \
  -d '{"query": "질의 내용", "kb_ids": ["my-kb"], "top_k": 10}'
```

---

## 성능 문제

### 임베딩 속도 느림

```bash
# 현재 프로바이더 확인
curl http://localhost:8000/api/v1/admin/quality/embedding/stats

# TEI 사용 권장 (ONNX보다 빠름)
# .env:
USE_CLOUD_EMBEDDING=true
BGE_TEI_URL=http://localhost:8080
```

Fallback 순서: TEI > Ollama > ONNX. TEI가 가장 빠릅니다.

### 메모리 사용량 높음

- ONNX 임베딩 사용 시 FP32 → INT8 양자화로 전환 (2-3x 메모리 절감)
  ```bash
  python scripts/quantize_bge_m3.py
  # .env: KNOWLEDGE_BGE_ONNX_MODEL_PATH=./models/bge-m3-int8
  ```
- Redis L1 캐시 크기 조정: `CacheConfig.l1_max_entries` (기본 10000)
- Qdrant prefetch 제한: `HybridSearchWeights.prefetch_max` (기본 150)

### 인제스트 느림

```bash
# 배치 사이즈 및 워커 수 조정 (.env)
KNOWLEDGE_PIPELINE_MAX_WORKERS=4
KNOWLEDGE_PIPELINE_BATCH_SIZE=50

# 대용량 파일 시 증분 모드 활용
KNOWLEDGE_PIPELINE_INCREMENTAL_MODE=true
```

---

## 인증 문제

### JWT 토큰 만료

```
401 Unauthorized: Token expired
```

```bash
# 리프레시 토큰으로 갱신
curl -X POST http://localhost:8000/auth/refresh \
  --cookie "refresh_token=<token>"
```

기본 만료: access 60분, refresh 8시간. `.env`에서 조정:
```
AUTH_JWT_ACCESS_EXPIRE_MINUTES=120
AUTH_JWT_REFRESH_EXPIRE_HOURS=24
```

### AUTH_PROVIDER=internal 설정 시 로그인 실패

1. `AUTH_JWT_SECRET` 설정 확인 (필수)
2. 초기 admin 계정 생성 확인

---

## K8s 배포 문제

### Pod가 Pending 상태

```bash
kubectl -n knowledge describe pod <pod-name>
# Events 섹션 확인

# 리소스 부족 시
kubectl -n knowledge get nodes -o wide
kubectl top nodes

# PVC 미바인딩 시
kubectl -n knowledge get pvc
```

### HPA 미작동

```bash
kubectl -n knowledge get hpa
# TARGETS에 <unknown> 표시 시 metrics-server 확인
kubectl -n kube-system get pods | grep metrics
```

### 이미지 Pull 실패

```bash
# k3s에서 로컬 이미지 직접 로드
sudo k3s ctr images import <(docker save knowledge-local:latest)
```

---

## Docker Compose 문제

### 포트 충돌

```bash
# 사용 중인 포트 확인
lsof -i :8000  # API
lsof -i :6333  # Qdrant
lsof -i :7687  # Neo4j
lsof -i :5432  # PostgreSQL
lsof -i :6379  # Redis
lsof -i :8866  # PaddleOCR
```

### 볼륨 데이터 초기화

```bash
# 전체 초기화 (데이터 삭제)
docker compose down -v

# 특정 서비스만 재시작
docker compose restart qdrant
```

### Apple Silicon (M-series) 호환성

PaddleOCR는 amd64 전용입니다. Apple Silicon에서는:
- Rosetta 2 에뮬레이션 사용 (`platform: linux/amd64` 설정)
- 또는 PaddleOCR 없이 실행 (OCR 기능 비활성화)
