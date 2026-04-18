# ADR 0001 — Agentic RAG 아키텍처

- **Status**: Accepted
- **Date**: 2026-04-18
- **Decision-makers**: axiomedge maintainer

## Context

현재 `/api/v1/search/hub` 는 9단계 파이프라인이지만 **단일 패스 retrieve-then-answer**.
LLM 이 self-reflect 하거나 도구를 동적으로 호출하지 않음. 시중 Agentic RAG (LangGraph,
LlamaIndex Agent, OpenAI Assistants) 는 vector-only + English-tuned — axiomedge 의
한국어 GraphRAG / Edge LLM / CRAG / Tiered response 자산을 활용 못 함.

## Decision

**axiomedge 자산을 도구화한 Agentic 시스템 구현**.

### 핵심 설계 원칙

1. **Protocol-based**: `AgentLLM`, `Tool` 추상화 — 구현체 swappable.
2. **Provider-agnostic LLM**: 단일 `LLM_PROVIDER` env (sagemaker/ollama/openai/anthropic/edge).
   GraphRAG 의 `GRAPHRAG_USE_SAGEMAKER` legacy flag 도 동일 path 로 통합.
3. **JSON-mode lowest common denominator**: function calling 미지원 LLM 도 동일 인터페이스
   (json_repair 로 fallback). function calling 가능한 provider 는 자동 활용 (성능 옵션).
4. **5개 axiomedge 자산을 도구로 노출**: Korean NLP / GraphRAG / Edge LLM /
   CRAG retry / OCR re-search.
5. **회귀 위험 0**: 별도 `/api/v1/agentic/ask` endpoint — 기존 `/search/hub` 무변경.
6. **Cost guard**: max_steps=5, max_iterations=3, budget_usd=0.10 (모두 env override).

### 데이터 모델 (immutable, JSON-serializable)

```
AgentTrace
├── plan: Plan (query, sub_queries, steps, complexity)
├── iterations: list[list[AgentStep]]  # reflection 으로 N번 plan→execute
├── critiques: list[Critique]
├── final_answer: str
├── tokens: TokenUsage
└── llm_provider: str
```

→ Streamlit Trace viewer 가 이 model 그대로 렌더 (나중에 React 포팅 시 재활용).

### Agent loop 의사 코드

```python
plan = await llm.plan(query, available_tools=registry.specs())
for iteration in range(max_iterations):
    results = []
    for step in plan.steps:
        if cost_guard.exceeded(): break
        result = await registry.get(step.tool).execute(step.args, state)
        results.append(result)
    answer = await llm.synthesize(query, results)
    critique = await llm.reflect(query, results, answer)
    if critique.is_sufficient: return answer
    if critique.next_action == "give_up": return None
    plan = await llm.plan(critique.revised_query or query)
```

## Differentiation 5축

| # | 자산 | Agent 화 |
|---|---|---|
| 1 | Korean NLP | `planner.py` — KiwiPy morpheme 분해 → sub-query LLM 호출 |
| 2 | GraphRAG | `tools/graph_query.py` + planner routing (entity 패턴 감지) |
| 3 | Edge LLM | `routing.py` — complexity ≤2 → edge, >2 → HQ |
| 4 | CRAG retry | `reflection.py` — confidence low → revised_query 재시도 |
| 5 | OCR re-search | `tools/re_ocr.py` — confidence < 0.7 → 다른 PaddleOCR 설정 재처리 |

## Alternatives 검토

### A. LangGraph 도입
- Pros: 검증된 라이브러리, 풍부한 예제.
- Cons: English-tuned, axiomedge 의 한국어 / GraphRAG / Edge 통합 어려움. dep 폭증.
- **거부** — 우리 차별화 자산을 일급 시민으로 다루는 구조 필요.

### B. 기존 `/search/hub` 에 mode=agentic 추가
- Pros: 단일 endpoint.
- Cons: 회귀 위험 매우 높음 (현 핵심 path), 코드 복잡도 폭증.
- **거부** — 신규 endpoint 분리 (`/api/v1/agentic/ask`).

### C. Provider-specific function calling 활용
- Pros: OpenAI / Anthropic 의 native tool use 가 더 정확.
- Cons: Ollama / SageMaker EXAONE 미지원. lock-in.
- **부분 채택** — JSON-mode 가 baseline. function calling 가능한 provider 는 자동 활용
  (구현체 내부 결정), 외부 인터페이스는 동일.

## Consequences

### Positive

- LLM provider swap 자유 (sagemaker → ollama → openai 등 env 한 줄)
- 차별화 5축 모두 일급 시민 (도구로 명시적 노출)
- 기존 RAG 회귀 위험 0
- Streamlit Trace viz 데이터 모델 = 미래 React UI 데이터 모델

### Negative

- 새 코드 ~25 파일 (src/agentic/* + tests + docs) — 유지보수 부담
- LLM 호출 횟수 증가 (plan + execute + reflect + synthesize) → 비용 증가
  → cost_guard 로 budget 강제
- Ollama 같은 작은 LLM 은 JSON 형식 깨질 수 있음 → json_repair 의존

### Neutral

- GraphRAG extractor 의 `GRAPHRAG_USE_SAGEMAKER` flag deprecation — backward compat 유지
- 향후 stage별 다른 LLM 필요 시 `<STAGE>_LLM_PROVIDER` 패턴 추가 (지금은 도입 X)

## References

- Plan: `/Users/jeongbeom.kim/.claude/plans/proud-prancing-blossom.md`
- 기존 LLM provider: `src/core/providers/llm.py`
- 기존 GraphRAG: `src/search/graph_expander.py`, `src/pipelines/graphrag/extractor.py`
- 기존 CRAG: `src/search/crag_evaluator.py`
- 기존 Tiered: `src/search/tiered_response.py`
- 기존 Edge LLM: `src/edge/server.py`
