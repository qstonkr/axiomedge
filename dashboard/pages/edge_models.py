"""엣지 모델 관리

검색 그룹 기반 Small LM 엣지 모델 생성/관리/배포.
3탭: 설정, 학습, 운영.

Created: 2026-04-06
"""

import streamlit as st

st.set_page_config(page_title="엣지 모델", page_icon="🤖", layout="wide")

from components.constants import DISTILL_STATUS_ICONS, EDGE_LOG_SUCCESS_ICON  # noqa: E402
from components.sidebar import hide_default_nav, render_sidebar  # noqa: E402
from services import api_client  # noqa: E402
from services.api_client import api_failed  # noqa: E402

hide_default_nav()
render_sidebar(show_admin=True)

st.title("🤖 엣지 모델 관리")

tab_settings, tab_train, tab_ops = st.tabs(["설정", "학습", "운영"])


# =============================================================================
# Tab 1: 설정 — 프로필 CRUD
# =============================================================================
with tab_settings:
    st.caption(
        "검색 그룹별 엣지 모델 빌드 프로필을 관리합니다. "
        "검색 그룹, 베이스 모델, LoRA/학습 파라미터, 응답 스타일을 설정합니다."
    )

    profiles_result = api_client.list_distill_profiles()
    if api_failed(profiles_result):
        st.error("API 연결 실패")
        if st.button("🔄 재시도", key="retry_profiles"):
            st.cache_data.clear()
            st.rerun()
    else:
        profiles = profiles_result.get("profiles", {})

        # ── 프로필 목록 ──
        if not profiles:
            st.info("등록된 프로필이 없습니다. 아래에서 새로 만드세요.")

        for name, profile in profiles.items():
            enabled = profile.get("enabled", False)
            icon = "🟢" if enabled else "⚪"

            with st.container(border=True):
                col_info, col_detail, col_actions = st.columns([3, 3, 2])

                with col_info:
                    st.markdown(f"**{icon} {name}**")
                    st.caption(
                        f"검색그룹: {profile.get('search_group', '')} | "
                        f"모델: {profile.get('base_model', '').split('/')[-1]} | "
                        f"{'활성' if enabled else '비활성'}"
                    )

                with col_detail:
                    lora = profile.get("lora", {})
                    training = profile.get("training", {})
                    st.caption(
                        f"LoRA r={lora.get('r', 8)} a={lora.get('alpha', 16)} | "
                        f"Epochs: {training.get('epochs', 3)} | "
                        f"LR: {training.get('learning_rate', 2e-4)}"
                    )

                with col_actions:
                    btn1, btn2 = st.columns(2)
                    with btn1:
                        if st.button("✏️", key=f"edit_{name}", help="편집"):
                            st.session_state["editing_profile"] = name
                    with btn2:
                        if st.button("🗑️", key=f"del_{name}", help="삭제"):
                            st.session_state[f"confirm_del_{name}"] = True

                if st.session_state.get(f"confirm_del_{name}", False):
                    st.warning(f"**{name}** 프로필을 삭제하시겠습니까?")
                    c1, c2 = st.columns(2)
                    with c1:
                        if st.button("확인 삭제", key=f"confirm_del2_{name}", type="primary"):
                            result = api_client.delete_distill_profile(name)
                            if not api_failed(result):
                                st.success("삭제 완료")
                                st.session_state[f"confirm_del_{name}"] = False
                                st.cache_data.clear()
                                st.rerun()
                    with c2:
                        if st.button("취소", key=f"cancel_del_{name}"):
                            st.session_state[f"confirm_del_{name}"] = False
                            st.rerun()

        st.markdown("---")

        # ── 프로필 생성/편집 폼 ──
        editing_name = st.session_state.get("editing_profile", "")
        editing = profiles.get(editing_name, {}) if editing_name else {}
        form_title = f"✏️ '{editing_name}' 편집" if editing_name else "➕ 새 프로필"

        with st.expander(form_title, expanded=bool(editing_name)):
            groups_result = api_client.list_search_groups_for_distill()
            group_options = []
            if not api_failed(groups_result):
                group_options = [g.get("name", "") for g in groups_result.get("groups", []) if g.get("name")]

            with st.form("profile_form"):
                form_name = st.text_input(
                    "프로필 이름", value=editing_name, disabled=bool(editing_name),
                )
                form_desc = st.text_input("설명", value=editing.get("description", ""))

                default_group_idx = 0
                if editing.get("search_group") and editing["search_group"] in group_options:
                    default_group_idx = group_options.index(editing["search_group"])
                form_group = st.selectbox(
                    "검색 그룹", options=group_options if group_options else ["(없음)"],
                    index=default_group_idx,
                )

                model_options = [
                    "Qwen/Qwen2.5-0.5B-Instruct",
                    "Qwen/Qwen2.5-1.5B-Instruct",
                    "google/gemma-3-1b-it",
                ]
                default_model_idx = 0
                if editing.get("base_model") in model_options:
                    default_model_idx = model_options.index(editing["base_model"])
                form_model = st.selectbox("베이스 모델", options=model_options, index=default_model_idx)
                form_enabled = st.checkbox("활성화", value=editing.get("enabled", True))

                st.markdown("**LoRA 설정**")
                lora_cfg = editing.get("lora", {})
                lc1, lc2, lc3 = st.columns(3)
                with lc1:
                    form_lora_r = st.number_input("Rank", value=lora_cfg.get("r", 8), min_value=4, max_value=64)
                with lc2:
                    form_lora_alpha = st.number_input("Alpha", value=lora_cfg.get("alpha", 16), min_value=8, max_value=128)
                with lc3:
                    form_lora_dropout = st.number_input(
                        "Dropout", value=lora_cfg.get("dropout", 0.05), min_value=0.0, max_value=0.5, step=0.01,
                    )

                st.markdown("**학습 설정**")
                train_cfg = editing.get("training", {})
                tc1, tc2, tc3 = st.columns(3)
                with tc1:
                    form_epochs = st.number_input("Epochs", value=train_cfg.get("epochs", 3), min_value=1, max_value=20)
                with tc2:
                    form_batch = st.number_input("Batch", value=train_cfg.get("batch_size", 4), min_value=1, max_value=32)
                with tc3:
                    form_lr = st.number_input(
                        "Learning Rate", value=train_cfg.get("learning_rate", 2e-4), format="%.1e", step=1e-5,
                    )

                st.markdown("**응답 스타일**")
                qa_cfg = editing.get("qa_style", {})
                qc1, qc2 = st.columns(2)
                with qc1:
                    form_qa_mode = st.selectbox(
                        "모드", options=["concise", "detailed"],
                        index=0 if qa_cfg.get("mode", "concise") == "concise" else 1,
                    )
                with qc2:
                    form_max_tokens = st.number_input(
                        "최대 응답 토큰", value=qa_cfg.get("max_answer_tokens", 256), min_value=64, max_value=2048,
                    )

                submitted = st.form_submit_button("저장", type="primary")
                if submitted:
                    body = {
                        "name": form_name,
                        "description": form_desc,
                        "search_group": form_group,
                        "base_model": form_model,
                        "enabled": form_enabled,
                        "lora": {"r": form_lora_r, "alpha": form_lora_alpha, "dropout": form_lora_dropout},
                        "training": {"epochs": form_epochs, "batch_size": form_batch, "learning_rate": form_lr},
                        "qa_style": {"mode": form_qa_mode, "max_answer_tokens": form_max_tokens},
                    }
                    if editing_name:
                        result = api_client.update_distill_profile(form_name, body)
                    else:
                        result = api_client.create_distill_profile(body)
                    if not api_failed(result):
                        st.success("저장 완료")
                        st.session_state["editing_profile"] = ""
                        st.cache_data.clear()
                        st.rerun()
                    else:
                        st.error(f"저장 실패: {result.get('error', '')}")


