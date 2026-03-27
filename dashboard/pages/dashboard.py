"""KB 현황 대시보드 (병합)

구 2_dashboard + admin/1_dashboard 병합.
3 탭: KB 개요, 파이프라인 실행, KB 분류

Created: 2026-02-20
"""

import streamlit as st

st.set_page_config(page_title="KB 현황", page_icon="📊", layout="wide")


import pandas as pd
import plotly.express as px

from components.constants import (
    KB_STATUS_ICONS,
    PIPELINE_STEP_KEYS,
    PIPELINE_STEP_LABELS,
    RUN_STATUS_ICONS,
    STEP_STATUS_ICONS,
    TIER_ICONS,
)
from components.sidebar import hide_default_nav, render_sidebar
from services import api_client
from services.api_client import api_failed

hide_default_nav()
render_sidebar(show_admin=True)

st.title("📊 KB 현황")

# ── 탭 구성 ──
tab_overview, tab_pipeline, tab_category, tab_l1 = st.tabs(["KB 개요", "파이프라인 실행", "KB 분류", "L1 카테고리"])

TOP_LEVEL_STAGE_LABELS = {
    "crawl": "수집",
    "ingest": "인제스천",
    "terms": "용어 유사도",
    "publish": "퍼블리시",
}


# =============================================================================
# 탭 1: KB 개요
# =============================================================================
with tab_overview:
    kbs_result = api_client.list_kbs()
    agg_result = api_client.get_kb_aggregation()

    if api_failed(kbs_result) or api_failed(agg_result):
        st.error("API 연결 실패. 재시도 해주세요.")
        if st.button("🔄 재시도", key="retry_overview"):
            st.cache_data.clear()
            st.rerun()
    else:
        # ── 집계 메트릭 카드 ──
        agg = agg_result
        total_kbs = agg.get("total_kbs", 0)
        total_docs = agg.get("total_documents", 0)
        total_chunks = agg.get("total_chunks", 0)
        avg_quality = agg.get("avg_quality_score", 0)

        is_partial = agg.get("stats_partial", False) or (
            total_docs == 0 and total_chunks == 0 and total_kbs > 0
        )
        if is_partial:
            st.warning(
                "⚠️ Qdrant 통계를 수집하지 못해 캐시된 데이터를 표시 중입니다. "
                "데이터가 최신이 아닐 수 있습니다."
            )

        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("총 KB 수", f"{total_kbs:,}개")
        with col2:
            st.metric("총 문서 수", f"{total_docs:,}개")
        with col3:
            st.metric("총 청크 수", f"{total_chunks:,}개")
        with col4:
            st.metric(
                "평균 품질 점수",
                f"{avg_quality:.1f}점" if isinstance(avg_quality, (int, float)) else str(avg_quality),
                help=(
                    "**산정 기준 (0-100점)**\n\n"
                    "- 콘텐츠 길이: 최대 60점 (로그 스케일)\n"
                    "- 구조 보너스: 테이블·코드·헤더·이미지·링크 각 +8점\n"
                    "- 품질 등급: GOLD +15 / SILVER +10 / BRONZE +5\n\n"
                    "**등급 기준**\n\n"
                    "- 90+ A(우수) · 70+ B(양호) · 50+ C(보통)\n"
                    "- 30+ D(미흡) · 30미만 F(부족)\n\n"
                    "대부분 문서가 500-2000자 텍스트 위주이며 "
                    "구조 요소가 적어 C-B등급에 분포합니다."
                ),
            )

        st.markdown("---")

        # ── KB 목록 테이블 ──
        st.subheader("KB 목록")
        kbs = kbs_result.get("items", kbs_result.get("kbs", []))
        if kbs:
            rows = []
            for kb in kbs:
                rows.append({
                    "이름": kb.get("name", "-"),
                    "티어": kb.get("tier", "-"),
                    "상태": kb.get("status", "-"),
                    "Live 문서 수": kb.get("document_count", 0),
                    "Live 청크 수": kb.get("chunk_count", 0),
                    "실험 문서 수": kb.get("experiment_document_count", 0),
                    "실험 청크 수": kb.get("experiment_chunk_count", 0),
                    "실험 상태": kb.get("experiment_status", "idle"),
                    "퍼블리시 전략": kb.get("publish_strategy", "legacy"),
                    "실험 퍼블리시": kb.get("experiment_publish_status", "not_started"),
                    "KB ID": kb.get("kb_id", kb.get("id", "-")),
                })
            df = pd.DataFrame(rows)

            # 상태 색상 표시
            df["상태"] = df["상태"].apply(lambda s: f"{KB_STATUS_ICONS.get(s, '⚪')} {s}")
            df["실험 상태"] = df["실험 상태"].apply(lambda s: f"{RUN_STATUS_ICONS.get(s, '⚪')} {s}")
            df["티어"] = df["티어"].apply(lambda t: f"{TIER_ICONS.get(t, '')} {t}")

            st.dataframe(df, use_container_width=True, hide_index=True)
        else:
            st.info("등록된 KB가 없습니다.")


