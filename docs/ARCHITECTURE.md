# Architecture

## System Overview

```
                         ┌──────────────┐
                         │   Dashboard  │  Streamlit (:8501)
                         │  (Monitoring │
                         │   + Search)  │
                         └──────┬───────┘
                                │
┌──────────┐                    │                    ┌──────────┐
│  CLI     │────────────────────┼────────────────────│   MCP    │
│ (crawl,  │                    │                    │  Server  │
│  ingest, │                    ▼                    │ (stdio/  │
│  search) │            ┌───────────────┐            │  SSE)    │
└──────────┘            │   FastAPI     │            └──────────┘
                        │   API (:8000) │◀───────────────┘
                        │               │
                        │  ┌──────────┐ │
                        │  │  Auth    │ │  RBAC/ABAC
                        │  │Middleware│ │  (local/internal/keycloak/azure_ad)
                        │  └──────────┘ │
                        │               │
                        │  ┌──────────┐ │
                        │  │  Routes  │ │  search, rag, ingest, kb, admin, ...
                        │  └────┬─────┘ │
                        │       │       │
                        │  ┌────▼─────┐ │
                        │  │ Services │ │  Singleton, lifespan-managed
                        │  └────┬─────┘ │
                        └───────┼───────┘
                                │
          ┌─────────┬───────────┼───────────┬──────────┐
          │         │           │           │          │
          ▼         ▼           ▼           ▼          ▼
     ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐
     │ Qdrant │ │ Neo4j  │ │Postgres│ │ Redis  │ │ Ollama │
     │(vector)│ │(graph) │ │  (meta)│ │(cache) │ │ (LLM)  │
     └────────┘ └────────┘ └────────┘ └────────┘ └────────┘
                                                       │
     ┌────────┐ ┌────────┐                      ┌─────┴────┐
     │  TEI   │ │  TEI   │                      │ SageMaker│
     │(embed) │ │(rerank)│                      │(optional)│
     └────────┘ └────────┘                      └──────────┘

     ┌────────────┐
     │ PaddleOCR  │  Docker container (amd64)
     │ (OCR API)  │  korean_PP-OCRv5_mobile
     └────────────┘
```

## Service Initialization

FastAPI lifespan에서 9개 카테고리로 분리 초기화:

```
_init_services()
  ├── _init_database()     PostgreSQL + 13 repositories + domain services
  ├── _init_cache()        Redis + multi-layer cache (L1 in-memory + L2 semantic)
  ├── _init_dedup()        4-stage dedup pipeline
  ├── _init_vectordb()     Qdrant client/collections/search/store
  ├── _init_graph()        Neo4j + graph repo/expander/integrity
  ├── _init_embedding()    TEI > Ollama > ONNX fallback chain
  ├── _init_llm()          Ollama or SageMaker + GraphRAG
  ├── _init_search_services()  Query preprocessor, reranker, RAG pipeline
  └── _init_auth()         Auth provider + RBAC/ABAC
```

`AppState` (`src/api/state.py`)가 모든 서비스 참조를 보유. dict-style + attribute 접근 모두 지원.

## Search Pipeline

```
사용자 질의
    │
    ▼
┌─────────────────────┐
│  Query Classifier    │  OWNER_QUERY / PROCEDURE / TROUBLESHOOT / CONCEPT / GENERAL
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│ Query Preprocessor   │  형태소 분석 (KiwiPy), fuzzy 교정, 용어집 매칭
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│  Query Expansion     │  동의어, 그래프 확장, 크로스-KB 확장
└─────────┬───────────┘
          │
          ▼
┌─────────────────────────────────────────────┐
│  Hybrid Search (Qdrant)                      │
│  ┌────────┐ ┌─────────┐ ┌─────────────────┐ │
│  │ Dense  │ │ Sparse  │ │    ColBERT       │ │
│  │(0.35)  │ │ (0.35)  │ │    (0.30)        │ │
│  └────┬───┘ └────┬────┘ └────────┬─────────┘ │
│       └──────────┼───────────────┘            │
│                  ▼                            │
│            RRF Fusion                         │
└─────────────────┬───────────────────────────┘
                  │
                  ├──── + Graph Search (Neo4j multi-hop)
                  │
                  ▼
┌─────────────────────┐
│ Composite Reranker   │  model(0.6) + base(0.3) + source(0.1) + FAQ/axis boost
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│  CRAG Confidence     │  correct / ambiguous / wrong 분류
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│  LLM Generation      │  EXAONE 3.5 (Ollama/SageMaker)
│  + Answer Guard      │  환각 방지, 신뢰도 평가
└─────────────────────┘
```

쿼리 유형별 라우팅:
- **OWNER_QUERY**: 그래프에서 담당자 직접 조회, LLM 스킵
- **PROCEDURE**: sparse 가중치 상향 (0.45), 절차 매칭 강화
- **CONCEPT**: dense 가중치 상향 (0.45), 의미 검색 강화

## Ingestion Pipeline

