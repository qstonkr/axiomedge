"""인제스천 게이트 -- 문서 인제스천 품질/보안 검증 현황

게이트 통계, 거부/보류 문서 목록.

Created: 2026-03-25
"""

import streamlit as st

st.set_page_config(page_title="인제스천 게이트", page_icon="🚦", layout="wide")


from components.sidebar import render_sidebar
from services import api_client
from services.api_client import api_failed

render_sidebar()

st.title("🚦 인제스천 게이트")
st.info(
    "문서 인제스천 전 품질/보안 검증 게이트입니다. "
    "통과하지 못한 문서는 거부(REJECT) 또는 보류(HOLD) 처리됩니다."
)

tab_stats, tab_blocked = st.tabs(["게이트 현황", "거부/보류 문서"])


# =============================================================================
# 탭 1: 게이트 현황
# =============================================================================
with tab_stats:
    result = api_client._request("GET", "/api/v1/admin/pipeline/gates/stats")

    if api_failed(result):
        st.warning("데이터를 불러올 수 없습니다.")
        if st.button("재시도", key="retry_gate_stats"):
            st.cache_data.clear()
            st.rerun()
    else:
        total = result.get("total", result.get("total_checks", 0))
        passed = result.get("passed", result.get("proceed_count", 0))
        held = result.get("held", result.get("hold_count", 0))
        rejected = result.get("rejected", result.get("reject_count", 0))
        quarantined = result.get("quarantined", result.get("quarantine_count", 0))

        # Metric cards
        m1, m2, m3, m4, m5 = st.columns(5)
        with m1:
            st.metric("총 검사 수", f"{total:,}")
        with m2:
            st.metric("통과 (PROCEED)", f"{passed:,}")
        with m3:
            st.metric("보류 (HOLD)", f"{held:,}")
        with m4:
            st.metric("거부 (REJECT)", f"{rejected:,}")
        with m5:
            st.metric("격리 (QUARANTINE)", f"{quarantined:,}")

        # Pass rate
        if total > 0:
            pass_rate = passed / total
            st.progress(min(pass_rate, 1.0), text=f"통과율: {pass_rate:.1%}")
            if pass_rate >= 0.9:
                st.success(f"통과율 양호: {pass_rate:.1%}")
            elif pass_rate >= 0.7:
                st.warning(f"통과율 보통: {pass_rate:.1%}")
            else:
                st.error(f"통과율 낮음: {pass_rate:.1%}")
        else:
            st.info("아직 검사 이력이 없습니다.")

        # Gate-level breakdown
        gates = result.get("gates", result.get("gate_details", []))
        if gates:
            st.markdown("---")
            st.subheader("게이트별 통계")

            for gate in gates:
                gate_id = gate.get("gate_id", gate.get("id", "-"))
                gate_name = gate.get("name", gate.get("gate_name", gate_id))
                gate_total = gate.get("total", 0)
                gate_failed = gate.get("failed", gate.get("fail_count", 0))
                fail_rate = gate.get("fail_rate", (gate_failed / gate_total if gate_total > 0 else 0))

                with st.container(border=True):
                    gcol1, gcol2, gcol3 = st.columns([3, 1, 1])
                    with gcol1:
                        st.markdown(f"**{gate_name}** (`{gate_id}`)")
                    with gcol2:
                        st.metric("검사", f"{gate_total:,}건", label_visibility="collapsed")
                    with gcol3:
                        if fail_rate > 0.1:
                            st.metric("실패율", f"{fail_rate:.1%}", delta="주의", delta_color="inverse")
                        else:
                            st.metric("실패율", f"{fail_rate:.1%}")

    # Help expander
    with st.expander("도움말: 게이트 정책", expanded=False):
        st.markdown(
            """
            | Verdict | 의미 | 조건 |
            |---------|------|------|
            | **PROCEED** | 통과 | 모든 검사 통과 또는 Core WARN만 존재 |
            | **HOLD** | 보류 | Core 검사 1건 실패 (수동 검토 필요) |
            | **REJECT** | 거부 | Core 검사 2건 이상 실패 또는 Hard-reject 해당 |
            | **QUARANTINE** | 격리 | 보안 검사 실패 (IG-06, IG-07) |

            **Hard-reject 게이트**: IG-05(크기 초과), IG-11~14(형식 오류), IG-16/17(인코딩/손상)

            **보안 게이트**: IG-06(민감정보 탐지), IG-07(악성코드 의심)
            """
        )


# =============================================================================
# 탭 2: 거부/보류 문서
# =============================================================================
with tab_blocked:
    blocked_result = api_client._request("GET", "/api/v1/admin/pipeline/gates/blocked")

    if api_failed(blocked_result):
        st.warning("데이터를 불러올 수 없습니다.")
        if st.button("재시도", key="retry_gate_blocked"):
            st.cache_data.clear()
            st.rerun()
    else:
        items = blocked_result.get("items", blocked_result.get("documents", []))

        if items:
            st.caption(f"총 {len(items)}건의 거부/보류 문서")

            VERDICT_BADGES = {
                "REJECT": ":red[REJECT]",
                "HOLD": ":orange[HOLD]",
                "QUARANTINE": ":violet[QUARANTINE]",
            }
            SEVERITY_BADGES = {
                "CRITICAL": ":red[CRITICAL]",
                "HIGH": ":orange[HIGH]",
                "MEDIUM": ":orange[MEDIUM]",
                "LOW": ":green[LOW]",
            }

            # Filter controls
            filter_col1, filter_col2 = st.columns(2)
            with filter_col1:
                verdict_filter = st.multiselect(
                    "Verdict 필터",
                    options=["REJECT", "HOLD", "QUARANTINE"],
                    default=["REJECT", "HOLD", "QUARANTINE"],
                    key="gate_verdict_filter",
                )
            with filter_col2:
                severity_filter = st.multiselect(
                    "Severity 필터",
                    options=["CRITICAL", "HIGH", "MEDIUM", "LOW"],
                    default=["CRITICAL", "HIGH", "MEDIUM", "LOW"],
                    key="gate_severity_filter",
                )

            filtered = [
                item for item in items
                if item.get("verdict", item.get("status", "")).upper() in verdict_filter
                and item.get("severity", "MEDIUM").upper() in severity_filter
            ]

            if filtered:
                for item in filtered:
                    doc_name = item.get("document_name", item.get("name", item.get("filename", "-")))
                    verdict = item.get("verdict", item.get("status", "REJECT")).upper()
                    gate_id = item.get("gate_id", item.get("failed_gate", "-"))
                    severity = item.get("severity", "MEDIUM").upper()
                    reason = item.get("reason", item.get("message", item.get("description", "-")))
                    checked_at = item.get("checked_at", item.get("created_at", ""))

                    verdict_badge = VERDICT_BADGES.get(verdict, verdict)
                    severity_badge = SEVERITY_BADGES.get(severity, severity)

                    with st.container(border=True):
                        bcol1, bcol2 = st.columns([3, 1])
                        with bcol1:
                            st.markdown(f"**{doc_name}**")
                            st.markdown(f"{verdict_badge} | {severity_badge} | Gate: `{gate_id}`")
                        with bcol2:
                            if checked_at:
                                st.caption(checked_at[:16])

                        st.caption(f"사유: {reason}")
            else:
                st.info("선택한 필터 조건에 해당하는 문서가 없습니다.")
        else:
            st.info("거부 또는 보류된 문서가 없습니다.")
