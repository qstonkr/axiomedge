# Configuration Reference

모든 설정은 환경 변수로 제어됩니다. `.env` 파일 또는 시스템 환경 변수를 사용하세요.

## Infrastructure

### PostgreSQL

| 변수 | 기본값 | 설명 |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://knowledge:knowledge@localhost:5432/knowledge_db` | 데이터베이스 연결 URL |
| `POOL_SIZE` | `5` | 커넥션 풀 크기 |
| `MAX_OVERFLOW` | `10` | 최대 오버플로 커넥션 |

### Redis

| 변수 | 기본값 | 설명 |
|---|---|---|
| `REDIS_URL` | `redis://localhost:6379` | Redis 연결 URL |

### Qdrant

| 변수 | 기본값 | 설명 |
|---|---|---|
| `QDRANT_URL` | `http://localhost:6333` | Qdrant 서버 URL |
| `QDRANT_COLLECTION_NAME` | `knowledge` | 기본 컬렉션명 |
| `QDRANT_ENTITY_COLLECTION_NAME` | `knowledge_entities` | 엔티티 컬렉션명 |
| `QDRANT_DENSE_DIMENSION` | `1024` | Dense 벡터 차원 (BGE-M3 고정) |
| `QDRANT_TIMEOUT` | `30` | 연결 타임아웃 (초) |
| `QDRANT_SEARCH_TIMEOUT_MS` | `5000` | 검색 타임아웃 (ms) |

### Neo4j

| 변수 | 기본값 | 설명 |
|---|---|---|
| `NEO4J_ENABLED` | `true` | 그래프 DB 활성화 |
| `NEO4J_URI` | `bolt://localhost:7687` | Neo4j 연결 URI |
| `NEO4J_USER` | `neo4j` | 사용자명 |
| `NEO4J_PASSWORD` | (빈값) | 비밀번호 |
| `NEO4J_AUTH` | `none` | 인증 방식 |

---

## LLM

### Ollama

| 변수 | 기본값 | 설명 |
|---|---|---|
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama 서버 URL |
| `OLLAMA_MODEL` | `exaone3.5:7.8b` | LLM 모델명 |
| `OLLAMA_EMBEDDING_MODEL` | `bge-m3:latest` | Ollama 임베딩 모델 |
| `OLLAMA_TIMEOUT` | `60` | 타임아웃 (초, 10-300) |
| `OLLAMA_CONTEXT_LENGTH` | `32768` | 컨텍스트 길이 (1024-32768) |
| `OLLAMA_MAX_CONTENT_LENGTH` | `4000` | 최대 콘텐츠 길이 |

### SageMaker (선택)

| 변수 | 기본값 | 설명 |
|---|---|---|
| `USE_SAGEMAKER_LLM` | `false` | SageMaker로 전환 (API 서버 전체) |
| `GRAPHRAG_USE_SAGEMAKER` | `false` | GraphRAG 추출만 SageMaker 사용 |
| `SAGEMAKER_ENDPOINT_NAME` | `oreo-exaone-dev` | SageMaker 엔드포인트명 |
| `SAGEMAKER_REGION` | `ap-northeast-2` | AWS 리전 |
| `AWS_PROFILE` | (없음) | AWS 프로파일 |

---

## Embedding

### Feature Flags

| 변수 | 기본값 | 설명 |
|---|---|---|
| `USE_CLOUD_EMBEDDING` | `true` | `true`: TEI 사용, `false`: Ollama/ONNX fallback |

### TEI (Text Embeddings Inference)

| 변수 | 기본값 | 설명 |
|---|---|---|
| `BGE_TEI_URL` | `http://localhost:8080` | BGE-M3 TEI 서버 URL |
| `RERANKER_TEI_URL` | `http://localhost:8081` | Reranker TEI 서버 URL |

### ONNX (Fallback)

| 변수 | 기본값 | 설명 |
|---|---|---|
| `KNOWLEDGE_BGE_ONNX_MODEL_PATH` | `./models/bge-m3` | ONNX 모델 경로 |
| `KNOWLEDGE_BGE_ONNX_FILE_NAME` | (기본) | ONNX 파일명 (INT8: `model_quantized.onnx`) |
| `KNOWLEDGE_BGE_MAX_LENGTH` | `512` | 최대 토큰 길이 |
| `KNOWLEDGE_BGE_COLBERT_MAX_TOKENS` | `128` | ColBERT 최대 토큰 |

---

## OCR

| 변수 | 기본값 | 설명 |
|---|---|---|
| `PADDLEOCR_API_URL` | `http://localhost:8866/ocr` | PaddleOCR API URL |
| `OCR_MIN_CONFIDENCE` | `0.65` | 최소 OCR 신뢰도 |

---

## Pipeline

