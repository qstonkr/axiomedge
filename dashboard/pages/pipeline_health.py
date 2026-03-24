"""5-Stage Pipeline Health Dashboard

RAG pipeline observability: Collection -> Parsing -> Embedding -> Query -> Response.
Each stage shows key metrics from the oreo-agents API and Datadog StatsD.

Created: 2026-03-15
"""
import streamlit as st

st.set_page_config(page_title="Pipeline Health", page_icon="🏥", layout="wide")


from pathlib import Path
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from components.sidebar import hide_default_nav, render_sidebar
from services import api_client
from services.api_client import api_failed

hide_default_nav()
render_sidebar(show_admin=True)

st.title("🏥 Pipeline Health")
st.caption("RAG 파이프라인 5단계 건강 상태: Collection → Parsing → Embedding → Query → Response")

# -- Constants ----------------------------------------------------------------
STAGES = [
    ("collection", "Collection (수집)"),
    ("parsing", "Parsing (파싱)"),
    ("embedding", "Embedding (임베딩)"),
    ("query", "Query (조회)"),
    ("response", "Response (응답)"),
]
STAGE_STEP_MAP: dict[str, list[str]] = {
    "collection": ["preprocess"],
    "parsing": ["korean", "chunk", "dedup"],
    "embedding": ["embed"],
    "query": [],
    "response": [],
}
STATUS_EMOJI = {"green": "🟢", "yellow": "🟡", "red": "🔴", "gray": "⚪"}


def _stage_status(metrics: dict, stage_key: str) -> tuple[str, str]:
    """Derive (color, label) for a pipeline stage based on error_rate."""
    steps = metrics.get("steps", metrics.get("step_metrics", {}))
    if not steps:
        return "red", "데이터 없음"
    step_keys = STAGE_STEP_MAP.get(stage_key, [])
    if not step_keys:
        return "gray", "별도 확인 필요"
    error_rates = [steps.get(sk, {}).get("error_rate", 0) for sk in step_keys]
    avg_err = sum(error_rates) / len(error_rates) if error_rates else 0
    if avg_err >= 0.15:
        return "red", f"오류율 {avg_err:.0%}"
    if avg_err >= 0.05:
        return "yellow", f"주의 {avg_err:.0%}"
    return "green", f"정상 {avg_err:.0%}"


# -- Tabs ---------------------------------------------------------------------
tab_overview, tab_collection, tab_parsing, tab_embedding, tab_query, tab_config = st.tabs(
    ["Overview", "Collection (수집)", "Parsing (파싱)",
     "Embedding (임베딩)", "Query (조회)", "Config Consistency (정합성)"],
)

