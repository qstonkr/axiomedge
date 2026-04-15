"""엣지 모델 관리

검색 그룹 기반 Small LM 엣지 모델 생성/관리/배포.
3탭: 설정, 학습, 운영.

Created: 2026-04-06
"""

import streamlit as st

st.set_page_config(page_title="엣지 모델", page_icon="🤖", layout="wide")

from components.constants import (  # noqa: E402
    CURATION_STATUS_ICONS,
    DISTILL_STATUS_ICONS,
    EDGE_LOG_SUCCESS_ICON,
    EDGE_SERVER_STATUS_ICONS,
    quality_badge,
)
from components.sidebar import hide_default_nav, render_sidebar  # noqa: E402
from services import api_client  # noqa: E402
from services.api_client import api_failed  # noqa: E402

hide_default_nav()
render_sidebar(show_admin=True)

st.title("🤖 엣지 모델 관리")

# 프로필은 전체 페이지에서 1번만 로드
_profiles_result = api_client.list_distill_profiles()
_profiles_ok = not api_failed(_profiles_result)
_all_profiles = _profiles_result.get("profiles", {}) if _profiles_ok else {}
_enabled_profiles = [k for k, v in _all_profiles.items() if v.get("enabled")]

tab_settings, tab_train, tab_curation, tab_servers, tab_ops, tab_base_models = st.tabs([
    "설정", "학습/모델관리", "데이터 큐레이션", "엣지 서버", "운영", "베이스 모델 레지스트리",
])


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

                # 베이스 모델 후보는 API (distill_base_models 테이블) 에서 로드.
                # SSOT: src/distill/seed.py 의 DEFAULT_BASE_MODELS + 대시보드/DB 수정분.
                base_models_result = api_client.list_distill_base_models()
                base_models_rows = (
                    base_models_result.get("models", [])
                    if not api_failed(base_models_result) else []
                )
                registry_empty = not base_models_rows

                def _build_model_label(m: dict) -> str:
                    """드롭다운 라벨 — hf_id + 라이선스/검증 플래그 + notes."""
                    flags: list[str] = []
                    if m.get("commercial_use"):
                        flags.append("상업OK")
                    else:
                        flags.append("상업X")
                    if not m.get("verified"):
                        flags.append("미검증")
                    tag = " · ".join(flags)
                    label = f"{m['hf_id']}  [{tag}]"
                    notes = m.get("notes")
                    if notes:
                        label += f" {notes}"
                    return label

                model_options = [m["hf_id"] for m in base_models_rows]
                model_labels = {m["hf_id"]: _build_model_label(m) for m in base_models_rows}

                # 기존 DB 프로필이 레지스트리에 없는 레거시 모델을 참조하면
                # 선택지에 "legacy" 라벨로 추가해서 저장된 값이 silent migration
                # 되지 않도록 보존한다. 사용자가 능동적으로 새 모델을 고를 때까지 유지.
                current_model = editing.get("base_model")
                if current_model and current_model not in model_options:
                    model_options = [current_model, *model_options]
                    model_labels[current_model] = f"{current_model}  [legacy · 교체 권장]"

                if registry_empty and not model_options:
                    # 레지스트리도 비어 있고 편집할 레거시 값도 없음 → 저장 불가
                    st.error(
                        "베이스 모델 레지스트리가 비어 있습니다. "
                        "API/DB 를 확인하세요. 프로필 저장이 비활성화됩니다.",
                    )
                    form_model = ""
                else:
                    default_model_idx = (
                        model_options.index(current_model)
                        if current_model in model_options else 0
                    )
                    form_model = st.selectbox(
                        "베이스 모델",
                        options=model_options,
                        index=default_model_idx,
                        format_func=lambda m: model_labels.get(m, m),
                    )
                    if registry_empty:
                        st.warning(
                            "베이스 모델 레지스트리가 비어 있어 새 모델을 선택할 수 없습니다. "
                            "기존 legacy 값만 유지 가능합니다.",
                        )
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

                # 베이스 모델 미선택 상태면 저장 차단 — 레지스트리 빈 상태에서
                # 잘못된 값이 DB 에 들어가는 걸 프론트 단에서 1차 방어 (서버도
                # _validate_base_model 로 2차 방어).
                submit_disabled = not form_model
                submitted = st.form_submit_button(
                    "저장", type="primary", disabled=submit_disabled,
                )
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

    if not _profiles_ok:
        st.error("API 연결 실패")
    else:
        profiles = _all_profiles
        enabled = _enabled_profiles

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

            # 승인 데이터 현황
            approved_stats = api_client.get_training_data_stats(selected)
            if not api_failed(approved_stats):
                asc1, asc2, asc3 = st.columns(3)
                with asc1:
                    st.metric("승인 데이터", f"{approved_stats.get('total', 0):,}건")
                with asc2:
                    st.caption("큐레이션 탭에서 데이터를 준비하세요")

            bc1, bc2, bc3 = st.columns(3)
            with bc1:
                if st.button("🚀 자동 생성 빌드", key="btn_build"):
                    result = api_client.trigger_distill_build({"profile_name": selected})
                    if not api_failed(result):
                        st.success(f"빌드 시작: {result.get('version', '')}")
                        st.cache_data.clear()
                        st.rerun()
            with bc2:
                approved_count = approved_stats.get("total", 0) if not api_failed(approved_stats) else 0
                if approved_count > 0:
                    if st.button("🚀 큐레이션 데이터 빌드", type="primary", key="btn_curated_build"):
                        result = api_client.trigger_distill_build({
                            "profile_name": selected,
                            "use_curated_data": True,
                        })
                        if not api_failed(result):
                            st.success(f"큐레이션 빌드 시작: {result.get('version', '')}")
                            st.cache_data.clear()
                            st.rerun()
                else:
                    st.button("🚀 큐레이션 데이터 빌드", disabled=True, key="btn_curated_build_disabled")
            with bc3:
                st.caption(
                    "**자동 생성**: RAG 로그에서 자동 수집\n\n"
                    "**큐레이션**: 승인된 데이터만 사용"
                )

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
                            # 테스트 빌드 뱃지
                            import json as _json2
                            ds = build.get("data_sources", "{}")
                            try:
                                ds_dict = _json2.loads(ds) if isinstance(ds, str) else ds or {}
                            except (ValueError, TypeError):
                                ds_dict = {}
                            is_test = ds_dict.get("source") == "curated" or "test" in str(ds_dict)
                            test_badge = " 🧪" if is_test else ""
                            st.markdown(f"**{build.get('version', '-')}{test_badge}**")
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
                            if not build.get("deployed_at") and status in ("completed", "failed"):
                                if st.button("🗑️", key=f"del_build_{build['id']}", help="빌드 삭제"):
                                    st.session_state[f"confirm_del_build_{build['id']}"] = True

                        if st.session_state.get(f"confirm_del_build_{build.get('id')}"):
                            st.warning(f"빌드 **{build.get('version')}**를 삭제하시겠습니까? S3 모델도 함께 삭제됩니다.")
                            dc1, dc2 = st.columns(2)
                            with dc1:
                                if st.button("확인 삭제", type="primary", key=f"confirm_del_b_{build['id']}"):
                                    result = api_client.delete_build(build["id"])
                                    if not api_failed(result):
                                        st.success("빌드 삭제 완료")
                                        st.session_state[f"confirm_del_build_{build['id']}"] = False
                                        st.cache_data.clear()
                                        st.rerun()
                            with dc2:
                                if st.button("취소", key=f"cancel_del_b_{build['id']}"):
                                    st.session_state[f"confirm_del_build_{build['id']}"] = False
                                    st.rerun()

                        # 진행중 프로그레스
                        if status in ("generating", "training", "evaluating", "quantizing", "deploying"):
                            steps = ["generating", "training", "evaluating", "quantizing", "deploying"]
                            progress = (steps.index(status) + 1) / len(steps)
                            st.progress(progress, text=badge)

                        # 실패 메시지
                        if status == "failed" and build.get("error_message"):
                            st.error(f"[{build.get('error_step', '')}] {build['error_message']}")


            # ── 베이스 모델 리셋 ──
            st.markdown("---")
            st.subheader("베이스 모델 리셋")
            st.caption("파인튜닝을 초기화하고 원본 베이스 모델(양자화 GGUF)로 되돌립니다.")
            if st.button("🔄 베이스 모델로 리셋", key="btn_reset_base"):
                st.session_state["confirm_reset_base"] = True

            if st.session_state.get("confirm_reset_base"):
                st.warning("모든 파인튜닝을 무시하고 베이스 모델로 리셋합니다. 진행하시겠습니까?")
                rc1, rc2 = st.columns(2)
                with rc1:
                    if st.button("확인", type="primary", key="btn_confirm_reset"):
                        result = api_client.reset_to_base_model(selected)
                        if not api_failed(result):
                            st.success(f"베이스 모델 리셋 시작: {result.get('version', '')}")
                            st.session_state["confirm_reset_base"] = False
                            st.cache_data.clear()
                            st.rerun()
                with rc2:
                    if st.button("취소", key="btn_cancel_reset"):
                        st.session_state["confirm_reset_base"] = False
                        st.rerun()

            # ── 모델 버전 히스토리 ──
            st.markdown("---")
            st.subheader("모델 버전 히스토리")
            versions_result = api_client.list_model_versions(selected)
            if not api_failed(versions_result):
                versions = versions_result.get("items", [])
                if versions:
                    import pandas as pd
                    df = pd.DataFrame([
                        {
                            "버전": v.get("version", ""),
                            "모델": v.get("model_name", v.get("base_model", "").split("/")[-1]),
                            "데이터": f"{v.get('training_samples', 0):,}",
                            "Loss": f"{v.get('train_loss', 0):.4f}" if v.get("train_loss") else "-",
                            "크기(MB)": f"{v.get('gguf_size_mb', 0):.0f}" if v.get("gguf_size_mb") else "-",
                            "SHA256": (v.get("gguf_sha256", "") or "")[:12] + "...",
                            "상태": "🟢 배포중" if v.get("deployed_at") else "⚪ 이전",
                        }
                        for v in versions
                    ])
                    st.dataframe(df, use_container_width=True)
                else:
                    st.info("배포된 빌드가 없습니다.")