# =============================================================================
# 탭 2: 파이프라인 실행
# =============================================================================
with tab_pipeline:
    pipeline_result = api_client.get_pipeline_status()
    runs_result = api_client.list_ingestion_runs()
    stats_result = api_client.get_ingestion_stats()
    kbs_for_pipeline = api_client.list_kbs()

    if api_failed(pipeline_result):
        st.error("API 연결 실패. 재시도 해주세요.")
        if st.button("🔄 재시도", key="retry_pipeline"):
            st.cache_data.clear()
            st.rerun()
    else:
        # ── 파이프라인 상태 요약 ──
        pipeline_status = pipeline_result.get("status", "idle")
        active_jobs = pipeline_result.get("active_jobs", pipeline_result.get("active_runs", 0))
        error_count = pipeline_result.get("error_count_24h", 0)
        current_kb = pipeline_result.get("current_kb")
        current_step = pipeline_result.get("current_step")
        current_stage = pipeline_result.get("current_stage")
        mode = pipeline_result.get("mode", pipeline_result.get("status", "canonical"))
        target_kb_id = pipeline_result.get("target_kb_id")
        collection_kind = pipeline_result.get("collection_kind", "live")
        publish_status = pipeline_result.get("publish_status", "not_started")
        stage_summaries = pipeline_result.get("stage_summaries", {})

        s1, s2, s3, s4, s5 = st.columns(5)
        with s1:
            status_badge = {
                "active": "🔵 실행중",
                "idle": "⏸️ 대기",
                "completed": "🟢 완료",
                "failed": "🔴 실패",
            }.get(pipeline_status, f"⚪ {pipeline_status}")
            st.metric("파이프라인 상태", status_badge)
        with s2:
            st.metric("활성 작업", f"{active_jobs}개")
        with s3:
            st.metric("24h 오류", f"{error_count}건")
        with s4:
            st.metric("실행 모드", "experiment" if mode == "experiment" else "canonical")
        with s5:
            st.metric("퍼블리시 상태", publish_status)

        # 실행 중인 경우 KB/step 정보 표시
        if pipeline_status == "active" and current_kb:
            step_label = PIPELINE_STEP_LABELS.get(current_step, current_step) if current_step else "-"
            stage_label = TOP_LEVEL_STAGE_LABELS.get(current_stage, current_stage) if current_stage else "-"
            st.info(
                f"현재 인제스천: **{current_kb}** — 상위 단계: **{stage_label}** — 세부 단계: **{step_label}**"
            )

        if mode == "experiment":
            target_display = target_kb_id or "-"
            st.warning(
                "현재 파이프라인은 canonical 검색본이 아니라 experiment 경로에서 실행 중입니다. "
                f"실험 대상 KB: **{target_display}** / collection kind: **{collection_kind}**"
            )
        else:
            st.caption(
                "현재 표시된 상태는 canonical/live 경로 기준입니다. "
                "experiment run은 publish 전까지 기존 검색본에 직접 영향을 주지 않습니다."
            )

        st.markdown("---")

        # ── 4-Step 상위 단계 상태 ──
        st.subheader("상위 파이프라인 단계 (4단계)")
        for stage in ["crawl", "ingest", "terms", "publish"]:
            info = stage_summaries.get(stage, {})
            status = info.get("status", "pending")
            progress = info.get("progress", 0)
            label = TOP_LEVEL_STAGE_LABELS.get(stage, stage)

            col_label, col_bar = st.columns([1, 3])
            with col_label:
                status_icon = STEP_STATUS_ICONS.get(status, "⏸️")
                st.write(f"{status_icon} **{label}**")
            with col_bar:
                st.progress(min(progress / 100, 1.0) if isinstance(progress, (int, float)) else 0.0)

        st.markdown("---")

        # ── 10-Step 파이프라인 진행 상태 ──
        st.subheader("인제스천 파이프라인 (10단계)")

        steps_data = pipeline_result.get("steps", {})
        for step in PIPELINE_STEP_KEYS:
            info = steps_data.get(step, {})
            status = info.get("status", "idle")
            progress = info.get("progress", 0)
            label = PIPELINE_STEP_LABELS.get(step, step)

            col_label, col_bar = st.columns([1, 3])
            with col_label:
                status_icon = STEP_STATUS_ICONS.get(status, "⏸️")
                st.write(f"{status_icon} **{label}**")
            with col_bar:
                st.progress(min(progress / 100, 1.0) if isinstance(progress, (int, float)) else 0.0)

        st.markdown("---")

        # ── 인제스천 통계 요약 ──
        if not api_failed(stats_result):
            st.subheader("인제스천 통계")
            total_runs = stats_result.get("total_runs", 0)
            success_count = stats_result.get("success_count", stats_result.get("successful", 0))
            error_count_stats = stats_result.get("error_count", stats_result.get("failed", 0))
            avg_dur = stats_result.get("avg_duration_ms", 0)
            last_run = stats_result.get("last_run_at", "-")

            c1, c2, c3, c4 = st.columns(4)
            with c1:
                st.metric("총 실행 수", f"{total_runs}건")
            with c2:
                st.metric("성공", f"{success_count}건")
            with c3:
                st.metric("실패", f"{error_count_stats}건")
            with c4:
                st.metric("평균 소요시간", f"{avg_dur:.0f}ms" if avg_dur else "-")

            if last_run and last_run != "-":
                st.caption(f"마지막 실행: {last_run}")

            st.markdown("---")

        # ── 실행 이력 ──
        st.subheader("인제스천 실행 이력")
        runs = []
        if not api_failed(runs_result):
            runs = runs_result.get("items", runs_result.get("runs", []))
        if runs:
            rows = []
            for run in runs:
                rows.append({
                    "실행 ID": (run.get("job_id", run.get("run_id", run.get("id", "-"))) or "-")[:12],
                    "KB": run.get("kb_id", run.get("kb_name", "-")),
                    "상태": run.get("status", "-"),
                    "시작 시간": run.get("created_at", run.get("started_at", "-")),
                    "소요 시간": run.get("duration", "-"),
                })
            df_runs = pd.DataFrame(rows)

            df_runs["상태"] = df_runs["상태"].apply(lambda s: RUN_STATUS_ICONS.get(s, s))
            st.dataframe(df_runs, use_container_width=True, hide_index=True)
        else:
            st.info("인제스천 실행 이력이 없습니다. Temporal 스케줄이 실행되면 여기에 표시됩니다.")

        st.markdown("---")
        st.subheader("Experiment 검증")
        if api_failed(kbs_for_pipeline):
            st.info("KB 목록을 읽지 못해 experiment 검증 정보를 표시하지 않습니다.")
        else:
            pipeline_kb_items = kbs_for_pipeline.get("items", kbs_for_pipeline.get("kbs", []))
            if not pipeline_kb_items:
                st.info("검증할 KB가 없습니다.")
            else:
                kb_options = {
                    kb.get("name", kb.get("id", "")): kb
                    for kb in pipeline_kb_items
                }
                selected_kb_name = st.selectbox(
                    "검증 KB 선택",
                    options=list(kb_options.keys()),
                    key="pipeline_experiment_kb_select",
                )
                selected_kb = kb_options[selected_kb_name]
                selected_kb_id = selected_kb.get("id", selected_kb.get("kb_id", ""))

                c1, c2, c3, c4 = st.columns(4)
                with c1:
                    st.metric("Live 청크", selected_kb.get("chunk_count", 0))
                with c2:
                    st.metric("실험 청크", selected_kb.get("experiment_chunk_count", 0))
                with c3:
                    st.metric("퍼블리시 전략", selected_kb.get("publish_strategy", "legacy"))
                with c4:
                    st.metric("실험 상태", selected_kb.get("experiment_status", "idle"))

                strategy_options = ["legacy", "alias_live"]
                current_strategy = selected_kb.get("publish_strategy", "legacy")
                selected_strategy = st.selectbox(
                    "퍼블리시 전략",
                    options=strategy_options,
                    index=strategy_options.index(current_strategy)
                    if current_strategy in strategy_options
                    else 0,
                    key=f"pipeline_publish_strategy_{selected_kb_id}",
                )
                if selected_strategy != current_strategy:
                    st.warning(
                        "전략 변경은 기존 settings를 유지한 채 publish_strategy만 갱신합니다. "
                        "alias_live는 experiment 검증 후 alias 전환을 허용합니다."
                    )
                    if st.button("퍼블리시 전략 저장", key=f"pipeline_publish_strategy_save_{selected_kb_id}"):
                        update_result = api_client.update_kb_publish_strategy(
                            selected_kb_id,
                            selected_strategy,
                        )
                        if api_failed(update_result):
                            st.error(
                                f"퍼블리시 전략 저장 실패: {update_result.get('error', 'unknown error')}"
                            )
                        else:
                            st.success(
                                f"publish_strategy를 {update_result.get('publish_strategy', selected_strategy)} 로 저장했습니다."
                            )
                            st.cache_data.clear()
                            st.rerun()

                experiment_detail = api_client.get_latest_experiment_run(selected_kb_id)
                experiment_run_id = selected_kb.get("experiment_run_id")
                if api_failed(experiment_detail) and experiment_run_id:
                    experiment_detail = api_client.get_pipeline_run_detail(experiment_run_id)

                if not api_failed(experiment_detail):
                    experiment_run_id = experiment_detail.get("run_id")
                    st.caption(
                        f"run_id={experiment_detail.get('run_id', '-')}, "
                        f"target_kb_id={experiment_detail.get('target_kb_id', '-')}, "
                        f"publish_status={experiment_detail.get('publish_status', 'not_started')}"
                    )
                    exp_stage_summaries = experiment_detail.get("stage_summaries", {})
                    for stage in ["crawl", "ingest", "terms", "publish"]:
                        info = exp_stage_summaries.get(stage, {})
                        label = TOP_LEVEL_STAGE_LABELS.get(stage, stage)
                        status = info.get("status", "pending")
                        progress = info.get("progress", 0)
                        col_label, col_bar = st.columns([1, 3])
                        with col_label:
                            st.write(f"{STEP_STATUS_ICONS.get(status, '⏸️')} **{label}**")
                        with col_bar:
                            st.progress(
                                min(progress / 100, 1.0)
                                if isinstance(progress, (int, float))
                                else 0.0
                            )
                else:
                    st.caption("아직 latest experiment run 이 없습니다.")

                dry_run_state_key = f"pipeline_publish_dry_run_result_{selected_kb_id}"

                sync_sources = selected_kb.get("sync_sources") or []
                if isinstance(sync_sources, list) and sync_sources:
                    st.markdown("##### Experiment sync 시작")
                    sync_source_options: dict[str, dict] = {}
                    for source in sync_sources:
                        if not isinstance(source, dict):
                            continue
                        source_name = source.get("name") or source.get("source_type") or "unnamed-source"
                        sync_source_options[str(source_name)] = source

                    selected_sync_source_name = st.selectbox(
                        "실행할 sync source",
                        options=list(sync_source_options.keys()),
                        key=f"pipeline_experiment_sync_source_{selected_kb_id}",
                    )
                    selected_sync_source = sync_source_options[selected_sync_source_name]
                    selected_source_type = selected_sync_source.get("source_type")
                    st.caption(
                        f"source_type={selected_source_type or '-'}, "
                        f"entry_point={selected_sync_source.get('entry_point', '-')}"
                    )
                    sync_preflight_state_key = (
                        f"pipeline_experiment_sync_preflight_{selected_kb_id}"
                    )
                    if st.button(
                        "Experiment sync preflight",
                        key=f"pipeline_experiment_sync_preflight_btn_{selected_kb_id}",
                    ):
                        preflight_result = api_client.validate_kb_sync(
                            selected_kb_id,
                            mode="experiment",
                            source_type=selected_source_type,
                            sync_source_name=selected_sync_source_name,
                        )
                        if api_failed(preflight_result):
                            st.error(
                                f"experiment sync preflight 실패: {preflight_result.get('error', 'unknown error')}"
                            )
                        else:
                            st.session_state[sync_preflight_state_key] = preflight_result

                    preflight_result = st.session_state.get(sync_preflight_state_key)
                    if isinstance(preflight_result, dict):
                        st.json(preflight_result)

                    confirm_sync_key = f"pipeline_experiment_sync_confirm_{selected_kb_id}"
                    confirmed_experiment_sync = st.checkbox(
                        "canonical KB는 그대로 두고 experiment 경로로만 수동 sync를 시작합니다.",
                        key=confirm_sync_key,
                    )
                    if confirmed_experiment_sync and st.button(
                        "Experiment sync 시작",
                        key=f"pipeline_experiment_sync_trigger_{selected_kb_id}",
                    ):
                        sync_result = api_client.trigger_kb_sync(
                            selected_kb_id,
                            mode="experiment",
                            source_type=selected_source_type,
                            sync_source_name=selected_sync_source_name,
                        )
                        if api_failed(sync_result):
                            st.error(
                                f"experiment sync 시작 실패: {sync_result.get('error', 'unknown error')}"
                            )
                        else:
                            st.success(
                                "experiment sync를 시작했습니다. latest experiment run 상태를 다시 확인합니다."
                            )
                            st.cache_data.clear()
                            st.session_state.pop(dry_run_state_key, None)
                            st.session_state.pop(sync_preflight_state_key, None)
                            st.rerun()
                else:
                    st.caption("등록된 sync source가 없어 experiment sync를 시작할 수 없습니다.")

                if st.button("Dry-run publish 확인", key="pipeline_publish_dry_run"):
                    dry_run_result = api_client.publish_experiment_dry_run(
                        selected_kb_id,
                        run_id=experiment_run_id,
                    )
                    if api_failed(dry_run_result):
                        st.error(f"dry-run 실패: {dry_run_result.get('error', 'unknown error')}")
                    else:
                        st.session_state[dry_run_state_key] = dry_run_result

                dry_run_result = st.session_state.get(dry_run_state_key)
                if isinstance(dry_run_result, dict):
                    st.json(dry_run_result)
                    if dry_run_result.get("can_execute"):
                        confirm_key = f"pipeline_publish_execute_confirm_{selected_kb_id}"
                        confirmed = st.checkbox(
                            "실제 live alias 전환을 이해했고, 기존 canonical collection을 직접 덮어쓰지 않는다는 점을 확인했습니다.",
                            key=confirm_key,
                        )
                        if confirmed and st.button(
                            "Execute publish",
                            type="primary",
                            key=f"pipeline_publish_execute_{selected_kb_id}",
                        ):
                            execute_result = api_client.publish_experiment_execute(
                                selected_kb_id,
                                run_id=experiment_run_id,
                            )
                            if api_failed(execute_result):
                                st.error(
                                    f"execute 실패: {execute_result.get('error', 'unknown error')}"
                                )
                            else:
                                st.success(
                                    f"publish 완료: {execute_result.get('live_collection_name', '-')}"
                                )
                                st.session_state[dry_run_state_key] = execute_result
                                st.cache_data.clear()
                                st.rerun()


