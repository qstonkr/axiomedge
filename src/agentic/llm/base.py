"""Base AgentLLM — JSON-mode driver wrapping any ``LLMClient`` (Protocol).

작은 LLM (Ollama / Edge) 도 JSON 응답 가능 → lowest common denominator.
function-calling 가능한 provider (OpenAI/Anthropic) 는 자체 adapter 에서 override.

JSON parsing 은 ``json_repair`` (이미 의존성) 으로 깨진 응답도 복구 시도.
"""

# pyright: reportGeneralTypeIssues=false

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from src.agentic.protocols import (
    AgentLLM,
    AgentStep,
    Critique,
    Plan,
    ToolResult,
    ToolSpec,
)
from src.nlp.llm.types import LLMClient

logger = logging.getLogger(__name__)


_PLAN_SYSTEM_PROMPT = """당신은 한국어 지식검색 RAG 시스템의 plan agent 입니다.
사용자 질문을 분석해 사용할 도구의 호출 계획 (Plan) 을 JSON 으로 출력합니다.

규칙:
1. 가능한 한 적은 단계로 답할 수 있도록 plan 합니다 (단순 사실 = 1 단계).
2. 한국어 entity (사람·매장·시스템) 가 명시적으로 등장하면 graph_query 우선.
3. 일반 의미 검색은 qdrant_search.
4. 모호한 도메인 용어는 glossary_lookup.
5. 시점 표현 ('차주', '지난 주') 은 time_resolver 로 정규화.
6. 출력은 반드시 valid JSON — 다른 텍스트 금지.
"""

_REFLECT_SYSTEM_PROMPT = """당신은 한국어 RAG 시스템의 reflection agent 입니다.
주어진 evidence + answer 가 사용자 질문에 충분한지 판단해 JSON 출력합니다.

next_action:
- "answer": evidence 가 충분 → answer 채택
- "retry_with_query": query 변형으로 재시도 (revised_query 포함)
- "try_different_kb": 다른 KB 시도 (revised_kb_ids 포함)
- "give_up": 충분한 정보 없음

출력은 반드시 valid JSON.
"""


