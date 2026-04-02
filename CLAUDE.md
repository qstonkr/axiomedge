# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**knowledge-local** is a standalone, local-first knowledge management system with RAG capabilities. It provides document ingestion, hybrid vector+graph search, and LLM-powered answer generation — all running locally with no cloud dependencies.

Tech stack: Python 3.12+, FastAPI, Streamlit, Qdrant (vector), Neo4j (graph), PostgreSQL, Redis, Ollama (LLM/embedding), BGE-M3 (embedding), PaddleOCR. Korean NLP support via KiwiPy + KSS.

## Commands

```bash
# Setup
make setup              # uv sync + prints model download instructions
make start              # docker compose up (PostgreSQL, Qdrant, Neo4j, Ollama, Redis, TEI, PaddleOCR)
make stop               # docker compose down

# Run services
make api                # FastAPI on :8000 (uvicorn, 2 workers)
make dashboard          # Streamlit on :8501

# CLI
make ingest ARGS="--source ./docs/ --kb-id my-kb"
make search ARGS="query --kb-id my-kb"
make crawl ARGS="..."

# Tests
make test               # All tests (pytest)
make test-unit          # Unit tests only (no services needed)
make test-integration   # Requires running API
make test-e2e           # Requires all services

# Build & Deploy
make docker-build       # Build API container
make k8s-deploy         # Deploy to k3s
```

Single test: `uv run pytest tests/unit/test_foo.py::test_bar -v --no-cov`

## Architecture

```
CLI (cli/)  ──┐
Dashboard ────┼──▶ FastAPI API (:8000)
(Streamlit)   │     ├── Routes (src/api/routes/)
              │     ├── Middleware (auth, CORS)
              │     └── Services (singleton, lifespan-managed)
              │           │
              │     ┌─────┴──────────────────────┐
              │     ▼                              ▼
              │  Pipeline                       Search/RAG
              │  (src/pipeline/)                (src/search/)
              │  parse→chunk→embed→dedup→store  classify→expand→search→rerank→generate
              │     │                              │
              └─────┴──────────────────────────────┘
                    │         │          │         │
                 Qdrant    Neo4j    PostgreSQL   Redis
                (vectors)  (graph)  (metadata)  (cache)
```

### Service Initialization

Services are singleton-initialized during FastAPI lifespan in `src/api/app.py`. The init is split into 9 category-based functions orchestrated by `_init_services()`:

```
_init_services()
  ├── _init_database(state, settings)   # PostgreSQL + 13 repositories + domain services
  ├── _init_cache(state)                # Redis + multi-layer cache + idempotency
  ├── _init_dedup(state)                # 4-stage dedup pipeline
  ├── _init_vectordb(state, settings)   # Qdrant client/collections/search/store
  ├── _init_graph(state, settings)      # Neo4j + graph repo/expander/integrity
  ├── _init_embedding(state, settings)  # TEI > Ollama > ONNX fallback
  ├── _init_llm(state, settings)        # Ollama or SageMaker + GraphRAG
  ├── _init_search_services(state)      # Query preprocessor, reranker, RAG pipeline
  └── _init_auth(state, settings)       # Auth provider + RBAC/ABAC
```

- **AppState** (`src/api/state.py`): Typed dataclass replacing untyped dict. Supports both attribute access (`state.embedder`) and dict-style (`state["embedder"]`, `state.get("key")`) for backward compatibility.
- Routes access services via `state = _get_state(); state.embedder` or `state.get("embedder")`
- No global service imports (avoids circular imports)

### Protocols & Abstractions

| Protocol | File | Implementations |
|---|---|---|
| `EmbeddingProvider` | `src/embedding/types.py` | OllamaEmbeddingProvider, TEIEmbeddingProvider, OnnxBgeEmbeddingProvider |
| `LLMClient` | `src/llm/types.py` | OllamaClient, SageMakerLLMClient |
| `GraphRepository` | `src/graph/types.py` | Neo4jGraphRepository, NoOpNeo4jGraphRepository |
| `ICacheLayer` | `src/cache/cache_types.py` | L1InMemoryCache, L2SemanticCache |
| `BaseRepository` | `src/database/repositories/base.py` | All 13 domain repositories (except KBRegistryRepository) |

All Protocols are `runtime_checkable` and use structural typing — implementations satisfy them without explicit inheritance.

### SSOT (Single Source of Truth) Rules

Constants and thresholds are centralized. **Do not hardcode values** — always reference the SSOT:

| Value | SSOT Location | Usage |
|---|---|---|
| Embedding dimension (1024) | `config_weights.EmbeddingConfig.dimension` | All providers, vectordb, guard |
| LLM model name | `config.DEFAULT_LLM_MODEL` | ollama_client, conflict_detector, graphrag |
| Embedding model name | `config.DEFAULT_EMBEDDING_MODEL` | ollama_provider, provider_factory |
| Database URL | `config.DEFAULT_DATABASE_URL` | DatabaseSettings, init_db |
| Confidence thresholds | `config_weights.ConfidenceConfig` | answer_service, rag_pipeline |
| Trust score weights | `config_weights.TrustScoreWeights` | trust_score_service |
| Cache domain thresholds | `config_weights.CacheConfig` | cache_types.DOMAIN_THRESHOLDS |
| Dedup thresholds | `config_weights.DedupConfig` | dedup_pipeline |
| Similarity fallback | `config_weights.SimilarityThresholds` | enhanced_similarity_matcher |
| Prompt templates | `src/search/tiered_response.py` | answer_service imports from here |
| Sparse token hash | `src/embedding/embedding_guard.sparse_token_hash()` | ollama_provider, tei_provider |
| JWT issuer/algorithm | `config.AuthSettings.jwt_issuer/jwt_algorithm` | jwt_service, providers |
| Bcrypt rounds (12) | `src/auth/password.BCRYPT_ROUNDS` | password hashing |

### Key Module Responsibilities

| Module | Role |
|---|---|
| `src/api/app.py` | FastAPI app + 9 init functions |
| `src/api/state.py` | Typed AppState (dict-compatible) |
| `src/api/routes/` | FastAPI routers, registered via `include_router()` |
| `src/pipeline/` | Ingestion: parse → chunk → embed → dedup (4-stage) → store to Qdrant/Neo4j |
| `src/search/` | RAG: query classify → preprocess → expand → hybrid search → rerank → LLM generate → answer guard |
| `src/vectordb/` | Qdrant operations (hybrid search: dense + sparse + ColBERT via RRF) |
| `src/graph/` | Neo4j operations (entity resolution, multi-hop search) |
| `src/embedding/` | BGE-M3 providers with fallback: TEI → Ollama → ONNX. Protocol in `types.py` |
| `src/llm/` | LLM clients (Ollama/SageMaker). Shared utils in `utils.py`, Protocol in `types.py` |
| `src/database/` | SQLAlchemy async ORM models + repository pattern. Base class in `repositories/base.py` |
| `src/auth/` | Auth: Internal(email/pw)+Local(API key)+Keycloak+AzureAD providers, JWT, RBAC, ABAC |
| `src/cache/` | Multi-layer: L1 in-memory + L2 Redis semantic cache. ICacheLayer ABC |
| `src/config.py` | Pydantic Settings (env-driven) + SSOT constants (DEFAULT_LLM_MODEL, etc.) |
| `src/config_weights.py` | Frozen dataclasses for all tunable thresholds/weights (env-overridable) |

### Search Pipeline Detail

Query types are classified (`OWNER_QUERY`, `PROCEDURE`, `TROUBLESHOOT`, `CONCEPT`, `GENERAL`) and routed differently — e.g., owner queries skip LLM generation. The composite reranker fuses model score (0.6), base similarity (0.3), and source weight (0.1) with FAQ/axis boosts.

### Ingestion Pipeline Detail

Two-stage pipeline with JSONL checkpoint for crash safety:

```
Stage 1 (parse/OCR):  file → parse_file_enhanced() → broken CMap detection → 300 DPI render
                      → PaddleOCR → LLM noise correction (EXAONE) → JSONL checkpoint
Stage 2 (ingest):     JSONL → preprocess → chunk → passage clean → contextual retrieval prefix
                      → embed (dense+sparse) → Qdrant/Neo4j store
```

- **JSONL checkpoint** (`src/pipeline/jsonl_checkpoint.py`): crash-safe append+fsync. If OCR segfaults, already-parsed docs survive. Re-run Stage 2 via `POST /api/v1/knowledge/reingest-from-jsonl`
- **Broken CMap font detection** (`_has_broken_cmap_fonts()`): PowerPoint PDF exports often have stripped ToUnicode CMap tables. Pages with broken fonts (large embedded font but <20 Unicode mappings) are routed to OCR instead of text extraction
- **Contextual Retrieval** (Anthropic pattern): each chunk gets `[Context] Document: {title} | Section {i}/{n}` + `[Summary]` prefix before embedding (35-49% retrieval improvement)
- **Passage Cleaning**: sentence dedup + incomplete fragment removal before embedding
- 4-stage dedup: bloom filter → exact hash → semantic similarity → LLM conflict detection
- Supports PDF, DOCX, PPTX, XLSX, images (PaddleOCR). GraphRAG extraction produces entities and relationships for Neo4j

