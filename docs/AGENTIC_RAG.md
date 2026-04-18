# Agentic RAG — axiomedge

axiomedge 의 Agentic RAG 시스템 사용 가이드 + 차별화 5축 설명.

## 무엇이 다른가

시중 Agentic RAG (LangGraph, LlamaIndex Agent, OpenAI Assistants) 는 **vector-only +
English-tuned**. axiomedge 는 5개 자산을 도구로 노출 — Agent 가 상황에 맞게 라우팅:

| # | 자산 | 도구화 |
|---|---|---|
| 1 | **Korean NLP** (KiwiPy + KSS) | `planner.py` — morpheme 분해 → 한국어 sub-query |
| 2 | **GraphRAG** (Neo4j entity/relation) | `tools/graph_query.py` — vector vs graph 동적 라우팅 |
| 3 | **Edge ↔ HQ LLM** | `routing.py` — 단순=edge (sub-second) / 복잡=HQ |
| 4 | **CRAG + Tiered** | `reflection.py` — confidence low → revised query 재시도 |
| 5 | **OCR domain dict** | `tools/re_ocr.py` — confidence < 0.7 chunk re-search |

## Quick Start

```bash
# 1. 환경 (LLM provider 한 줄로 swap)
export LLM_PROVIDER=sagemaker  # 또는 ollama / openai / anthropic / edge
export AWS_PROFILE=<your-aws-profile>  # SageMaker 시
# (옵션) Edge LLM routing
export AGENTIC_EDGE_URL=http://store-001.edge.local:8001

# 2. API 시작
make api

# 3. 호출
curl -X POST http://localhost:8000/api/v1/agentic/ask \
  -H "Content-Type: application/json" \
  -d '{"query": "신촌점 차주 매장 점검 일정 알려줘", "kb_ids": ["g-espa"]}'

# 4. Trace 시각화
make dashboard
# → 사이드바 "외부 연동 → 🧠 Agent Trace"
```

## API

### POST /api/v1/agentic/ask

Request:
```json
{
  "query": "신촌점 차주 매장 점검 일정",
  "kb_ids": ["g-espa"]   // optional
}
```

Response:
```json
{
  "trace_id": "uuid",
  "answer": "...",
  "llm_provider": "sagemaker",
  "iteration_count": 1,
  "total_steps_executed": 3,
  "total_duration_ms": 4521.3,
  "estimated_cost_usd": 0.0042,
  "confidence": 0.85
}
```

### GET /api/v1/agentic/traces/{id}

단계별 상세 trace JSON — Streamlit Trace viewer 가 호출.

### GET /api/v1/agentic/traces?limit=20

최근 trace 목록 (history).

## Architecture

```
src/agentic/
├── protocols.py         # AgentLLM, Tool, Plan, Critique, AgentTrace
├── agent.py             # plan → execute → synthesize → reflect → (retry)
├── planner.py           # Korean enrichment + tiered planning
├── reflection.py        # (in agent.py) — Critique 기반 재시도
├── routing.py           # Edge ↔ HQ complexity routing
├── cost_guard.py        # max_steps / iterations / budget / timeout
├── tools/               # 6개 도구 (qdrant, graph, glossary, time, kb_list, re_ocr)
└── llm/                 # AgentLLM adapters (ollama/sagemaker/openai/anthropic/edge)
```

## 환경 변수

### LLM provider (필수 1개)
- `LLM_PROVIDER` — `sagemaker | ollama | openai | anthropic | edge` (기본 `ollama`)
- `USE_SAGEMAKER_LLM=true` — legacy alias for `LLM_PROVIDER=sagemaker`

### Provider별 자격증명
- `AWS_PROFILE`, `SAGEMAKER_ENDPOINT_NAME` (sagemaker)
- `OLLAMA_BASE_URL`, `OLLAMA_MODEL` (ollama)
- `OPENAI_API_KEY` (openai — Day 12+ 활성)
- `ANTHROPIC_API_KEY` (anthropic — Day 12+ 활성)
- `AGENTIC_EDGE_URL`, `AGENTIC_EDGE_API_KEY` (edge)