# =============================================================================
# Tab 3: 데이터 큐레이션
# =============================================================================
with tab_curation:
    st.caption(
        "학습 데이터를 자동 생성하고, 품질 점수(일관성/범용성)를 확인한 후 "
        "승인/거부/편집하여 학습에 사용할 데이터를 큐레이션합니다."
    )

    if not _profiles_ok:
        st.error("API 연결 실패")
    else:
        profiles = _all_profiles
        enabled = _enabled_profiles

        if not enabled:
            st.info("활성화된 프로필이 없습니다.")
        else:
            selected = st.selectbox("프로필", options=enabled, key="curation_profile")

            sub_data, sub_aug, sub_term = st.tabs(["📄 데이터셋", "🔄 질문 변형", "📚 용어 학습"])

            # ==== 서브탭 1: 데이터셋 ====
            with sub_data:
                st.subheader("청크 기반 데이터 생성")
                gen_col1, gen_col2 = st.columns(2)
            with gen_col1:
                if st.button("🔄 데이터 생성 시작", key="btn_gen_data"):
                    result = api_client.generate_training_data({"profile_name": selected})
                    if not api_failed(result):
                        st.success("데이터 생성 시작됨")
                        st.cache_data.clear()
                        st.rerun()
            with gen_col2:
                if st.button("🧪 테스트 데이터 생성", key="btn_gen_test"):
                    result = api_client.generate_test_data({
                        "profile_name": selected, "count": 50,
                    })
                    if not api_failed(result):
                        st.success("테스트 데이터 생성 시작됨 (백그라운드)")
                        st.cache_data.clear()
                        st.rerun()

            # 배치 현황
            stats = api_client.get_training_data_stats(selected)
            if not api_failed(stats):
                mc1, mc2, mc3, mc4 = st.columns(4)
                with mc1:
                    st.metric("승인", f"{stats.get('total', 0):,}")
                with mc2:
                    st.metric("RAG 로그", f"{stats.get('usage_log', 0):,}")
                with mc3:
                    st.metric("청크 QA", f"{stats.get('chunk_qa', 0):,}")
                with mc4:
                    st.metric("수동/재학습", f"{stats.get('manual', 0) + stats.get('retrain', 0):,}")

                # Reformatter 산출물 — pending 이 있으면 눈에 띄게 노출
                ref_approved = stats.get("reformatted_approved", 0)
                ref_pending = stats.get("reformatted_pending", 0)
                if ref_approved or ref_pending:
                    rc1, rc2, rc3 = st.columns(3)
                    with rc1:
                        st.metric("재작성본 승인", f"{ref_approved:,}")
                    with rc2:
                        st.metric("재작성본 대기 ⚠️", f"{ref_pending:,}")
                    with rc3:
                        if ref_pending:
                            st.caption(
                                "↓ 아래 리뷰 섹션에서 '소스 타입 = reformatted' 필터로 확인 후 승인하세요"
                            )

                st.markdown("---")

                # ── Step 2: 리뷰 ──
                st.subheader("리뷰")

                # 필터
                fc1, fc2, fc3, fc4 = st.columns(4)
                with fc1:
                    filter_status = st.selectbox(
                        "상태", options=["pending", "approved", "rejected", None],
                        format_func=lambda x: CURATION_STATUS_ICONS.get(x, "전체") if x else "전체",
                        index=0, key="cur_status",
                    )
                with fc2:
                    filter_sort = st.selectbox(
                        "정렬", options=["consistency_score", "generality_score", "created_at"],
                        format_func=lambda x: {"consistency_score": "일관성↑", "generality_score": "범용성↑", "created_at": "최신"}[x],
                        key="cur_sort",
                    )
                with fc3:
                    filter_source = st.selectbox(
                        "소스 타입",
                        options=[None, "reformatted", "test_seed", "usage_log", "chunk_qa", "manual", "retrain"],
                        format_func=lambda x: "전체" if x is None else x,
                        key="cur_source",
                    )
                with fc4:
                    cur_page = st.number_input("페이지", value=1, min_value=1, key="cur_page")

                # 자동 필터 버튼
                af1, af2 = st.columns(2)
                with af1:
                    if st.button("✅ 스마트 일괄 승인", key="btn_smart_approve"):
                        result = api_client.smart_approve(selected, source_type=filter_source)
                        if not api_failed(result):
                            st.success(
                                f"승인: {result.get('approved', 0)}건 | "
                                f"거부: {result.get('rejected', 0)}건 | "
                                f"정리: {result.get('cleaned', 0)}건"
                            )
                            st.cache_data.clear()
                            st.rerun()
                with af2:
                    if st.button("❌ 범용성 0.3↓ 전체 거부", key="btn_auto_reject"):
                        td = api_client.list_training_data(
                            selected, status="pending", limit=10000,
                        )
                        if not api_failed(td):
                            ids = [
                                it["id"] for it in td.get("items", [])
                                if (it.get("generality_score") or 1) <= 0.3
                            ]
                            if ids:
                                api_client.review_training_data({"ids": ids, "status": "rejected"})
                                st.success(f"{len(ids)}건 자동 거부")
                                st.cache_data.clear()
                                st.rerun()

                # QA 카드 목록
                page_size = 20
                td_result = api_client.list_training_data(
                    selected, status=filter_status, source_type=filter_source,
                    limit=page_size, offset=(cur_page - 1) * page_size,
                )
                if not api_failed(td_result):
                    items = td_result.get("items", [])
                    total = td_result.get("total", 0)
                    st.caption(f"총 {total}건 (페이지 {cur_page}/{max(1, (total + page_size - 1) // page_size)})")

                    for item in items:
                        with st.container(border=True):
                            src_type = item.get("source_type", "")
                            test_tag = " 🧪" if src_type == "test_seed" else ""
                            hdr = (
                                f"📊 일관성: {quality_badge(item.get('consistency_score'))}  "
                                f"🌐 범용성: {quality_badge(item.get('generality_score'))}  "
                                f"타입: {src_type}{test_tag}"
                            )
                            if item.get("augmented_from"):
                                hdr += "  🔗 변형"
                                if item.get("augmentation_verified"):
                                    hdr += " ✅"
                            st.markdown(hdr)

                            st.markdown(f"**Q:** {item.get('question', '')}")
                            st.caption(f"A: {item.get('answer', '')[:200]}")

                            status_icon = CURATION_STATUS_ICONS.get(item.get("status", ""), "")
                            ac1, ac2, ac3 = st.columns([1, 1, 2])
                            with ac1:
                                if item.get("status") != "approved":
                                    if st.button("✅ 승인", key=f"approve_{item['id']}"):
                                        api_client.review_training_data(
                                            {"ids": [item["id"]], "status": "approved"},
                                        )
                                        st.cache_data.clear()
                                        st.rerun()
                            with ac2:
                                if item.get("status") != "rejected":
                                    if st.button("❌ 거부", key=f"reject_{item['id']}"):
                                        api_client.review_training_data(
                                            {"ids": [item["id"]], "status": "rejected"},
                                        )
                                        st.cache_data.clear()
                                        st.rerun()
                            with ac3:
                                st.caption(status_icon)

                st.markdown("---")
                st.info("빌드는 **학습/모델관리** 탭에서 진행하세요.")

            # ==== 서브탭 2: 질문 변형 ====
            with sub_aug:
                st.subheader("승인 데이터 질문 변형")
                st.caption("승인된 QA를 다양한 표현으로 변형 → Hub Search로 검증 → pending 저장")
                if st.button("🔄 질문 변형 생성 (x3)", key="btn_augment"):
                    result = api_client.augment_training_data({
                        "profile_name": selected, "max_variants": 3,
                    })
                    if not api_failed(result):
                        st.success("질문 변형 생성 시작됨 (백그라운드)")
                        st.cache_data.clear()
                        st.rerun()

                st.markdown("---")

                # 변형 데이터 리뷰
                st.subheader("변형 데이터 리뷰")
                aug_status = st.selectbox(
                    "상태", options=["pending", "approved", "rejected", None],
                    format_func=lambda x: CURATION_STATUS_ICONS.get(x, "전체") if x else "전체",
                    key="aug_status",
                )
                aug_page = st.number_input("페이지", value=1, min_value=1, key="aug_page")

                # _aug 소스타입을 API에서 직접 필터 (test_seed_aug)
                aug_data = api_client.list_training_data(
                    selected, source_type="test_seed_aug", status=aug_status,
                    limit=20, offset=(aug_page - 1) * 20,
                )
                if not api_failed(aug_data):
                    aug_items = aug_data.get("items", [])
                    aug_total = aug_data.get("total", 0)
                    st.caption(f"변형 데이터: {aug_total}건 (페이지 {aug_page}/{max(1, (aug_total + 19) // 20)})")

                    # 스마트 일괄 승인
                    if aug_total > 0:
                        if st.button("✅ 스마트 일괄 승인", key="btn_aug_smart_approve"):
                            result = api_client.smart_approve(selected, source_type="test_seed_aug")
                            if not api_failed(result):
                                st.success(
                                    f"승인: {result.get('approved', 0)}건 | "
                                    f"거부: {result.get('rejected', 0)}건"
                                )
                                st.cache_data.clear()
                                st.rerun()

                    for it in aug_items:
                        with st.container(border=True):
                            st.markdown(
                                f"🔗 변형 | {CURATION_STATUS_ICONS.get(it.get('status', ''))}"
                            )
                            st.markdown(f"**Q:** {it.get('question', '')[:70]}")
                            st.caption(f"A: {it.get('answer', '')[:100]}")
                            ac1, ac2 = st.columns(2)
                            with ac1:
                                if it.get("status") != "approved":
                                    if st.button("✅", key=f"aug_appr_{it['id']}"):
                                        api_client.review_training_data({"ids": [it["id"]], "status": "approved"})
                                        st.cache_data.clear()
                                        st.rerun()
                            with ac2:
                                if it.get("status") != "rejected":
                                    if st.button("❌", key=f"aug_rej_{it['id']}"):
                                        api_client.review_training_data({"ids": [it["id"]], "status": "rejected"})
                                        st.cache_data.clear()
                                        st.rerun()

            # ==== 서브탭 3: 용어 학습 ====
            with sub_term:
                st.subheader("PBU 도메인 용어 QA")
                st.caption("PBU_ 도메인 표준 용어 → '~가 뭐야?' QA 자동 생성 (Kiwi 형태소 분석 일반어 필터)")
                tc1, tc2 = st.columns(2)
                with tc1:
                    if st.button("📚 용어 QA 생성", key="btn_term_qa"):
                        result = api_client.generate_term_qa({
                            "profile_name": selected, "top_n": 772,
                        })
                        if not api_failed(result):
                            st.success("용어 QA 생성 시작됨")
                            st.cache_data.clear()
                            st.rerun()
                with tc2:
                    if st.button("🧹 용어 QA 삭제", key="btn_del_term"):
                        result = api_client.delete_by_source_type(selected, "term_qa")
                        if not api_failed(result):
                            st.success(f"용어 QA {result.get('deleted', 0)}건 삭제")
                        st.cache_data.clear()
                        st.rerun()

                st.markdown("---")

                # 용어 QA 리뷰
                term_filter = st.selectbox(
                    "상태", options=["pending", "approved", "rejected", None],
                    format_func=lambda x: CURATION_STATUS_ICONS.get(x, "전체") if x else "전체",
                    key="term_status",
                )
                term_page = st.number_input("페이지", value=1, min_value=1, key="term_page")
                term_data = api_client.list_training_data(
                    selected, source_type="term_qa", status=term_filter,
                    limit=20, offset=(term_page - 1) * 20,
                )
                if not api_failed(term_data):
                    term_items = term_data.get("items", [])
                    term_total = term_data.get("total", 0)
                    st.caption(f"용어 QA: {term_total}건 (페이지 {term_page}/{max(1, (term_total + 19) // 20)})")

                    # 일괄 버튼
                    ta1, ta2 = st.columns(2)
                    with ta1:
                        if st.button("✅ 스마트 일괄 승인", key="btn_term_approve_all"):
                            result = api_client.smart_approve(selected, source_type="term_qa")
                            if not api_failed(result):
                                st.success(
                                    f"승인: {result.get('approved', 0)}건 | "
                                    f"거부: {result.get('rejected', 0)}건 | "
                                    f"정리: {result.get('cleaned', 0)}건"
                                )
                                st.cache_data.clear()
                                st.rerun()
                    with ta2:
                        if st.button("❌ 전체 거부", key="btn_term_reject_all"):
                            td = api_client.list_training_data(selected, source_type="term_qa", status="pending", limit=10000)
                            if not api_failed(td):
                                ids = [it["id"] for it in td.get("items", [])]
                                if ids:
                                    api_client.review_training_data({"ids": ids, "status": "rejected"})
                                    st.success(f"{len(ids)}건 거부")
                                    st.cache_data.clear()
                                    st.rerun()

                    for it in term_items:
                        with st.container(border=True):
                            st.markdown(
                                f"📚 {CURATION_STATUS_ICONS.get(it.get('status', ''))} | "
                                f"{it.get('kb_id', '')}"
                            )
                            st.markdown(f"**Q:** {it.get('question', '')[:60]}")
                            st.caption(f"A: {it.get('answer', '')[:100]}")
                            ac1, ac2 = st.columns(2)
                            with ac1:
                                if it.get("status") != "approved":
                                    if st.button("✅", key=f"term_appr_{it['id']}"):
                                        api_client.review_training_data({"ids": [it["id"]], "status": "approved"})
                                        st.cache_data.clear()
                                        st.rerun()
                            with ac2:
                                if it.get("status") != "rejected":
                                    if st.button("❌", key=f"term_rej_{it['id']}"):
                                        api_client.review_training_data({"ids": [it["id"]], "status": "rejected"})
                                        st.cache_data.clear()
                                        st.rerun()

            # ── 초기화 (데이터셋 서브탭 하단) ──
            with sub_data:
                st.markdown("---")
                st.subheader("초기화")
            st.caption("테스트 데이터 또는 빌드를 삭제합니다. 운영 데이터에는 영향을 주지 않습니다.")
            reset_col1, reset_col2 = st.columns(2)
            with reset_col1:
                if st.button("🧹 테스트 데이터 삭제", key="btn_del_test_data"):
                    st.session_state["confirm_del_test"] = True
            with reset_col2:
                st.caption("source_type='test_seed'인 데이터만 삭제")

            if st.session_state.get("confirm_del_test"):
                st.warning("테스트 시드 데이터를 모두 삭제하시겠습니까?")
                cc1, cc2 = st.columns(2)
                with cc1:
                    if st.button("확인 삭제", type="primary", key="btn_confirm_del_test"):
                        result = api_client.delete_by_source_type(selected, "test_seed")
                        if not api_failed(result):
                            st.success(f"테스트 데이터 {result.get('deleted', 0)}건 삭제")
                            st.session_state["confirm_del_test"] = False
                            st.cache_data.clear()
                            st.rerun()
                with cc2:
                    if st.button("취소", key="btn_cancel_del_test"):
                        st.session_state["confirm_del_test"] = False
                        st.rerun()