# =============================================================================
# Tab 2: 학습 — 데이터 현황 + 빌드 + 이력
# =============================================================================
with tab_train:
    st.caption(
        "RAG 실서비스 응답 중 고품질(CRAG correct) QA를 기반으로 학습합니다. "
        "데이터가 부족하면 KB 청크에서 보조 QA를 자동 생성합니다."
    )

    profiles_result = api_client.list_distill_profiles()
    if api_failed(profiles_result):
        st.error("API 연결 실패")
    else:
        profiles = profiles_result.get("profiles", {})
        enabled = [k for k, v in profiles.items() if v.get("enabled")]

        if not enabled:
            st.info("활성화된 프로필이 없습니다. '설정' 탭에서 프로필을 만드세요.")
        else:
            selected = st.selectbox("프로필", options=enabled, key="train_profile")

            # ── 학습 데이터 현황 ──
            stats = api_client.get_training_data_stats(selected)
            if not api_failed(stats):
                sc1, sc2, sc3, sc4 = st.columns(4)
                with sc1:
                    st.metric("전체", f"{stats.get('total', 0):,}건")
                with sc2:
                    st.metric("RAG 로그 (메인)", f"{stats.get('usage_log', 0):,}건")
                with sc3:
                    st.metric("청크 QA (보조)", f"{stats.get('chunk_qa', 0):,}건")
                with sc4:
                    st.metric("재학습 추가", f"{stats.get('retrain', 0):,}건")

            # ── 데이터 미리보기 ──
            with st.expander("학습 데이터 미리보기", expanded=False):
                td_result = api_client.list_training_data(selected, limit=20)
                if not api_failed(td_result):
                    items = td_result.get("items", [])
                    if items:
                        import pandas as pd
                        df = pd.DataFrame([
                            {
                                "타입": i.get("source_type", ""),
                                "질문": i.get("question", "")[:60],
                                "답변": i.get("answer", "")[:60],
                                "상태": i.get("status", ""),
                            }
                            for i in items
                        ])
                        st.dataframe(df, use_container_width=True)
                    else:
                        st.info("학습 데이터가 없습니다. 빌드를 시작하면 RAG 로그에서 자동 수집됩니다.")

            # ── 수동 QA 추가 ──
            with st.expander("수동 QA 추가", expanded=False):
                with st.form("manual_qa_form"):
                    manual_q = st.text_input("질문")
                    manual_a = st.text_area("답변", height=80)
                    if st.form_submit_button("추가", type="primary"):
                        if manual_q and manual_a:
                            result = api_client.add_training_data({
                                "profile_name": selected,
                                "question": manual_q,
                                "answer": manual_a,
                                "source_type": "manual",
                            })
                            if not api_failed(result):
                                st.success("추가 완료")
                                st.cache_data.clear()
                                st.rerun()
                        else:
                            st.warning("질문과 답변을 모두 입력하세요.")

            st.markdown("---")

            # ── 빌드 시작 ──
            st.subheader("모델 빌드")

            bc1, bc2 = st.columns([3, 1])
            with bc2:
                if st.button("🚀 빌드 시작", type="primary", key="btn_build"):
                    result = api_client.trigger_distill_build({"profile_name": selected})
                    if not api_failed(result):
                        st.success(f"빌드 시작: {result.get('version', '')}")
                        st.cache_data.clear()
                        st.rerun()
                    else:
                        st.error(f"빌드 실패: {result.get('error', '')}")

            # ── 빌드 이력 ──
            builds_result = api_client.list_distill_builds(profile_name=selected)
            if not api_failed(builds_result):
                builds = builds_result.get("items", [])
                if not builds:
                    st.info("빌드 이력이 없습니다.")

                for build in builds:
                    status = build.get("status", "pending")
                    badge = DISTILL_STATUS_ICONS.get(status, f"⚪ {status}")

                    with st.container(border=True):
                        col_info, col_metrics, col_actions = st.columns([3, 3, 2])

                        with col_info:
                            st.markdown(f"**{build.get('version', '-')}**")
                            st.caption(
                                f"모델: {build.get('base_model', '').split('/')[-1]} | "
                                f"데이터: {build.get('training_samples', 0):,}건"
                            )

                        with col_metrics:
                            st.markdown(f"상태: {badge}")
                            parts = []
                            if build.get("train_loss"):
                                parts.append(f"loss: {build['train_loss']:.4f}")
                            if build.get("gguf_size_mb"):
                                parts.append(f"크기: {build['gguf_size_mb']:.0f}MB")
                            if build.get("eval_faithfulness"):
                                parts.append(
                                    f"Faith: {build['eval_faithfulness']:.2f} / "
                                    f"Relev: {build.get('eval_relevancy', 0):.2f}"
                                )
                            if parts:
                                st.caption(" | ".join(parts))

                        with col_actions:
                            if status == "completed" and not build.get("deployed_at"):
                                if st.button("🚀 배포", key=f"deploy_{build['id']}"):
                                    result = api_client.deploy_build(build["id"])
                                    if not api_failed(result):
                                        st.success("배포 완료")
                                        st.cache_data.clear()
                                        st.rerun()
                            if build.get("deployed_at"):
                                if st.button("↩️ 롤백", key=f"rollback_{build['id']}"):
                                    result = api_client.rollback_build(build["id"])
                                    if not api_failed(result):
                                        st.success("롤백 완료")
                                        st.cache_data.clear()
                                        st.rerun()

                        # 진행중 프로그레스
                        if status in ("generating", "training", "evaluating", "quantizing", "deploying"):
                            steps = ["generating", "training", "evaluating", "quantizing", "deploying"]
                            progress = (steps.index(status) + 1) / len(steps)
                            st.progress(progress, text=badge)

                        # 실패 메시지
                        if status == "failed" and build.get("error_message"):
                            st.error(f"[{build.get('error_step', '')}] {build['error_message']}")


