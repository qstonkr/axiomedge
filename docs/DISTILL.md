# Distill Plugin — 엣지 모델 생성/관리/배포

검색 그룹(PBU/HBU) 단위로 Small LM 엣지 모델을 생성하는 플러그인.
RAG QA 데이터로 LoRA SFT 학습 → GGUF 양자화 → S3 배포 → 매장 엣지 서빙.

## 아키텍처

```
┌──────────────────────────────────────────────────────────────┐
│  중앙 서버 (knowledge-local)                                  │
│                                                              │
│  RAG 시스템 (코어, 변경 없음)                                  │
│    └── usage_log에 answer+chunks 저장 (DISTILL_LOG_FULL_CONTEXT) │
│                                                              │
│  Distill Plugin (선택적)                                      │
│    ├── 데이터 생성: KB 청크 → Teacher(EXAONE) QA 생성           │
│    ├── 학습: LoRA SFT (peft + trl)                           │
│    ├── 평가: Teacher judge + 임베딩 유사도                     │
│    ├── 양자화: GGUF Q4_K_M (llama.cpp)                       │
│    └── 배포: S3 업로드 + pre-signed URL manifest              │
│                                                              │
└──────────────────────┬───────────────────────────────────────┘
                       │ S3 (모델 파일 + 로그)
                       ▼
┌──────────────────────────────────────────────────────────────┐
│  매장 엣지 서버                                                │
│    ├── llama-cpp-python 추론 서버 (:8080)                     │
│    ├── S3 모델 자동 동기화 (sync.py)                           │
│    └── 사용 로그 → S3 업로드 → 중앙 수집 → 재학습               │
└──────────────────────────────────────────────────────────────┘
```

## 모듈 구조

```
src/distill/
├── config.py              # 프로필 스키마 + 빌드 상태 상수
├── models.py              # DB 4테이블 (독립 DistillBase)
├── data_generator.py      # facade → data_gen/ 패키지
├── data_gen/
│   ├── qa_generator.py    # 청크/로그 기반 QA 생성
│   ├── quality_filter.py  # self-consistency, answer-only, 길이 정규화
│   ├── dataset_builder.py # 중복 제거, augmentation, 밸런싱, JSONL
│   └── llm_helper.py      # Teacher LLM 호출 + Qdrant 스크롤
├── repository.py          # facade → repositories/ 패키지
├── repositories/
│   ├── profile.py         # Profile CRUD
│   ├── build.py           # Build CRUD
│   ├── edge_log.py        # Edge Log CRUD + analytics
│   └── training_data.py   # Training Data CRUD + stats
├── trainer.py             # LoRA SFT (peft + trl)
├── evaluator.py           # Teacher judge + 임베딩 유사도
├── quantizer.py           # GGUF 양자화 (llama.cpp)
├── deployer.py            # S3 배포 + manifest
├── edge_log_collector.py  # S3 로그 수집
└── service.py             # 파이프라인 오케스트레이터

src/api/routes/distill.py  # API 엔드포인트 (20+)
dashboard/pages/edge_models.py  # 대시보드 4탭

edge/
├── server.py              # 엣지 추론 서버 (FastAPI + llama-cpp)
├── sync.py                # S3 모델 동기화 + 로그 업로드
├── Dockerfile
└── docker-compose.yml
```

## 설정

### 인프라 설정 (env vars — src/config.py DistillSettings)

| 환경변수 | 기본값 | 설명 |
|---------|--------|------|
| `DISTILL_ENABLED` | `true` | 플러그인 활성화 |
| `DISTILL_CONFIG_PATH` | `distill.yaml` | 프로필 YAML 경로 |
| `DISTILL_WORK_DIR` | `/tmp/distill` | 빌드 작업 디렉토리 |
| `DISTILL_LLM_CONCURRENCY` | `3` | Teacher LLM 동시 호출 수 |
| `DISTILL_LLM_TIMEOUT_SEC` | `120` | LLM 호출 타임아웃 (초) |
| `DISTILL_BUILD_TIMEOUT_SEC` | `7200` | 빌드 전체 타임아웃 (초) |
| `DISTILL_LOG_FULL_CONTEXT` | `false` | usage_log에 answer+chunks 저장 |
| `DISTILL_RAG_API_URL` | `http://localhost:8000` | 재학습 시 Teacher RAG URL |

