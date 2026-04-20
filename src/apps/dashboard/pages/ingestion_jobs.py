"""인제스천 작업 관리

IngestionRun 목록, 진행 상태, 수동 트리거, 취소 기능.

Created: 2026-02-20
"""

import streamlit as st

st.set_page_config(page_title="인제스천 작업", page_icon="📥", layout="wide")

from components.deprecate_banner import deprecated_for

deprecated_for("/admin/ingest", "Ingest 작업")



from components.constants import PIPELINE_STEPS, PIPELINE_STEP_LABELS, STEP_STATUS_ICONS
from components.sidebar import hide_default_nav, render_sidebar
from services import api_client
from services.api_client import api_failed

hide_default_nav()
render_sidebar(show_admin=True)

st.title("📥 인제스천 작업")

# pipeline/status는 per-job 진행 바 fallback용으로만 사용 (상단 중복 표시 제거)
pipeline_status = api_client.get_pipeline_status()

# =============================================================================
# 인제스천 실행 목록
# =============================================================================
st.subheader("작업 목록")

# 필터 행
filter_col1, filter_col2 = st.columns(2)
with filter_col1:
    status_filter = st.selectbox(
        "상태 필터",
        options=["전체", "PENDING", "RUNNING", "COMPLETED", "FAILED"],
        key="ingestion_status_filter",
    )
with filter_col2:
    kb_filter_id = st.text_input("KB ID 필터", placeholder="특정 KB만 조회 (선택)", key="ingestion_kb_filter")

filter_status = None if status_filter == "전체" else status_filter
filter_kb = kb_filter_id if kb_filter_id else None

runs_result = api_client.list_ingestion_runs(kb_id=filter_kb, status=filter_status)

if api_failed(runs_result):
    st.error("API 연결 실패. 재시도 해주세요.")
    if st.button("🔄 재시도", key="retry_runs"):
        st.cache_data.clear()
        st.rerun()
    st.stop()

runs = runs_result.get("items", runs_result.get("runs", []))

# ── run_id 기준 중복 제거 (동일 run이 여러 번 나오는 경우 방지) ──
seen_run_ids: set[str] = set()
unique_runs: list[dict] = []
for r in runs:
    rid = r.get("run_id", r.get("id", ""))
    if rid and rid not in seen_run_ids:
        seen_run_ids.add(rid)
        unique_runs.append(r)
    elif not rid:
        unique_runs.append(r)
runs = unique_runs

# ── 상태별 메트릭 ──
col1, col2, col3, col4 = st.columns(4)
all_statuses = [r.get("status", "UNKNOWN") for r in runs]
with col1:
    st.metric("전체", f"{len(runs)}개")
with col2:
    st.metric("실행 중", f"{all_statuses.count('RUNNING')}개")
with col3:
    st.metric("완료", f"{all_statuses.count('COMPLETED')}개")
with col4:
    st.metric("실패", f"{all_statuses.count('FAILED')}개")

st.markdown("---")

