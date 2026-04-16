# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**knowledge-local** is a standalone knowledge management system with RAG capabilities for GS Retail. Document ingestion, hybrid search, LLM-powered answer generation with cloud/local flexibility.

Tech stack: Python 3.12+, FastAPI, Streamlit, Qdrant (vector), Neo4j (graph), PostgreSQL, Redis, BGE-M3 (embedding via TEI), EXAONE (LLM via SageMaker/Ollama), PaddleOCR. Korean NLP via KiwiPy + KSS.

## Commands

```bash
# Local dev
make setup                      # uv sync
make setup-distill-toolchain    # llama.cpp convert+quantize+libllama (one-matched-commit build)
                                # — 필수 for distill 빌드. 신규 아키텍처 추가 시 재실행.
                                # 상세: docs/DISTILL_TOOLCHAIN.md
make start / stop               # docker compose up/down
make api                        # FastAPI :8000
make dashboard                  # Streamlit :8501

# CLI
make ingest ARGS="--source ./docs/ --kb-id my-kb"
make crawl ARGS="--source ./docs/ --output ./crawl_results/"

# Confluence Crawler
CONFLUENCE_PAT=your_pat uv run python scripts/confluence_crawler.py --page-id 373865276 --full
uv run python scripts/confluence_crawler.py --source faq --sample 10

# Tests
make test-unit          # 5,000+ tests, no services needed (~50s)
uv run pytest tests/unit/test_foo.py::test_bar -v --no-cov  # single test

# Deploy
make docker-build       # Build API container
make k8s-deploy         # kubectl apply -k deploy/k8s/

# Lint
uvx ruff check src/ scripts/  # must be clean (E402 exempt for Streamlit)
```

## Architecture

```
CLI (src/cli/)  ──┐
Dashboard ────┼──▶ FastAPI API (:8000)
(src/apps/dashboard/)   │     ├── Routes → Helpers (thin handler + business logic split)
              │     ├── Middleware (auth, CORS)
              │     └── Services (singleton, lifespan-managed)
              │           │
              │     ┌─────┴──────────────────────┐
              │     ▼                              ▼
              │  Pipeline                       Search/RAG
              │  (src/pipelines/)                (src/search/)
              │  parse→chunk→embed→dedup→store  classify→expand→search→rerank→generate
              │     │                              │
              └─────┴──────────────────────────────┘
                    │         │          │         │         │
                 Qdrant    Neo4j    PostgreSQL   Redis    TEI/SageMaker
                (vectors)  (graph)  (metadata)  (cache)  (cloud embed/LLM)

Distill (src/distill/)  ──▶ Edge Model Pipeline
  data_gen/ → QA 생성 + consistency + 범용성 필터 + augmentation 검증
  trainer.py → LoRA SFT
  quantizer.py → GGUF 양자화 + SHA256
  deployer.py → S3 배포 + manifest
  service.py → 파이프라인 오케스트레이터 (데이터 큐레이션 + 빌드)
  repositories/ → profile, build, training_data, edge_log, edge_server

Edge Server (src/edge/)  ──▶ 매장 엣지 서버
  server.py → llama-cpp 추론 + heartbeat
  sync.py → S3 모델 sync + heartbeat push + 앱 업데이트
  install.sh/ps1 → 크로스 플랫폼 설치 (Linux/Windows/macOS)
```

### Cloud Services

| Service | Flag | Cloud | Local Fallback |
|---------|------|-------|----------------|
| Embedding | `USE_CLOUD_EMBEDDING=true` | TEI (`BGE_TEI_URL`) | Ollama → ONNX |
| Reranker | `RERANKER_TEI_URL` | TEI (bge-reranker-v2-m3) | Local cross-encoder |
| LLM | `USE_SAGEMAKER_LLM=true` | SageMaker (EXAONE) | Ollama |
| OCR | `PADDLEOCR_API_URL` | EC2 on-demand (`PADDLEOCR_INSTANCE_ID`) | Local PaddleOCR |

