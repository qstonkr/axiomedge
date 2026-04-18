# axiomedge

한국어 문서 기반 GraphRAG 지식 관리 시스템. 문서 인제스트 → 하이브리드 검색 → LLM 답변 생성 → 엣지 모델 distill·배포까지 한 파이프라인으로 다룹니다.

## 주요 기능

- **하이브리드 검색**: Qdrant dense + sparse RRF, identifier/keyword/문서 다양성 부스트, cross-encoder rerank
- **GraphRAG**: Neo4j 기반 엔티티·관계 추출 + 그래프 확장 답변
- **2-stage 인제스트**: parse/OCR → checkpoint(JSONL) → chunk/embed/dedup/store, crash-safe + incremental
- **한국어 NLP**: KiwiPy + KSS, OCR 도메인 사전 + 초성 fuzzy 보정
- **클라우드/로컬 전환**: 임베딩(TEI ↔ Ollama/ONNX), LLM(SageMaker EXAONE ↔ Ollama), OCR(EC2 PaddleOCR ↔ 로컬)
- **엣지 모델 파이프라인**: QA 큐레이션 → LoRA SFT → GGUF 양자화 → S3 배포 → llama.cpp 매장 엣지 서버

## 기술 스택

Python 3.12+ · FastAPI · Streamlit · Qdrant · Neo4j · PostgreSQL · Redis · BGE-M3(TEI) · EXAONE · PaddleOCR · llama.cpp

## 빠른 시작

```bash
# 의존성 설치 + 인프라 기동
make setup
make start

# API + Dashboard
make api          # FastAPI :8000
make dashboard    # Streamlit :8501

# 문서 인제스트
make ingest ARGS="--source ./docs/ --kb-id my-kb"

# 테스트
make test-unit    # 5,000+ tests, ~50s
```

상세는 [`docs/QUICKSTART.md`](docs/QUICKSTART.md) (30분 온보딩) 참고.

## 아키텍처

```
CLI / Dashboard ──▶ FastAPI ──┬─▶ Ingestion (parse→chunk→embed→dedup→store)
                              ├─▶ Search/RAG (classify→expand→search→rerank→generate)
                              ├─▶ GraphRAG (entity/relation extraction + Neo4j)
                              └─▶ Distill (QA curation→LoRA→GGUF→S3) ──▶ Edge Server (llama.cpp)
                                       │         │          │         │         │
                                    Qdrant    Neo4j    PostgreSQL   Redis    TEI/SageMaker
```

## 검색 파이프라인 (9단계)

캐시 → 전처리/확장/분류 → 임베딩 → Qdrant 하이브리드 → identifier·키워드·문서 다양성·날짜 필터 → cross-encoder rerank → composite rerank(엔티티 부스트) → 그래프 확장 → CRAG 평가 → LLM 답변 → 환각 가드.

## 문서

| 문서 | 내용 |
|------|------|
| [QUICKSTART](docs/QUICKSTART.md) | 30분 온보딩 — clone → 첫 검색 |
| [ARCHITECTURE](docs/ARCHITECTURE.md) | 시스템 다이어그램 + 데이터 흐름 |
| [RAG_PIPELINE](docs/RAG_PIPELINE.md) | 검색 9단계 상세 + 가중치 근거 |
| [INGESTION_PIPELINE](docs/INGESTION_PIPELINE.md) | 2-stage 인제스트, checkpoint, incremental |
| [GRAPHRAG](docs/GRAPHRAG.md) | 엔티티·관계 추출 + 그래프 확장 |
| [DISTILL](docs/DISTILL.md) | 엣지 모델 distill 파이프라인 |
| [API](docs/API.md) | 138 endpoints |
| [DEPLOYMENT](docs/DEPLOYMENT.md) | K8s 배포 가이드 |
| [SECURITY](docs/SECURITY.md) | 인증, prompt injection 방어, 데이터 격리 |
| [CONFIGURATION](docs/CONFIGURATION.md) | 환경 변수 + 튜닝 파라미터 |
| [TROUBLESHOOTING](docs/TROUBLESHOOTING.md) | 자주 발생하는 이슈 |

## 기여

[CONTRIBUTING.md](CONTRIBUTING.md) — 개발 setup, 코드 스타일, PR 절차.