# ── 실행 목록 ──
if runs:
    for idx, run in enumerate(runs):
        run_id = run.get("run_id", run.get("id", "-"))
        run_id_short = run_id[:12] if len(run_id) > 12 else run_id
        kb_name = run.get("kb_name", run.get("kb_id", "-"))
        status = run.get("status", "UNKNOWN")
        started_at = run.get("started_at", "-")
        duration = run.get("duration", "-")
        source_type = run.get("source_type", "-")
        current_step = run.get("current_step", "")
        step_progress = run.get("step_progress", {})

        status_badge = {
            "PENDING": "🟡 대기",
            "RUNNING": "🔵 실행 중",
            "COMPLETED": "🟢 완료",
            "FAILED": "🔴 실패",
        }.get(status, f"⚪ {status}")

        with st.container(border=True):
            col_info, col_status, col_action = st.columns([3, 2, 1])

            with col_info:
                st.markdown(f"**{run_id_short}** - {kb_name}")
                st.caption(f"소스: {source_type} | 시작: {started_at} | 소요: {duration}")

            with col_status:
                st.markdown(f"상태: {status_badge}")
                if current_step:
                    step_label = PIPELINE_STEP_LABELS.get(current_step, current_step)
                    st.caption(f"현재 단계: {step_label}")

            with col_action:
                if status == "RUNNING":
                    if st.button("⏹️ 취소", key=f"cancel_{run_id}_{idx}"):
                        result = api_client.cancel_ingestion(run_id)
                        if api_failed(result):
                            st.error("취소 실패")
                        else:
                            st.success("취소 요청 완료")
                            st.cache_data.clear()
                            st.rerun()

            # ── 실행 중인 작업: 진행 정보 ──
            if status == "RUNNING":
                # step_progress가 작업에 직접 포함된 경우 사용
                effective_steps = step_progress
                # 없으면 pipeline/status에서 가져온 전역 데이터 사용 (현재 KB 일치 시)
                if not effective_steps and not api_failed(pipeline_status):
                    pipeline_kb = pipeline_status.get("current_kb", "")
                    if pipeline_kb == run.get("kb_id", ""):
                        effective_steps = pipeline_status.get("steps", {})

                if effective_steps:
                    st.markdown("**파이프라인 진행 상태:**")
                    for step_key, step_label in PIPELINE_STEPS:
                        step_info = effective_steps.get(step_key, {})
                        s_status = step_info.get("status", "pending")
                        s_progress = step_info.get("progress", 0)

                        step_icon = STEP_STATUS_ICONS.get(s_status, "⏸️")

                        col_l, col_r = st.columns([1, 4])
                        with col_l:
                            st.write(f"{step_icon} {step_label}")
                        with col_r:
                            st.progress(min(s_progress / 100, 1.0) if isinstance(s_progress, (int, float)) else 0.0)
                else:
                    # step 데이터 없으면 기본 progress 표시
                    prog = run.get("progress", {})
                    fetched = prog.get("documents_fetched", 0)
                    ingested = prog.get("documents_ingested", 0)
                    chunks = prog.get("chunks_stored", 0)
                    if fetched or ingested or chunks:
                        st.caption(f"문서 수집: {fetched} | 인제스트: {ingested} | 청크 저장: {chunks}")
else:
    st.info("인제스천 실행 이력이 없습니다.")

st.markdown("---")

# =============================================================================
# 수동 인제스천 트리거
# =============================================================================
st.subheader("수동 인제스천 트리거")

with st.form("trigger_ingestion_form"):
    trigger_col1, trigger_col2 = st.columns(2)

    with trigger_col1:
        # KB 선택
        kbs_result = api_client.list_kbs()
        if api_failed(kbs_result):
            trigger_kb_id = st.text_input("KB ID", placeholder="KB ID 직접 입력")
        else:
            kb_items = kbs_result.get("items", kbs_result.get("kbs", []))
            if kb_items:
                kb_opts = {kb.get("name", kb.get("kb_id", "")): kb.get("kb_id", kb.get("id", "")) for kb in kb_items}
                selected_name = st.selectbox("대상 KB", options=list(kb_opts.keys()))
                trigger_kb_id = kb_opts[selected_name]
            else:
                trigger_kb_id = st.text_input("KB ID", placeholder="KB ID 직접 입력")

    with trigger_col2:
        trigger_source_type = st.selectbox(
            "소스 타입",
            options=["CONFLUENCE", "JIRA", "GIT", "TEAMS", "GWIKI", "SHAREPOINT", "MANUAL"],
        )

    trigger_description = st.text_area("설명 (선택)", placeholder="인제스천 사유")

    submitted = st.form_submit_button("인제스천 시작", type="primary")
    if submitted:
        if not trigger_kb_id:
            st.error("대상 KB를 선택해주세요.")
        else:
            body = {
                "kb_id": trigger_kb_id,
                "source_type": trigger_source_type,
            }
            if trigger_description:
                body["description"] = trigger_description

            with st.spinner("인제스천 트리거 중..."):
                result = api_client.trigger_ingestion(body)
                if api_failed(result):
                    st.error("인제스천 트리거 실패")
                else:
                    new_run_id = result.get("run_id", result.get("id", ""))
                    st.success(f"인제스천이 시작되었습니다. (실행 ID: {new_run_id})")
                    st.cache_data.clear()
                    st.rerun()