### 프로필 설정 (distill.yaml 또는 DB)

```yaml
profiles:
  pbu-store:
    enabled: true
    search_group: PBU              # 검색 그룹 = 학습 대상 KB 범위
    base_model: "Qwen/Qwen2.5-0.5B-Instruct"
    lora:
      r: 8                         # LoRA rank (4~64)
      alpha: 16                    # LoRA alpha (8~128)
      dropout: 0.05
    training:
      epochs: 3
      batch_size: 4
      learning_rate: 0.0002
      max_seq_length: 512
    qa_style:
      mode: concise                # concise | detailed
      max_answer_tokens: 256
      answer_only_ratio: 0.8       # 80% 답변만, 20% 추론 포함
    data_quality:
      self_consistency_samples: 3   # Teacher N회 응답
      self_consistency_threshold: 0.75
      augmentation_count: 3         # 질문 paraphrase 수
    deploy:
      s3_bucket: gs-knowledge-models
      s3_prefix: pbu-store/
      quantize: q4_k_m
```

## DB 스키마

독립 `DistillBase` 사용 (RAG 코어 테이블과 분리).

| 테이블 | 용도 |
|--------|------|
| `distill_profiles` | 빌드 프로필 설정 |
| `distill_builds` | 빌드/학습 이력 (상태, 메트릭, S3 URI) |
| `distill_edge_logs` | 매장 엣지 서버 사용 로그 |
| `distill_training_data` | 학습 데이터 QA 쌍 |

```bash
# 테이블 생성
uv run python scripts/distill_init_db.py
```

## API 엔드포인트

모두 `/api/v1/distill/` prefix:

### 프로필
- `GET /profiles` — 전체 목록
- `GET /profiles/{name}` — 상세
- `POST /profiles` — 생성
- `PUT /profiles/{name}` — 수정
- `DELETE /profiles/{name}` — 삭제
- `GET /search-groups` — 선택 가능한 검색 그룹

### 빌드
- `POST /builds` — 빌드 트리거 (`{"profile_name": "pbu-store"}`)
- `GET /builds` — 이력 조회
- `GET /builds/{id}` — 상세/진행 상태
- `POST /builds/{id}/deploy` — 배포 (S3 manifest 갱신)
- `POST /builds/{id}/rollback` — 롤백

### 학습 데이터
- `GET /training-data` — 목록 (필터: profile, source_type, status)
- `POST /training-data` — 수동 QA 추가
- `PUT /training-data/review` — 승인/거부

### 엣지 로그
- `POST /edge-logs/collect` — S3 로그 수집
- `GET /edge-logs` — 목록 (필터: profile, store, success)
- `GET /edge-logs/analytics` — 통계
- `GET /edge-logs/failed` — 실패 질의

### 재학습
- `POST /retrain` — 실패 질문 → 학습 데이터 추가 + 빌드 트리거

## 대시보드

사이드바 **외부 연동 > 엣지 모델** (4탭):

| 탭 | 기능 |
|----|------|
| 모델 관리 | 빌드 트리거, 이력/상태 조회, 배포/롤백 |
| 빌드 설정 | 프로필 CRUD (검색 그룹, LoRA, 학습, 응답 스타일) |
| 실사용 로그 | S3 로그 수집, 매장별 질의 조회, 실패 필터 |
| 재학습 | 실패 질문 → 정답 입력/자동 생성 → 학습 데이터 추가 → 재학습 |

## 엣지 서버

