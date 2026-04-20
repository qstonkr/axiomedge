"""Agent Trace viewer — Agentic RAG 실행 trace 시각화.

차별화 5축이 trace 단계별로 어떻게 활용됐는지 펼쳐 보여줌.
나중에 React UI 포팅 시 동일 데이터 모델 (AgentTrace JSON) 그대로 사용 가능.
"""

# pyright: reportMissingImports=false  # streamlit + sibling services packages

from __future__ import annotations

from typing import Any

import streamlit as st

from components.sidebar import render_sidebar
from services.api import agentic_ask, get_agent_trace, list_agent_traces

st.set_page_config(page_title="Agent Trace", page_icon="🤖", layout="wide")

from components.deprecate_banner import deprecated_for

deprecated_for("/admin/traces", "Agent Trace")
render_sidebar()

st.title("🤖 Agent Trace Viewer")
st.caption("Agentic RAG 실행 단계별 시각화 — plan → execute → reflect → retry.")


# ---------------------------------------------------------------------------
# Trace renderer — Section 2 의 "Trace 불러오기" 버튼이 호출하므로 정의가
# 호출보다 위에 있어야 한다 (Streamlit page 는 top-down 실행, forward
# reference 시 NameError).
# ---------------------------------------------------------------------------
def _render_trace(trace: dict[str, Any]) -> None:
    """전체 trace 단계별 시각화."""
    st.markdown(f"#### 질문: {trace.get('query', '?')}")
    st.markdown(f"**최종 답변**: {trace.get('final_answer') or '_(없음)_'}")

    # Top metrics row
    cols = st.columns(5)
    cols[0].metric("Provider", trace.get("llm_provider", "?"))
    cols[1].metric("Iterations", len(trace.get("iterations") or []))
    total_steps = sum(len(it) for it in (trace.get("iterations") or []))
    cols[2].metric("Total Steps", total_steps)
    tokens = trace.get("tokens") or {}
    cols[3].metric("Tokens", tokens.get("prompt_tokens", 0) + tokens.get("completion_tokens", 0))
    cols[4].metric("Cost", f"${tokens.get('estimated_cost_usd', 0):.4f}")

    # Initial plan
    st.markdown("### 🧠 초기 Plan")
    plan = trace.get("plan") or {}
    st.markdown(f"- **Sub-queries**: {plan.get('sub_queries') or []}")
    st.markdown(f"- **Estimated complexity**: {plan.get('estimated_complexity', '?')} / 5")
    st.markdown(f"- **Rationale**: {plan.get('rationale', '_(없음)_')}")

    # Iterations
    iterations = trace.get("iterations") or []
    critiques = trace.get("critiques") or []
    for i, (steps, critique) in enumerate(zip(iterations, critiques)):
        with st.expander(
            f"📍 Iteration {i + 1}  ·  steps={len(steps)}  ·  "
            f"sufficient={'✅' if critique.get('is_sufficient') else '❌'}  ·  "
            f"confidence={critique.get('confidence', 0):.2f}",
            expanded=(i == 0),
        ):
            for j, step in enumerate(steps):
                result = step.get("result") or {}
                ok = "✅" if result.get("success") else "❌"
                st.markdown(
                    f"**Step {j + 1}** {ok} `{step.get('tool')}`  ·  "
                    f"{step.get('duration_ms', 0):.0f}ms",
                )
                st.markdown(f"- **Rationale**: {step.get('rationale', '')}")
                with st.popover("Args"):
                    st.json(step.get("args") or {})
                with st.popover("Result"):
                    st.json(result.get("data"))
                    if result.get("metadata"):
                        st.caption("Metadata:")
                        st.json(result.get("metadata"))
                    if result.get("error"):
                        st.error(result.get("error"))

            st.markdown("**Critique**")
            st.markdown(f"- next_action: `{critique.get('next_action')}`")
            if critique.get("missing"):
                st.markdown(f"- missing: {critique.get('missing')}")
            if critique.get("revised_query"):
                st.markdown(f"- revised_query: {critique.get('revised_query')}")
            if critique.get("rationale"):
                st.caption(critique.get("rationale"))


# ---------------------------------------------------------------------------
# Section 1 — Run new agent query
# ---------------------------------------------------------------------------

