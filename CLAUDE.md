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

Services are singleton-initialized during FastAPI lifespan in `src/api/app.py`:
- `_init_services()` creates all service instances and stores them in the `_state` dict
- Routes access services via `state = _get_state(); state.get("service_name")`
- No global service imports (avoids circular imports)

### Key Module Responsibilities

| Module | Role |
|---|---|
| `src/api/routes/` | FastAPI routers, registered in `src/api/app.py` via `include_router()` |
| `src/pipeline/` | Ingestion: parse → chunk → embed → dedup (4-stage) → store to Qdrant/Neo4j |
| `src/search/` | RAG: query classify → preprocess → expand → hybrid search → rerank → LLM generate → answer guard |
| `src/vectordb/` | Qdrant operations (hybrid search: dense + sparse + ColBERT via RRF) |
| `src/graph/` | Neo4j operations (entity resolution, multi-hop search) |
| `src/embedding/` | BGE-M3 providers with fallback: TEI → Ollama → ONNX |
| `src/database/` | SQLAlchemy async ORM models + repository pattern |
| `src/auth/` | Auth providers (Local/Keycloak/AzureAD), RBAC + ABAC engines |
| `src/cache/` | Multi-layer: L1 in-memory + L2 Redis semantic cache |
| `src/config.py` | Pydantic Settings (env-driven, prefixed) |
| `src/config_weights.py` | Frozen dataclasses for all tunable thresholds/weights (env-overridable) |

### Search Pipeline Detail

Query types are classified (`OWNER_QUERY`, `PROCEDURE`, `TROUBLESHOOT`, `CONCEPT`, `GENERAL`) and routed differently — e.g., owner queries skip LLM generation. The composite reranker fuses model score (0.6), base similarity (0.3), and source weight (0.1) with FAQ/axis boosts.

### Ingestion Pipeline Detail

4-stage dedup: bloom filter → exact hash → semantic similarity → LLM conflict detection. Supports PDF, DOCX, PPTX, XLSX, images (PaddleOCR). GraphRAG extraction produces entities and relationships for Neo4j.

## Code Conventions

- **Async everywhere**: routes, repositories, and service methods are `async def`. CPU-bound work (embedding) uses `asyncio.to_thread()`
- **Ruff**: target `py312`, line-length 100
- **Config**: all env vars via Pydantic Settings (see `.env.example`). Weights/thresholds in `src/config_weights.py` with `_env_float()`/`_env_int()` helpers
- **Repository pattern**: each domain entity has a repository class in `src/database/repositories/`
- **Route pattern**: create router in `src/api/routes/`, register in `src/api/app.py`
