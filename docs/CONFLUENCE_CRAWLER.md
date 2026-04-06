# Confluence Crawler & Data Source Pipeline

Confluence 위키에서 지식을 크롤링하고 자동으로 인제스천하는 파이프라인.

## 개요

```
데이터 소스 트리거 (Dashboard/API)
  ↓
PaddleOCR EC2 자동 시작 (stopped → running)
  ↓
Confluence REST API 크롤링 (BFS 병렬)
  ↓
CrawlResultConnector → RawDocument 변환
  ↓
IngestionPipeline (chunk → embed → store)
  ↓
KB 자동 등록 + 카운트 업데이트
  ↓
PaddleOCR EC2 자동 정지 (비용 절약)
```

## 아키텍처

### Confluence 크롤러 패키지 (`src/connectors/confluence/`)

오레오 에코시스템의 `confluence_full_crawler.py` (4,671줄)에서 추출하여 8개 모듈로 분리:

| 모듈 | 역할 |
|------|------|
| `__init__.py` | `crawl_space()` 진입점, 패키지 facade |
| `config.py` | `CrawlerConfig` 데이터클래스, 환경변수 로딩 |
| `models.py` | 13개 데이터클래스 (`FullPageContent`, `CrawlSpaceResult` 등) |
| `html_parsers.py` | 8개 HTML 파서 (테이블, 멘션, 매크로, 링크, 섹션 등) |
| `structured_ir.py` | RAG 최적화 Structured IR 생성 |
| `attachment_parser.py` | PDF/Excel/Word/PPT 첨부파일 파싱 + PaddleOCR |
| `client.py` | Confluence REST API 클라이언트 + BFS/DFS 크롤링 |
| `output.py` | JSON/JSONL 출력 |

### 데이터 소스 트리거 (`src/api/routes/data_source_sync.py`)

```
POST /api/v1/admin/data-sources/{source_id}/trigger?sync_mode=full
```

백그라운드 `asyncio.create_task`로 실행. 전체 흐름:
1. `_resolve_page_id()` — 데이터 소스 설정에서 Confluence page_id 추출
2. `_start_ocr_instance()` — PaddleOCR EC2 온디맨드 시작 + health 체크
3. `_run_crawl_pipeline()` — `crawl_space()` 호출 + JSON 출력
4. `_fetch_documents()` — `CrawlResultConnector`로 RawDocument 변환
5. `_run_ingestion()` — `IngestionPipeline`으로 각 문서 인제스천
6. `_ensure_kb_and_update_counts()` — KB registry 자동 등록 + 카운트
7. `_stop_ocr_instance()` — PaddleOCR EC2 정지

## 사용법

### 1. 프론트엔드 (Dashboard)

1. **데이터 소스 관리** 페이지에서 데이터 소스 생성
   - 타입: `crawl_result`
   - KB ID: 원하는 KB 이름
   - 메타데이터에 `root_page_id` 또는 위키 URL 입력
2. **동기화 실행** 버튼 클릭

### 2. API 직접 호출

```bash
# 데이터 소스 생성
curl -X POST http://localhost:8000/api/v1/admin/data-sources \
  -H "Content-Type: application/json" \
  -d '{
    "name": "AX_Role_Wiki",
    "source_type": "crawl_result",
    "kb_id": "AX_Role",
    "metadata": {
      "root_page_id": "373865276",
      "description": "AX챗봇 지식관리 위키"
    }
  }'

# 트리거 (full sync)
curl -X POST "http://localhost:8000/api/v1/admin/data-sources/{source_id}/trigger?sync_mode=full"

# 상태 확인
curl http://localhost:8000/api/v1/admin/data-sources/{source_id}/status
```

### 3. CLI

```bash
# 직접 크롤링 (인제스천 없이 JSONL 출력만)
CONFLUENCE_PAT=your_pat uv run python scripts/confluence_crawler.py \
  --page-id 373865276 --full --max-concurrent 3

# 특정 소스
uv run python scripts/confluence_crawler.py --source faq --full

# 샘플 (10페이지)
uv run python scripts/confluence_crawler.py --page-id 373865276 --sample 10

# 전체 소스 순차
uv run python scripts/confluence_crawler.py --all-sources --full
```