with st.expander("새 Agentic 질문 실행", expanded=True):
    col_q, col_btn = st.columns([5, 1])
    with col_q:
        query = st.text_input(
            "질문", value="", placeholder="예: 신촌점 차주 매장 점검 일정 알려줘",
            key="agent_query",
        )
    with col_btn:
        st.write("")
        st.write("")
        ask_clicked = st.button("질문하기", type="primary", use_container_width=True)

    kb_filter = st.text_input(
        "KB 필터 (콤마 구분, 비우면 전체)",
        value="", key="agent_kb_filter",
    )

    if ask_clicked and query.strip():
        kb_ids = [k.strip() for k in kb_filter.split(",") if k.strip()] or None
        with st.spinner("Agent 실행 중... (plan → execute → reflect)"):
            try:
                result = agentic_ask(query.strip(), kb_ids=kb_ids)
                if result.get("_api_failed"):
                    st.error(f"실행 실패: {result.get('error', 'unknown')}")
                else:
                    st.session_state["last_trace_id"] = result.get("trace_id")
                    st.success(f"trace_id: {result.get('trace_id')}")
                    cols = st.columns(4)
                    cols[0].metric("Provider", result.get("llm_provider", "?"))
                    cols[1].metric("Iterations", result.get("iteration_count", 0))
                    cols[2].metric("Steps", result.get("total_steps_executed", 0))
                    cols[3].metric(
                        "Duration",
                        f"{result.get('total_duration_ms', 0) / 1000:.1f}s",
                    )
                    cols2 = st.columns(2)
                    cols2[0].metric(
                        "Confidence", f"{result.get('confidence', 0) * 100:.0f}%",
                    )
                    cols2[1].metric(
                        "Cost", f"${result.get('estimated_cost_usd', 0):.4f}",
                    )
                    st.markdown("### 답변")
                    st.markdown(result.get("answer") or "_(빈 답변)_")
            except Exception as e:  # noqa: BLE001
                st.error(f"오류: {e}")

# ---------------------------------------------------------------------------
# Section 2 — Inspect trace
# ---------------------------------------------------------------------------

st.markdown("---")
st.markdown("### 🔍 Trace 상세 조회")

trace_id_input = st.text_input(
    "trace_id 입력 (위 답변의 trace_id 자동 채움)",
    value=st.session_state.get("last_trace_id", ""),
    key="trace_id_input",
)

if st.button("Trace 불러오기", key="load_trace_btn") and trace_id_input.strip():
    try:
        trace = get_agent_trace(trace_id_input.strip())
        if trace.get("_api_failed"):
            st.error(f"조회 실패: {trace.get('error')}")
        else:
            _render_trace(trace)
    except Exception as e:  # noqa: BLE001
        st.error(f"오류: {e}")

# ---------------------------------------------------------------------------
# Section 3 — Recent traces
# ---------------------------------------------------------------------------

st.markdown("---")
st.markdown("### 📜 최근 Trace 목록")

if st.button("새로고침", key="refresh_traces"):
    st.session_state.pop("_traces_cache", None)

if "_traces_cache" not in st.session_state:
    try:
        st.session_state["_traces_cache"] = list_agent_traces(limit=20)
    except Exception as e:  # noqa: BLE001
        st.session_state["_traces_cache"] = {"_api_failed": True, "error": str(e)}

cache = st.session_state.get("_traces_cache", {})
if cache.get("_api_failed"):
    st.warning(f"목록 로드 실패: {cache.get('error')}")
else:
    traces = cache.get("traces", [])
    if not traces:
        st.info("아직 실행된 trace 가 없습니다.")
    else:
        for t in traces:
            with st.expander(
                f"🔹 {t.get('query', '?')[:80]}  ·  "
                f"{t.get('llm_provider', '')}  ·  "
                f"{t.get('iteration_count', 0)} iter  ·  "
                f"{t.get('total_duration_ms', 0) / 1000:.1f}s",
            ):
                st.code(t.get("trace_id", ""), language="text")
                st.markdown(f"**미리보기**: {t.get('answer_preview', '')}")
                if st.button("상세보기", key=f"view_{t.get('trace_id')}"):
                    st.session_state["trace_id_input"] = t.get("trace_id", "")
                    st.rerun()


# (_render_trace 정의는 page top 으로 이동 — Section 2 forward reference 회피)
