# 개선 계획 (Improvement Plan)

**생성**: 2026-04-16
**원본 리뷰**: 8개 축 × 235 findings (2026-04-16 전체 코드 리뷰 세션)
**상태 범례**: ⏳ 대기 · 🔨 진행중 · ✅ 완료 · ❌ 취소 · 🔀 머지됨

이 문서는 **살아있는 진행 현황판** 이다. 각 PR 완료 시 이 문서도 같이 업데이트해서 현재 어디까지 왔는지 누구나 바로 알 수 있어야 한다.

---

## 목차

1. [배경](#배경)
2. [제약 사항](#제약-사항)
3. [테스트 커버리지 정책](#테스트-커버리지-정책)
4. [Phase A — Blocker](#phase-a--blocker-pr1pr7)
5. [Phase B — 구조 개선](#phase-b--구조-개선-pr8pr12)
6. [Phase C — Minor + 커버리지 backfill](#phase-c--minor--커버리지-backfill-pr13n-지속)
7. [Phase D — 장기 확장성](#phase-d--장기-확장성-선택)
8. [축별 Findings 요약](#축별-findings-요약)
9. [완료 로그](#완료-로그)

---

## 배경

2026-04-16 전체 코드 리뷰 (8개 병렬 audit 에이전트) 결과 총 235개의 개선 항목이 발견됨. 축별:

| 축 | Blocker | Major | Minor | Nit | 합계 |
|---|---|---|---|---|---|
| SSOT | 4 | 9 | 10 | 3 | 26 |
| SRP | 4 | 8 | 18 | 10 | 40 |
| 하드코딩 | 3 | 10 | 6 | 3 | 22 |
| 프롬프트 인젝션 | 4 | 4 | 5 | 5 | 18 |
| 품질 | 4 | 5 | 12 | 4 | 25 |
| 성능 | 5 | 5 | 12 | 8 | 30 |
| 모듈화/확장성 | 4 | 6 | 14 | 6 | 30 |
| 문서 | 6 | 7 | 3 | 1 | 17 + 코드 주석 6영역 |
| **합계** | **34** | **54** | **80** | **40** | **~235** |

한 세션에 전부 끝내려다 품질이 떨어졌던 경험 반복 방지를 위해, **계획 문서를 먼저 커밋 → PR 단위 배치 실행 → 체크박스로 추적** 방식 채택.

---

## 제약 사항

다음 6개 제약은 **모든 PR 에 적용**된다.

### 1. Streamlit 은 개발/테스트용

`dashboard/` 의 Streamlit 코드는 **내부 테스트 도구**. 향후 사용자/관리자 SPA 가 별도 리포지토리로 분리 예정. 따라서:

- `dashboard/` 의 대규모 리팩터 / i18n 중앙화 / 구조 변경은 **우선순위 낮음**
- 대신 `src/api/routes/` 인터페이스 설계 (Pydantic 모델, 권한, URL prefix) 에 집중
- 향후 admin/user SPA 가 `routes/admin/*` + `routes/user/*` prefix 에 각각 붙음

### 2. API/프론트 런타임 재시작 신중

사용자가 진행 중 작업 있을 수 있음. 재시작 없이 반영할 수 있는 변경은 재시작 없이 처리:

- DB 스키마 변경: `docker exec psql` 로 현재 DB 에 직접 반영 + 커밋 후 재시작 시 seed 자동 매칭 (insert-if-missing 패턴)
- 코드 변경: 커밋 후 사용자가 편한 시점에 재시작

### 3. 한 세션 한 배치 원칙

2시간 넘는 작업은 품질 떨어짐. 따라서:

- 각 배치 = 1 PR = 1 브랜치
- 커밋 완료 후 사용자 머지 대기 → 다음 배치
- Agent 병렬 실행은 조사 단계에서만, 실제 코드 변경은 순차

### 4. 테스트 선행

각 PR 은 push 전에:

- `uvx ruff check` lint clean
- `uv run pytest` 전체 통과
- 새 코드는 단위 테스트 동반 (아래 커버리지 정책)

### 5. 문서 동반

구조 변경 / 새 기능 / 새 제약 사항이 있는 PR 은 관련 문서 업데이트 포함 필수:

- 영향받는 `docs/*.md` 갱신
- `CLAUDE.md` 관련 섹션 유지
- 이 IMPROVEMENT_PLAN.md 체크박스 업데이트

### 6. 테스트 커버리지 ≥ 80%

**모든 PR 이 수정하는 파일은 line 커버리지 80% 이상** 유지.

측정 방법:
```bash
uv run pytest --cov=src --cov-report=term-missing --cov-fail-under=80
```

기준 미달 시:
- 누락 테스트 직접 추가 (권장)
- PR 을 더 작게 쪼개서 복잡한 분기만 먼저
- 명백히 테스트 불가능한 코드는 `# pragma: no cover` + 리뷰어 승인

**CI 에서 강제** — PR6 에서 설정. 상세는 [테스트 커버리지 정책](#테스트-커버리지-정책) 섹션.

---

## 테스트 커버리지 정책

### 측정 범위

- **대상**: `src/` 하위 모든 Python 코드
- **제외**:
  - `dashboard/` — Streamlit, 곧 SPA 로 교체
  - `scripts/` — one-off
  - `cli/` — 선택적 (중요 entrypoint 만)
- **도구**: `pytest-cov` (이미 `pyproject.toml:dev` 에 설치됨)

### 커버리지 Floor 상승 스케줄

| 시점 | 전체 floor | Touched file 요구 |
|---|---|---|
| 지금 (측정 전) | 미정 | 미설정 |
| Phase A PR6 완료 | 현재 + 5% | ≥ 80% |
| Phase A 종료 | 70% | ≥ 80% |
| Phase B 종료 | 75% | ≥ 80% |
| Phase C 종료 | 85% | ≥ 80% |

Phase A PR6 에서 실제 측정 후 floor 확정.

### 테스트 유형별 비중 (가이드라인)

- **Unit** (`tests/unit/`): 80% — mock 기반, <60s 전체 실행
- **Integration** (`tests/integration/`): 15% — 실제 서비스 (Qdrant/Neo4j/PG)
- **E2E** (`tests/e2e/`): 5% — 전체 flow

### 허용되는 `pragma: no cover`

- Streamlit UI 렌더링 함수 (상태 재현 불가)
- `if __name__ == "__main__":` CLI entrypoint
- 외부 subprocess wrapper 의 바이너리 없을 때 fallback
- PR 리뷰어 명시적 승인 필요

### CI 강제 (PR6 에서 설정)

- `pytest --cov-fail-under=<현재치>` 설정
- 각 PR 마다 임계값 monotonic 상승 (내려가면 CI fail)
- HTML + term 리포트 artifact 저장

---

## Phase A — Blocker (PR1~PR7)

**목표**: 프로덕션 보안/안정성 리스크 제거 + 커버리지 기준선.
**예상 기간**: 2~3주

### PR0. 개선 계획 문서 커밋 🔀

- **Severity**: — (인프라)
- **Files**:
  - [x] `docs/IMPROVEMENT_PLAN.md` (이 문서)
  - [x] `CLAUDE.md` 에 plan 참조 한 줄 추가
- **Effort**: 1h
- **Branch**: `agent/improvement-plan-doc` → **PR #24 머지 (2026-04-16)**

---

### PR1. 프롬프트 인젝션 방어 🔀

- **Severity**: 🔴 Blocker (4개 항목)
- **축**: Prompt Injection
- **Why**: user-controlled 데이터 (질문, 답변, 청크) 가 LLM prompt 에 raw f-string 으로 주입됨. 악성 문서 한 개로 RAG 응답 hijack 가능. augmenter verify 가 substring 매칭이라 공격자가 답변에 `SEMANTIC=YES LEAK=NO` 심어 judge 우회 가능.
- **Files**:
  - [x] 신규 `src/llm/prompt_safety.py` — `wrap()`, `neutralize_instructions()`, `safe_user_input()`, `parse_strict_verdict()`, `parse_strict_score()` (실제로는 `src/distill/data_gen/` 대신 `src/llm/` 에 배치 — search 에서도 import 하므로)
  - [x] `src/distill/data_gen/reformatter.py::REFORMAT_PROMPT_TEMPLATE` XML delimit 로 교체
  - [x] `src/distill/data_gen/question_augmenter.py` — AUGMENT/VERIFY template + `_verify_llm` strict parser
  - [x] `src/distill/data_gen/qa_generator.py` — chunk injection delimit
  - [x] `src/distill/data_gen/generality_filter.py` — strict score parsing
  - [x] `src/distill/data_gen/quality_filter.py` — 동일 패턴
  - [x] `src/search/tiered_response.py::_format_context` — chunk content delimit + sanitize
  - [x] 신규 `tests/unit/test_prompt_safety.py` (37 cases)
- **Effort**: 6~8h (실제 2h)
- **근거**: 리뷰 blocker 1~4
- **머지**: PR #24 (2026-04-16)

---

### PR2. Bare except 정리 + 로깅 🔀

- **Severity**: 🔴 Blocker (4개 항목)
- **축**: Quality
- **Why**: `except Exception: pass` 로 실패를 은폐. 프로덕션 장애 디버깅 불가.
- **Files**:
  - [x] `src/api/routes/search.py` — usage log 실패 `logger.warning(..., exc_info=True)`
  - [x] `src/api/routes/kb.py` — KB chunk count 실패 warning
  - [x] `src/api/routes/health.py` — 8개 health check `logger.debug()` 각각
  - [x] `src/api/routes/distill.py` — IP resolve + S3 manifest fetch 실패 warning
  - [x] `src/distill/evaluator.py::_embedding_similarity` — text preview 로깅
  - [x] `src/api/routes/admin.py::_parse_llm_json_response` — 3단계 debug + 최종 warning + preview
- **머지**: PR #24 (2026-04-16)

---

### PR3. 성능 Blocker 🔀

- **Severity**: 🔴 Blocker (3개 항목)
- **축**: Performance
- **Why**: 매 검색마다 KB registry 풀로드 + 그래프 multi-hop 직렬 호출 + keyword fallback 직렬 → 사용자 체감 latency 악화.
- **Files**:
  - [x] `src/api/routes/search_helpers.py::get_active_kb_ids` 신규 — 60s TTL 메모리 캐시 (search.py 대신 여기에 배치 — circular import 회피)
  - [x] `src/api/routes/search.py::_step_keyword_fallback` — 컬렉션 scroll `asyncio.gather()` 병렬화 (`_scroll_one` 내부 helper)
  - [x] `src/graph/multi_hop_searcher.py::find_related` — 5회 Neo4j 쿼리 `asyncio.gather()` 병렬화
  - [x] 신규 `tests/unit/test_search_perf.py` (5 cases: cache hit/TTL/active-filter + parallelism wall-clock 검증 + isolation)
- **머지**: PR #24 (2026-04-16)

---

### PR4. 툴체인 env var 강제 🔨

- **Severity**: 🔴 Blocker (2개 SSOT 항목)
- **축**: SSOT + Hardcoding
- **Why**: `quantizer.py::_resolve_*` 의 `$PATH` fallback 이 homebrew bottle ↔ 소스 빌드 버전 드리프트 재발 위험. 2026-04-16 EXAONE 이 이것 때문에 깨진 사례 있음.
- **Files**:
  - [x] `src/distill/quantizer.py::_resolve_convert_script()` — env var 없으면 즉시 None + error log (opt-in `DISTILL_ALLOW_PATH_FALLBACK=1` 일 때만 $PATH 탐색)
  - [x] `src/distill/quantizer.py::_resolve_quantize_bin()` — 동일
  - [x] `src/distill/quantizer.py::_path_fallback_allowed()` — 신규 helper (1/true/yes/on)
  - [x] `src/distill/config.py::DistillDefaults.min_training_samples` 5000 → 200 (distill.yaml 과 일치)
  - [x] `src/distill/config.py::DistillDefaults.build_timeout_sec` 제거 — `src/config.py::DistillSettings.build_timeout_sec` 가 SSOT
  - [x] `distill.yaml` — `build_timeout_sec` 제거 + SSOT 주석
  - [x] 신규 `tests/unit/test_quantizer_toolchain.py` (16 cases: fallback flag + env var 필수화 + DistillDefaults drift fix)

---

### PR5. Config 드리프트 정리 🔨

- **Severity**: 🟠 Major
- **축**: SSOT
- **Files**:
  - [x] `src/config.py::QdrantSettings` — `dense_dimension`, `dense_vector_name`, `sparse_vector_name` 3개 dead fields 제거. NOTE 로 SSOT 위치 명시 (`config_weights.embedding.dimension`, `vectordb.client.DEFAULT_*_VECTOR_NAME`)
  - [x] `src/config_weights.py::EmbeddingConfig` — dimension/batch_size 의미 주석 추가 (pipeline batch 와 혼동 방지)
  - [x] 신규 `tests/unit/test_config_drift.py` (8 cases — dead field 제거, SSOT 참조 검증)
- **Deferred to Phase C**:
  - LLM 모델명 K8s/Helm templating (infra PR 영역)
  - `PipelineSettings.batch_size` 개명 (깊은 리팩터)

---

### PR6. 테스트 커버리지 기준선 수립 ⏳

- **Severity**: — (인프라)
- **축**: Quality (테스트)
- **Why**: 모든 향후 PR 이 커버리지 80% 를 지키려면 현재 상태 측정 + CI 강제 필요.
- **베이스라인**: **77.0%** (5,631 pass tests, 28,833 statements)
- **Files**:
  - [x] `pyproject.toml::[tool.coverage.*]` — source=["src"], omit, exclude_lines (fail_under 는 Makefile/CI 에 명시)
  - [x] 신규 `scripts/coverage_gate.py` — touched file 80% floor + missing file detection
  - [x] 신규 `docs/TESTING.md` — 정책/실행/작성 가이드/pragma 기준/backfill 목록
  - [x] `Makefile` — `test-unit` coverage 통합 (`--cov-fail-under=75`), `test-unit-fast` 분리, `test-coverage-gate` target 신규
  - [x] `.gitignore` — `.coverage*`, `htmlcov/`, `coverage.json`
  - [x] 신규 `tests/unit/test_coverage_gate.py` (15 cases — filter/loader/main flow)
- **Follow-up (Phase B 내)**:
  - Bitbucket Pipelines CI 에 `make test-unit && make test-coverage-gate` 추가 (CI 파일 위치 확인 후)
- **Known issue** (별도 처리):
  - `test_data_source_sync::TestRunIngestion` 2개, `test_document_parser_extended::TestParsePptx` 1개 — 기존부터 fail (main 머지 이전부터). Phase C 에서 fix.
  - `test_summary_tree_builder` 7개 — flaky, seed 고정 필요. Phase C 에서 fix.

---

### PR7. 문서 블로커 3개 🔨

- **Severity**: 🔴 Blocker (6개 중 3개)
- **축**: Documentation
- **Files**:
  - [x] 신규 `docs/QUICKSTART.md` — 10 단계 (요구사항 → clone → start → Ollama pull → api → dashboard → ingest → search → test → stop) + 자주 막히는 곳 + 다음 단계
  - [x] 신규 `docs/RAG_PIPELINE.md` — 9단계 파이프라인 흐름 + 단계별 상세 + 가중치 SSOT + 캐시 계층 + 응답 스키마 + fallback + 관찰성
  - [x] 신규 `docs/INGESTION_PIPELINE.md` — Stage 1/2, JSONL checkpoint, incremental, crash recovery, 병렬 튜닝, 품질 게이트
  - [x] `CLAUDE.md::Documentation` 테이블에 QUICKSTART + RAG_PIPELINE + INGESTION_PIPELINE + TESTING 추가 + "신규 개발자 QUICKSTART 부터" 가이드

---

## Phase B — 구조 개선 (PR8~PR12)

**목표**: 플러그인/레지스트리 패턴 도입. 확장성 확보.
**예상 기간**: 3~4주

### PR8. Provider registry 중앙화 🔨

- **Severity**: 🔴 Blocker (modularity 축)
- **축**: Modularity / SRP
- **Files**:
  - [x] 신규 `src/providers/__init__.py` — facade re-export
  - [x] 신규 `src/providers/llm.py` — `@register_llm_provider("ollama")` decorator + factory + `_resolve_provider_name` (LLM_PROVIDER env + legacy USE_SAGEMAKER_LLM)
  - [x] 신규 `src/providers/auth.py` — `@register_auth_provider("local/internal/keycloak/azure_ad")` — Settings + state 기반 factory
  - [x] `src/api/app.py::_init_llm` — registry 호출로 단순화 (22 → 5 줄)
  - [x] `src/api/app.py::_init_auth` — registry 호출로 if-elif 체인 (39 lines) 제거
  - [x] 신규 `tests/unit/test_providers_registry.py` (18 cases)
- **Deferred**:
  - `src/embedding/provider_factory.py` → `src/providers/embedding.py` 이동 (facade 유지로 deferred 가능, PR 작게 유지)
  - OCR / VectorDB / Graph provider registry (Phase D 장기 과제)
  - [ ] `src/api/app.py` — `create_llm_client()`, `create_auth_provider()` 로 단순화
  - [ ] `src/auth/providers.py::create_auth_provider` if-elif 제거
- **Effort**: 6~8h

### PR9. `routes/distill.py` 분할 🔀 (partial)

- **Severity**: 🔴 Blocker (SRP)
- **축**: SRP + Modularity
- **Files (완료)**:
  - [x] `src/api/routes/distill_training_data.py` (379줄, 14 endpoint 이동)
  - [x] `src/api/routes/distill.py` 1373 → 1078줄 + deferred `_get_state` import
  - [x] Circular import 해결 + `_spawn_background` 패턴 통합
  - [x] `tests/unit/test_distill_training_data_routes.py` (14 cases)
- **Follow-up (Phase C)**:
  - distill_profiles, distill_builds, distill_edge_servers, distill_base_models, distill_edge_logs 분리
  - distill.py → thin facade
  - SPA admin prefix 검토
- **머지**: PR #27 (2026-04-16)

### PR10. Distill data generation Pipeline Stage Protocol 🔀

- **Severity**: 🟠 Major
- **축**: SRP + Modularity
- **Files (완료)**:
  - [x] 신규 `src/distill/pipeline/__init__.py`
  - [x] 신규 `src/distill/pipeline/stages.py` — DataGenStage Protocol + DataGenContext + DataGenPipeline + make_context
  - [ ] 신규 `src/distill/pipeline/stages/qa_generation.py`
  - [ ] 신규 `src/distill/pipeline/stages/generality.py`
  - [ ] 신규 `src/distill/pipeline/stages/reformat.py` — AnswerReformatter 어댑터
  - [ ] 신규 `src/distill/pipeline/stages/augment.py` — QuestionAugmenter 어댑터
  - [ ] 신규 `src/distill/pipeline/builder.py` — `DataGenPipeline().add_stage(...)`
  - [ ] `src/distill/service.py::generate_data_for_review` — pipeline 조립만 하는 얇은 코드로
- **Effort**: 8~12h
- **Test plan**: 기존 통합 테스트 pass + 단계별 unit 테스트 신규

### PR11. Config 디렉터리 재편 ⏳

- **Severity**: 🟠 Major
- **축**: SSOT
- **Why**: config 가 `config.py`, `config_weights.py`, `distill/config.py`, `distill.yaml` 4개 파일에 분산. 687줄 단일 파일 (`config_weights.py`) 도 비대.
- **Files**:
  - [ ] 신규 `src/config/__init__.py` — 모든 re-export
  - [ ] 신규 `src/config/settings.py` ← 기존 `src/config.py`
  - [ ] 신규 `src/config/weights/search.py` ← 기존 `config_weights.py` 의 RerankerWeights 등
  - [ ] 신규 `src/config/weights/embedding.py`
  - [ ] 신규 `src/config/weights/distill.py`
  - [ ] 신규 `src/config/weights/chunking.py`
  - [ ] 신규 `src/config/profiles.py` ← 기존 `src/distill/config.py::DistillProfile`
  - [ ] 기존 `src/config.py`, `src/config_weights.py` 는 facade 로 유지 (backward compat)
- **Effort**: 6~8h
- **리스크**: import path 대량 변경 — facade 로 완화

### PR12. Major 문서 6개 ⏳

- **Severity**: 🟠 Major
- **축**: Documentation
- **Files**:
  - [ ] `docs/GRAPHRAG.md` — entity/relation 추출 규칙, prompt 설계, Neo4j 로더
  - [ ] `docs/GLOSSARY.md` — PBU/HBU/FBU, KB naming convention, entity types, distill profile 명칭
  - [ ] `docs/DEVELOPMENT.md` — async 패턴, repository/service 계층, Pydantic/SQLAlchemy 규칙
  - [ ] `docs/OPS.md` — 장애 대응, 롤백, DB migration 수동 절차
  - [ ] `docs/SECURITY.md` — 인증, API key, prompt injection 방어, output safety, data isolation
  - [ ] `docs/MIGRATION_GUIDE.md` — Alembic 없이 schema 변경 절차
- **Effort**: 10~12h

---

## Phase C — Minor + 커버리지 backfill (PR13~N, 지속)

**목표**: 기술부채 감축 + 기존 저커버리지 파일 보완.
**원칙**: 각 PR 작은 단위 (≤ 4h). 한 PR 에 여러 concern 섞지 않음.

### 코드 정리 (열거형)

- [x] 하드코딩 서비스 URL → `get_settings()` 일관 적용 (src/ 29파일 완료, scripts/ 추후)
- [x] Confluence `https://wiki.gsretail.com` SSOT
- [x] Redis URL SSOT (`RedisSettings` 추가)
- [x] TEI embedding/reranker URL SSOT (`TeiSettings` 추가)
- [ ] Timeout 리터럴 (`600`, `7200`, `10`) → config
- [ ] Chunk size 리터럴 (`_KSS_MAX_CHARS=2000`) → config_weights
- [ ] Pydantic `dict | None` → subclass 변환 (ProfileCreateRequest 등)
- [ ] `ProfileUpdateRequest` 도 동일
- [x] Ruff custom rule — bare except 방지 (`BLE001` 활성화, 기존 564건 noqa pragma)
- [ ] `config_weights.py` 687줄 → PR11 이후 최종 분할
- [ ] Kanana 라이선스 재확인 → `commercial_use=True` 승격
- [ ] EXAONE convert 패치 upstream 기여 (llama.cpp GitHub)
- [x] 코드 주석 강화 — `composite_reranker.py` RRF 가중치 근거
- [x] 코드 주석 강화 — `graphrag/extractor.py` 필터링 규칙 why
- [x] 코드 주석 강화 — `similarity/matcher.py` 3-layer 전략
- [ ] Feedback type enum 화 vs plugin
- [ ] Search pipeline 단계 protocol 화 (hub_search 13단계 분리)
- [ ] Ingestion pipeline 단계 protocol 화 (ingest 14단계 분리)
- [ ] AttachmentParser PDF / PPT / Image 분리
- [ ] `_init_search_services()` SearchServicesFactory 추출
- [ ] `run_pipeline()` BuildPipelineExecutor 추출

### 테스트 커버리지 backfill

PR6 측정 결과 기반으로 확정. 현재 예상 대상:

- [ ] `src/pipeline/ingestion.py` (3500+줄)
- [x] `src/search/composite_reranker.py` (24 tests — entity/keyword/graph/source/position/edge)
- [ ] `src/search/similarity/matcher.py` (900줄)
- [x] `src/pipeline/graphrag/extractor.py` (32 tests — corruption/invalid/reclassify/validate)
- [ ] `src/connectors/confluence/attachment_parser.py` (1876줄)
- [ ] `src/distill/service.py` 나머지 메서드
- [ ] `src/distill/trainer.py`
- [ ] `src/distill/evaluator.py`
- [ ] `src/api/routes/auth.py` (repository 직접 쿼리)
- [ ] `src/api/routes/quality.py` (golden set 로직)

각 파일 별도 PR. 목표: 전체 line coverage **80% → 85%** 달성.

---

## Phase D — 장기 확장성 (선택)

- [ ] Vector store Protocol 추상화 (`VectorStore` interface + `providers/vectordb.py`)
- [ ] Graph store Protocol 추상화 (`GraphStore` + `providers/graph.py`)
- [ ] `src/api/routes/user/` vs `routes/admin/` 완전 분리 (SPA 도입 직전)
- [ ] Plugin auto-discover for routes (`src/api/routes/*.py` 자동 include)
- [ ] Ingestion pipeline 완전 plugin 화 (`IngestionStage` registry)
- [ ] Connector plugin registry (`@ConnectorRegistry.register("notion")`)
- [ ] Search pipeline 완전 plugin 화 (`SearchStage` registry)

---

## 축별 Findings 요약

8개 audit agent 원본 결과. 각 축 상세는 세션 기록 또는 PR 별 근거 섹션 참고.

### SSOT (26)
- **Blocker**: toolchain env var fallback, DistillDefaults 드리프트, min_training_samples 5000 vs 200, build_timeout_sec 중복
- **Major**: embedding dim 3곳, vector name 2곳, LLM 모델명 4곳, S3 bucket 3곳, PostgreSQL default, Ollama URL
- **Minor/Nit**: q4_k_m 양자화, PBU 도메인 용어, EXAONE 모델 ID, LoRA 설정 불일치 등

### SRP (40)
- **Blocker**: `generate_data_for_review` (6단계), `hub_search` (13단계), `get_page_full` (8책임), `ingest` (14단계)
- **Major**: `run_pipeline`, `parse_pdf`, `distill.py`, `_init_search_services`, `edge_models.py` (Streamlit 제외), `smart_approve`, `crawl_bfs`, `_build_provision_config`
- **Minor**: 18개 (routes 보조 함수, search pipeline step 함수 등)
- **Nit**: 10개

### 하드코딩 (22)
- **Critical**: 서비스 포트/URL 100+ 파일, Confluence base URL, 모델 ID 중복
- **High**: timeout, chunk size, reranker 가중치, K8s manifest URL, Docker compose
- **Medium**: training 하이퍼, Streamlit UI (유보), script 상수
- **Low**: 재시도, 경로, sagemaker endpoint

### 프롬프트 인젝션 (18)
- **Blocker**: reformatter/augmenter f-string 주입, tiered_response chunk 주입, generality_filter score injection
- **Major**: qa_generator chunk injection, dataset_builder paraphrase, RAG pipeline user query, answer service retry
- **Minor**: json_repair 신뢰, citation 기반 공격, slicing 기반 length limit
- **Nit**: sanitize regex 한/중 패턴, ollama role 미분리

### 품질 (25)
- **Blocker**: search.py bare except, kb.py KB count 은폐, distill.py S3 manifest silent, health.py 8개 check silent
- **Major**: evaluator embedding fallback, teacher judge 실패 0.5, admin JSON parse 3번 pass, OCR health fallback
- **Minor**: Streamlit cache clear 남용, dict | None 타입, idempotency, 로그 레벨
- **Nit**: 4개

### 성능 (30)
- **Blocker**: keyword fallback 직렬, multi-hop 직렬, KB registry 매 요청, Ollama sync (이미 OK), distill QA generator 배치 누락
- **Major**: Qdrant collection cache miss, query expansion cache 없음, augmenter retry backoff, Confluence max_concurrent=1
- **Minor**: 12개
- **Nit**: 8개

### 모듈화/확장성 (30)
- **Blocker**: API route 수동 등록, Connector hard 타입 분기, Embedding factory 타입 분기, Data gen stages 순서 박힘, Ingestion pipeline 경직
- **Major**: LLM factory 부재, OCR 선택 로직 분산, Auth provider if-elif 중복, Search matcher pipeline, KB scoping 중복
- **Minor**: 14개 (Reranker strategy, Trust metric plugin 등)
- **Nit**: 6개

### 문서 (17)
- **Blocker**: QUICKSTART, RAG_PIPELINE, INGESTION_PIPELINE, DATA_MODEL, EDGE_SERVER, DEPLOYMENT 업데이트
- **Major**: GRAPHRAG, GLOSSARY, DEVELOPMENT, OPS, SECURITY, PERFORMANCE, MIGRATION_GUIDE
- **Minor**: CLAUDE.md 업데이트, INFERENCE_STRATEGIES, CONFLUENCE_CRAWLER 확장

---

## 완료 로그

| 날짜 | PR | 내용 | 비고 |
|---|---|---|---|
| 2026-04-15 | #22 | Fix/gguf tokenizer model gemma3 | 이전 머지 |
| 2026-04-16 | #23 | Base model registry + admin UI + toolchain | Phase 0 완료 |
| 2026-04-16 | #24 | IMPROVEMENT_PLAN + PR1 prompt injection + PR2 bare except + PR3 perf | Phase A batch 1 |
| 2026-04-16 | #25 | PR4 toolchain env var strict + PR5 config drift | Phase A batch 2 |
| 2026-04-16 | #26 (대기) | PR6 coverage baseline + PR7 blocker docs | Phase A batch 3 — **Phase A 완료** |

---

## 참고

- **원본 리뷰 세션**: 2026-04-16 Claude Code 세션 (8개 병렬 audit 에이전트)
- **리포지토리**: `code.gsretail.com/scm/dxcoes/gsr-ai-knowledge-hub.git`
- **Notion**: https://www.notion.so/gscoe/Knowledge-RAG-Small-LM-335b093f73b3807faa4ce3dabec5ba75 (베이스 모델 정책 기록됨)
- **관련 메모리**:
  - `project_base_model_default_policy.md` — 베이스 모델 default 정책
  - `project_future_frontend_split.md` — SPA 분리 계획