### Module Structure (refactored)

Large files are split into helpers/sub-modules with **facade re-exports** for backward compatibility:

| Module | Structure | Role |
|--------|-----------|------|
| `src/api/routes/search.py` | + `search_helpers.py` | Search pipeline (orchestrator + step helpers) |
| `src/api/routes/quality.py` | + `quality_helpers.py` | Quality gates + golden set (route + SQL helpers) |
| `src/api/routes/auth.py` | + `auth_helpers.py` | Auth endpoints (route + service helpers) |
| `src/api/routes/glossary.py` | + `glossary_helpers.py` | Glossary CRUD (route + business logic) |
| `src/pipelines/graphrag/` | extractor + models + prompts + _neo4j_persistence | GraphRAG entity/relation extraction + Neo4j persistence |
| `src/search/enhanced_similarity_matcher.py` | → `similarity/` pkg | Similarity matching (matcher, strategies, utils) |
| `src/apps/dashboard/services/api/` | 8 modules | Frontend API client (core, kb, search, glossary, quality, admin, auth, misc) |
| `src/connectors/confluence/` | 8-module pkg | Confluence crawler (client, models, html_parsers, attachment_parser, config, output, structured_ir) |
| `src/distill/` | service + data_gen/ + repositories/ | Edge model distillation (QA curation, LoRA SFT, GGUF, S3 deploy) |
| `src/distill/data_gen/` | 5-module pkg | Data generation (qa_generator, quality_filter, generality_filter, dataset_builder, test_data_templates) |
| `src/distill/repositories/` | 5-module pkg | Distill DB repos (profile, build, training_data, edge_log, edge_server) |
| `src/distill/pipeline/` | stages.py + data_gen_stages.py | DataGenStage Protocol + 6 stage (QA/Generality/Augment/Reformat) |
| `src/distill/build_executor.py` | standalone | Build pipeline orchestrator (generate→train→quantize→evaluate→deploy) |
| `src/edge/` | server.py + sync.py + provision.sh | Edge server (llama-cpp inference, heartbeat, cross-platform deploy) |

**Infrastructure & Initialization:**

| Module | Role |
|--------|------|
| `src/config/` | Settings 패키지 (16개 Settings 클래스 — DB, Qdrant, Neo4j, Ollama, Redis, Confluence, TEI, AWS 등). `from src.config import get_settings` |
| `src/config/weights/` | 하이퍼파라미터 패키지 (7 서브모듈 — search, confidence, quality, pipeline, llm, cache, _helpers) |
| `src/core/providers/` | Provider registry (llm, auth, embedding, connector) + Protocol re-exports (protocols.py) |
| `src/api/route_discovery.py` | Route auto-discover — routes/ 자동 스캔 + include_router |
| `src/api/search_services_factory.py` | Search 서비스 초기화 factory (8개 서비스) |
| `src/search/pipeline/` | SearchStage Protocol + SearchPipeline builder |
| `src/pipelines/stages/` | IngestionStage Protocol + IngestionPipelineRunner (early-exit) |


### Key Patterns

- **SSOT**: `src/config/` (env vars — 16개 Settings 클래스 incl. AwsSettings), `src/config/weights/` (thresholds/weights). 서비스 URL 추가 시 반드시 `config/settings.py`에 Settings 클래스 추가 후 `get_settings()` 로 참조.
- **Protocols**: `IVectorStore`, `IGraphStore`, `ISearchEngine`, `IEmbedder`, `ISparseEmbedder`, `IConnector`, `SearchStage`, `IngestionStage`, `DataGenStage` — structural typing, runtime_checkable.
- **Repository**: `BaseRepository` in `src/stores/postgres/repositories/base.py` for all domain repos.
- **AppState**: `src/api/state.py` — typed dataclass, dict-compatible. Routes access via `_get_state()`.
- **Entity boost**: `composite_reranker.py` extracts store/person names from query, boosts matching chunks.
- **Week search**: `search_helpers.py` matches "N월 N주차", "YYYY년 N주차", "M월 D일" patterns to document names.
- **OCR correction**: `ocr_corrector.py` has domain dictionary with choseong-based fuzzy matching.

