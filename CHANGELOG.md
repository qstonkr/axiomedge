# Changelog

## [0.2.0] - 2026-04-04

### Added
- Cloud TEI 배포 (BGE-M3 embedding + BGE reranker) — TEI > Ollama > ONNX fallback 체인
- Identifier 패턴 검색 (사번, 전화번호 등) + document diversity for chunk-level recall
- Date-aware sparse weighting (날짜 토큰 형태소 강화)
- Date-filtered supplementary search (시간 특정 질의)
- Person MENTIONED_IN 그래프 관계 + 날짜 기반 필터링
- Relative time resolution (상대 시간 해석: "지난주", "이번 달")
- KB-specific context to golden set prompt
- Business logic/service policy to itops_general KB context
- MCP 서버 (stdio/SSE) — KH API 래핑 (search, ask, find_expert)
- RAGAS-style evaluation: context-aware judge + CRAG recommendation + recall 메트릭
- Golden set 관리 페이지 (Dashboard)
- Metadata enrichment batch script
- Batch chunk cleaning pipeline

### Changed
- Rerank pool multiplier 5 → 8 (wider candidate pool for date/person queries)
- Dynamic graph injection score (E2E grounding 개선)
- Golden set prompt 개선: store names + specific context 필수화
- Improved person extraction + MENTIONED_IN graph search
- Search call throttling in RAG evaluation script

### Fixed
- Person MENTIONED_IN: execute_query 사용 (raw session 대신)
- 228 SonarQube code smells (S8415 + S8410)
- 77 SonarQube code smells (non-complexity)
- 36 S3776 (complexity 16-20) across 28 files
- 6 SonarQube reliability issues (Overall Code)
- Document parser complexity + pytest-asyncio auto mode
- CORSMiddleware 순서 수정 (AuthMiddleware 이후)
- S4144 중복 코드 (get_entity_count/get_document_count)

### Infrastructure
- K8s production readiness: Kustomize, HPA (1-4 replicas), startup/readiness/liveness probes
- Helm chart 구조
- CI/CD pipeline
- Prometheus metrics endpoint (`/metrics?format=prometheus`)
- Incremental crawl/ingest 지원

### Quality
- 테스트 커버리지 80% → 85% (3,335 tests)
- OCR domain dictionary correction
- CRAG + recall metrics in eval-results API and dashboard

## [0.1.0] - 2026-03-01

Initial release.

- Hybrid search: dense + sparse + ColBERT via RRF
- 4-stage dedup pipeline (bloom → hash → semantic → LLM)
- PaddleOCR integration (Korean PP-OCRv5)
- EXAONE 3.5 LLM (Ollama)
- BGE-M3 embedding (ONNX)
- Neo4j GraphRAG
- Multi-layer cache (L1 in-memory + L2 semantic)
- Auth system (local/internal/keycloak/azure_ad)
- Streamlit dashboard
- JSONL crash-safe checkpoint
- Knowledge Trust Score (KTS)