# =============================================================================
# 탭 3: KB 분류
# =============================================================================
with tab_category:
    st.subheader("KB별 카테고리 분류")
    st.caption("2-Stage 분류: 키워드 매칭 → LLM 폴백")

    # KB 선택
    kbs_for_select = api_client.list_kbs()
    if api_failed(kbs_for_select):
        st.error("API 연결 실패. 재시도 해주세요.")
        if st.button("🔄 재시도", key="retry_category"):
            st.cache_data.clear()
            st.rerun()
    else:
        kb_items = kbs_for_select.get("items", kbs_for_select.get("kbs", []))
        if kb_items:
            kb_options = {kb.get("name", kb.get("kb_id", "")): kb.get("kb_id", kb.get("id", "")) for kb in kb_items}
            selected_kb_name = st.selectbox("KB 선택", options=list(kb_options.keys()), key="cat_kb_select")
            selected_kb_id = kb_options[selected_kb_name]

            cat_result = api_client.get_kb_categories(selected_kb_id)
            if api_failed(cat_result):
                st.error("API 연결 실패. 재시도 해주세요.")
                if st.button("🔄 재시도", key="retry_cat_detail"):
                    st.cache_data.clear()
                    st.rerun()
            else:
                categories = cat_result.get("categories", [])
                if categories:
                    cat_names = [c.get("name", c.get("category", "기타")) for c in categories]
                    cat_counts = [c.get("count", c.get("document_count", 0)) for c in categories]

                    fig = px.pie(
                        names=cat_names,
                        values=cat_counts,
                        title=f"{selected_kb_name} 카테고리 분포",
                        hole=0.3,
                    )
                    fig.update_layout(margin=dict(l=20, r=20, t=40, b=20))
                    st.plotly_chart(fig, use_container_width=True)

                    # 테이블 표시
                    df_cat = pd.DataFrame({"카테고리": cat_names, "문서 수": cat_counts})
                    df_cat = df_cat.sort_values("문서 수", ascending=False).reset_index(drop=True)
                    st.dataframe(df_cat, use_container_width=True, hide_index=True)
                else:
                    st.info("카테고리 정보가 없습니다.")
        else:
            st.info("등록된 KB가 없습니다.")


