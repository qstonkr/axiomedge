"""Agentic RAG core protocols + immutable data model.

이 모듈은 어떤 third-party 의존성도 없도록 유지 — Agent loop 의 SSOT.
LLM/Tool 구현체는 이 Protocol 만족하면 swappable.

데이터 모델은 모두 ``@dataclass(frozen=True)`` — Streamlit Trace viz 가
JSON serialize 해서 그대로 쓸 수 있도록 설계.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


# =============================================================================
# Tool 추상화
# =============================================================================


@dataclass(frozen=True)
class ToolSpec:
    """LLM planner 에 전달되는 tool 메타데이터.

    description 과 args_schema 가 LLM prompt 의 도구 카탈로그를 구성.
    """

    name: str
    description: str
    args_schema: dict[str, Any]  # JSON schema (subset)


@dataclass(frozen=True)
class ToolResult:
    """Tool 한 번 실행 결과.

    metadata 에 token_cost, sources, confidence 등 부가정보를 담아
    cost_guard / reflection 이 활용.
    """

    success: bool
    data: Any
    metadata: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


class Tool(ABC):
    """모든 도구의 공통 ABC.

    구현체는 ``name``, ``description``, ``args_schema`` 를 클래스 속성으로
    선언하고 ``execute`` 만 구현. ``state`` 는 FastAPI app state dict (DI).
    """

    name: str = ""
    description: str = ""
    args_schema: dict[str, Any] = {}

    @abstractmethod
    async def execute(self, args: dict[str, Any], state: dict[str, Any]) -> ToolResult:
        """도구 한 번 실행.

        Args:
            args: LLM planner 가 채운 인자 (args_schema 검증 후 호출).
            state: FastAPI app state — qdrant_search/graph_repo/embedder 등 가용.

        Returns:
            ToolResult — success=False 시 error 필드에 사유.
        """

    def spec(self) -> ToolSpec:
        """planner 가 활용할 메타데이터 반환."""
        return ToolSpec(
            name=self.name, description=self.description, args_schema=self.args_schema,
        )


# =============================================================================
# Plan / Step / Critique
# =============================================================================


@dataclass(frozen=True)
class AgentStep:
    """단일 plan step — tool 호출 1회 + 결과.

    ``result`` 와 ``duration_ms`` 는 execution 후 채워짐 (일반적으로
    immutable + ``replace`` 패턴).
    """

    step_id: str
    plan_index: int
    tool: str
    args: dict[str, Any]
    rationale: str
    result: ToolResult | None = None
    duration_ms: float | None = None
    error: str | None = None


@dataclass(frozen=True)
class Plan:
    """LLM 이 생성한 실행 plan.

    sub_queries: Korean NLP 로 분해된 하위 질문 (planner 가 생성).
    estimated_complexity: 1-5 (routing 에 활용 — 단순=1, 복합 multi-hop=5).
    """

    query: str
    sub_queries: list[str]
    steps: list[AgentStep]
    estimated_complexity: int
    rationale: str = ""  # plan 전체에 대한 LLM 의 reasoning

    @property
    def step_count(self) -> int:
        return len(self.steps)


@dataclass(frozen=True)
class Critique:
    """Reflection 결과 — answer 충분/재시도 결정.

    next_action:
      - "answer": critique 통과, answer 사용
      - "retry_with_query": revised_query 로 재 plan
      - "try_different_kb": 같은 query 로 다른 KB 재시도
      - "give_up": 재시도 한계 도달
    """

    is_sufficient: bool
    confidence: float  # 0.0 ~ 1.0
    next_action: str
    missing: list[str] = field(default_factory=list)
    revised_query: str | None = None
    revised_kb_ids: list[str] | None = None
    rationale: str = ""


# =============================================================================
# 전체 실행 trace
# =============================================================================


@dataclass(frozen=True)
class TokenUsage:
    """LLM 토큰 사용 누적."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    estimated_cost_usd: float = 0.0


@dataclass(frozen=True)
class AgentTrace:
    """Agent 실행 전체 trace — Streamlit viz / API response 의 단일 source.

    각 iteration 은 plan 한 번 + 그 plan 의 steps + critique 한 개.
    """

    trace_id: str
    query: str
    plan: Plan  # 첫 plan (이후 retry plan 들은 iterations 안에)
    iterations: list[list[AgentStep]] = field(default_factory=list)
    critiques: list[Critique] = field(default_factory=list)
    final_answer: str = ""
    total_duration_ms: float = 0.0
    tokens: TokenUsage = field(default_factory=TokenUsage)
    llm_provider: str = ""

    @property
    def iteration_count(self) -> int:
        return len(self.iterations)

    @property
    def total_steps_executed(self) -> int:
        return sum(len(it) for it in self.iterations)


# =============================================================================
# AgentLLM Protocol (provider-agnostic)
# =============================================================================


@runtime_checkable
class AgentLLM(Protocol):
    """JSON-mode 우선 — 모든 LLM provider 와 호환되는 인터페이스.

    구현체:
      - OllamaAgentLLM, SageMakerAgentLLM (기존 클라이언트 wrap)
      - OpenAIAgentLLM, AnthropicAgentLLM (function calling 자동 활용)
      - EdgeAgentLLM (src/edge HTTP /ask)

    method 들은 모두 async — LLM 호출은 항상 I/O bound.
    plan/reflect 는 JSON 파싱 책임을 구현체에 (json_repair fallback 포함).
    """

    async def plan(
        self,
        query: str,
        available_tools: list[ToolSpec],
        context: str = "",
    ) -> Plan:
        """질문을 분석해 실행 plan 생성."""
        ...

    async def reflect(
        self,
        query: str,
        evidence: list[ToolResult],
        answer: str | None,
    ) -> Critique:
        """수집한 evidence + 답변을 평가, 충분/재시도 결정."""
        ...

    async def synthesize(
        self,
        query: str,
        evidence: list[ToolResult],
    ) -> str:
        """최종 답변 생성 (citation 포함)."""
        ...

    @property
    def provider_name(self) -> str:
        """trace 에 기록될 provider 이름 (예: 'sagemaker', 'ollama')."""
        ...
