# Domain Glossary

GS Retail knowledge-local 프로젝트에서 사용하는 도메인 용어 정의.
코드/문서/대화에서 혼동 방지를 위한 single reference.

---

## 조직 축약어

| 약어 | 정식 명칭 | 설명 |
|---|---|---|
| **PBU** | 편의점 사업부 | GS25 편의점 운영. 점주/매장 직원 대상 KB |
| **HBU** | 홈쇼핑 사업부 | GS홈쇼핑/온라인 판매 |
| **FBU** | 식품 사업부 | 가공식품 제조/수입 |
| **디지털** | 디지털 사업부 | 전자상거래 플랫폼 (우리동네GS 등) |
| **DXCOE** | DX Center of Excellence | AI/데이터 조직 (이 프로젝트의 소유 팀) |

---

## Knowledge Base (KB)

| 용어 | 정의 |
|---|---|
| **KB** | Knowledge Base. 하나의 검색 단위. 문서들의 논리적 그룹. |
| **KB ID** | KB 식별자. 예: `pbu-store`, `hbu-online`, `food-import` |
| **KB ID prefix** | Qdrant collection 이름은 `kb_<kb_id>` (하이픈 → 언더스코어). 예: `kb_pbu_store` |
| **Search Group** | 여러 KB 를 묶은 그룹. Distill 학습 범위 지정에 사용. 예: `PBU`, `HBU`, `전체` |
| **Collection** | Qdrant 의 물리적 저장 단위. KB 당 1개 collection. |
| **Tier** | KB 중요도 등급. `primary` / `secondary` / `archive` |

---

## 검색 파이프라인

| 용어 | 정의 |
|---|---|
| **Hub Search** | `POST /api/v1/search/hub` — 검색 + 재정렬 + LLM 답변을 한 번에 |
| **Dense vector** | BGE-M3 임베딩 (float32[1024]). 의미 기반 검색. |
| **Sparse vector** | ColBERT token-level 임베딩. 키워드 매칭 강화. |
| **RRF** | Reciprocal Rank Fusion. dense + sparse 결과 병합 알고리즘. |
| **Composite Rerank** | Neural reranker (0.6) + base score (0.3) + source prior (0.1) + entity boost |
| **CRAG** | Corrective RAG. 검색 결과 품질 평가 → confidence 결정. |
| **Tiered Response** | 쿼리 유형 (factual/procedural/comparative) 별 LLM 프롬프트 선택. |
| **Answer Guard** | 생성된 답변의 hallucination 감지. embedding 유사도 기반. |
| **Graph Expansion** | Neo4j entity/relation 으로 검색 결과 보강 (multi-hop). |
| **Confidence** | 검색 결과 신뢰도. `높음` / `중간` / `낮음`. CRAG score 기반. |

---

## 인제스트 파이프라인

| 용어 | 정의 |
|---|---|
| **Stage 1** | 파싱 단계. 파일 → 텍스트 + OCR → JSONL checkpoint |
| **Stage 2** | 인덱싱 단계. 텍스트 → chunk → embed → dedup → store |
| **Chunk** | 문서를 분할한 단위. ~2500자, 1문장 overlap. KSS 한국어 분리. |
| **Content Hash** | 청크 내용의 SHA256. incremental 인제스트에서 변경 감지 + idempotent upsert. |
| **JSONL Checkpoint** | Stage 1 완료 시 `stage1.jsonl` 에 저장. Crash 복구 지점. |
| **Dedup (4-stage)** | Bloom filter → exact hash → Jaccard (≥0.80) → semantic (≥0.95) |
| **Contextual Prefix** | 상위 섹션 요약을 chunk 앞에 prepend. embedding 품질 향상. |
| **Domain Dict** | OCR 오타 보정용 GS Retail 전문 용어 사전. choseong fuzzy matching. |

---

## GraphRAG