class JsonAgentLLM:
    """기본 AgentLLM — LLMClient 를 JSON-mode 로 drive.

    구현체는 단순히 LLMClient 와 provider_name 만 전달하면 됨.
    function calling 활용하려면 plan/reflect 를 override.
    """

    def __init__(self, client: LLMClient, provider_name: str) -> None:
        self._client = client
        self._provider_name = provider_name

    @property
    def provider_name(self) -> str:
        return self._provider_name

    async def plan(
        self, query: str, available_tools: list[ToolSpec], context: str = "",
    ) -> Plan:
        prompt = self._build_plan_prompt(query, available_tools, context)
        raw = await self._client.generate(
            prompt, system_prompt=_PLAN_SYSTEM_PROMPT,
            max_tokens=2048, temperature=0.1,
        )
        return self._parse_plan(raw, query, available_tools)

    async def reflect(
        self, query: str, evidence: list[ToolResult], answer: str | None,
    ) -> Critique:
        prompt = self._build_reflect_prompt(query, evidence, answer)
        raw = await self._client.generate(
            prompt, system_prompt=_REFLECT_SYSTEM_PROMPT,
            max_tokens=512, temperature=0.0,
        )
        return self._parse_critique(raw)

    async def synthesize(self, query: str, evidence: list[ToolResult]) -> str:
        # Reuse existing RAG response generator path — context = evidence 변환
        context = []
        for ev in evidence:
            if not ev.success or ev.data is None:
                continue
            content = ev.data if isinstance(ev.data, str) else json.dumps(
                ev.data, ensure_ascii=False, default=str,
            )
            context.append({
                "content": content[:2000],
                "metadata": ev.metadata,
            })
        return await self._client.generate_response(query=query, context=context)

    # =========================================================================
    # Prompt builders
    # =========================================================================

    @staticmethod
    def _build_plan_prompt(
        query: str, available_tools: list[ToolSpec], context: str = "",
    ) -> str:
        tool_lines = []
        for spec in available_tools:
            args_summary = json.dumps(spec.args_schema, ensure_ascii=False)
            tool_lines.append(f"- {spec.name}: {spec.description}\n  args_schema: {args_summary}")
        tool_catalog = "\n".join(tool_lines)
        ctx_block = f"\n[추가 컨텍스트]\n{context}\n" if context else ""
        return f"""[가용 도구]
{tool_catalog}
{ctx_block}
[사용자 질문]
{query}

[출력 — JSON]
{{
  "sub_queries": ["..."],
  "estimated_complexity": 1-5,
  "rationale": "왜 이 plan 인지 한 줄",
  "steps": [
    {{"tool": "qdrant_search", "args": {{"query": "...", "top_k": 5}}, "rationale": "..."}}
  ]
}}"""

    @staticmethod
    def _build_reflect_prompt(
        query: str, evidence: list[ToolResult], answer: str | None,
    ) -> str:
        ev_lines = []
        for i, ev in enumerate(evidence):
            status = "OK" if ev.success else f"FAIL: {ev.error}"
            preview = json.dumps(ev.data, ensure_ascii=False, default=str)[:500] if ev.data else ""
            ev_lines.append(f"[Evidence {i}] ({status}) {preview}")
        ev_block = "\n".join(ev_lines) if ev_lines else "(없음)"
        ans_block = answer or "(아직 없음)"
        return f"""[사용자 질문]
{query}

[수집한 evidence]
{ev_block}

[현재 답변]
{ans_block}

[출력 — JSON]
{{
  "is_sufficient": true | false,
  "confidence": 0.0-1.0,
  "next_action": "answer" | "retry_with_query" | "try_different_kb" | "give_up",
  "missing": ["..."],
  "revised_query": "..." | null,
  "revised_kb_ids": ["..."] | null,
  "rationale": "한 줄 설명"
}}"""

    # =========================================================================
    # Parsers
    # =========================================================================

    @staticmethod
    def _parse_json(raw: str) -> dict[str, Any]:
        """JSON 파싱 — 깨진 형식은 json_repair 로 복구 시도."""
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            try:
                from json_repair import repair_json
                repaired = repair_json(raw)
                if isinstance(repaired, str):
                    return json.loads(repaired)
                if isinstance(repaired, dict):
                    return repaired
            except (ImportError, json.JSONDecodeError, ValueError) as e:
                logger.warning("JSON repair failed: %s", e)
            return {}

    @staticmethod
    def _parse_plan(raw: str, query: str, available_tools: list[ToolSpec]) -> Plan:
        data = JsonAgentLLM._parse_json(raw)
        valid_tool_names = {t.name for t in available_tools}
        steps_data = data.get("steps") or []
        steps: list[AgentStep] = []
        for i, sd in enumerate(steps_data):
            if not isinstance(sd, dict):
                continue
            tool_name = sd.get("tool", "")
            if tool_name not in valid_tool_names:
                logger.warning("planner produced unknown tool %r — dropping", tool_name)
                continue
            steps.append(AgentStep(
                step_id=str(uuid.uuid4()),
                plan_index=i,
                tool=tool_name,
                args=sd.get("args") or {},
                rationale=sd.get("rationale", ""),
            ))
        sub_queries = data.get("sub_queries") or [query]
        if not isinstance(sub_queries, list):
            sub_queries = [query]
        complexity_raw = data.get("estimated_complexity", 1)
        try:
            complexity = max(1, min(5, int(complexity_raw)))
        except (TypeError, ValueError):
            complexity = 1
        return Plan(
            query=query,
            sub_queries=[str(q) for q in sub_queries][:5],
            steps=steps,
            estimated_complexity=complexity,
            rationale=str(data.get("rationale", "")),
        )

    @staticmethod
    def _parse_critique(raw: str) -> Critique:
        data = JsonAgentLLM._parse_json(raw)
        next_action = str(data.get("next_action", "answer"))
        if next_action not in {"answer", "retry_with_query", "try_different_kb", "give_up"}:
            next_action = "answer"
        confidence_raw = data.get("confidence", 0.5)
        try:
            confidence = max(0.0, min(1.0, float(confidence_raw)))
        except (TypeError, ValueError):
            confidence = 0.5
        return Critique(
            is_sufficient=bool(data.get("is_sufficient", False)),
            confidence=confidence,
            next_action=next_action,
            missing=[str(m) for m in (data.get("missing") or [])][:5],
            revised_query=data.get("revised_query") or None,
            revised_kb_ids=data.get("revised_kb_ids") or None,
            rationale=str(data.get("rationale", "")),
        )


# Runtime check — JsonAgentLLM 가 AgentLLM Protocol 만족함을 명시
_check_protocol: AgentLLM