| 변수 | 기본값 | 설명 |
|---|---|---|
| `KNOWLEDGE_PIPELINE_RUNTIME_BASE_DIR` | `/tmp/knowledge-local` | 파이프라인 작업 디렉토리 |
| `KNOWLEDGE_PIPELINE_MAX_WORKERS` | `4` | 최대 워커 수 (1-16) |
| `KNOWLEDGE_PIPELINE_BATCH_SIZE` | `50` | 배치 크기 (10-500) |
| `KNOWLEDGE_PIPELINE_INCREMENTAL_MODE` | `true` | 증분 모드 |
| `KNOWLEDGE_PIPELINE_FORCE_REBUILD` | `false` | 전체 재빌드 강제 |

---

## API Server

| 변수 | 기본값 | 설명 |
|---|---|---|
| `API_HOST` | `0.0.0.0` | 바인딩 호스트 |
| `API_PORT` | `8000` | 바인딩 포트 |

## Dashboard

| 변수 | 기본값 | 설명 |
|---|---|---|
| `DASHBOARD_API_URL` | `http://localhost:8000` | API 서버 URL |
| `DASHBOARD_API_TIMEOUT` | `30` | API 타임아웃 (초) |
| `DASHBOARD_SEARCH_TIMEOUT` | `60` | 검색 타임아웃 (초) |

---

## Authentication

| 변수 | 기본값 | 설명 |
|---|---|---|
| `AUTH_ENABLED` | `false` | 인증 활성화 (`false` = 전체 접근 허용) |
| `AUTH_PROVIDER` | `local` | `local` / `internal` / `keycloak` / `azure_ad` |

### Internal Auth (JWT)

| 변수 | 기본값 | 설명 |
|---|---|---|
| `AUTH_JWT_SECRET` | (필수) | JWT 서명 시크릿 |
| `AUTH_JWT_ALGORITHM` | `HS256` | JWT 알고리즘 |
| `AUTH_JWT_ACCESS_EXPIRE_MINUTES` | `60` | Access 토큰 만료 (분) |
| `AUTH_JWT_REFRESH_EXPIRE_HOURS` | `8` | Refresh 토큰 만료 (시간) |
| `AUTH_JWT_ISSUER` | `oreo-internal-api` | JWT 발급자 |
| `AUTH_COOKIE_SECURE` | `false` | HTTPS에서만 쿠키 전송 |

### Keycloak

| 변수 | 기본값 | 설명 |
|---|---|---|
| `AUTH_KEYCLOAK_URL` | (없음) | Keycloak 서버 URL |
| `AUTH_KEYCLOAK_REALM` | `knowledge` | Realm 이름 |
| `AUTH_KEYCLOAK_CLIENT_ID` | `knowledge-local` | 클라이언트 ID |
| `AUTH_KEYCLOAK_CLIENT_SECRET` | (없음) | 클라이언트 시크릿 |

### Azure AD

| 변수 | 기본값 | 설명 |
|---|---|---|
| `AUTH_AZURE_AD_TENANT_ID` | (없음) | 테넌트 ID |
| `AUTH_AZURE_AD_CLIENT_ID` | (없음) | 클라이언트 ID |

### Local (API Key)

| 변수 | 기본값 | 설명 |
|---|---|---|
| `AUTH_LOCAL_API_KEYS` | `{}` | JSON: `{"key": {"email": "...", "name": "...", "roles": [...]}}` |

---

## Quality Settings

| 변수 | 기본값 | 설명 |
|---|---|---|
| `KNOWLEDGE_QUALITY_MIN_CONTENT_LENGTH` | `50` | 최소 콘텐츠 길이 (10-1000) |
| `KNOWLEDGE_QUALITY_STALE_THRESHOLD_DAYS` | `730` | 문서 노후화 기준 (일) |
| `KNOWLEDGE_QUALITY_STALE_WEIGHT` | `0.7` | 노후화 가중치 (0.0-1.0) |

---

## Tuning Parameters (config_weights.py)

런타임에서 `PUT /api/v1/admin/config/weights`로 동적 변경 가능. 서버 재시작 시 기본값 복원.

### Reranker 가중치

| 파라미터 | 기본값 | 설명 |
|---|---|---|
| `reranker.model_weight` | `0.6` | 모델 점수 가중치 |
| `reranker.base_weight` | `0.3` | 기본 유사도 가중치 |
| `reranker.source_weight` | `0.1` | 소스 유형 가중치 |
| `reranker.faq_boost` | `1.2` | FAQ 문서 부스트 |
| `reranker.mmr_lambda` | `0.7` | MMR 다양성 파라미터 |
| `reranker.graph_distance_weight` | `0.15` | 그래프 거리 가중치 (env: `RERANKER_GRAPH_DISTANCE_WEIGHT`) |

### Hybrid Search 가중치

| 파라미터 | 기본값 | 설명 |
|---|---|---|
| `hybrid_search.dense_weight` | `0.35` | Dense 벡터 가중치 |
| `hybrid_search.sparse_weight` | `0.35` | Sparse 벡터 가중치 |
| `hybrid_search.colbert_weight` | `0.30` | ColBERT 가중치 |
| `hybrid_search.prefetch_multiplier` | `5` | Prefetch 배율 |
| `hybrid_search.prefetch_max` | `150` | 최대 prefetch 수 |

