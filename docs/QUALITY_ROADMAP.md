# 코드 품질 로드맵 — 7.1 → 9.0

**생성**: 2026-04-17
**근거**: 8개 축 종합 감사 (335 파일, 82K 줄, 5,900+ 테스트 기준)
**목표**: 12 PR, 7 Phase를 통해 종합 점수 9.0 달성

---

## 현재 상태 (Baseline)

| 축 | 점수 | 측정 근거 |
|---|---|---|
| **타입 안전성** | **6/10** | return type 60% (1,729/2,843), Any 303건, mypy/pyright 미설정 |
| **아키텍처** | 7/10 | 클린 레이어링, Protocol 패턴. 500줄+ 파일 10개, import-linter 없음 |
| **에러 처리** | 7/10 | bare except 0건 (ruff BLE001). noqa:BLE001 pragma 567건. 커스텀 예외 2개뿐 |
| **보안** | 7/10 | rate limiter 있음(env-gated). CORS 과도 허용. 보안 헤더/CSRF/pip-audit 없음 |
| **성능** | 7/10 | L1+L2 캐시 있음. Neo4j 쿼리 timeout 미설정. 임베딩 전용 캐시 없음 |
| **테스트** | 8/10 | 5,999 tests, 77% 커버리지, 클라우드 fallback 테스트 부족 |
| **코드 메트릭** | 8/10 | 335파일, 72%가 200줄 이하. 테스트:코드 비율 0.84 |
| **문서** | 8/10 | OpenAPI /docs 활성. mkdocs 미설정. 가중치 튜닝 가이드 없음 |
| **종합** | **7.1** | |

---

## 점수 진행표

| Phase | 타입 | 에러 | 보안 | 성능 | 테스트 | 아키텍처 | 문서 | 메트릭 | **종합** |
|---|---|---|---|---|---|---|---|---|---|
| 현재 | 6.0 | 7.0 | 7.0 | 7.0 | 8.0 | 7.0 | 8.0 | 8.0 | **7.1** |
| P1 후 | 7.5 | 7.0 | 7.0 | 7.0 | 8.0 | 7.0 | 8.0 | 8.0 | **7.4** |
| P2 후 | 7.5 | 8.5 | 7.0 | 7.0 | 8.0 | 7.0 | 8.0 | 8.0 | **7.6** |
| P3 후 | 7.5 | 8.5 | 9.0 | 7.0 | 8.0 | 7.0 | 8.0 | 8.0 | **7.9** |
| P4 후 | 7.5 | 8.5 | 9.0 | 8.5 | 8.0 | 7.0 | 8.0 | 8.0 | **8.1** |
| P5 후 | 7.5 | 8.5 | 9.0 | 8.5 | 9.0 | 7.0 | 8.0 | 8.0 | **8.2** |
| P6 후 | 8.5 | 8.5 | 9.0 | 8.5 | 9.0 | 9.0 | 8.0 | 9.0 | **8.7** |
| P7 후 | 9.0 | 9.0 | 9.0 | 8.5 | 9.0 | 9.0 | 9.0 | 9.0 | **9.0** |

---

## 실행 순서 + 의존성

```
P1 (타입) ──────────────────────────→ P6 (아키텍처: pyright standard 승격)
               ↘
P3 (보안)       P2 (에러) ──→ P5 (테스트: 도메인 예외 필요)
(독립)                         ↘
P4 (성능)                       P6 (아키텍처) ──→ P7 (문서: 모듈 구조 필요)
(독립)
```

**권장 실행 순서**: P1 → P3 → P2 → P4 → P5 → P6 → P7

- P3(보안)을 P2 전에: 독립적이고 저공수 + 고가시성
- P2(에러)를 P5 전에: 도메인 예외 계층이 테스트에 필요
- P6(아키텍처)을 마지막 전에: pyright standard 승격 + 파일 분할

---

## Phase 1: 타입 안전성 기반 (6 → 7.5)

**예상**: 2 PR, ~4일

### PR 1A: pyright 설정 + CI 게이트

**변경 파일**:
- `pyproject.toml` — `[tool.pyright]` 섹션 추가
- `Makefile` — `type-check` target 추가
- `bitbucket-pipelines.yml` — pyright 스텝 추가

**설정**:
```toml
[tool.pyright]
pythonVersion = "3.12"
typeCheckingMode = "basic"           # Phase 6에서 standard로 승격
reportMissingReturnType = "warning"
include = ["src"]
exclude = ["src/apps/dashboard"]     # Streamlit, 향후 교체
```

### PR 1B: 최다 Any 파일 타입 정리

**변경 파일**:
- `src/api/state.py` (56 Any) — 각 필드에 Protocol/class 타입 지정
- `src/stores/neo4j/types.py` (10 Any) — TypedDict 추가
- `src/stores/neo4j/repository.py` (15 Any) — return type 추가
- `src/search/similarity/matcher.py` (12 Any) — match result 타입 정의