# =============================================================================
# Tab 4: 엣지 서버
# =============================================================================
with tab_servers:
    st.caption("매장 엣지 서버의 상태를 모니터링하고, 모델/앱 업데이트를 요청합니다.")

    if not _profiles_ok:
        st.error("API 연결 실패")
    else:
        profiles = _all_profiles
        enabled = _enabled_profiles

        if not enabled:
            st.info("활성화된 프로필이 없습니다.")
        else:
            selected = st.selectbox("프로필", options=enabled, key="server_profile")

            # ── Fleet 현황 ──
            fleet = api_client.get_fleet_stats(selected)
            if not api_failed(fleet):
                fc1, fc2, fc3, fc4 = st.columns(4)
                with fc1:
                    st.metric("전체", fleet.get("total", 0))
                with fc2:
                    st.metric("🟢 온라인", fleet.get("online", 0))
                with fc3:
                    st.metric("⚪ 오프라인", fleet.get("offline", 0))
                with fc4:
                    st.metric("🔴 에러", fleet.get("error", 0))

            st.markdown("---")

            # ── 매장 등록 (출고 전) ──
            st.subheader("매장 등록")
            st.caption("장비 출고 전 매장을 사전 등록합니다. 등록 후 출고 설정을 다운로드하여 장비에 세팅합니다.")

            with st.expander("➕ 새 매장 등록", expanded=False):
                rc1, rc2 = st.columns(2)
                with rc1:
                    new_store_id = st.text_input(
                        "매장 ID", placeholder="gangnam-01", key="reg_store_id",
                        help="영소문자, 숫자, 하이픈만 가능 (3~50자). 예: gangnam-01, busan-haeundae",
                    )
                with rc2:
                    new_display = st.text_input("매장명", placeholder="강남1호점", key="reg_display")

                import re as _re
                valid_id = bool(new_store_id and _re.match(r"^[a-z0-9][a-z0-9_-]{1,48}[a-z0-9]$", new_store_id))
                if new_store_id and not valid_id:
                    st.error("매장 ID는 영소문자, 숫자, 하이픈만 가능합니다 (3~50자)")
                if st.button("등록", key="reg_btn", type="primary", disabled=not valid_id):
                    reg_result = api_client.register_edge_server(
                        new_store_id, selected, new_display,
                    )
                    if not api_failed(reg_result):
                        st.success(f"✅ 매장 **{new_store_id}** 등록 완료")
                        st.warning("⚠️ 아래 출고 명령어는 이 화면에서만 확인 가능합니다. 반드시 복사하세요.")
                        st.markdown("**출고 명령어** (본사에서 장비에 실행):")
                        st.code(reg_result.get("provision_command", ""), language="bash")
                    else:
                        st.error(f"등록 실패: {reg_result.get('detail', reg_result)}")

            # 출고 설정 조회 (기존 매장)
            with st.expander("📋 출고 설정 조회", expanded=False):
                prov_store = st.text_input("매장 ID 입력", key="prov_store_id")
                if st.button("조회", key="prov_btn", disabled=not prov_store):
                    prov = api_client.get_provision_config(prov_store)
                    if not api_failed(prov):
                        st.json(prov.get("env", {}))
                        st.markdown("**출고 명령어:**")
                        st.code(prov.get("provision_command", ""), language="bash")
                    else:
                        st.error(f"조회 실패: {prov.get('detail', prov)}")

            st.markdown("---")

            # ── 서버 목록 ──
            st.subheader("서버 목록")
            sf1, sf2 = st.columns(2)
            with sf1:
                server_status_filter = st.selectbox(
                    "상태 필터",
                    options=[None, "online", "offline", "error"],
                    format_func=lambda x: "전체" if x is None else EDGE_SERVER_STATUS_ICONS.get(x, x) + f" {x}",
                    key="srv_status",
                )

            servers_result = api_client.list_edge_servers(
                profile_name=selected, status=server_status_filter,
            )
            if not api_failed(servers_result):
                servers = servers_result.get("items", [])
                if not servers:
                    st.info("등록된 서버가 없습니다.")

                for srv in servers:
                    status_icon = EDGE_SERVER_STATUS_ICONS.get(srv.get("status", ""), "❓")
                    os_badge = f"[{srv.get('os_type', '?')}]" if srv.get("os_type") else ""
                    display = srv.get("display_name") or srv.get("store_id", "")

                    with st.container(border=True):
                        st.markdown(
                            f"{status_icon} **{display}** ({srv.get('store_id', '')}) {os_badge}"
                        )
                        st.caption(
                            f"앱: {srv.get('app_version', '?')}  "
                            f"모델: {srv.get('model_version', '?')}  "
                            f"지연: {srv.get('avg_latency_ms', 0)}ms  "
                            f"RAM: {srv.get('ram_used_mb', '?')}/{srv.get('ram_total_mb', '?')}MB"
                        )

                        hb = srv.get("last_heartbeat", "")
                        sr = srv.get("success_rate")
                        sr_str = f"성공률: {sr:.1%}" if sr is not None else ""
                        st.caption(
                            f"마지막 heartbeat: {hb[:16] if hb else '없음'}  "
                            f"질의: {srv.get('total_queries', 0):,}건  "
                            f"{sr_str}"
                        )

                        bc1, bc2, bc3 = st.columns(3)
                        with bc1:
                            if st.button("🔄 모델 업데이트", key=f"upd_model_{srv['store_id']}"):
                                api_client.request_server_update(srv["store_id"], "model")
                                st.success("모델 업데이트 요청됨 (다음 sync 시 반영)")
                                st.cache_data.clear()
                        with bc2:
                            if st.button("📦 앱 업데이트", key=f"upd_app_{srv['store_id']}"):
                                api_client.request_server_update(srv["store_id"], "app")
                                st.success("앱 업데이트 요청됨 (다음 sync 시 반영)")
                                st.cache_data.clear()
                        with bc3:
                            if st.button("🗑️ 등록 해제", key=f"del_srv_{srv['store_id']}"):
                                api_client.delete_edge_server(srv["store_id"])
                                st.cache_data.clear()
                                st.rerun()

            st.markdown("---")

            # ── 앱 버전 관리 ──
            st.subheader("앱 버전 관리")
            st.caption(
                "엣지 앱(server.py / sync.py)은 중앙 API의 edge-files 엔드포인트에서 "
                "소스 파일로 제공됩니다. 여기서는 manifest의 버전 태그만 갱신하고, "
                "엣지는 다음 heartbeat(5분 이내)에 자동으로 재다운로드 + 재시작합니다."
            )

            # 현재 앱 버전 표시
            app_info = api_client.get_app_info(selected)
            if not api_failed(app_info):
                ai1, ai2 = st.columns(2)
                with ai1:
                    st.metric("현재 앱 버전", app_info.get("app_version") or "미배포")
                with ai2:
                    st.metric("현재 모델 버전", app_info.get("model_version") or "미배포")

            with st.expander("앱 버전 태그 갱신", expanded=False):
                new_ver = st.text_input(
                    "새 버전 태그 (예: v1.0.1 또는 git SHA)",
                    value="",
                    key="new_app_version",
                    placeholder="v1.0.1",
                )
                if st.button("버전 태그 적용", key="btn_set_app_version",
                             disabled=not new_ver.strip()):
                    result = api_client.set_app_version(selected, new_ver.strip())
                    if not api_failed(result):
                        st.success(f"앱 버전 태그 갱신 완료: {new_ver.strip()}")
                        st.info("각 매장에 '📦 앱 업데이트' 요청을 보내거나, "
                                "'구버전 앱 전체 업데이트 요청' 버튼으로 일괄 적용하세요.")
                        st.cache_data.clear()
                st.caption(
                    "이 작업은 S3 manifest.json의 `app_version` 필드만 업데이트합니다. "
                    "실제 소스 파일 재다운로드는 각 엣지가 heartbeat 시 자동으로 수행합니다."
                )

            st.markdown("---")

            # ── 새 서버 등록 ──
            st.subheader("새 서버 등록")
            with st.expander("설치 명령어 생성", expanded=False):
                new_store = st.text_input("매장 ID", placeholder="gangnam-01", key="new_store_id")
                if new_store:
                    import secrets
                    if f"api_key_{new_store}" not in st.session_state:
                        st.session_state[f"api_key_{new_store}"] = secrets.token_urlsafe(32)
                    api_key = st.session_state[f"api_key_{new_store}"]

                    st.markdown("**Linux / macOS:**")
                    linux_cmd = (
                        f"curl -sL https://s3.../install.sh | \\\n"
                        f"  STORE_ID={new_store} \\\n"
                        f"  EDGE_API_KEY={api_key} \\\n"
                        f"  MANIFEST_URL=https://s3.../manifest.json \\\n"
                        f"  CENTRAL_API_URL=https://knowledge-api.gs.internal \\\n"
                        f"  bash"
                    )
                    st.code(linux_cmd, language="bash")

                    st.markdown("**Windows (PowerShell):**")
                    win_cmd = (
                        f'$env:STORE_ID="{new_store}"\n'
                        f'$env:EDGE_API_KEY="{api_key}"\n'
                        f'$env:MANIFEST_URL="https://s3.../manifest.json"\n'
                        f'$env:CENTRAL_API_URL="https://knowledge-api.gs.internal"\n'
                        f"irm https://s3.../install.ps1 | iex"
                    )
                    st.code(win_cmd, language="powershell")

            st.markdown("---")

            # ── 일괄 작업 ──
            st.subheader("일괄 작업")
            st.caption("대상: 최신 버전이 아닌 온라인 서버")
            ba1, ba2 = st.columns(2)
            with ba1:
                if st.button("구버전 모델 전체 업데이트 요청", key="btn_bulk_model"):
                    result = api_client.bulk_request_update(selected, "model")
                    if not api_failed(result):
                        st.success(f"{result.get('updated', 0)}대 업데이트 요청")
                        st.cache_data.clear()
            with ba2:
                if st.button("구버전 앱 전체 업데이트 요청", key="btn_bulk_app"):
                    result = api_client.bulk_request_update(selected, "app")
                    if not api_failed(result):
                        st.success(f"{result.get('updated', 0)}대 업데이트 요청")
                        st.cache_data.clear()
            st.caption("ⓘ 업데이트 요청은 다음 sync 주기(최대 5분)에 엣지 서버가 자동 반영합니다.")