# =============================================================================
# 탭 4: L1 카테고리
# =============================================================================
with tab_l1:
    st.subheader("L1 카테고리 분포")
    st.caption("전사 고정 7개 L1 대분류 기반 문서 분포 현황")

    l1_cats_result = api_client.list_l1_categories()
    l1_stats_result = api_client.get_l1_stats()

    if api_failed(l1_cats_result) and api_failed(l1_stats_result):
        st.error("L1 카테고리 API 연결 실패. 재시도 해주세요.")
        if st.button("🔄 재시도", key="retry_l1"):
            st.cache_data.clear()
            st.rerun()
    else:
        # ── L1 카테고리 마스터 목록 ──
        l1_cats = l1_cats_result.get("items", l1_cats_result.get("categories", [])) if isinstance(l1_cats_result, dict) else l1_cats_result
        if not isinstance(l1_cats, list):
            l1_cats = []

        if l1_cats and not api_failed(l1_cats_result):
            active_cats = [c for c in l1_cats if c.get("is_active", True)]
            st.markdown("##### L1 카테고리 정의")
            df_l1_master = pd.DataFrame({
                "L1 카테고리": [c.get("name", "") for c in active_cats],
                "설명": [c.get("description", "") for c in active_cats],
                "키워드 수": [len(c.get("keywords", [])) for c in active_cats],
            })
            st.dataframe(df_l1_master, use_container_width=True, hide_index=True)
            st.markdown("---")

        # ── L1 분포 (집계 API 1회 호출) ──
        if not api_failed(l1_stats_result):
            l1_counts = l1_stats_result.get("l1_counts", {})
            total_docs = l1_stats_result.get("total_docs", l1_stats_result.get("total_documents", 0))
            etc_count = l1_stats_result.get("etc_count", l1_stats_result.get("uncategorized", 0))
            etc_ratio = l1_stats_result.get("etc_ratio", 0.0)
            kb_breakdown = l1_stats_result.get("kb_breakdown", [])

            if l1_counts:
                st.markdown("##### 전체 L1 분포")

                m1, m2, m3 = st.columns(3)
                with m1:
                    st.metric("총 분류 문서", f"{total_docs:,}건")
                with m2:
                    st.metric("기타 문서", f"{etc_count:,}건")
                with m3:
                    color = "inverse" if etc_ratio > 0.15 else "normal"
                    st.metric(
                        "기타 비율",
                        f"{etc_ratio:.1%}",
                        delta=f"{'초과' if etc_ratio > 0.15 else 'OK'} (임계 15%)",
                        delta_color=color,
                    )

                # 도넛 차트
                fig_l1 = px.pie(
                    names=list(l1_counts.keys()),
                    values=list(l1_counts.values()),
                    title="전체 L1 카테고리 분포",
                    hole=0.3,
                    color_discrete_sequence=px.colors.qualitative.Set2,
                )
                fig_l1.update_layout(margin=dict(l=20, r=20, t=40, b=20))
                st.plotly_chart(fig_l1, use_container_width=True)

                st.markdown("---")

                # KB별 스택 바 차트
                if kb_breakdown:
                    st.markdown("##### KB별 L1 분포")
                    df_kb_l1 = pd.DataFrame(kb_breakdown)
                    df_kb_l1.columns = ["KB", "L1", "문서 수"]
                    fig_stack = px.bar(
                        df_kb_l1,
                        x="KB",
                        y="문서 수",
                        color="L1",
                        title="KB별 L1 카테고리 분포",
                        color_discrete_sequence=px.colors.qualitative.Set2,
                    )
                    fig_stack.update_layout(
                        barmode="stack",
                        margin=dict(l=20, r=20, t=40, b=20),
                        xaxis_tickangle=-45,
                    )
                    st.plotly_chart(fig_stack, use_container_width=True)

                    # 상세 피벗 테이블
                    df_pivot = df_kb_l1.pivot_table(
                        index="KB", columns="L1", values="문서 수", aggfunc="sum", fill_value=0
                    )
                    st.dataframe(df_pivot, use_container_width=True)
            else:
                st.info("L1 분류 데이터가 없습니다. 재분류 배치 실행 후 확인해 주세요.")