### Search Pipeline Steps

```
1. Cache check (L1 → L2)
2. Query preprocess (typo, time resolution) → expand → classify
3. Embed (dense + sparse via cloud TEI)
4. Qdrant hybrid search (RRF: dense + sparse)
   4.35 Identifier search (JIRA, filenames, CamelCase)
   4.4  Keyword boost + KB diversity
   4.42 Document diversity (max 5/doc, intra-doc Jaccard dedup)
   4.45 Date filter (doc_date field)
   4.46 Week-name search (document_name matching)
4.5 Passage cleaning
4.6 Cross-encoder rerank (cloud TEI or local)
5.  Composite rerank (model 0.6 + base 0.3 + source 0.1 + entity boost)
6.  Graph expansion (Neo4j entity/relationship enrichment)
7.  CRAG evaluation
8.  LLM answer generation (tiered response)
9.  Answer guard (hallucination detection)
```

### Ingestion Pipeline

Two-stage with JSONL checkpoint (crash-safe) + incremental support:

```
Stage 1: file → parse/OCR → domain dict correction → JSONL checkpoint
Stage 2: JSONL → chunk → passage clean → contextual prefix → embed → dedup (4-stage) → store
```

- Incremental: `src/cli/crawl.py` tracks `.crawl_state.json`, `src/cli/ingest.py` checks Qdrant content_hash
- Batch OCR cleaning: `scripts/backfill/batch_clean_chunks.py` (payload update, no re-embedding)
- **Confluence crawl**: `src/connectors/confluence/` — BFS parallel crawl + `CrawlResultConnector` → `IngestionPipeline`
- **Data source trigger**: Dashboard or API trigger → crawl → ingest → KB auto-register (see `docs/CONFLUENCE_CRAWLER.md`)

## Code Conventions

- **Async everywhere**: routes, repos, services are `async def`. CPU-bound uses `asyncio.to_thread()`.
- **Ruff**: target `py312`, line-length 100. E402 exempt for Streamlit pages.
- **No bare except**: always log exceptions, never `except: pass`.
- **Route pattern**: thin handler in `routes/X.py`, business logic in `routes/X_helpers.py`.
- **Tests**: `tests/unit/` (5,000+ tests, ~50s). New code must have tests.
- **Data source trigger**: `POST /api/v1/admin/data-sources/{id}/trigger` → background crawl + ingest.

## Parallel Development (Multi-Agent Workflow)

Multiple Claude CLI instances can work simultaneously using **git worktree isolation**:

```bash
# Setup aliases (one-time)
source scripts/ops/aliases.sh

# Terminal 1: search improvement
kl-new search "검색 품질 개선"     # creates worktree + starts Claude

# Terminal 2: pipeline work
kl-new pipeline "OCR 수정"         # separate worktree + Claude

# After work
kl-pr "검색 품질 개선"              # push + create PR
kl-done search                     # cleanup worktree

kl-list                            # show active worktrees
kl-home                            # return to main
```

### Domain Ownership (CODEOWNERS)

| Domain | Files | Owner |
|--------|-------|-------|
| Search/RAG | `src/search/`, `src/nlp/llm/`, `src/nlp/embedding/`, `src/stores/qdrant/` | @search-owner |
| Pipeline | `src/pipelines/`, `src/connectors/`, `src/cli/` | @pipeline-owner |
| Auth | `src/auth/` | @security-owner |
| Frontend | `src/apps/dashboard/` | @frontend-owner |
| Infra | `deploy/k8s/`, `deploy/helm/`, `.github/` | @infra-owner |
| Shared (review required) | `src/api/routes/`, `src/config/`, `src/stores/postgres/` | multiple reviewers |

### Branch Convention