## 크롤링 모드

### BFS (기본)

`asyncio.Queue` + N개 워커로 병렬 크롤링. 전체 크롤링 시 사용.

```python
await crawl_space(config, page_id="...", use_bfs=True, max_concurrent=3)
```

- 장점: 깊이에 관계없이 균일한 병렬 처리
- 313페이지 기준 ~18초

### DFS (resume/sample)

재귀 탐색. resume 모드나 샘플 크롤링 시 자동 선택.

```python
await crawl_space(config, page_id="...", resume=True)  # 자동 DFS
await crawl_space(config, page_id="...", max_pages=10)  # 자동 DFS
```

## PaddleOCR EC2 관리

첨부파일 OCR이 필요할 때만 EC2 인스턴스를 자동 시작/정지하여 비용 절약.

### 환경변수

```bash
PADDLEOCR_INSTANCE_ID=i-09c72e77a614f1ea2   # EC2 인스턴스 ID
PADDLEOCR_API_URL=http://3.36.127.165:8866   # fallback URL
```

### 라이프사이클

```
트리거 → EC2 start → health 대기 (최대 3분) → 크롤링+인제스천 → EC2 stop
```

- 중지 상태 비용: EBS만 (~$3/월)
- 구동 시 비용: c5.xlarge $0.17/hr
- Public IP는 stop/start 시 변경됨 → 코드에서 매번 `describe-instances`로 조회

### Docker 이미지

```bash
# ECR에 푸시됨
863518448167.dkr.ecr.ap-northeast-2.amazonaws.com/knowledge-local/paddleocr:latest

# 로컬 빌드
docker build -t paddleocr-api -f docker/paddleocr/Dockerfile docker/paddleocr/
```

- 커스텀 PaddlePaddle wheel (MKLDNN=OFF, 소스 빌드)
- PP-OCRv5 한국어 모델 사전 탑재
- CV Pipeline (shape/arrow detection) 포함
- 엔드포인트: `POST /ocr`, `POST /analyze`, `GET /health`

## AWS 인프라

| 서비스 | 인스턴스 | 용도 |
|--------|---------|------|
| EC2 c5.4xlarge | 54.180.231.139 | TEI 임베딩(:8080) + 리랭커(:8081) |
| EC2 c5.xlarge | (동적 IP) | PaddleOCR API(:8866) — 온디맨드 |
| SageMaker | oreo-exaone-dev | EXAONE LLM |

## 체크포인트 & 재개

대규모 크롤링(수천 페이지) 시 중단 후 재개 지원:

```bash
# 크롤링 중 Ctrl+C → 체크포인트 자동 저장
# 재개
uv run python scripts/confluence_crawler.py --source itops --resume

# 체크포인트 삭제 후 처음부터
uv run python scripts/confluence_crawler.py --source itops --fresh-full
```

- 체크포인트: `~/.knowledge-local/crawl/checkpoint.json`
- 증분 JSONL: `~/.knowledge-local/crawl/incremental_{source}.jsonl`
- 10페이지마다 자동 저장

## 환경변수 전체 목록

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `CONFLUENCE_PAT` | (필수) | Confluence Personal Access Token |
| `CONFLUENCE_BASE_URL` | `https://wiki.gsretail.com` | Confluence 서버 URL |
| `CONFLUENCE_VERIFY_SSL` | `false` | SSL 인증서 검증 (사내 프록시) |
| `CONFLUENCE_OUTPUT_DIR` | `~/.knowledge-local/crawl` | 크롤 출력 디렉토리 |
| `CRAWL_OUTPUT_DIR` | `~/.knowledge-local/crawl` | 트리거 시 출력 디렉토리 |
| `PADDLEOCR_API_URL` | - | PaddleOCR API 서버 URL |
| `PADDLEOCR_INSTANCE_ID` | - | PaddleOCR EC2 인스턴스 ID |

## 테스트

```bash
# 크롤러 패키지 테스트 (310개, ~1초)
uv run pytest tests/unit/test_confluence_package.py tests/unit/test_confluence_client.py -v --no-cov

# 데이터 소스 sync 테스트 (48개)
uv run pytest tests/unit/test_data_source_sync.py -v --no-cov
```
