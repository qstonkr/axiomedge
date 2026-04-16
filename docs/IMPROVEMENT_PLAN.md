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

### PR0. 개선 계획 문서 커밋 🔨

- **Severity**: — (인프라)
- **Files**:
  - [ ] `docs/IMPROVEMENT_PLAN.md` (이 문서)
  - [ ] `CLAUDE.md` 에 plan 참조 한 줄 추가
- **Effort**: 1h
- **Branch**: `agent/improvement-plan-doc`

---

### PR1. 프롬프트 인젝션 방어 ⏳

- **Severity**: 🔴 Blocker (4개 항목)
- **축**: Prompt Injection
- **Why**: user-controlled 데이터 (질문, 답변, 청크) 가 LLM prompt 에 raw f-string 으로 주입됨. 악성 문서 한 개로 RAG 응답 hijack 가능. augmenter verify 가 substring 매칭이라 공격자가 답변에 `SEMANTIC=YES LEAK=NO` 심어 judge 우회 가능.
- **Files**:
  - [ ] 신규 `src/distill/data_gen/prompt_safety.py` — `wrap()`, `neutralize_instructions()`, `safe_user_input()`, `parse_strict_verdict()`, `parse_strict_score()`
  - [ ] `src/distill/data_gen/reformatter.py::REFORMAT_PROMPT_TEMPLATE` XML delimit 로 교체
  - [ ] `src/distill/data_gen/question_augmenter.py` — AUGMENT/VERIFY template + `_verify_llm` strict parser
  - [ ] `src/distill/data_gen/qa_generator.py` — chunk injection delimit
  - [ ] `src/distill/data_gen/generality_filter.py` — strict score parsing
  - [ ] `src/distill/data_gen/quality_filter.py` — 동일 패턴
  - [ ] `src/search/tiered_response.py::_format_context` — chunk content delimit + sanitize
  - [ ] 신규 `tests/unit/test_prompt_safety.py`
- **Effort**: 6~8h
- **Test plan**:
  - Unit: wrap / neutralize / parse_strict_verdict 경계 케이스
  - Unit: reformatter / augmenter 각각 악성 입력 (indirection 시도) 에 대해 `[BLOCKED]` 치환 확인
  - Integration (수동): 답변에 `SEMANTIC=YES LEAK=NO` 심어 augmenter.verify 호출 → rejected 확인
- **Coverage 목표**: prompt_safety.py 100% / 나머지 touched line 80%+
- **근거**: 리뷰 blocker 1~4

---

### PR2. Bare except 정리 + 로깅 ⏳

- **Severity**: 🔴 Blocker (4개 항목)
- **축**: Quality
- **Why**: `except Exception: pass` 로 실패를 은폐. 프로덕션 장애 디버깅 불가.
- **Files**:
  - [ ] `src/api/routes/search.py:1083` — usage log 실패 로깅
  - [ ] `src/api/routes/kb.py:60` — KB chunk count 실패 로깅
  - [ ] `src/api/routes/health.py:23-80` — 8개 health check 로깅
  - [ ] `src/api/routes/distill.py:1100-1103` — IP resolve fallback 로깅
  - [ ] `src/api/routes/distill.py:1265` — S3 manifest fetch 실패 로깅
  - [ ] `src/distill/evaluator.py:145-148, 164-165` — judge / similarity 로깅
  - [ ] `src/api/routes/admin.py:741-766` — JSON parse 3번 pass 블록 로깅
- **Effort**: 2~3h
- **Test plan**: lint + 기존 tests pass + side-effect 로거 호출 검증 unit
- **Coverage 목표**: touched file 80%+

---

### PR3. 성능 Blocker ⏳

