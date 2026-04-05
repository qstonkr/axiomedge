"""Job Monitor

인제스천/크롤링 Job 상태 모니터링 페이지.

Created: 2026-03-25
"""

import time

import streamlit as st

st.set_page_config(page_title="Job Monitor", page_icon="⚙️", layout="wide")

from components.sidebar import hide_default_nav, render_sidebar
from services import api_client
from services.api_client import api_failed

hide_default_nav()
render_sidebar()

st.title("⚙️ Job Monitor")

# =============================================================================
# Controls
# =============================================================================
ctrl_col1, ctrl_col2, ctrl_col3 = st.columns([2, 1, 1])

with ctrl_col1:
    st.caption("인제스천/크롤링 Job 실행 상태를 모니터링합니다.")

with ctrl_col2:
    auto_refresh = st.toggle("자동 새로고침", value=False, key="job_auto_refresh")

with ctrl_col3:
    if st.button("🔄 새로고침", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

st.markdown("---")

# =============================================================================
# Status badges
# =============================================================================
STATUS_BADGES = {
    "running": "🟢 Running",
    "completed": "✅ Completed",
    "failed": "❌ Failed",
    "pending": "🟡 Pending",
    "cancelled": "⚪ Cancelled",
    "queued": "🔵 Queued",
    "processing": "🔄 Processing",
}


def _status_badge(status: str) -> str:
    return STATUS_BADGES.get(status.lower(), f"⚪ {status}")


# =============================================================================
# Job List
# =============================================================================
jobs_result = api_client.list_jobs()

if api_failed(jobs_result):
    st.error("Job 목록을 불러올 수 없습니다. API 서버를 확인해주세요.")
else:
    jobs = jobs_result.get("items", jobs_result.get("jobs", []))

    if jobs:
        # Summary metrics
        total = len(jobs)
        running = sum(1 for j in jobs if j.get("status", "").lower() == "running")
        failed = sum(1 for j in jobs if j.get("status", "").lower() == "failed")
        completed = sum(1 for j in jobs if j.get("status", "").lower() == "completed")

        m1, m2, m3, m4 = st.columns(4)
        with m1:
            st.metric("전체", f"{total}건")
        with m2:
            st.metric("실행 중", f"{running}건")
        with m3:
            st.metric("완료", f"{completed}건")
        with m4:
            st.metric("실패", f"{failed}건")

        st.markdown("---")

        # Job table
        for job in jobs:
            job_id = job.get("job_id", job.get("id", "-"))
            status = job.get("status", "unknown")
            job_type = job.get("job_type", job.get("type", "-"))
            created_at = job.get("created_at", "-")
            docs_processed = job.get("processed", job.get("documents_processed", 0))
            chunks_created = job.get("chunks", job.get("chunks_created", 0))
            error_list = job.get("errors", [])
            error_count = len(error_list) if isinstance(error_list, list) else error_list

            with st.container(border=True):
                col_status, col_info, col_stats, col_action = st.columns([1.5, 3, 2, 1])

                with col_status:
                    st.markdown(f"**{_status_badge(status)}**")
                    st.caption(job_type)

                with col_info:
                    st.markdown(f"**Job:** `{job_id}`")
                    st.caption(f"생성: {created_at}")

                with col_stats:
                    total_files = job.get("total_files", 0)
                    st.caption(
                        f"파일: {docs_processed}/{total_files} | "
                        f"청크: {chunks_created:,} | "
                        f"에러: {error_count}"
                    )

                with col_action:
                    if status.lower() == "processing":
                        if st.button("⛔ 중지", key=f"job_cancel_{job_id}", use_container_width=True):
                            cancel_result = api_client.cancel_job(job_id)
                            if not api_failed(cancel_result):
                                st.toast(f"Job {job_id} 취소 요청 완료")
                                st.rerun()
                            else:
                                st.error("취소 실패")
                    if st.button("상세", key=f"job_detail_{job_id}", use_container_width=True):
                        st.session_state[f"_show_job_{job_id}"] = not st.session_state.get(
                            f"_show_job_{job_id}", False
                        )

            # Job detail (expandable)
            if st.session_state.get(f"_show_job_{job_id}", False):
                detail_result = api_client.get_job(job_id)
                if not api_failed(detail_result):
                    with st.expander(f"Job {job_id} 상세", expanded=True):
                        detail_col1, detail_col2 = st.columns(2)
                        with detail_col1:
                            st.json({
                                "job_id": detail_result.get("id", job_id),
                                "kb_id": detail_result.get("kb_id", "-"),
                                "status": detail_result.get("status", "-"),
                                "created_at": detail_result.get("created_at", "-"),
                                "updated_at": detail_result.get("updated_at", "-"),
                                "completed_at": detail_result.get("completed_at", "") or "-",
                            })
                        with detail_col2:
                            d_total = detail_result.get("total_files", 0)
                            d_processed = detail_result.get("processed", 0)
                            d_chunks = detail_result.get("chunks", 0)
                            d_errors = detail_result.get("errors", [])
                            st.json({
                                "total_files": d_total,
                                "processed": d_processed,
                                "chunks": d_chunks,
                                "error_count": len(d_errors) if isinstance(d_errors, list) else d_errors,
                            })

                        d_errors = detail_result.get("errors", [])
                        if isinstance(d_errors, list) and d_errors:
                            st.markdown("**에러 상세:**")
                            for err in d_errors[:20]:
                                st.error(str(err))
                else:
                    st.warning("상세 정보를 불러올 수 없습니다.")
    else:
        st.info("등록된 Job이 없습니다.")

# =============================================================================
# Auto-refresh polling
# =============================================================================
if auto_refresh:
    placeholder = st.empty()
    placeholder.caption("10초 후 자동 새로고침...")
    time.sleep(10)
    st.rerun()