### PaddleOCR (Critical: build from source)

PaddleOCR runs in a Docker container (`docker/paddleocr/`). **PaddlePaddle must be built from source with `-DWITH_MKLDNN=OFF`**.

Why: PaddlePaddle 3.x PIR executor auto-inserts OneDNN passes at graph lowering (compile time). Runtime flags (`FLAGS_use_mkldnn`, `FLAGS_enable_pir_api`) are ineffective — the OneDNN dialect is already baked in. This causes `ConvertPirAttribute2RuntimeAttribute not support` errors on CPU. The only solution is a source build without OneDNN.

```bash
# Build PaddlePaddle wheel (one-time, ~1-2 hours)
docker build -f docker/paddleocr/build_paddle.Dockerfile -t paddle-builder docker/paddleocr/
docker run --rm -v $(pwd)/docker/paddleocr/wheels:/out paddle-builder cp /paddle/dist/*.whl /out/

# The wheel is then used in the PaddleOCR Dockerfile
```

Key PaddleOCR settings:
- Model: `korean_PP-OCRv5_mobile_rec` (88% accuracy, +65% vs v3) + `PP-OCRv5_mobile_det`
- API: PaddleOCR 3.x (`paddleocr>=3.4.0`) with `.predict()` (not `.ocr()`)
- `use_angle_cls=False` (segfault trigger), pre-filter images <20x20px
- `restart: unless-stopped` on Docker container
- Models pre-downloaded to `/root/.paddlex/official_models/` (avoid SSL issues)

### Auth System

Four providers via `AUTH_PROVIDER` env var: `local` (API key, default), `internal` (email/password+JWT), `keycloak`, `azure_ad`. `AUTH_ENABLED=false` bypasses all auth (anonymous admin).

**Internal auth flow** (`AUTH_PROVIDER=internal`):
- Login: `POST /auth/login` → bcrypt verify → JWT access (60min) + refresh (8h) in HttpOnly cookies
- Refresh: `POST /auth/refresh` → token rotation with family tracking (reuse detection)
- Logout: `POST /auth/logout` → revoke token family + clear cookies
- Register: `POST /auth/register` (admin only) → bcrypt hash + DB user
- JWT claims match oreo-ecosystem: `{sub, email, display_name, roles, permissions, jti, iss="oreo-internal-api"}`

**Key files**: `jwt_service.py` (token create/verify), `token_store.py` (PostgreSQL refresh token rotation), `password.py` (bcrypt), `providers.py` (InternalAuthProvider)

**Config** (`src/config.py` AuthSettings): `AUTH_JWT_SECRET` (required), `AUTH_JWT_ACCESS_EXPIRE_MINUTES`, `AUTH_JWT_REFRESH_EXPIRE_HOURS`, `AUTH_COOKIE_SECURE`, `AUTH_ADMIN_INITIAL_PASSWORD`

**RBAC**: 5 roles (viewer→contributor→editor→kb_manager→admin). Permission format `resource:action`. Admin gets `*:*`.

## Code Conventions

- **Async everywhere**: routes, repositories, and service methods are `async def`. CPU-bound work (embedding) uses `asyncio.to_thread()`
- **Ruff**: target `py312`, line-length 100. Run `uvx ruff check src/` — must be clean
- **Config**: all env vars via Pydantic Settings (see `.env.example`). Weights/thresholds in `src/config_weights.py` with `_env_float()`/`_env_int()` helpers
- **SSOT**: never hardcode dimensions, model names, thresholds, or DB URLs. Import from `config.py` or `config_weights.py` (see SSOT table above)
- **Protocols**: new providers/clients must satisfy existing Protocols (`EmbeddingProvider`, `LLMClient`, `GraphRepository`). Check with `isinstance(inst, Protocol)`
- **Repository pattern**: inherit from `BaseRepository` in `src/database/repositories/base.py`. KBRegistryRepository is the only exception (manages own engine)
- **Route pattern**: create router in `src/api/routes/`, register in `src/api/app.py`
- **Service init**: add new services to the appropriate `_init_*()` function in `app.py` and add the field to `AppState` in `src/api/state.py`
- **Tests**: unit tests in `tests/unit/` (no services needed), integration in `tests/integration/` (needs API), e2e in `tests/e2e/` (needs all services)