- **Severity**: 🔴 Blocker (3개 항목)
- **축**: Performance
- **Why**: 매 검색마다 KB registry 풀로드 + 그래프 multi-hop 직렬 호출 + keyword fallback 직렬 → 사용자 체감 latency 악화.
- **Files**:
  - [ ] `src/api/routes/search.py:251-254` — `kb_registry.list_all()` TTL 60s 캐시 추가
  - [ ] `src/api/routes/search.py:487-511` — keyword fallback 컬렉션 순회 `asyncio.gather()` 병렬화
  - [ ] `src/graph/multi_hop_searcher.py:218-246` — 5회 직렬 Neo4j 쿼리 `asyncio.gather()` 병렬화
  - [ ] `src/graph/multi_hop_searcher.py:284-294` — expert finding fallback 병렬화 검토 (first-result-wins)
- **Effort**: 3~4h
- **Test plan**:
  - Unit: mocked Neo4j client 에 여러 호출 주입 → concurrent execution 확인
  - Unit: KB cache TTL hit/miss 동작
  - (선택) Integration: 전후 latency 비교
- **Coverage 목표**: touched file 80%+

---

### PR4. 툴체인 env var 강제 ⏳

- **Severity**: 🔴 Blocker (2개 SSOT 항목)
- **축**: SSOT + Hardcoding
- **Why**: `quantizer.py::_resolve_*` 의 `$PATH` fallback 이 homebrew bottle ↔ 소스 빌드 버전 드리프트 재발 위험. 오늘 EXAONE 이 이것 때문에 깨진 사례 있음.
- **Files**:
  - [ ] `src/distill/quantizer.py::_resolve_convert_script()` — env var 없으면 즉시 에러 + setup 스크립트 안내 (`$PATH` fallback 제거 or loud error)
  - [ ] `src/distill/quantizer.py::_resolve_quantize_bin()` — 동일
  - [ ] `distill.yaml` vs `src/distill/config.py::DistillDefaults` `min_training_samples` 드리프트 (5000 vs 200) 해소 — YAML 은 profile override 만
  - [ ] `src/distill/config.py::DistillDefaults.build_timeout_sec` vs `src/config.py::DistillSettings.build_timeout_sec` 중복 하나로 통합
  - [ ] `distill.yaml` 해당 필드 제거
- **Effort**: 2~3h
- **Test plan**: unit (env var 없을 때 에러 발생 확인) + 통합 (setup 스크립트 재실행으로 end-to-end)
- **Coverage 목표**: touched file 80%+

---

### PR5. Config 드리프트 정리 ⏳

- **Severity**: 🟠 Major (중요하지만 Blocker 는 아님. Phase A 에 포함해 기반 확립)
- **축**: SSOT + Hardcoding
- **Why**: embedding dimension (1024) 가 `config.py`, `config_weights.py`, `vectordb/client.py` 3곳. vector name (`bge_dense`/`bge_sparse`) 가 `config.py` 와 `vectordb/client.py` 2곳. LLM 모델명이 `config.py`, k8s, helm 4곳. 변경 시 드리프트 위험.
- **Files**:
  - [ ] `src/config_weights.py::EmbeddingConfig` 를 SSOT 로, `src/config.py::QdrantSettings.dense_dimension` 제거 → import
  - [ ] `src/vectordb/client.py::DEFAULT_DENSE_VECTOR_NAME` / `SPARSE_VECTOR_NAME` 을 SSOT 로, `config.py` NOTE 로만 관리되던 것 제거 → import
  - [ ] LLM 모델명 (`exaone3.5:7.8b`) — `src/config.py::DEFAULT_LLM_MODEL` 을 SSOT 로, k8s/helm manifest 는 templated env var 로 (`{{ .Values.llm.model }}`)
  - [ ] `src/config.py::QdrantSettings.batch_size` vs `src/config_weights.py::PipelineConfig.batch_size` (`50` vs `32`) 통합
- **Effort**: 3~4h
- **Test plan**:
  - Unit: import chain 정상 동작
  - Integration: embedding provider 가 동일 dim 으로 초기화
  - Lint check: 하드코딩된 `1024`, `bge_dense`, `exaone3.5:7.8b` 가 config 파일 외부에 없는지 grep