**검증**:
```bash
grep -r ": Any" src/ --include="*.py" | wc -l   # 303 → 200 이하
uv run pyright src/ 2>&1 | tail -1               # 에러 카운트 기록
```

---

## Phase 2: 에러 처리 성숙 (7 → 8.5)

**예상**: 2 PR, ~3일

### PR 2A: 도메인 예외 계층 + 상위 200건 pragma 제거

**신규 파일**: `src/core/exceptions.py`
```
KnowledgeBaseError (base)
├── ConfigurationError
├── StorageError
│   ├── VectorStoreError
│   ├── GraphStoreError
│   └── DatabaseError
├── ProviderError
│   ├── EmbeddingError
│   └── LLMError
├── PipelineError
│   ├── IngestionError
│   └── DedupError
├── SearchError
├── AuthenticationError    ← auth/providers.py에서 이동
└── TransitionError        ← core/lifecycle.py에서 이동
```

**변경 파일** (pragma 제거 순서):
| 파일 | 현재 pragma | 교체 예외 |
|---|---|---|
| `src/api/app.py` | 30건 | ImportError, ConnectionRefusedError, OSError |
| `src/api/routes/glossary.py` | 21건 | DatabaseError, ValueError |
| `src/api/routes/kb.py` | 20건 | VectorStoreError, DatabaseError |
| `src/api/routes/quality.py` | 19건 | StorageError |
| `src/api/routes/admin.py` | 19건 | DatabaseError, LLMError |
| `src/connectors/confluence/attachment_parser.py` | 18건 | subprocess.TimeoutExpired, IOError |
| `src/api/routes/_search_steps.py` | 17건 | SearchError, EmbeddingError |

**변경 파일**: `src/api/errors.py` — 도메인 예외 → HTTP 상태 코드 매핑

### PR 2B: stores + pipelines 레이어 170건 pragma 제거

**변경 파일**:
| 파일 | 현재 pragma | 교체 예외 |
|---|---|---|
| `src/stores/neo4j/repository.py` | 20건 | GraphStoreError |
| `src/pipelines/ingestion.py` | 14건 | IngestionError |
| `src/stores/qdrant/store.py` | 10건 | VectorStoreError |
| `src/stores/redis/l2_semantic_cache.py` | 10건 | StorageError |
| `src/connectors/confluence/client.py` | 13건 | ConnectionError, TimeoutError |
| `src/edge/sync.py` | 13건 | ConnectionError, IOError |
| 기타 search/distill 파일 | ~90건 | 각 도메인 예외 |

**검증**:
```bash
grep -rc "noqa: BLE001" src/ | awk -F: '{s+=$2}END{print s}'  # 567 → 200 이하
```

---

## Phase 3: 보안 강화 (7 → 9)

**예상**: 2 PR, ~2일

### PR 3A: CORS 엄격화 + 보안 헤더 미들웨어

**변경 파일**:
- `src/api/app.py` — CORS 설정 수정:
  - `allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"]`
  - `allow_headers=["Authorization", "Content-Type", "X-Request-ID"]`

**신규 파일**: `src/api/middleware/security_headers.py`
```
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
Strict-Transport-Security: max-age=31536000  (HTTPS_ENABLED일 때)
Content-Security-Policy: default-src 'self'  (CSP_ENABLED일 때)
Referrer-Policy: strict-origin-when-cross-origin
```

**신규 파일**: `tests/unit/test_security_headers.py`

### PR 3B: pip-audit CI + 쿠키 보안

**변경 파일**:
- `pyproject.toml` — dev 의존성에 `pip-audit` 추가
- `Makefile` — `audit` target 추가
- `bitbucket-pipelines.yml` — pip-audit 스텝 추가
- `src/auth/jwt_service.py` — 쿠키에 `SameSite=Strict` 설정
- `docs/SECURITY.md` — 보안 설정 가이드 갱신

**검증**:
```bash
curl -I localhost:8000/health | grep -i "x-frame\|content-security\|strict-transport"
uv run pip-audit --strict
```

---

## Phase 4: 성능 기준선 (7 → 8.5)

**예상**: 1 PR, ~2일

### PR 4A: Neo4j timeout + 임베딩 캐시 + 벤치마크

**변경 파일**:
- `src/config/settings.py` — `Neo4jSettings.query_timeout_ms: int = 30000` 추가
- `src/stores/neo4j/client.py` — 세션/트랜잭션에 timeout 적용
- `src/api/search_services_factory.py` — 쿼리 임베딩 LRU 캐시 (L0) 래핑
- `scripts/benchmark/load_test.py` — p50/p95/p99 출력 + JSON 저장

**신규 파일**: `docs/benchmarks/baseline.json` (첫 벤치마크 결과)

**검증**:
```bash
# Neo4j timeout 테스트
uv run pytest tests/unit/ -k "neo4j and timeout"
# 벤치마크 실행 (서버 실행 중일 때)
uv run python scripts/benchmark/load_test.py --output docs/benchmarks/baseline.json
```

