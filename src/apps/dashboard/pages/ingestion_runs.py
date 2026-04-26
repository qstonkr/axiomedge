"""인제스천 Run 상세 + 실패 추적 (PR-10 I).

`knowledge_ingestion_runs` 의 최근 N 개 row 와 각 run 의 실패 문서 목록을
표시. 실패 row 가 있는 run 에 대해 "재시도" 버튼 제공 → CLI 명령 안내 또는
admin API endpoint 호출.

Created: 2026-04-26 (PR-10 I)
"""

import streamlit as st

st.set_page_config(
    page_title="Ingestion Runs", page_icon="🧾", layout="wide",
)

from components.sidebar import hide_default_nav, render_sidebar  # noqa: E402
from services import api_client  # noqa: E402

hide_default_nav()
render_sidebar(show_admin=True)

st.title("🧾 Ingestion Runs (최근 실패 추적)")
st.caption(
    "PR-1/PR-10 — 파일별 실패 사유·stage·traceback 영속화 결과를 표시합니다. "
    "CLI 에서 ``--retry-failed RUN_ID`` 로 재시도 가능."
)


# =============================================================================
# 최근 Run 목록
# =============================================================================
@st.cache_data(ttl=10)
def _fetch_runs(limit: int = 50) -> list[dict]:
    """API → IngestionRunRepository.list_recent."""
    try:
        return api_client.get(
            f"/api/v1/admin/pipeline/ingestion-runs?limit={limit}",
            cache_key=f"ingestion_runs:{limit}",
        ) or []
    except Exception as e:  # noqa: BLE001
        st.error(f"Failed to fetch runs: {e}")
        return []


@st.cache_data(ttl=10)
def _fetch_failures(run_id: str) -> list[dict]:
    """API → IngestionFailureRepository.list_by_run."""
    try:
        return api_client.get(
            f"/api/v1/admin/pipeline/runs/{run_id}/failures",
            cache_key=f"failures:{run_id}",
        ) or []
    except Exception as e:  # noqa: BLE001
        st.warning(f"Failures lookup failed for {run_id}: {e}")
        return []


col_top1, col_top2 = st.columns([3, 1])
with col_top1:
    limit = st.number_input(
        "표시 건수", min_value=10, max_value=500, value=50, step=10,
    )
with col_top2:
    if st.button("🔄 새로고침"):
        st.cache_data.clear()
        st.rerun()

runs = _fetch_runs(limit=int(limit))

if not runs:
    st.info(
        "Ingestion run history 가 비어 있습니다. CLI ingest 를 실행하거나 "
        "API `/api/v1/knowledge/ingest` 를 호출한 후 다시 확인해 주세요."
    )
    st.stop()

# 요약 메트릭
total = len(runs)
failed_runs = sum(1 for r in runs if r.get("status") == "failed")
total_docs = sum(int(r.get("documents_ingested") or 0) for r in runs)
total_chunks = sum(int(r.get("chunks_stored") or 0) for r in runs)

m1, m2, m3, m4 = st.columns(4)
m1.metric("최근 Run", f"{total}건")
m2.metric("실패 Run", f"{failed_runs}건")
m3.metric("Ingested 문서", f"{total_docs:,}")
m4.metric("저장 청크", f"{total_chunks:,}")

# =============================================================================
# Run 표
# =============================================================================
st.subheader("Run 목록")
st.caption("각 행을 클릭하면 실패 문서와 traceback 을 펼쳐볼 수 있습니다.")

for run in runs:
    run_id = run.get("id") or run.get("run_id") or ""
    kb_id = run.get("kb_id", "?")
    status = run.get("status", "?")
    started = run.get("started_at", "")
    completed = run.get("completed_at", "")
    docs_in = run.get("documents_ingested", 0) or 0
    docs_fail = run.get("documents_rejected", 0) or 0

    icon = "✅" if status == "completed" else (
        "❌" if status == "failed" else "⏳"
    )
    label = (
        f"{icon} `{run_id[:8]}…`  kb=**{kb_id}**  "
        f"status=`{status}`  docs={docs_in}/{docs_in + docs_fail}  "
        f"started={started}"
    )
    with st.expander(label):
        c1, c2 = st.columns([2, 1])
        with c1:
            st.write(f"**Run ID**: `{run_id}`")
            st.write(f"**Source**: {run.get('source_name', 'N/A')}")
            st.write(f"**Completed**: {completed}")
            st.write(
                f"**Chunks**: {run.get('chunks_stored', 0):,} stored, "
                f"{run.get('chunks_deduped', 0):,} deduped"
            )

        with c2:
            if st.button("🔁 재시도 (CLI 명령 표시)", key=f"retry_{run_id}"):
                st.code(
                    f"uv run python -m src.cli.ingest "
                    f"--retry-failed {run_id} --kb-id {kb_id}",
                    language="bash",
                )
                st.info(
                    "위 명령은 failures 테이블의 source_uri 가 있는 항목만 "
                    "재시도합니다. 진행은 별도 새 run 으로 추적됩니다."
                )

        # 실패 문서 목록
        failures = _fetch_failures(run_id)
        if failures:
            st.markdown("**실패 문서 목록**")
            rows = []
            for f in failures[:50]:
                rows.append({
                    "doc_id": (f.get("doc_id") or "")[:12] + "…",
                    "stage": f.get("stage", "?"),
                    "reason": (f.get("reason") or "")[:80],
                    "source_uri": (f.get("source_uri") or "")[:50],
                    "failed_at": f.get("failed_at", ""),
                })
            st.dataframe(rows, use_container_width=True, hide_index=True)

            # traceback 첫 1개 펼쳐 표시 (참고용)
            tb = failures[0].get("traceback")
            if tb:
                with st.expander("첫 실패의 traceback (참고)"):
                    st.code(tb, language="python")
        elif status == "failed":
            st.warning(
                "Run 은 failed 상태이지만 failures 테이블에 row 가 없습니다. "
                "API/CLI 가 failure_repo wiring 전이거나 errors 가 errors[:10] "
                "에만 기록됐을 수 있습니다."
            )
        else:
            st.success("실패 문서 없음")