---

### PR6. 테스트 커버리지 기준선 수립 ⏳

- **Severity**: — (인프라)
- **축**: Quality (테스트)
- **Why**: 사용자 요청 — 모든 향후 PR 이 커버리지 80% 를 지키려면 먼저 현재 상태를 측정하고 CI 로 강제해야 함.
- **Files**:
  - [ ] `pyproject.toml::[tool.pytest.ini_options]` — `addopts = "--cov=src --cov-report=term-missing --cov-report=html --cov-fail-under=<현재치>"` 추가
  - [ ] `scripts/coverage_gate.py` — `git diff --name-only main...HEAD` 로 touched file 만 80% 강제
  - [ ] 신규 `docs/TESTING.md` — 테스트 가이드, mock 패턴, fixtures, `pragma: no cover` 허용 기준, 베이스라인 숫자, backfill 대상
  - [ ] `Makefile` — `make test-unit` 이 cov 리포트 같이 출력
  - [ ] Bitbucket Pipelines 또는 `.github/workflows/*.yml` CI 설정 (현재 CI 구조 확인 후 결정)
- **Effort**: 4~5h (베이스라인 측정 + 문서 작성 포함)
- **Test plan**: `make test-unit` 실행해 cov 리포트 생성 확인. CI 시뮬레이션 로컬 실행.

---

### PR7. 문서 블로커 3개 ⏳

- **Severity**: 🔴 Blocker (6개 중 3개)
- **축**: Documentation
- **Why**: 신규 팀원이 repo clone 후 첫 search 까지 30분 안에 도달하려면 QUICKSTART 필수. RAG 엔지니어가 검색 튜닝하려면 RAG_PIPELINE 필수. Ingestion 문제 디버깅엔 INGESTION_PIPELINE 필수.
- **Files**:
  - [ ] `docs/QUICKSTART.md` — 사전 요구사항, setup → start → first ingest → first search, 문제 해결
  - [ ] `docs/RAG_PIPELINE.md` — 9단계 입출력 스키마, 튜닝 포인트, 캐시 정책, 가중치 근거
  - [ ] `docs/INGESTION_PIPELINE.md` — 2-stage pipeline, JSONL checkpoint, incremental, 크래시 복구, 병렬 worker 튜닝
  - [ ] `CLAUDE.md` 의 "Documentation" 테이블에 3개 추가
- **Effort**: 6~8h
- **Test plan**: 다른 팀원이 각 문서대로 실행 후 피드백

---

## Phase B — 구조 개선 (PR8~PR12)

**목표**: 플러그인/레지스트리 패턴 도입. 확장성 확보.
**예상 기간**: 3~4주

### PR8. Provider registry 중앙화 ⏳

- **Severity**: 🔴 Blocker (modularity 축)
- **축**: Modularity / SRP
- **Why**: LLM / Auth / OCR provider 선택이 `app.py` 에 if-elif 로 박힘. 새 provider 추가 시 3~5 파일 수정.
- **Files**:
  - [ ] 신규 `src/providers/llm.py` — `@register_llm_provider("ollama")` decorator + factory
  - [ ] 신규 `src/providers/auth.py` — `@register_auth_provider("keycloak")` decorator
  - [ ] 기존 `src/embedding/provider_factory.py` → `src/providers/embedding.py` re-export (backward compat)
  - [ ] `src/api/app.py` — `create_llm_client()`, `create_auth_provider()` 로 단순화
  - [ ] `src/auth/providers.py::create_auth_provider` if-elif 제거
- **Effort**: 6~8h

### PR9. `routes/distill.py` 분할 ⏳