- `agent/<name>` — worktree-isolated feature branches
- PR required for merge to `main`
- CI runs lint + 5,000+ unit tests on every PR (Bitbucket Pipelines + SonarQube)

## Evaluation

RAG quality is tracked via golden set evaluation:

```bash
AWS_PROFILE=jeongbeomkim uv run python scripts/run_rag_evaluation.py          # all KBs (261 questions)
AWS_PROFILE=jeongbeomkim uv run python scripts/run_rag_evaluation.py g-espa   # single KB
```

- **JUDGE**: RAGAS-style context-aware (faithfulness checks against retrieved chunks, not just expected answer)
- **Metrics**: Faithfulness, Relevancy, Completeness, Source Recall, CRAG
- **Golden set**: `rag_golden_set` table in PostgreSQL (261 items across 6 KBs)
- **Current scores**: F:0.62, R:0.78, C:0.66, Recall:85%

## Documentation

| Doc | Content |
|-----|---------|
| `docs/IMPROVEMENT_PLAN.md` | **Living improvement plan** — 진행 중 품질 개선 작업 (PR 단위 체크리스트, Phase A~D) |
| `docs/QUICKSTART.md` | **신규 개발자 온보딩** — 30분 안에 clone → 첫 검색까지 |
| `docs/RAG_PIPELINE.md` | 검색 파이프라인 9단계 상세, 입출력, 가중치 근거, 캐시, 디버깅 |
| `docs/INGESTION_PIPELINE.md` | 2-stage 인제스트, checkpoint, incremental, 병렬화 |
| `docs/ARCHITECTURE.md` | System diagrams, pipeline flows, data flow |
| `docs/API.md` | 138 endpoints with examples |
| `docs/DEPLOYMENT.md` | K8s deployment guide |
| `docs/DISTILL.md` | Distill 파이프라인, 베이스 모델 레지스트리, default 정책 |
| `docs/DISTILL_TOOLCHAIN.md` | llama.cpp 툴체인 설치/업그레이드/패치 관리 |
| `docs/GLOSSARY.md` | 도메인 용어 정의 (PBU/HBU, KB, search group, distill, GraphRAG) |
| `docs/SECURITY.md` | 인증, prompt injection 방어, 데이터 격리, 답변 안전성 |
| `docs/DATA_MODEL.md` | DB 스키마 요약 (PostgreSQL/Qdrant/Neo4j/Redis) |
| `docs/TESTING.md` | Test policy, coverage floor, pragma 허용 기준, backfill 목록 |
| `docs/GRAPHRAG.md` | GraphRAG entity/relation 추출, 필터링 규칙, graph expansion |
| `docs/DEVELOPMENT.md` | 코드 컨벤션, async 패턴, 계층 구조, pipeline/provider 패턴 |
| `docs/OPS.md` | Operations runbook — 장애 대응, 롤백, DB/캐시/엣지 관리 |
| `docs/MIGRATION_GUIDE.md` | DB 스키마 변경 절차 (Alembic 없음), 환경 전환 |
| `docs/DATA_MODEL.md` | DB 스키마 요약 (PostgreSQL/Qdrant/Neo4j/Redis) |
| `docs/CONFIGURATION.md` | All env vars + tuning parameters |
| `docs/TROUBLESHOOTING.md` | Common issues + solutions |
| `docs/CONFLUENCE_CRAWLER.md` | Confluence crawler pipeline, PaddleOCR EC2, data source trigger |
| `CONTRIBUTING.md` | Dev setup, code style, PR process |
| `CHANGELOG.md` | Version history |

**진행 중인 품질 개선 작업이 있으면 `docs/IMPROVEMENT_PLAN.md` 를 먼저 확인**하세요. 이 문서는 리뷰에서 발견된 findings + PR 단위 실행 계획을 체크박스로 추적합니다.

**신규 개발자는 `docs/QUICKSTART.md`** 부터 시작하세요 — setup + first search 까지 30분 가이드.
