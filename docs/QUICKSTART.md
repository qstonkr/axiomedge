# Quickstart

**목표**: clone 후 **30분 안** 에 첫 검색 / 첫 인제스트까지 도달.
**대상**: 신규 팀원, 환경 재구축, 다른 머신에서 개발 시작.

---

## 0. 사전 요구사항

| 항목 | 버전 | 설치 확인 |
|---|---|---|
| macOS / Linux | — | `uname -a` |
| Python | **3.12+** | `python3 --version` |
| [uv](https://github.com/astral-sh/uv) | 최신 | `uv --version` |
| Docker Desktop | 4.0+ | `docker ps` |
| Git | any | `git --version` |
| 디스크 여유 | ≥ 20GB | `df -h /` |
| 메모리 | ≥ 16GB 권장 | — |

**선택 사항** (distill 빌드 시 필요):

- `llama.cpp` 툴체인 (`make setup-distill-toolchain` 으로 자동 빌드)
- AWS CLI (`~/.aws/credentials` 또는 env var — SageMaker/S3/TEI 접근 시)

### uv 설치 (필수)

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

---

## 1. Clone & 의존성 설치 (약 5분)

```bash
git clone https://code.gsretail.com/scm/dxcoes/gsr-ai-knowledge-hub.git
cd gsr-ai-knowledge-hub

# 의존성 설치 + venv 생성
make setup
```

`make setup` = `uv sync`. `.venv/` 디렉터리가 생성되고 모든 패키지가 설치된다.

---

## 2. 서비스 기동 (약 5분)

```bash
make start
```

Docker Compose 로 다음 서비스가 올라온다:

| 서비스 | 포트 | 역할 |
|---|---|---|
| PostgreSQL | 5432 | 메타데이터, usage log, 프로필 |
| Qdrant | 6333 | 벡터 검색 (dense + sparse) |
| Neo4j | 7474 / 7687 | GraphRAG (엔티티/관계) |
| Redis | 6379 | L1/L2 검색 캐시 |
| Ollama | 11434 | 로컬 LLM (기본 `exaone3.5:7.8b`) |
| PaddleOCR | 8866 | OCR (선택적) |

### 헬스 체크

```bash
docker ps | grep -E "postgres|qdrant|neo4j|redis|ollama"
```

모든 컨테이너가 `Up` 상태인지 확인. 문제 있으면:

- `docker compose logs <service>` 로 로그 확인
- 포트 충돌: `lsof -i :6333` 등으로 확인
- 상세: `docs/TROUBLESHOOTING.md`

---

## 3. Ollama LLM 모델 다운로드 (최초 1회, 약 5분)

```bash
docker exec -it $(docker ps -q -f name=ollama) ollama pull exaone3.5:7.8b
```

(선택) 임베딩 모델:

```bash
docker exec -it $(docker ps -q -f name=ollama) ollama pull bge-m3
```

> 💡 BGE-M3 는 기본적으로 TEI (Text Embeddings Inference) 경로를 쓰므로 Ollama 다운로드는 fallback 용. 상세는 `docs/CONFIGURATION.md`.

---

## 4. API 서버 기동 (별도 터미널)

```bash
make api
```

- 주소: `http://localhost:8000`
- 헬스: `curl http://localhost:8000/health`
- OpenAPI: `http://localhost:8000/docs`

응답 예시:

```json
{
  "status": "healthy",
  "checks": {
    "qdrant": true,
    "neo4j": true,
    "embedding": true,
    "llm": true,
    "redis": true,
    "database": true,
    "paddleocr": false
  }
}
```

`degraded` 로 뜨면 `docker ps` 로 서비스 상태 먼저 확인. `checks` 에 `false`
항목이 있으면 해당 서비스 로그 체크.

---

## 5. 대시보드 기동 (선택, 별도 터미널)

```bash
make dashboard
```

- 주소: `http://localhost:8501`
- KB 목록, 검색, 프로필, 엣지 모델 관리 등 UI

> ⚠️ Streamlit 대시보드는 **개발/테스트 용도**. 프로덕션 UI 는 향후 별도 SPA
> 프로젝트로 분리 예정 (`knowledge-user-web` + `knowledge-admin-web`).

---

## 6. 첫 문서 인제스트 (약 5분)

간단한 Markdown 파일 하나를 test KB 에 넣는다.

```bash
mkdir -p /tmp/quickstart-docs
cat > /tmp/quickstart-docs/sample.md <<'EOF'
# GS25 폐기 절차

상품 폐기는 POS 에 등록 후 폐기 박스에 담아 본부에 반납합니다.

- 유통기한 경과 상품은 매일 마감 전 확인
- 폐기 박스는 매주 월요일 회수
- 이의 제기는 영업일 기준 3일 내
EOF

make ingest ARGS="--source /tmp/quickstart-docs --kb-id quickstart-test"
```

인제스트 pipeline 이 다음을 수행:

1. 파일 파싱 (Markdown / PDF / PPT / Confluence 등)
2. Chunk 분할 (sliding window, 한국어 KSS)
3. Domain dictionary 적용 (OCR correction)
4. Embedding (dense + sparse via BGE-M3)
5. Qdrant 저장 (collection = `kb_quickstart_test`)
6. GraphRAG 엔티티 추출 (Neo4j)

상세: `docs/INGESTION_PIPELINE.md`.

---

## 7. 첫 검색 쿼리

```bash
curl -sS -X POST http://localhost:8000/api/v1/search/hub \
  -H "Content-Type: application/json" \
  -d '{
    "query": "폐기 절차",
    "kb_ids": ["quickstart-test"],
    "top_k": 5,
    "include_answer": true
  }' | jq '.answer, .confidence, .chunks[0]'
```

응답 예시:

```json
"폐기 절차: 상품을 POS에 등록한 뒤 폐기 박스에 담아 본부에 반납합니다..."
"높음"
{
  "chunk_id": "...",
  "document_name": "sample.md",
  "content": "상품 폐기는 POS 에 등록 후...",
  "score": 0.87
}
```

잘 된다! 🎉

상세 검색 파이프라인: `docs/RAG_PIPELINE.md`.

---

## 8. 테스트 실행

```bash
# 빠른 (no coverage)
make test-unit-fast

# 전체 (coverage 측정 + HTML 리포트)
make test-unit

# 특정 파일만
uv run pytest tests/unit/test_prompt_safety.py -v --no-cov
```

5,000+ unit test, 대부분 mock 기반으로 50~90s 내 완료.

### Coverage 확인

```bash
make test-unit
open htmlcov/index.html
```

현재 baseline 은 **77%** (2026-04-16 기준). 각 PR 이 touched file 80%+ 유지 필요.
상세: `docs/TESTING.md`.

---

## 9. 정지

```bash
# API / Dashboard 는 Ctrl+C

# Docker services
make stop
```

데이터는 Docker volume 에 유지되므로 다시 `make start` 하면 그대로 복구.

---

## 10. 자주 막히는 곳

### `make start` 가 포트 충돌

```bash
# 충돌 포트 찾기
lsof -i :5432  # PostgreSQL
lsof -i :6333  # Qdrant

# 로컬에 이미 설치된 Postgres / Redis 가 있으면 정지:
brew services stop postgresql@15
brew services stop redis
```

### `make api` 시 "Qdrant connection refused"

```bash
# Qdrant 컨테이너 재시작
docker restart knowledge-local-qdrant-1

# 로그 확인
docker logs knowledge-local-qdrant-1 --tail 50
```

### Ollama 모델 다운로드 느림

- 12GB+ 용량이므로 초기 5~15분 걸릴 수 있음
- 진행 상황: `docker exec -it $(docker ps -q -f name=ollama) ollama list`
- 중단되면 다시 `ollama pull`

### `make setup-distill-toolchain` 실패

- git clone 단계: 회사망 MITM certificate 문제일 수 있음 → `GIT_SSL_NO_VERIFY=1`
- cmake: `brew install cmake`
- 상세: `docs/DISTILL_TOOLCHAIN.md`

### 대시보드 "API 연결 실패"

- `make api` 가 돌고 있는지 확인
- `curl http://localhost:8000/health` 직접 호출
- 8000 포트 방화벽 확인

---

## 다음 단계

- **검색 튜닝**: `docs/RAG_PIPELINE.md` — 9단계 파이프라인 상세
- **데이터 인제스트**: `docs/INGESTION_PIPELINE.md` — checkpoint, incremental
- **Confluence 연동**: `docs/CONFLUENCE_CRAWLER.md`
- **Distill 엣지 모델 빌드**: `docs/DISTILL.md` + `docs/DISTILL_TOOLCHAIN.md`
- **운영/인프라**: `docs/DEPLOYMENT.md` (K8s), `docs/CONFIGURATION.md` (env vars)
- **테스트 작성**: `docs/TESTING.md`
- **기여**: `CONTRIBUTING.md`

### 코드 구조 빠르게 파악

```
src/
├── api/          # FastAPI routes, helpers, middleware
├── search/       # RAG pipeline (9 steps)
├── pipeline/     # Ingestion (2 stages)
├── embedding/    # BGE-M3 providers (TEI/Ollama/ONNX)
├── vectordb/     # Qdrant client wrappers
├── graph/        # Neo4j GraphRAG
├── llm/          # Ollama/SageMaker clients + prompt_safety
├── distill/      # Edge model training pipeline
├── connectors/   # Confluence, Git, etc.
└── config.py     # Infrastructure settings SSOT
```

상세 아키텍처: `docs/ARCHITECTURE.md`.

---

## 제약 사항

- **Streamlit 대시보드**: 개발/테스트 전용. 큰 리팩터 금지 (곧 SPA 로 교체).
- **API 재시작**: 개발자 진행 중 작업 있을 수 있음 → 최소화.
- **커버리지 80%**: 모든 신규/수정 코드 필수. `make test-coverage-gate` 로 확인.
- **상세**: `docs/IMPROVEMENT_PLAN.md` 제약 사항 섹션.