```
Stage 1: Parse/OCR
─────────────────
파일 (PDF/DOCX/PPTX/XLSX/이미지)
    │
    ▼
┌──────────────────────┐
│  parse_file_enhanced()│
│  ├─ 텍스트 추출       │
│  ├─ Broken CMap 감지  │  PowerPoint PDF의 깨진 폰트 → OCR 라우팅
│  └─ 스캔 PDF 감지     │  텍스트 < 30자/페이지 → OCR
└──────────┬───────────┘
           │
    ┌──────┴──────┐
    ▼             ▼
 텍스트 OK     PaddleOCR
                  │
                  ▼
           LLM 노이즈 교정
                  │
    ┌─────────────┘
    ▼
┌──────────────────┐
│ JSONL Checkpoint  │  크래시 안전 (append + fsync)
└──────────┬───────┘
           │
Stage 2: Ingest
───────────────
           │
           ▼
┌──────────────────────┐
│  전처리               │  문장 중복 제거, 불완전 조각 제거
└──────────┬───────────┘
           ▼
┌──────────────────────┐
│  청킹                 │  2500자, 1문장 오버랩, 최대 500청크/문서
└──────────┬───────────┘
           ▼
┌──────────────────────┐
│  Contextual Retrieval │  [Context] Document: {title} | Section {i}/{n}
│  + Passage Cleaning   │  [Summary] 접두사 추가 (검색 정확도 35-49% 향상)
└──────────┬───────────┘
           ▼
┌──────────────────────┐
│  Embedding            │  BGE-M3: dense(1024) + sparse + ColBERT
└──────────┬───────────┘
           ▼
┌──────────────────────┐
│  4-Stage Dedup        │
│  1. Bloom filter      │  빠른 중복 필터
│  2. Exact hash        │  Jaccard ≥ 0.80
│  3. Semantic          │  Cosine ≥ 0.90
│  4. LLM conflict      │  충돌 감지 (선택)
└──────────┬───────────┘
           ▼
     ┌─────┴─────┐
     ▼           ▼
  Qdrant      Neo4j
 (벡터 저장)  (엔티티/관계)
```

## Data Flow

```
┌─────────────┐
│  문서 원본    │  PDF, DOCX, PPTX, XLSX, 이미지
└──────┬──────┘
       │ 파싱/OCR
       ▼
┌─────────────┐     ┌─────────────┐
│  JSONL 체크  │────▶│  PostgreSQL  │  문서 메타데이터, 피드백, 용어집,
│  포인트      │     │             │  신뢰도 점수, 파이프라인 이력
└──────┬──────┘     └─────────────┘
       │ 청킹/임베딩
       ▼
┌─────────────┐     ┌─────────────┐
│   Qdrant    │     │   Neo4j     │  엔티티, 관계, 커뮤니티
│  (벡터 DB)  │     │ (그래프 DB)  │  multi-hop 탐색
│  dense +    │     └─────────────┘
│  sparse +   │
│  ColBERT    │     ┌─────────────┐
└─────────────┘     │   Redis     │  L1 인메모리 캐시
                    │             │  L2 시맨틱 캐시
                    │             │  멱등성 키
                    └─────────────┘
```

## Embedding Provider Fallback

```
TEI (Cloud/Self-hosted)
    │ 실패 시
    ▼
Ollama (bge-m3:latest)
    │ 실패 시
    ▼
ONNX (로컬 FP32/INT8)
```

모든 프로바이더는 `EmbeddingProvider` Protocol을 충족하며 dense(1024) + sparse + ColBERT 벡터를 동일하게 반환.

## Distill Pipeline (Edge Model)

```
[데이터 큐레이션]
  usage_log (CRAG correct) ─┐
  KB 청크 QA 생성 ───────────┼→ merge + dedup → self-consistency → generality filter
  테스트 데이터 생성 ─────────┘    → augmentation + verify → DB (status=pending)
                                        ↓
  사람 리뷰 (대시보드) → 승인/거부/편집 → DB (status=approved)
                                        ↓
[빌드]
  approved 데이터 export → LoRA SFT → 평가 게이트 → GGUF 양자화 (+ SHA256)
    → S3 배포 (manifest.json) → 모델 버전 기록

[엣지 서버]
  install.sh/ps1 → 바이너리 + 모델 다운로드 → 서비스 등록 (systemd/nssm/launchd)
  sync.py (5분 주기):
    → 중앙 heartbeat push (상태/버전/시스템 정보)
    → manifest 확인 → 모델 업데이트 / 앱 staging
    → 로그 업로드
  update-edge.sh/ps1 → staging → 서비스 중지 → swap → 시작 → 헬스체크 → 롤백
```

## Key Files

| 파일 | 역할 |
|---|---|
| `src/api/app.py` | FastAPI 앱 + 9개 init 함수 |
| `src/api/state.py` | AppState (typed, dict-compatible) |
| `src/config.py` | Pydantic Settings (환경 변수) |
| `src/config_weights.py` | 튜닝 파라미터 (frozen dataclass) |
| `src/search/rag_pipeline.py` | RAG 파이프라인 오케스트레이션 |
| `src/search/tiered_response.py` | 프롬프트 템플릿 (SSOT) |
| `src/pipeline/jsonl_checkpoint.py` | JSONL 크래시 안전 체크포인트 |
| `src/vectordb/client.py` | Qdrant 하이브리드 검색 |
| `src/graph/neo4j_repository.py` | Neo4j 그래프 연산 |
| `src/embedding/types.py` | EmbeddingProvider Protocol |
| `src/distill/service.py` | Distill 파이프라인 오케스트레이터 |
| `src/distill/data_gen/generality_filter.py` | 범용성 필터 (매장/날짜/직원 종속 탈락) |
| `src/distill/repositories/edge_server.py` | 엣지 서버 CRUD + heartbeat + fleet 관리 |
| `edge/server.py` | 엣지 llama-cpp 추론 서버 |
| `edge/sync.py` | S3 모델 sync + heartbeat push + 앱 업데이트 |