# == 1) Overview ==============================================================
with tab_overview:
    st.subheader("5-Stage 파이프라인 상태")
    metrics_result = api_client.get_pipeline_metrics()
    if api_failed(metrics_result):
        st.error("파이프라인 메트릭 API 연결 실패")
        if st.button("재시도", key="retry_overview"):
            st.cache_data.clear()
            st.rerun()
    else:
        cols = st.columns(5)
        for idx, (key, label) in enumerate(STAGES):
            color, status_text = _stage_status(metrics_result, key)
            emoji = STATUS_EMOJI.get(color, "⚪")
            with cols[idx]:
                st.markdown(
                    f'<div style="text-align:center;padding:16px;border-radius:8px;'
                    f'border:1px solid #ddd;">'
                    f'<div style="font-size:2rem;">{emoji}</div>'
                    f'<div style="font-weight:bold;margin-top:4px;">{label}</div>'
                    f'<div style="color:gray;font-size:0.85rem;">{status_text}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
        st.markdown("---")
        st.subheader("종합 지표")
        m1, m2, m3, m4 = st.columns(4)
        with m1:
            st.metric("총 처리 문서", f"{metrics_result.get('total_documents_processed', 0):,}")
        with m2:
            sr = metrics_result.get("success_rate", 0)
            st.metric("성공률", f"{sr:.1%}" if isinstance(sr, float) else str(sr))
        with m3:
            st.metric("처리량 (docs/min)", f"{metrics_result.get('throughput', 0):.1f}")
        with m4:
            st.metric("평균 지연 (ms)", f"{metrics_result.get('avg_latency_ms', 0):.0f}")

# == 2) Collection (수집) =====================================================
with tab_collection:
    st.subheader("KB 동기화 현황")
    kbs_result = api_client.list_kbs()
    if api_failed(kbs_result):
        st.error("KB 목록 API 연결 실패")
        if st.button("재시도", key="retry_collection"):
            st.cache_data.clear()
            st.rerun()
    else:
        kb_items = kbs_result.get("items", [])
        if kb_items:
            rows = []
            for kb in kb_items:
                kb_id = kb.get("id", kb.get("kb_id", "-"))
                status = kb.get("status", "UNKNOWN")
                status_badge = {"active": "🟢 활성", "disabled": "⚪ 비활성",
                                "syncing": "🔵 동기화 중", "error": "🔴 오류"
                                }.get(status.lower(), f"⚪ {status}")
                stats = api_client.get_kb_stats(kb_id)
                doc_count = error_count = 0
                last_sync = "-"
                if not api_failed(stats):
                    doc_count = stats.get("document_count", stats.get("total_documents", 0))
                    error_count = stats.get("error_count", stats.get("errors", 0))
                    last_sync = stats.get("last_synced_at", stats.get("last_sync", "-"))
                rows.append({"KB ID": kb_id, "이름": kb.get("name", "-"), "상태": status_badge,
                             "문서 수": doc_count, "오류 수": error_count, "마지막 동기화": last_sync})
            df = pd.DataFrame(rows)
            st.dataframe(df, use_container_width=True, hide_index=True)
            error_kbs = [r for r in rows if r["오류 수"] > 0]
            if error_kbs:
                st.markdown("---")
                st.subheader("KB별 오류 현황")
                fig = px.bar(x=[r["이름"] for r in error_kbs], y=[r["오류 수"] for r in error_kbs],
                             title="오류가 있는 KB", labels={"x": "KB", "y": "오류 수"},
                             color_discrete_sequence=["#dc3545"])
                fig.update_layout(margin=dict(l=20, r=20, t=40, b=20))
                st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("등록된 KB가 없습니다.")

# == 3) Parsing (파싱) ========================================================
with tab_parsing:
    st.subheader("중복 제거 파이프라인 통계")
    dedup_result = api_client.get_dedup_stats()
    if api_failed(dedup_result):
        st.warning("중복 제거 통계를 가져올 수 없습니다.")
        st.info("(Datadog 연동 예정) 파싱 세부 지표는 Datadog StatsD 메트릭으로 수집됩니다.")
    else:
        d1, d2, d3, d4 = st.columns(4)
        with d1:
            st.metric("검사 문서 수", f"{dedup_result.get('total_checked', 0):,}")
        with d2:
            st.metric("중복 감지", f"{dedup_result.get('duplicates_found', 0):,}")
        with d3:
            st.metric("중복 제거됨", f"{dedup_result.get('duplicates_removed', 0):,}")
        with d4:
            st.metric("충돌 미해결", f"{dedup_result.get('unresolved_conflicts', 0):,}")
        stages = dedup_result.get("stages", dedup_result.get("stage_stats", {}))
        if stages:
            st.markdown("---")
            st.subheader("4-Stage Dedup 파이프라인")
            st.caption("Bloom(<1ms) → LSH(<10ms) → SemHash(~50ms) → LLM Conflict(~100ms)")
            stage_names, stage_counts = [], []
            for skey, slabel in [("bloom", "Bloom Filter"), ("lsh", "LSH"),
                                 ("semhash", "SemHash"), ("llm_conflict", "LLM Conflict")]:
                info = stages.get(skey, {})
                stage_names.append(slabel)
                stage_counts.append(info.get("filtered", info.get("count", 0)))
            fig_dedup = px.bar(x=stage_names, y=stage_counts, title="단계별 중복 필터링 건수",
                               labels={"x": "단계", "y": "필터링 건수"}, color_discrete_sequence=["#17a2b8"])
            fig_dedup.update_layout(margin=dict(l=20, r=20, t=40, b=20))
            st.plotly_chart(fig_dedup, use_container_width=True)
    st.markdown("---")
    st.caption("(Datadog 연동 예정) 파싱 상세 지표: 청킹 성공률, 한국어 처리 품질, 컨텍스트 생성 시간")

# == 4) Embedding (임베딩) =====================================================
with tab_embedding:
    st.subheader("임베딩 메트릭")
    embed_result = api_client.get_embedding_stats()
    if api_failed(embed_result):
        st.warning("임베딩 통계를 가져올 수 없습니다.")
        st.info("(Datadog 연동 예정) 임베딩 레이턴시 및 배치 처리 메트릭")
    else:
        e1, e2, e3 = st.columns(3)
        with e1:
            st.metric("임베딩 모델", embed_result.get("model", "BGE-M3"))
        with e2:
            st.metric("총 임베딩 수", f"{embed_result.get('total_embeddings', 0):,}")
        with e3:
            avg_lat = embed_result.get("avg_latency_ms", 0)
            st.metric("평균 레이턴시 (ms)", f"{avg_lat:.0f}" if avg_lat else "-")
        vs_result = api_client.get_vectorstore_stats()
        if not api_failed(vs_result):
            st.markdown("---")
            st.subheader("벡터 저장소 현황")
            v1, v2, v3 = st.columns(3)
            with v1:
                st.metric("총 벡터 수", f"{vs_result.get('total_vectors', 0):,}")
            with v2:
                st.metric("컬렉션 수", f"{vs_result.get('collection_count', vs_result.get('collections', 0)):,}")
            with v3:
                disk_mb = vs_result.get("disk_usage_mb", vs_result.get("storage_mb", 0))
                st.metric("디스크 사용량", f"{disk_mb:,.0f} MB" if disk_mb else "-")
    st.markdown("---")
    st.caption("(Datadog 연동 예정) 배치 임베딩 처리량, GPU 사용률, 모델별 레이턴시 분포")

# == 5) Query (조회) — most critical tab ======================================
with tab_query:
    st.subheader("검색 레이턴시 분석")
    search_result = api_client.get_search_analytics()
    if api_failed(search_result):
        st.warning("검색 분석 데이터를 가져올 수 없습니다.")
    else:
        q1, q2, q3 = st.columns(3)
        with q1:
            st.metric("총 검색 수", f"{search_result.get('total_queries', 0):,}")
        with q2:
            avg_lat = search_result.get("avg_latency_ms", search_result.get("average_latency_ms", 0))
            st.metric("평균 레이턴시 (ms)", f"{avg_lat:.0f}" if avg_lat else "-")
        with q3:
            cache_hit = search_result.get("cache_hit_rate", 0)
            st.metric("캐시 히트율", f"{cache_hit:.1%}" if isinstance(cache_hit, float) else str(cache_hit))
        latency_breakdown = search_result.get("latency_breakdown", {})
        if latency_breakdown:
            st.markdown("---")
            st.subheader("레이턴시 구간별 분석")
            lbl_map = {"prefilter_ms": "Prefilter", "embedding_ms": "Embedding",
                       "qdrant_ms": "Qdrant 검색", "rerank_ms": "Reranking", "total_ms": "Total"}
            bd_labels, bd_values = [], []
            for key, label in lbl_map.items():
                val = latency_breakdown.get(key, 0)
                if val or key == "total_ms":
                    bd_labels.append(label)
                    bd_values.append(val)
            if bd_labels:
                colors = ["#17a2b8", "#28a745", "#6f42c1", "#fd7e14", "#dc3545"]
                fig_bd = go.Figure(go.Bar(
                    x=bd_labels, y=bd_values, marker_color=colors[: len(bd_labels)],
                    text=[f"{v:.0f}ms" for v in bd_values], textposition="auto"))
                fig_bd.update_layout(title="검색 레이턴시 구간 (ms)", xaxis_title="구간",
                                     yaxis_title="레이턴시 (ms)", margin=dict(l=20, r=20, t=40, b=20))
                st.plotly_chart(fig_bd, use_container_width=True)

    # -- Industry benchmark comparison --
    st.markdown("---")
    st.subheader("Industry Benchmark 비교")
    st.caption("RAG 파이프라인 주요 레이턴시 지표와 업계 기준 비교")

    cur_e2e = cur_embed = cur_vec = "-"
    st_e2e = st_embed = st_vec = "⚪ 미측정"
    if not api_failed(search_result):
        _total = search_result.get("avg_latency_ms", search_result.get("average_latency_ms", 0))
        if _total:
            cur_e2e = f"{_total:.0f}ms"
            st_e2e = "🟢 양호" if _total < 2000 else ("🟡 주의" if _total < 5000 else "🔴 초과")
        lb = search_result.get("latency_breakdown", {})
        _emb = lb.get("embedding_ms", 0)
        if _emb:
            cur_embed = f"{_emb:.0f}ms"
            st_embed = "🟢 양호" if _emb < 200 else ("🟡 주의" if _emb < 500 else "🔴 초과")
        _vq = lb.get("qdrant_ms", 0)
        if _vq:
            cur_vec = f"{_vq:.0f}ms"
            st_vec = "🟢 양호" if _vq < 500 else ("🟡 주의" if _vq < 1000 else "🔴 초과")

    bm = pd.DataFrame([
        {"Metric": "E2E Latency p50", "Industry Standard": "<2s", "Current": cur_e2e, "Status": st_e2e},
        {"Metric": "Embedding Latency", "Industry Standard": "<200ms", "Current": cur_embed, "Status": st_embed},
        {"Metric": "Vector Query", "Industry Standard": "<500ms", "Current": cur_vec, "Status": st_vec},
    ])

    def _hl(val: str) -> str:
        if "양호" in str(val):
            return "background-color: #d4edda; color: #155724"
        if "주의" in str(val):
            return "background-color: #fff3cd; color: #856404"
        if "초과" in str(val):
            return "background-color: #f8d7da; color: #721c24"
        return ""

    st.dataframe(bm.style.map(_hl, subset=["Status"]), use_container_width=True, hide_index=True)

# == 6) Config Consistency (정합성) ============================================
with tab_config:
    st.subheader("KB 설정 정합성 검사")
    st.caption("kb_config.yaml 설정과 실제 컬렉션 존재 여부를 비교합니다.")

    config_path = Path(__file__).resolve().parents[2] / "scripts" / "knowledge" / "kb_config.yaml"
    config_loaded = False
    kb_config_entries: list[dict] = []

    if config_path.exists():
        try:
            import yaml
            raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                for _key, entry in raw.items():
                    if isinstance(entry, dict) and "id" in entry:
                        kb_config_entries.append({
                            "config_key": _key, "id": entry["id"],
                            "name": entry.get("name", "-"), "tier": entry.get("tier", "-"),
                            "status": entry.get("status", "active"),
                        })
                config_loaded = True
        except Exception as exc:
            st.warning(f"kb_config.yaml 파싱 실패: {exc}")

    if not config_loaded:
        st.info("kb_config.yaml을 로드할 수 없습니다. "
                "프로덕션 Docker 이미지에서는 이 파일이 포함되지 않을 수 있습니다.")
    else:
        kbs_result = api_client.list_kbs()
        api_kb_ids: set[str] = set()
        if not api_failed(kbs_result):
            for kb in kbs_result.get("items", []):
                api_kb_ids.add(kb.get("id", kb.get("kb_id", "")))
        rows = []
        for entry in kb_config_entries:
            kb_id = entry["id"]
            exists = kb_id in api_kb_ids
            cfg_status = entry["status"]
            if cfg_status == "disabled":
                consistency = "⚪ 비활성 (검사 건너뜀)"
            elif exists:
                consistency = "🟢 일치"
            else:
                consistency = "🔴 불일치 (API 미존재)"
            rows.append({"Config Key": entry["config_key"], "KB ID": kb_id,
                         "이름": entry["name"], "Tier": entry["tier"],
                         "설정 상태": cfg_status, "API 존재": "O" if exists else "X",
                         "정합성": consistency})
        if rows:
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
            total = len(rows)
            matched = sum(1 for r in rows if "일치" in r["정합성"])
            mismatched = sum(1 for r in rows if "불일치" in r["정합성"])
            disabled = sum(1 for r in rows if "비활성" in r["정합성"])
            c1, c2, c3 = st.columns(3)
            with c1:
                st.metric("일치", f"{matched}/{total}")
            with c2:
                st.metric("불일치", f"{mismatched}/{total}")
            with c3:
                st.metric("비활성 (건너뜀)", f"{disabled}/{total}")
        else:
            st.info("kb_config.yaml에 유효한 KB 항목이 없습니다.")