```bash
# 로컬 실행
STORE_ID=gangnam EDGE_API_KEY=secret MODEL_PATH=./model.gguf \
  uv run uvicorn edge.server:app --port 8080

# Docker
STORE_ID=gangnam EDGE_API_KEY=secret docker compose -f edge/docker-compose.yml up -d
```

### 엔드포인트
- `POST /ask` — 질의 응답 (`X-API-Key` 헤더 인증)
- `GET /health` — 헬스체크 (모델 버전, 상태)
- `POST /reload` — 모델 안전 교체 (unload → load → healthcheck)

### 자동 업데이트 (sync.py)
```bash
# cron으로 주기 실행
python edge/sync.py                    # 모델 체크 + 로그 업로드
python edge/sync.py --check-only       # 모델 체크만
python edge/sync.py --upload-logs-only  # 로그 업로드만
```

### 동작 흐름
1. S3 manifest.json에서 최신 버전 확인
2. 새 버전이면 pre-signed URL로 GGUF 다운로드
3. SHA256 검증 → staging → current 교체 (rollback 보존)
4. `/reload` 호출 → graceful 모델 교체
5. 로컬 로그 JSONL → S3 업로드 (rename 방식 race condition 방지)

## 학습 데이터 파이프라인

```
1. 검색 그룹 → KB IDs (resolve_kb_ids)
2. Qdrant 청크 스크롤 → Teacher(EXAONE) QA 생성
3. Self-consistency 필터 (임베딩 cosine similarity ≥ 0.75)
4. Answer-only 변환 (80%, Teacher LLM으로 추론 제거)
5. 질문 augmentation (3x paraphrase — 구어체)
6. Usage log QA 추출 (DISTILL_LOG_FULL_CONTEXT=true 필요)
7. 재학습 데이터 병합 (실패 질문 → Teacher 정답 생성)
8. 중복 제거 (fuzz token_sort_ratio > 85)
9. source_type별 밸런싱
10. JSONL export (chat format)
```

## Phase 0: 파일럿 벤치마크

구현 전 모델 후보 비교 + 엣지 서빙 검증:

```bash
# 1. 토큰 효율 + 모델 추론 벤치마크
uv run python scripts/distill_pilot_benchmark.py --sample 20

# 2. GGUF 변환 (llama.cpp)
# 3. 엣지 서버 실행 + 테스트
uv run python scripts/distill_pilot_edge_test.py --api-key test123

# 4. 안정성 테스트 (1000 연속)
uv run python scripts/distill_pilot_edge_test.py --stress --count 1000

# 5. Teacher 비교
AWS_PROFILE=jeongbeomkim uv run python scripts/distill_pilot_compare.py \
  --edge-results pilot_edge_results.jsonl
```

### 합격 기준

| 항목 | 기준 |
|------|------|
| 응답 속도 | CPU-only p95 ≤ 3초 |
| 메모리 | ≤ 1.5GB (Docker 포함) |
| 안정성 | 1000 연속 무오류, 메모리 증가 ≤ 5% |
| 품질 | Teacher 대비 임베딩 유사도 ≥ 60% |
| Docker 기동 | ≤ 30초 |
| 모델 교체 | 다운타임 ≤ 5초 |

## RAG 코어 변경 사항 (최소)

| 파일 | 변경 |
|------|------|
| `src/config.py` | `DistillSettings` 추가 (env_prefix=`DISTILL_`) |
| `src/api/routes/search.py` | usage_log context에 answer+chunks 조건부 저장 |
| `src/api/app.py` | distill_repo + distill_service 초기화, 라우터 등록 |
| `dashboard/components/sidebar.py` | 외부 연동 그룹 추가 |
| `dashboard/components/constants.py` | DISTILL_STATUS_ICONS 추가 |
| `dashboard/services/api_client.py` | distill 모듈 re-export |

## 베이스 모델 레지스트리 (2026-04-16 기준)