# =============================================================================
# Tab 5: 운영 — 엣지 로그 + 재학습
# =============================================================================
with tab_ops:
    st.caption(
        "매장 엣지 서버의 실사용 로그를 수집하고, 실패 질문에 정답을 추가하여 재학습합니다."
    )

    if not _profiles_ok:
        st.error("API 연결 실패")
    else:
        profiles = _all_profiles
        enabled = _enabled_profiles

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
            selected_for_retrain = []

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


# =============================================================================
# Tab 6: 베이스 모델 레지스트리 관리
# =============================================================================
with tab_base_models:
    st.caption(
        "프로필 생성 시 선택 가능한 베이스 모델 목록을 관리합니다. "
        "여기서 추가/수정하면 `설정` 탭 드롭다운에 즉시 반영됩니다 "
        "(캐시 TTL 5분)."
    )

    # Admin 은 disabled 행도 봐야 함
    all_models_result = api_client.list_distill_base_models(enabled_only=False)
    if api_failed(all_models_result):
        st.error("API 연결 실패 — 베이스 모델 레지스트리를 불러올 수 없습니다.")
        if st.button("🔄 재시도", key="bm_retry"):
            st.cache_data.clear()
            st.rerun()
    else:
        all_models = all_models_result.get("models", [])

        # ── 현황 테이블 ──
        st.markdown("#### 현재 등록된 모델")
        if not all_models:
            st.info("등록된 베이스 모델이 없습니다. 아래에서 새로 추가하세요.")
        else:
            table_rows = [
                {
                    "정렬": m.get("sort_order", 0),
                    "hf_id": m["hf_id"],
                    "표시명": m.get("display_name", ""),
                    "파라미터": m.get("params") or "",
                    "라이선스": m.get("license") or "",
                    "상업": "✓" if m.get("commercial_use") else "",
                    "검증": "✓" if m.get("verified") else "",
                    "enabled": "✓" if m.get("enabled") else "",
                    "주의사항": (m.get("notes") or "")[:60],
                }
                for m in all_models
            ]
            st.dataframe(table_rows, use_container_width=True, hide_index=True)

        st.divider()

        # ── 편집/추가 폼 ──
        st.markdown("#### 추가 / 편집")

        edit_options = ["(새로 추가)"] + [m["hf_id"] for m in all_models]
        edit_target = st.selectbox(
            "편집 대상", options=edit_options, key="bm_edit_target",
        )

        editing_bm: dict = {}
        if edit_target != "(새로 추가)":
            editing_bm = next(
                (m for m in all_models if m["hf_id"] == edit_target), {},
            )

        with st.form("base_model_form", clear_on_submit=False):
            bc1, bc2 = st.columns([2, 1])
            with bc1:
                form_bm_hf_id = st.text_input(
                    "HF ID (org/repo)",
                    value=editing_bm.get("hf_id", ""),
                    disabled=bool(editing_bm),  # PK — 편집 시 수정 불가
                    placeholder="google/gemma-3-4b-it",
                    help="Hugging Face 모델 경로. 저장 후 수정 불가.",
                )
                form_bm_display = st.text_input(
                    "표시명",
                    value=editing_bm.get("display_name", ""),
                    placeholder="Gemma 3 4B it",
                )
                form_bm_notes = st.text_area(
                    "주의사항 / 라벨 (notes)",
                    value=editing_bm.get("notes") or "",
                    height=80,
                    help="드롭다운에 함께 표시됩니다.",
                )
            with bc2:
                form_bm_params = st.text_input(
                    "파라미터 수",
                    value=editing_bm.get("params") or "",
                    placeholder="4B",
                )
                form_bm_license = st.text_input(
                    "라이선스",
                    value=editing_bm.get("license") or "",
                    placeholder="Apache 2.0",
                )
                form_bm_sort_order = st.number_input(
                    "정렬 순서",
                    value=editing_bm.get("sort_order", 0),
                    min_value=0, max_value=10000, step=10,
                    help="낮을수록 드롭다운 상단.",
                )
                form_bm_commercial = st.checkbox(
                    "상업 배포 허용",
                    value=editing_bm.get("commercial_use", False),
                )
                form_bm_verified = st.checkbox(
                    "엣지 스택 검증 완료",
                    value=editing_bm.get("verified", False),
                    help="llama.cpp GGUF + LoRA SFT dry-run 통과.",
                )
                form_bm_enabled = st.checkbox(
                    "드롭다운 노출 (enabled)",
                    value=editing_bm.get("enabled", True),
                )

            bs1, bs2, _ = st.columns([1, 1, 4])
            with bs1:
                bm_submitted = st.form_submit_button("💾 저장", type="primary")
            with bs2:
                bm_delete_clicked = st.form_submit_button(
                    "🗑 삭제",
                    type="secondary",
                    disabled=not editing_bm,
                    help="편집 중인 행만 삭제 가능.",
                )

            if bm_submitted:
                if not form_bm_hf_id or not form_bm_display:
                    st.error("hf_id 와 표시명은 필수입니다.")
                else:
                    body = {
                        "hf_id": form_bm_hf_id.strip(),
                        "display_name": form_bm_display.strip(),
                        "params": (form_bm_params.strip() or None),
                        "license": (form_bm_license.strip() or None),
                        "commercial_use": form_bm_commercial,
                        "verified": form_bm_verified,
                        "notes": form_bm_notes.strip(),
                        "enabled": form_bm_enabled,
                        "sort_order": int(form_bm_sort_order),
                    }
                    result = api_client.upsert_distill_base_model(body)
                    if api_failed(result):
                        st.error(f"저장 실패: {result.get('error', '')}")
                    else:
                        st.success(f"저장 완료: {form_bm_hf_id}")
                        api_client.list_distill_base_models.clear()
                        st.rerun()

            if bm_delete_clicked and editing_bm:
                result = api_client.delete_distill_base_model(editing_bm["hf_id"])
                if api_failed(result):
                    st.error(f"삭제 실패: {result.get('error', '')}")
                else:
                    st.success(f"삭제 완료: {editing_bm['hf_id']}")
                    api_client.list_distill_base_models.clear()
                    st.rerun()

        st.caption(
            "💡 제거 대신 **enabled 토글**을 권장합니다 — 실제 삭제 시 이 모델을 "
            "참조하는 기존 프로필은 드롭다운에서 'legacy' 로 표시됩니다."
        )