---

## Phase 5: 테스트 + 커버리지 (8 → 9)

**예상**: 2 PR, ~3일

### PR 5A: 클라우드 fallback 통합 테스트 + 커버리지 80%

**신규 파일**:
- `tests/integration/test_embedding_fallback.py` — TEI 불가 → Ollama → ONNX
- `tests/integration/test_llm_fallback.py` — SageMaker 불가 → Ollama

**변경 파일**:
- `Makefile` — `--cov-fail-under=80`
- 저커버리지 파일 백필 (data_source_sync, qdrant/store, multi_layer_cache)

### PR 5B: 커버리지 85%

**변경 파일**:
- `Makefile` — `--cov-fail-under=85`
- 나머지 저커버리지 파일 백필

**검증**:
```bash
make test-unit  # --cov-fail-under=85 통과
uv run pytest tests/unit/ -q --no-cov  # 0 failures (3회 연속)
```

---

## Phase 6: 아키텍처 강제 (7 → 9)

**예상**: 1 PR, ~3일

### PR 6A: import-linter + 파일 분할 + pyright standard

**신규 파일**: `.importlinter`
```
[importlinter]
root_packages = src

[importlinter:contract:layered]
name = Clean Architecture Layers
type = layers
layers =
    src.api
    src.search | src.pipelines | src.distill
    src.stores | src.nlp | src.connectors
    src.core | src.config
```

**변경 파일**:
- `pyproject.toml` — dev 의존성에 `import-linter` 추가, pyright `standard` 승격
- `Makefile` — `lint-imports` target 추가

**파일 분할** (500줄+ → 500줄 이하):
| 파일 | 현재 | 분할 방법 |
|---|---|---|
| `attachment_parser.py` (1,419) | OCR 로직 | `_ocr_parsers.py` 추출 |
| `confluence/client.py` (1,212) | HTTP/auth | `_http.py` 추출 |
| `ingestion.py` (1,111) | stage 구현 | `stages/` 서브모듈로 |
| `_search_steps.py` (1,044) | embed/rerank/filter | 3파일 분할 |
| `neo4j/repository.py` (962) | query/search | `_queries.py` 추출 |

**검증**:
```bash
uv run lint-imports                    # clean
find src/ -name "*.py" ! -path "*/dashboard/*" -exec wc -l {} \; | awk '$1>500'  # 0건
uv run pyright src/ --level standard   # 에러 50건 이하
```

---

## Phase 7: 문서 마무리 (8 → 9)

**예상**: 1 PR, ~1일

### PR 7A: mkdocs + 가중치 튜닝 가이드

**신규 파일**:
- `mkdocs.yml` — Material 테마 + mkdocstrings
- `docs/WEIGHT_TUNING.md` — 검색 가중치 근거, 캐시 TTL, dedup 임계값 설명

**변경 파일**:
- `pyproject.toml` — dev 의존성에 `mkdocs`, `mkdocstrings[python]` 추가
- `Makefile` — `docs`, `docs-serve` target 추가
- Protocol 클래스 docstring 완성 (IVectorStore, IGraphStore 등)

**검증**:
```bash
make docs        # 빌드 성공, 경고 0건
make docs-serve  # localhost:8080에서 API 레퍼런스 확인
```

---

## 리스크 + 대응

| 리스크 | 확률 | 대응 |
|---|---|---|
| pyright 첫 실행 시 수백 에러 | 높음 | `basic` 모드 시작, 에러 카운트 monotonic ratchet |
| pragma 제거 시 런타임 동작 변경 | 중간 | 구체 예외만 사용, 파일별 테스트 확인 |
| import-linter가 순환 의존성 발견 | 중간 | 진단 먼저 실행, CI 게이트는 수정 완료 후 |
| 커버리지 85% 도달 난이도 | 중간 | ~2,300 statement 추가 커버 필요, 10개 파일 집중 |
| pip-audit이 기존 취약점 발견 | 낮음 | 즉시 패치 또는 의존성 업그레이드 |

---

## 총 공수 요약

| Phase | PR 수 | 예상 | 점수 향상 |
|---|---|---|---|
| **P1** 타입 안전성 | 2 | ~4일 | 7.1 → 7.4 |
| **P2** 에러 처리 | 2 | ~3일 | 7.4 → 7.6 |
| **P3** 보안 | 2 | ~2일 | 7.6 → 7.9 |
| **P4** 성능 | 1 | ~2일 | 7.9 → 8.1 |
| **P5** 테스트 | 2 | ~3일 | 8.1 → 8.2 |
| **P6** 아키텍처 | 1 | ~3일 | 8.2 → 8.7 |
| **P7** 문서 | 1 | ~1일 | 8.7 → 9.0 |
| **합계** | **11** | **~18일** | **7.1 → 9.0** |