# =============================================================================
# Tab 3: 운영 — 엣지 로그 + 재학습
# =============================================================================
with tab_ops:
    st.caption(
        "매장 엣지 서버의 실사용 로그를 수집하고, 실패 질문에 정답을 추가하여 재학습합니다."
    )

    profiles_result = api_client.list_distill_profiles()
    if api_failed(profiles_result):
        st.error("API 연결 실패")
    else:
        profiles = profiles_result.get("profiles", {})
        enabled = [k for k, v in profiles.items() if v.get("enabled")]

        if not enabled:
            st.info("활성화된 프로필이 없습니다.")
        else:
            col_sel, col_btn = st.columns([3, 1])
            with col_sel:
                selected = st.selectbox("프로필", options=enabled, key="ops_profile")
            with col_btn:
                st.markdown("")
                if st.button("📥 로그 수집", key="collect_logs"):
                    with st.spinner("S3에서 로그 수집 중..."):
                        result = api_client.collect_edge_logs(profile_name=selected)
                        if not api_failed(result):
                            st.success(f"수집 완료: {result.get('collected', 0)}건")
                            st.cache_data.clear()
                            st.rerun()

            # ── 메트릭 ──
            analytics = api_client.get_edge_analytics(selected)
            if not api_failed(analytics):
                mc1, mc2, mc3, mc4 = st.columns(4)
                with mc1:
                    st.metric("총 질의", f"{analytics.get('total_queries', 0):,}건")
                with mc2:
                    st.metric("평균 지연", f"{analytics.get('avg_latency_ms', 0):.0f}ms")
                with mc3:
                    st.metric("성공률", f"{analytics.get('success_rate', 0):.1%}")
                with mc4:
                    st.metric("매장 수", f"{analytics.get('store_count', 0)}개")

            st.markdown("---")

            # ── 실사용 로그 ──
            st.subheader("실사용 로그")

            fc1, fc2, fc3 = st.columns(3)
            with fc1:
                filter_store = st.text_input("매장 필터", placeholder="매장 ID", key="log_store")
            with fc2:
                filter_success = st.selectbox(
                    "결과",
                    options=[None, True, False],
                    format_func=lambda x: {None: "전체", True: "성공", False: "실패"}.get(x, str(x)),
                    key="log_success",
                )
            with fc3:
                filter_limit = st.number_input("표시 건수", value=50, min_value=10, max_value=200, key="log_limit")

            logs_result = api_client.list_edge_logs(
                profile_name=selected,
                store_id=filter_store if filter_store else None,
                success=filter_success,
                limit=filter_limit,
            )
            if api_failed(logs_result):
                st.warning("로그 조회 실패")
            else:
                logs = logs_result.get("items", [])
                if not logs:
                    st.info("수집된 로그가 없습니다.")
                else:
                    selected_for_retrain = []
                    for log in logs:
                        success = log.get("success", True)
                        icon = EDGE_LOG_SUCCESS_ICON.get(success, "⚪")

                        with st.container(border=True):
                            col_chk, col_log, col_ans = st.columns([0.5, 4, 3])

                            with col_chk:
                                if not success:
                                    if st.checkbox("", key=f"sel_{log['id']}", label_visibility="collapsed"):
                                        selected_for_retrain.append(log["id"])

                            with col_log:
                                st.markdown(f"{icon} **{log.get('query', '')}**")
                                st.caption(
                                    f"매장: {log.get('store_id', '')} | "
                                    f"{log.get('latency_ms', 0)}ms | "
                                    f"v{log.get('model_version', '')} | "
                                    f"{log.get('edge_timestamp', '')[:16]}"
                                )

                            with col_ans:
                                ans = log.get("answer", "")
                                st.caption(
                                    ans[:100] + ("..." if len(ans) > 100 else "")
                                    if ans else "(응답 없음)"
                                )

            st.markdown("---")

            # ── 재학습 ──
            st.subheader("재학습")

            failed_result = api_client.list_failed_edge_queries(selected)
            if not api_failed(failed_result):
                failed = failed_result.get("items", [])

                if not failed and not selected_for_retrain:
                    st.info("실패 질문이 없습니다.")
                else:
                    if selected_for_retrain:
                        st.info(f"위에서 {len(selected_for_retrain)}건 선택됨")

                    corrected_answers = {}
                    for item in failed[:10]:
                        with st.container(border=True):
                            st.markdown(f"**Q:** {item.get('query', '')}")
                            st.caption(f"매장: {item.get('store_id', '')}")
                            corrected = st.text_area(
                                "정답 입력 (비우면 RAG로 자동 생성)",
                                key=f"ans_{item['id']}",
                                height=68,
                            )
                            if corrected:
                                corrected_answers[item["id"]] = corrected

                    rc1, rc2 = st.columns(2)
                    with rc1:
                        if st.button("📚 학습 데이터에 추가", type="primary", key="btn_add_retrain"):
                            body = {
                                "profile_name": selected,
                                "edge_log_ids": selected_for_retrain or [f["id"] for f in failed[:10]],
                                "generate_answers": True,
                                "corrected_answers": corrected_answers,
                            }
                            result = api_client.trigger_retrain(body)
                            if not api_failed(result):
                                st.success(f"추가 완료: {result.get('added', 0)}건")
                                st.cache_data.clear()
                                st.rerun()
                            else:
                                st.error(f"실패: {result.get('error', '')}")
                    with rc2:
                        if st.button("🔄 재학습 시작", key="btn_retrain"):
                            result = api_client.trigger_distill_build({"profile_name": selected})
                            if not api_failed(result):
                                st.success(f"재학습 빌드 시작: {result.get('build_id', '')}")
                                st.cache_data.clear()
                                st.rerun()
                            else:
                                st.error(f"실패: {result.get('error', '')}")