드롭다운 SSOT: `distill_base_models` 테이블 (코드 seed: `src/distill/seed.py::DEFAULT_BASE_MODELS`).
Admin UI 에서 추가/편집/토글 가능. Seed 는 insert-if-missing 으로 admin 편집 보존.

### 등록 모델 (dry-run 검증 완료)

| 순위 | 모델 | 크기(Q4) | 속도 | 라이선스 | Default | 비고 |
|------|------|---------|------|---------|---------|------|
| 10 | `google/gemma-3-4b-it` | 2.4GB | 6-8 tok/s | Gemma (상업 OK) | ✅ | 멀티모달 wrapper, convert 가 text tower 자동 추출 |
| 20 | `kakaocorp/kanana-nano-2.1b-instruct` | 1.3GB | 6-15 tok/s | Kanana (재확인 필요) | | `LlamaForCausalLM`, 상업 승격 가능 |
| 30 | `LGAI-EXAONE/EXAONE-3.5-2.4B-Instruct` | 1.4GB | 18-34 tok/s | research-only | | **convert 패치 필요** — `scripts/patches/convert_hf_to_gguf_exaone.patch`, 상업 배포 전 LG AI Research 계약 필수 |
| 40 | `naver-hyperclovax/HyperCLOVAX-SEED-Text-Instruct-1.5B` | 1.0GB | 18-34 tok/s | HyperCLOVA X SEED (제한적) | | 엣지 SBC 최적, 한국어 특화 |

### 기본값 정책 — Gemma 3 4B

드롭다운 첫 항목 (`sort_order=10`) 은 **반드시 상업 배포 가능한 모델** 이어야 합니다.

1. **사고 방지**: 프로필 신규 생성 시 기본 선택이 `EXAONE` / `HyperCLOVA` 이면, 관리자가 라이선스 계약 체결 확인 없이 프로덕션 빌드 트리거할 위험. 상업 OK 기본값은 이 사고를 원천 차단.
2. **상업 대안 부족**: 상업 OK 등록 모델 중 한국어 품질이 충분한 건 Gemma 3 4B 하나. (Kanana 는 라이선스 재확인 필요, Qwen3 4B 는 한국어 부적합으로 제거)
3. **명시 선택 강제**: non-commercial 모델은 드롭다운에서 `[상업X · research-only]` 라벨이 붙어 관리자가 고의적으로 선택해야 사용됨.

**예외 — `pbu-store` 프로필**: GS Retail 내부 테스트용으로 EXAONE 3.5 2.4B 사용 중 (2026-04-15 관리자 명시 선택). 상업 배포 전 LG AI Research 와 별도 계약 체결 필수.

### 제거된 후보

- `Qwen/Qwen3-4B` — 파이프라인은 통과하지만 기본 `<think>` 모드 + 영어 답변 + 한국사 날짜 오답 (1392-1408, 1408-1419 로 run 마다 변동) 일관. 한국어 도메인 부적합. 2026-04-16 dry-run 2회 재현 확인 후 제거.

### 새 모델 추가 절차

1. `make setup-distill-toolchain` 으로 툴체인 최신화
2. Ad-hoc 스크립트로 download → convert → quantize → llama-cpp-python load → 한국어 QA 1~2개 확인
3. 통과하면 `src/distill/seed.py::DEFAULT_BASE_MODELS` 에 추가 (verified=True)
4. 또는 Admin UI 에서 직접 추가 (DB 에만 기록, seed 파일 미반영)
5. 새 아키텍처 (convert 가 모르는 모델) 인 경우 → `docs/DISTILL_TOOLCHAIN.md` 의 패치 관리 절차 참고

### 툴체인 요구사항

`src/distill/quantizer.py` 는 `DISTILL_CONVERT_SCRIPT` / `DISTILL_QUANTIZE_BIN` 환경변수로 경로 SSOT. Homebrew bottle 은 드리프트 위험 있으므로 `make setup-distill-toolchain` 으로 소스 빌드 권장. 상세: `docs/DISTILL_TOOLCHAIN.md`.
