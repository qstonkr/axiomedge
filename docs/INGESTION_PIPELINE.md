# Ingestion Pipeline

**파일**: `src/pipeline/ingestion.py` + `src/pipeline/chunker.py` + `src/pipeline/document_parser.py` + `src/pipeline/dedup/`
**Entry points**:
- CLI: `make ingest ARGS="--source ./docs/ --kb-id my-kb"`
- API: `POST /api/v1/ingest/*` (routes in `src/api/routes/ingest.py`)
- Data source sync: 자동 trigger (Confluence / Git)

**목적**: 파일/URL → 파싱 → chunk → embed → 중복 제거 → Qdrant/Neo4j 저장.
**핵심 특성**: **2-stage crash-safe** + **incremental** + **JSONL checkpoint**.

---

## 목차

1. [전체 흐름](#전체-흐름)
2. [Stage 1: Parse → Clean → Checkpoint](#stage-1-parse--clean--checkpoint)
3. [Stage 2: Chunk → Embed → Dedup → Store](#stage-2-chunk--embed--dedup--store)
4. [Incremental 모드](#incremental-모드)
5. [Crash recovery](#crash-recovery)
6. [병렬 처리 / 튜닝](#병렬-처리--튜닝)
7. [품질 게이트](#품질-게이트)
8. [관찰성 / 디버깅](#관찰성--디버깅)

---

## 전체 흐름

```
입력 (파일 / URL / Confluence / Git)
   │
   ├─ Stage 1  (파싱)
   │    ├─ document_parser  : PDF / PPT / Excel / Markdown / HTML / Confluence
   │    ├─ OCR trigger      : PDF 비표준 폰트 감지 시 PaddleOCR 호출
   │    ├─ Domain dict      : OCR 오타 보정 (choseong fuzzy)
   │    └─ JSONL checkpoint : 매 파일 종료 시 `pipeline/stage1.jsonl` 에 flush
   │
   ├─ Stage 2  (인덱싱)
   │    ├─ Chunker          : sliding window, KSS 문장 분리, content-aware break
   │    ├─ Context prefix   : 이전 문단 요약 prepend (tree context)
   │    ├─ Passage cleaning : boilerplate / footer / citation 태그 제거
   │    ├─ Embedder         : BGE-M3 dense + sparse + colbert
   │    ├─ Dedup (4-stage)  : hash → Jaccard → Levenshtein → semantic
   │    ├─ Vector store     : Qdrant `kb_<kb_id>` collection
   │    ├─ Graph store      : Neo4j entity/relation (GraphRAG)
   │    ├─ Tree index       : 상위 summary node 생성 (선택)
   │    └─ Term extraction  : 용어 사전 (glossary) 자동 제안
   │
   └─ Usage stats  (PostgreSQL `knowledge_ingestion_runs`)
```

Stage 1 과 Stage 2 는 **분리 가능** — 대용량 인제스트에서 Stage 1 만 돌려
JSONL 백업 만들고, Stage 2 는 나중에 재시작 가능.

전체 코드: `src/pipeline/ingestion.py::IngestionPipeline.ingest` (134 줄).
**SRP 리팩터 대상** — PR9 (Phase B) 에서 `IngestionStage` Protocol 로 분리 예정.

---

## Stage 1: Parse → Clean → Checkpoint

### 1-1. 파일 유형별 파서

`src/pipeline/document_parser.py::DocumentParser`:

| 확장자 | 파서 | 특징 |
|---|---|---|
| `.pdf` | `pymupdf` + PaddleOCR fallback | 텍스트 레이어 있으면 추출, 없으면 OCR |
| `.pptx` | `python-pptx` + OCR fallback | 슬라이드 이미지 렌더링 → OCR (shape extraction) |
| `.xlsx`, `.xls` | `openpyxl` / `pandas` | Sheet 별 분리 |
| `.md`, `.markdown` | 내부 | Front matter 분리 |
| `.html`, `.htm` | BeautifulSoup | Boilerplate 제거 |
| `.txt` | 원본 | 최소 전처리 |
| Confluence body | `src/connectors/confluence/html_parsers.py` | 코드/테이블/매크로/멘션 분리 |

### 1-2. OCR trigger 조건

PDF 의 경우:

1. `pymupdf` 로 텍스트 추출 시도
2. 페이지당 텍스트 길이 < 30 chars → OCR 필요로 판단
3. PaddleOCR API 호출 (`http://localhost:8866/ocr` 또는 EC2)
4. OCR 결과에 domain dictionary 적용 (`src/ocr/ocr_corrector.py`)

**Domain dictionary**:
- 파일: `src/ocr/domain_dict.py` (GS Retail 특화 용어)
- 매칭: 초성 기반 fuzzy (오타 방지)
- 예시: "폐기시" → "폐기 시", "POS시스템" → "POS 시스템"

### 1-3. JSONL checkpoint

매 파일 파싱 완료 시 `{runtime_base_dir}/pipeline/stage1.jsonl` 에 append + fsync:

```json
{"id": "doc_001", "source_path": "/docs/a.pdf", "content": "...", "metadata": {...}, "hash": "sha256..."}
```

Crash 시 **이 파일을 tail** 해서 이미 처리된 doc_id set 을 복원 → 중단 지점부터 재개.

**구현**: `src/pipeline/jsonl_checkpoint.py::JsonlCheckpoint`

---

## Stage 2: Chunk → Embed → Dedup → Store

### 2-1. Chunking

`src/pipeline/chunker.py::Chunker`:

- **방식**: Sliding window + 문장 경계 존중 (KSS 한국어 분리기)
- **파라미터** (SSOT: `config_weights.py::ChunkingConfig`):
  - `max_chunk_chars = 2500`
  - `overlap_sentences = 1` (overlap 문장 수)
  - `max_chunks_per_document = 500` (document 폭탄 방지)
- **Content-aware break**: 코드 블록 / 표는 분리하지 않음

### 2-2. Contextual prefix

상위 문단/섹션의 짧은 요약을 chunk 앞에 prepend → embedding 품질 향상.

**예시**:
```
[섹션: 폐기 절차] [문단 요약: 상품 폐기 시 POS 등록 절차]
상품 폐기는 POS 에 등록 후 폐기 박스에 담아 본부에 반납합니다.
```

### 2-3. Passage cleaning

`src/search/passage_cleaner.py::clean_chunks` (검색에서도 공용 함수):

- 인라인 citation 태그 (`[문서 3]`) 제거
- HTML 엔티티 디코딩
- 연속 공백 정규화
- Markdown 헤더/강조 표시 정리

### 2-4. Embedding

- **Providers** (chain): TEI (SageMaker) → Ollama → ONNX
- **Batch size**: `config_weights.py::EmbeddingConfig.batch_size = 32`
- **Retry**: 5초 fixed delay × 3회 (`ingestion.py` 의 `_EMBED_MAX_RETRIES`)
- **Output**: dense (1024-dim) + sparse (colbert) + colbert (token vectors)

### 2-5. Dedup (4-stage)

`src/pipeline/dedup/dedup_pipeline.py`:

| Stage | 방법 | 목적 | Threshold |
|---|---|---|---|
| 1 | Bloom filter | 저가 early filter | 확률적 |
| 2 | Exact hash | 정확 중복 | `content_hash` 일치 |
| 3 | Near duplicate | Jaccard / shingle | ≥ 0.80 |
| 4 | Semantic dedupe | Embedding cosine | ≥ 0.95 (비싼 단계) |

**발동 순서**: 싸게 → 비싸게. 앞 단계에서 중복으로 판정되면 뒤 단계는 skip.

**Feature flag**: `weights.dedup.enable_stage4` 로 semantic 단계 토글.

### 2-6. Vector store 저장

- **Target**: Qdrant collection `kb_<kb_id>` (예: `kb_pbu_store`)
- **Upsert**: content_hash 기반 deterministic id → **re-run 안전** (같은 문서 재인제스트해도 중복 없음)
- **Payload**: chunk text + metadata (document_name, kb_id, created_at, morphemes, etc.)
- **Vectors**: `bge_dense` (float32[1024]) + `bge_sparse` (ColBERT)

**구현**: `src/vectordb/store.py::QdrantStoreOperations.upsert`

### 2-7. Graph store 저장 (GraphRAG)

- **Target**: Neo4j
- **Entity extraction**: `src/pipeline/graphrag/extractor.py` — LLM 기반 (KOREAN_EXTRACTION_PROMPT)
- **Allowed entity types**: Person / Store / Team / System / Product / Concept / Location / Event
- **Relationships**: MENTIONS / MANAGES / WORKS_AT / RELATED_TO / HAS_ISSUE 등
- **Placeholder 필터**: "미상", "unknown", "TBD" 같은 sentinel 제외
- **Corruption 필터**: OCR 오류 (lone jamo, 반복 음절) 제외

**Feature flag**: `weights.ingestion.enable_graphrag`

### 2-8. Tree index (선택)

- **목적**: 상위 summary node 생성 (section-level) → 검색 시 tree context expansion 에서 사용
- **구현**: `src/pipeline/summary_tree_builder.py::SummaryTreeBuilder`
- **비용**: LLM 호출 비쌈. 중요 KB 에만 활성화.

### 2-9. Term extraction

- **목적**: 인제스트된 chunk 에서 새 용어 후보 자동 추출 → glossary 제안
- **구현**: `src/pipeline/term_extractor.py`
- **결과**: `glossary_terms` 테이블에 `status="pending"` 으로 저장 → 대시보드 큐레이션

---

## Incremental 모드

기본값: `KNOWLEDGE_PIPELINE_INCREMENTAL_MODE=true`

### 동작

1. 파일 hash 계산 (SHA256 of content)
2. Qdrant `content_hash` 로 기존 chunk 검색
3. Hash 일치 → **skip** (이미 처리됨)
4. Hash 불일치 → 기존 chunk 삭제 + 재처리
5. 해당 파일의 Neo4j 엔티티도 update

### 강제 재처리

```bash
KNOWLEDGE_PIPELINE_FORCE_REBUILD=true make ingest ARGS="--source ... --kb-id ..."
```

모든 파일을 새로 처리. chunk content 에 영향 없는 하이퍼파라미터 변경 (ex. chunk size) 시 사용.

### Hash 무엇을 포함하는가

- **기본**: 파일 raw bytes
- **Confluence**: page body + attachment content + labels
- **Git**: commit SHA + file mode
- **상세**: `src/pipeline/dedup/content_hash.py`

---

## Crash recovery

### Stage 1 중단 → 재개

```bash
# 재개 (자동 — 같은 source + kb_id 로 재실행)
make ingest ARGS="--source /data/docs --kb-id my-kb"
```

Stage 1 이 JSONL checkpoint 를 tail 해서 이미 처리된 파일 id set 을 복원 → 중단 지점부터 재시작.

### Stage 2 중단 → 재개

Stage 2 는 chunk-level idempotent (deterministic id + upsert) 이므로 **그냥 다시 돌려도 안전**. 이미 저장된 chunk 는 덮어쓰고, 새 chunk 만 추가.

### 전체 재시작 (drop + re-ingest)

```bash
# Qdrant collection 삭제
curl -X DELETE "http://localhost:6333/collections/kb_my_kb"

# Neo4j 관련 노드 삭제 (주의 — cross-KB 관계는 orphan 될 수 있음)
# docs/OPS.md 의 정지/롤백 절차 참고

# 재인제스트
make ingest ARGS="--source /data/docs --kb-id my-kb"
```

### 깨진 JSONL checkpoint 복구

```bash
# Checkpoint 파일 확인
ls -la /tmp/knowledge-local/pipeline/

# 수동 삭제 (완전 재처리)
rm /tmp/knowledge-local/pipeline/stage1.jsonl

# 특정 파일만 재처리하려면 해당 id 를 jsonl 에서 제거
grep -v "doc_001" stage1.jsonl > stage1.new.jsonl && mv stage1.new.jsonl stage1.jsonl
```

---

## 병렬 처리 / 튜닝

### Worker pool

- **Env var**: `KNOWLEDGE_PIPELINE_MAX_WORKERS` (기본 4)
- **Config**: `src/config.py::PipelineSettings.max_workers`
- **범위**: 1 ~ 16

### 동시성 선택 가이드

| 케이스 | 권장 worker 수 |
|---|---|
| 로컬 개발 (Mac M1/M2) | 4 |
| 서버 (8 core) | 6~8 |
| CI (2 core) | 2 |
| 대용량 OCR 병목 | 8+ (PaddleOCR EC2 동시성에 맞춰) |

### Batch size

- **Chunk-level**: `PipelineSettings.batch_size = 50` (한 번에 embed 요청할 chunk 수)
- **Embedder forward**: `EmbeddingConfig.batch_size = 32` (embedder 내부)
- **Qdrant upsert**: `weights.pipeline.qdrant_upsert_batch_size = 64`

**주의**: 같은 이름 `batch_size` 가 다른 개념이라 혼동 주의. `docs/CONFIGURATION.md` 참고.

### 병목 대응

| 병목 | 대응 |
|---|---|
| OCR | PaddleOCR EC2 instance 띄우기 (`docs/CONFLUENCE_CRAWLER.md`) |
| Embedding | TEI (SageMaker) 활성화 (`USE_CLOUD_EMBEDDING=true`) |
| Neo4j | `enable_graphrag=false` 로 일시 비활성화 |
| Qdrant | HNSW 파라미터 조정 (`weights.vectordb.*`) |

---

## 품질 게이트

`src/pipeline/ingestion_gate.py::IngestionGate`:

### Validation rules

| Rule | Threshold | 액션 |
|---|---|---|
| 최소 content 길이 | `min_content_length = 50` | 미달 → reject |
| 최대 파일 크기 | 50MB | 초과 → reject + warning |
| 언어 검출 | Korean / English | 다른 언어 → warning |
| 중복률 | Jaccard ≥ 0.95 | 기존과 완전 중복 → skip |
| OCR 신뢰도 | ≥ 0.65 (`OCR_MIN_CONFIDENCE`) | 낮으면 metadata 에 flag |

### 오류 분류

- **Parse error**: 파일 포맷 손상 → `pipeline/errors.jsonl` 에 기록 → 재시도 안 함
- **Transient error**: 네트워크 / Ollama timeout → 3회 재시도 (5s delay)
- **Validation reject**: 위 rule 위반 → `status=rejected` 로 checkpoint 에 기록

---

## 관찰성 / 디버깅

### Ingestion runs 테이블

```sql
SELECT run_id, kb_id, started_at, status, total_files, processed_files, failed_files
FROM knowledge_ingestion_runs
ORDER BY started_at DESC
LIMIT 10;
```

### 에러 로그

- **Location**: `{runtime_base_dir}/pipeline/errors.jsonl`
- **Format**: `{file_path, stage, error_type, message, timestamp}`
- **조회**: `tail -f errors.jsonl | jq`

### Progress 모니터링

```bash
# 실시간 카운트
watch -n 2 'wc -l /tmp/knowledge-local/pipeline/stage1.jsonl'

# Qdrant collection 크기
curl -sS "http://localhost:6333/collections/kb_my_kb" | jq '.result.points_count'
```

### Slow file 감지

```bash
# Python REPL
from src.pipeline.jsonl_checkpoint import JsonlCheckpoint
cp = JsonlCheckpoint("/tmp/knowledge-local/pipeline/stage1.jsonl")
# 처리 시간 sort
slow = sorted(cp.entries(), key=lambda e: e.get("parse_duration_ms", 0), reverse=True)[:10]
```

### Failed re-ingest (legalize-kr 용)

```bash
# 실패 리스트에서 재인제스트
uv run python scripts/reingest_failed_legal.py --source /data/legal --errors-file errors.jsonl
```

---

## CLI 사용 예시

### 단일 디렉터리

```bash
make ingest ARGS="--source /data/pbu-docs --kb-id pbu-store"
```

### Stage 1 만 (백업 용)

```bash
uv run python -m cli.ingest --source /data/docs --kb-id backup --stage1-only
```

### 특정 파일만

```bash
uv run python -m cli.ingest --source /data/docs/specific.pdf --kb-id test
```

### Incremental 강제 off

```bash
KNOWLEDGE_PIPELINE_FORCE_REBUILD=true make ingest ARGS="--source /data --kb-id my-kb"
```

### Confluence 크롤 + 자동 인제스트

```bash
CONFLUENCE_PAT=xxx uv run python scripts/confluence_crawler.py --page-id 373865276 --full
# 또는 data source 등록 후 대시보드에서 trigger — docs/CONFLUENCE_CRAWLER.md
```

---

## 향후 리팩터 (계획)

- **PR9 (Phase B)**: `ingest()` 134줄 → `IngestionStage` Protocol + `IngestionContext` 객체 기반 단계별 분리. 각 단계 plug-in 추가 가능.
- **Dedup plugin**: `DedupStage` Protocol 로 LLM-based semantic dedupe 같은 신규 단계 추가 쉽게.
- **Parallel worker**: 현재 per-file 병렬. 한 파일 내부 chunk 병렬 embed 는 아직 없음 (Phase D).

상세: `docs/IMPROVEMENT_PLAN.md` Phase B 섹션.