### Cost guard (옵션)
- `AGENTIC_MAX_STEPS=5` — iteration 당 max step
- `AGENTIC_MAX_ITERATIONS=3` — reflection 재시도 한계
- `AGENTIC_MAX_TOTAL_STEPS=12` — 누적 step 한계
- `AGENTIC_BUDGET_USD=0.10` — 토큰 비용 한계
- `AGENTIC_TIMEOUT_SECONDS=60` — 전체 timeout

### Edge routing (옵션 — `AGENTIC_EDGE_URL` 가 set 되면 자동 활성)
- `AGENTIC_EDGE_COMPLEXITY_THRESHOLD=2` — 이하 complexity 만 edge

## 평가

```bash
# 1. 기본 sample 5개 query
uv run python scripts/eval_agentic.py

# 2. 자체 query 파일
uv run python scripts/eval_agentic.py --queries-file my_queries.txt --kb g-espa --limit 20

# 3. 결과 (eval/agentic_ab.json)
{
  "naive_latency_p50": 1820,
  "agentic_latency_p50": 4521,
  "agentic_total_cost_usd": 0.012,
  "avg_iterations": 1.2,
  ...
}
```

## 5축 차별화 동작 예시

### 차별화 #1 (Korean NLP)
> "신촌점 김담당 차주 출장 일정"
- KiwiPy: NNP=[신촌점, 김담당], 시점="차주"
- planner context: "graph_query 우선 (신촌점, 김담당), time_resolver 권장 (차주)"

### 차별화 #2 (GraphRAG routing)
> "PBU 와 관련된 시스템 알려줘"
- Plan: graph_query(mode="entities", keywords=["PBU"]) 첫 단계 자동
- vector search 안 거치고 즉시 entity 노드 → 관련 시스템 추출

### 차별화 #3 (Edge routing)
> "오늘 휴무 매장 어디" (complexity 1)
- plan with HQ → estimated_complexity=1 → synthesize with **edge** (sub-second)
- HQ LLM 호출 1회만 (plan + reflect)

### 차별화 #4 (Reflection retry)
- 첫 답변 confidence 0.4 → critique: "정확한 시점 정보 없음, revised_query='신촌점 12월 점검'"
- 두 번째 plan + execute → confidence 0.85 → answer

### 차별화 #5 (OCR re-search)
- qdrant_search 결과에 chunk 5개 — 그 중 2개의 ocr_confidence=0.45
- agent: re_ocr_search 호출 → "low_confidence_count=2, recommendation: 다른 keyword 재시도"
- 재 plan → 다른 query 변형 시도

## Trace viewer (Streamlit)

`make dashboard` → "외부 연동 → 🧠 Agent Trace" 페이지:
1. 새 질문 실행 (메트릭 6개 + 답변 표시)
2. trace_id 입력 → 단계별 펼침 (args/result popover, critique 표시)
3. 최근 trace history (각 trace 펼침)

향후 React UI 포팅 시 동일 trace JSON 모델 그대로 재활용.

## Limitations

- Edge LLM 의 `synthesize` 만 활용 (plan/reflect 는 HQ 우선) — 작은 모델은 JSON 신뢰성 ↓
- OCR re-search 는 detect-only — 실제 re-OCR 통합은 PaddleOCR provider 가 inline 가용한 환경
- 다중 동시 사용자 환경에서 trace cache 는 in-memory (process-local) — production 은 Redis
- `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` 활성은 추후 (현재 stub)

## 관련 문서

- ADR: [`docs/adr/0001-agentic-rag.md`](adr/0001-agentic-rag.md)
- LLM provider: [`src/core/providers/llm.py`](../src/core/providers/llm.py)
- 기존 RAG: [`docs/RAG_PIPELINE.md`](RAG_PIPELINE.md)
- GraphRAG: [`docs/GRAPHRAG.md`](GRAPHRAG.md)