| 용어 | 정의 |
|---|---|
| **Entity** | 문서에서 추출된 개체. 유형: Person / Store / Team / System / Product / Concept / Location / Event |
| **Relationship** | 엔티티 간 관계. 허용 목록: MENTIONS / MANAGES / WORKS_AT / RELATED_TO / HAS_ISSUE / CAUSED_BY / RESOLVED_BY / PART_OF |
| **Multi-hop** | N-hop 그래프 탐색. 기본 2-hop. `find_related()` 로 구현. |
| **Entity Boost** | 쿼리 엔티티와 chunk 엔티티가 매칭되면 score × 1.15 |
| **Placeholder** | LLM 이 생성하는 sentinel 값 ("미상", "unknown", "TBD"). 필터로 제거. |

---

## Distill (엣지 모델)

| 용어 | 정의 |
|---|---|
| **Distill Profile** | 학습 설정 묶음. search_group + base_model + LoRA + training params + deploy config. |
| **Teacher Model** | QA 데이터 생성용 대형 LLM. 기본 `exaone-sagemaker`. |
| **Student Model** | 엣지 배포용 소형 LLM. Profile 의 `base_model` 필드. 예: `google/gemma-3-4b-it` |
| **Base Model Registry** | `distill_base_models` DB 테이블. 드롭다운 SSOT. |
| **LoRA SFT** | Low-Rank Adaptation Supervised Fine-Tuning. Teacher QA 로 Student 학습. |
| **GGUF** | llama.cpp 호환 양자화 모델 포맷. Q4_K_M 이 기본. |
| **Phase 1** | 기존 QA 생성 → LoRA 학습. |
| **Phase 1.5** | Answer reformatter (2문단 포맷) + Question augmenter (paraphrase × N) |
| **Reformatter** | 긴 RAG 답변을 1B 모델이 학습 가능한 2문단 포맷으로 재작성. `src/distill/data_gen/reformatter.py` |
| **Question Augmenter** | 한 fact 에 대해 N개 질문 표현 생성. exposures 증가. LLM judge 검증. |
| **Seed** | `src/distill/seed.py::DEFAULT_BASE_MODELS`. 앱 시작 시 insert-if-missing 으로 DB 에 주입. admin 편집 보존. |
| **Build** | 한 번의 학습 실행 단위. pending → generating → training → evaluating → quantizing → deploying → completed/failed |
| **Edge Server** | 매장에 배포된 llama-cpp-python 기반 추론 서버. heartbeat + model sync. |

---

## 인증 / 보안

| 용어 | 정의 |
|---|---|
| **Auth Provider** | 인증 방식. `local` (API key) / `internal` (JWT) / `keycloak` / `azure_ad` |
| **RBAC** | Role-Based Access Control. admin / curator / analyst / viewer |
| **ABAC** | Attribute-Based Access Control. 정책 기반 (KB 소유권 등) |
| **Prompt Injection** | 악성 입력으로 LLM 지시문 우회 시도. `src/llm/prompt_safety.py` 로 방어. |
| **Answer Guard** | 생성 답변 hallucination 감지. embedding 유사도 기반. |

---

## 인프라

| 용어 | 정의 |
|---|---|
| **TEI** | Text Embeddings Inference. SageMaker 에서 BGE-M3 서빙. |
| **PaddleOCR** | OCR 엔진. 로컬 또는 EC2 on-demand. |
| **Ollama** | 로컬 LLM 서빙. 개발/fallback 용. |
| **SageMaker** | AWS 클라우드 LLM/embedding 서빙. 프로덕션 경로. |

---

## 파일 네이밍 규칙

| 패턴 | 의미 |
|---|---|
| `kb_<id>` | Qdrant collection 이름 |
| `PBU_<term>` | PBU 도메인 용어 (glossary KB prefix) |
| `distill.yaml` | Distill profile seed 파일 |
| `*.local.py` / `*.scratch.py` | 개발 임시 파일 (gitignored) |
| `scripts/notion_*.py` | Notion 페이지 자동 업데이트 스크립트 |
| `sandbox/` | 개인 실험 코드 (gitignored) |

---

## 참고

- 검색 파이프라인 상세: `docs/RAG_PIPELINE.md`
- 인제스트 파이프라인 상세: `docs/INGESTION_PIPELINE.md`
- Distill 빌드: `docs/DISTILL.md`
- GraphRAG: `docs/GRAPHRAG.md` (Phase B PR12 에서 추가 예정)
- 전체 아키텍처: `docs/ARCHITECTURE.md`