- **Severity**: 🔴 Blocker (SRP)
- **축**: SRP + Modularity
- **Why**: 1360줄 / 53개 엔드포인트 / 7개 도메인 (Profile, Build, TrainingData, EdgeServer, BaseModel, EdgeLog, AppVersion) 혼재
- **Files**:
  - [ ] `src/api/routes/distill_profiles.py`
  - [ ] `src/api/routes/distill_builds.py`
  - [ ] `src/api/routes/distill_training_data.py`
  - [ ] `src/api/routes/distill_edge_servers.py`
  - [ ] `src/api/routes/distill_base_models.py`
  - [ ] `src/api/routes/distill_edge_logs.py`
  - [ ] `src/api/routes/distill.py` → facade 로 축소 (re-export only)
  - [ ] (미래 SPA 대비) `/api/v1/admin/distill/*` URL prefix 로 통일 검토
- **Effort**: 4~6h
- **리스크**: 기존 엔드포인트 URL 유지 필수 (외부 consumer 영향)

### PR10. Distill data generation Pipeline Stage Protocol ⏳

- **Severity**: 🟠 Major
- **축**: SRP + Modularity
- **Why**: `generate_data_for_review()` 가 6 단계 한 함수 150줄. 새 단계 추가 어려움.
- **Files**:
  - [ ] 신규 `src/distill/pipeline/__init__.py`
  - [ ] 신규 `src/distill/pipeline/stages.py` — `DataGenStage` Protocol + `DataGenContext`
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

- [ ] 하드코딩 서비스 URL → `get_settings()` 일관 적용 (100+ 파일 점진 정리)
- [ ] Confluence `https://wiki.gsretail.com` SSOT
- [ ] Timeout 리터럴 (`600`, `7200`, `10`) → config
- [ ] Chunk size 리터럴 (`_KSS_MAX_CHARS=2000`) → config_weights
- [ ] Pydantic `dict | None` → subclass 변환 (ProfileCreateRequest 등)
- [ ] `ProfileUpdateRequest` 도 동일
- [ ] Ruff custom rule — bare except 방지 (`BLE001` 활성화)
- [ ] `config_weights.py` 687줄 → PR11 이후 최종 분할
- [ ] Kanana 라이선스 재확인 → `commercial_use=True` 승격
- [ ] EXAONE convert 패치 upstream 기여 (llama.cpp GitHub)
- [ ] 코드 주석 강화 — `composite_reranker.py` RRF 가중치 근거
- [ ] 코드 주석 강화 — `graphrag/extractor.py` 필터링 규칙 why
- [ ] 코드 주석 강화 — `similarity/matcher.py` 3-layer 전략
- [ ] Feedback type enum 화 vs plugin
- [ ] Search pipeline 단계 protocol 화 (hub_search 13단계 분리)
- [ ] Ingestion pipeline 단계 protocol 화 (ingest 14단계 분리)
- [ ] AttachmentParser PDF / PPT / Image 분리
- [ ] `_init_search_services()` SearchServicesFactory 추출
- [ ] `run_pipeline()` BuildPipelineExecutor 추출

### 테스트 커버리지 backfill

PR6 측정 결과 기반으로 확정. 현재 예상 대상:

- [ ] `src/pipeline/ingestion.py` (3500+줄)
- [ ] `src/search/composite_reranker.py`
- [ ] `src/search/similarity/matcher.py` (900줄)
- [ ] `src/pipeline/graphrag/extractor.py`
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
| 2026-04-16 | PR0 | 개선 계획 문서 (이 문서) | 🔨 진행 중 |

---

## 참고

- **원본 리뷰 세션**: 2026-04-16 Claude Code 세션 (8개 병렬 audit 에이전트)
- **리포지토리**: `code.gsretail.com/scm/dxcoes/gsr-ai-knowledge-hub.git`
- **Notion**: https://www.notion.so/gscoe/Knowledge-RAG-Small-LM-335b093f73b3807faa4ce3dabec5ba75 (베이스 모델 정책 기록됨)
- **관련 메모리**:
  - `project_base_model_default_policy.md` — 베이스 모델 default 정책
  - `project_future_frontend_split.md` — SPA 분리 계획