쿼리 유형별 오버라이드:
- `concept_dense_weight: 0.45 / concept_sparse_weight: 0.25` (개념 질의)
- `procedure_dense_weight: 0.25 / procedure_sparse_weight: 0.45` (절차 질의)
- `date_query_dense_weight: 0.25 / date_query_sparse_weight: 0.45` (날짜 질의)

### Confidence 임계값

| 파라미터 | 기본값 | 설명 |
|---|---|---|
| `confidence.high` | `0.85` | 높은 신뢰도 |
| `confidence.medium` | `0.70` | 중간 신뢰도 |
| `confidence.low` | `0.50` | 낮은 신뢰도 |
| `confidence.crag_correct` | `0.60` | CRAG 정답 임계값 |
| `confidence.quality_gate_faithfulness` | `0.70` | 품질 게이트: 충실도 |
| `confidence.quality_gate_context_relevancy` | `0.65` | 품질 게이트: 컨텍스트 관련성 |
| `confidence.quality_gate_answer_relevancy` | `0.70` | 품질 게이트: 응답 관련성 |

### Search 기본값

| 파라미터 | 기본값 | 설명 |
|---|---|---|
| `search.top_k` | `5` | 기본 검색 결과 수 |
| `search.rerank_pool_multiplier` | `8` | Rerank 후보 배율 (top_k * 8) |
| `search.crag_block_threshold` | `0.3` | CRAG 차단 임계값 |
| `search.keyword_boost_weight` | `0.3` | 키워드 부스트 가중치 |

### Dedup 임계값

| 파라미터 | 기본값 | 설명 |
|---|---|---|
| `dedup.near_duplicate_threshold` | `0.80` | Jaccard (Stage 2) |
| `dedup.semantic_duplicate_threshold` | `0.90` | Cosine (Stage 3) |
| `dedup.enable_stage4` | `true` | LLM 충돌 감지 |

### Cache 설정

| 파라미터 | 기본값 | 설명 |
|---|---|---|
| `cache.l1_max_entries` | `10000` | L1 인메모리 최대 항목 |
| `cache.l1_ttl_seconds` | `300` | L1 TTL (5분) |
| `cache.l2_similarity_threshold` | `0.92` | L2 시맨틱 유사도 임계값 |
| `cache.l2_ttl_seconds` | `3600` | L2 TTL (1시간) |
| `cache.enable_semantic_cache` | `true` | 시맨틱 캐시 활성화 |

도메인별 TTL: policy(30분), code(30분), kb_search(1시간), general(2시간)

### LLM 파라미터

| 파라미터 | 기본값 | 설명 |
|---|---|---|
| `llm.temperature` | `0.7` | RAG 응답 온도 |
| `llm.max_tokens` | `2048` | 최대 생성 토큰 |
| `llm.classify_temperature` | `0.1` | 분류 작업 온도 |
| `llm.max_query_length` | `2000` | 쿼리 최대 길이 |
| `llm.max_context_per_doc` | `2000` | 문서당 최대 컨텍스트 |

### Chunking

| 파라미터 | 기본값 | 설명 |
|---|---|---|
| `chunking.max_chunk_chars` | `2500` | 청크 최대 문자 수 |
| `chunking.overlap_sentences` | `1` | 오버랩 문장 수 |
| `chunking.max_chunks_per_document` | `500` | 문서당 최대 청크 수 |

### Distill (엣지 모델)

| 환경변수 | 기본값 | 설명 |
|---|---|---|
| `DISTILL_ENABLED` | `true` | Distill 플러그인 활성화 |
| `DISTILL_CONFIG_PATH` | `distill.yaml` | 프로필 설정 파일 경로 |
| `DISTILL_WORK_DIR` | `/tmp/distill` | 학습 작업 디렉토리 |
| `DISTILL_LLM_CONCURRENCY` | `3` | Teacher LLM 동시 호출 수 |
| `DISTILL_LLM_TIMEOUT_SEC` | `120` | LLM 호출 타임아웃 |
| `DISTILL_BUILD_TIMEOUT_SEC` | `7200` | 빌드 전체 타임아웃 |
| `DISTILL_RAG_API_URL` | `http://localhost:8000` | 재학습 답변 생성용 RAG API |

### Edge Server (엣지 서버)

| 환경변수 | 기본값 | 설명 |
|---|---|---|
| `STORE_ID` | `unknown` | 매장 ID |
| `EDGE_API_KEY` | - | heartbeat 인증 키 |
| `MODEL_PATH` | `/models/current/model.gguf` | GGUF 모델 경로 |
| `LOG_DIR` | `/logs` | 질의 로그 디렉토리 |
| `EDGE_N_CTX` | `512` | llama-cpp context length |
| `EDGE_N_THREADS` | `4` | 추론 스레드 수 |
| `EDGE_MAX_TOKENS` | `256` | 최대 생성 토큰 |
| `MANIFEST_URL` | - | S3 manifest URL (모델 + 앱 버전) |
| `CENTRAL_API_URL` | - | 중앙 서버 API URL (heartbeat push) |
| `APP_VERSION` | `dev` | 앱 바이너리 버전 |
