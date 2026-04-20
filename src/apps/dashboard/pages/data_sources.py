"""데이터소스 관리

커넥터(Confluence, Jira, Git, Teams, GWiki) CRUD 및 동기화 관리.

Created: 2026-02-20
"""

import streamlit as st

st.set_page_config(page_title="데이터소스 관리", page_icon="📁", layout="wide")

from components.deprecate_banner import deprecated_for

deprecated_for("/admin/sources", "데이터 소스")



from components.sidebar import hide_default_nav, render_sidebar
from services import api_client
from services.api_client import api_failed

hide_default_nav()
render_sidebar(show_admin=True)

st.title("📁 데이터소스 관리")

# =============================================================================
# 데이터소스 목록
# =============================================================================
sources_result = api_client.list_data_sources()

if api_failed(sources_result):
    st.error("API 연결 실패. 재시도 해주세요.")
    if st.button("🔄 재시도", key="retry_sources"):
        st.cache_data.clear()
        st.rerun()
    st.stop()

sources = sources_result.get("items", sources_result.get("sources", []))

# ── 커넥터 타입별 아이콘 (로컬 지원 타입만) ──
connector_icons = {
    "file_upload": "📄",
    "crawl_result": "🔍",
}

# ── 메트릭 요약 ──
col1, col2, col3, col4 = st.columns(4)
with col1:
    st.metric("총 데이터소스", f"{len(sources)}개")
with col2:
    healthy = sum(1 for s in sources if s.get("health_status", s.get("status", "")) in ("HEALTHY", "ACTIVE", "CONNECTED"))
    st.metric("정상", f"{healthy}개")
with col3:
    errored = sum(1 for s in sources if s.get("health_status", s.get("status", "")) in ("ERROR", "DISCONNECTED"))
    st.metric("오류", f"{errored}개")
with col4:
    syncing = sum(1 for s in sources if s.get("health_status", s.get("status", "")) == "SYNCING")
    st.metric("동기화 중", f"{syncing}개")

st.markdown("---")

# =============================================================================
# 데이터소스 테이블
# =============================================================================
st.subheader("데이터소스 목록")

if sources:
    for source in sources:
        source_id = source.get("source_id", source.get("id", "-"))
        source_name = source.get("name", "-")
        connector_type = source.get("connector_type", source.get("source_type", source.get("type", "-")))
        health = source.get("health_status", source.get("status", "UNKNOWN")).upper()
        last_sync = source.get("last_synced_at", source.get("last_sync", "-"))
        doc_count = source.get("document_count", 0)

        icon = connector_icons.get(connector_type, "📁")
        health_badge = {
            "HEALTHY": "🟢 정상",
            "ACTIVE": "🟢 정상",
            "CONNECTED": "🟢 연결됨",
            "WARNING": "🟡 주의",
            "ERROR": "🔴 오류",
            "DISCONNECTED": "🔴 연결 해제",
            "SYNCING": "🔵 동기화 중",
            "INACTIVE": "⚪ 비활성",
            "UNKNOWN": "⚪ 미확인",
        }.get(health, f"⚪ {health}")

        with st.container(border=True):
            col_info, col_status, col_actions = st.columns([3, 2, 2])

            with col_info:
                kb_name = source.get("kb_name", source.get("kb_id", ""))
                st.markdown(f"**{icon} {source_name}**")
                kb_info = f" | KB: {kb_name}" if kb_name else ""
                st.caption(f"타입: {connector_type} | 문서: {doc_count:,}개{kb_info} | 마지막 동기화: {last_sync}")

            with col_status:
                st.markdown(f"상태: {health_badge}")

            with col_actions:
                btn_col1, btn_col2, btn_col3, btn_col4 = st.columns(4)
                with btn_col1:
                    if st.button("🔄 증분", key=f"sync_{source_id}"):
                        with st.spinner("증분 동기화 트리거 중..."):
                            result = api_client.trigger_data_source_sync(source_id, sync_mode="resume")
                            if api_failed(result):
                                st.error("동기화 트리거 실패")
                            else:
                                st.success("증분 동기화가 시작되었습니다.")
                                st.cache_data.clear()
                                st.rerun()
                with btn_col2:
                    if st.button("🔁 전체", key=f"full_sync_{source_id}"):
                        with st.spinner("전체 동기화 트리거 중..."):
                            result = api_client.trigger_data_source_sync(source_id, sync_mode="full")
                            if api_failed(result):
                                st.error("동기화 트리거 실패")
                            else:
                                st.success("전체 동기화가 시작되었습니다. (기존 체크포인트 무시)")
                                st.cache_data.clear()
                                st.rerun()
                with btn_col3:
                    if st.button("📋 상세", key=f"detail_{source_id}"):
                        st.session_state[f"show_detail_{source_id}"] = not st.session_state.get(
                            f"show_detail_{source_id}", False
                        )
                with btn_col4:
                    if st.button("🗑️ 삭제", key=f"delete_{source_id}"):
                        st.session_state[f"confirm_delete_{source_id}"] = True

            # ── 상세 정보 확장 ──
            if st.session_state.get(f"show_detail_{source_id}", False):
                status_result = api_client.get_data_source_status(source_id)
                if not api_failed(status_result):
                    st.json(status_result)
                else:
                    st.warning("상세 정보를 가져올 수 없습니다.")

            # ── 삭제 확인 ──
            if st.session_state.get(f"confirm_delete_{source_id}", False):
                st.warning(f"**{source_name}** 데이터소스를 삭제하시겠습니까?")
                c1, c2 = st.columns(2)
                with c1:
                    if st.button("확인 삭제", key=f"confirm_del_{source_id}", type="primary"):
                        result = api_client.delete_data_source(source_id)
                        if api_failed(result):
                            st.error("삭제 실패")
                        else:
                            st.success("삭제 완료")
                            st.session_state[f"confirm_delete_{source_id}"] = False
                            st.cache_data.clear()
                            st.rerun()
                with c2:
                    if st.button("취소", key=f"cancel_del_{source_id}"):
                        st.session_state[f"confirm_delete_{source_id}"] = False
                        st.rerun()
else:
    st.info("등록된 데이터소스가 없습니다.")

st.markdown("---")

# =============================================================================
# 파일 기반 데이터소스 추가
# =============================================================================
with st.expander("📄 파일 업로드 인제스천", expanded=False):
    st.caption("파일을 업로드하면 S3에 저장 후 자동으로 인제스천이 시작됩니다.")

    uploaded_files = st.file_uploader(
        "파일 선택",
        type=["pptx", "ppt", "pdf", "docx", "doc", "xlsx", "xls", "xlsm", "txt", "md", "csv", "json", "xml", "yaml", "yml", "jpg", "png", "jpeg"],
        accept_multiple_files=True,
        help="지원: PPTX, PDF, DOCX, XLSX, TXT, MD, CSV, JSON, XML, YAML, JPG, PNG, JPEG",
    )

    # KB 선택: 기존 KB 드롭다운 또는 신규 생성
    kb_mode = st.radio(
        "KB 선택 모드",
        ["기존 KB에 동기화", "신규 KB 생성"],
        horizontal=True,
        key="upload_kb_mode",
    )

    if kb_mode == "기존 KB에 동기화":
        # 기존 KB 목록 조회
        kb_list_result = api_client.list_kbs()
        if api_failed(kb_list_result):
            kb_options = []
        else:
            kbs = kb_list_result.get("kbs", kb_list_result.get("items", []))
            kb_options = [
                f"{kb.get('kb_id', kb.get('id', ''))} — {kb.get('name', '')}"
                for kb in kbs if kb.get("kb_id") or kb.get("id")
            ]

        if kb_options:
            selected_kb = st.selectbox("KB 선택", options=kb_options, key="upload_kb_select")
            file_kb_id = selected_kb.split(" — ")[0] if selected_kb else ""
        else:
            st.warning("등록된 KB가 없습니다. '신규 KB 생성'을 선택하세요.")
            file_kb_id = ""
        file_kb_name = None
        create_new_kb = False
    else:
        col_id, col_name = st.columns(2)
        with col_id:
            file_kb_id = st.text_input("KB ID", placeholder="my-new-kb", key="upload_kb_id",
                                       help="새로 생성할 KB ID (중복 시 오류)")
        with col_name:
            file_kb_name = st.text_input("KB 이름", placeholder="My Knowledge Base", key="upload_kb_name",
                                         help="KB 표시 이름")

        # Tier 선택
        file_tier = st.selectbox(
            "KB Tier",
            options=["team", "bu", "global"],
            format_func={"team": "👥 Team", "bu": "🏢 BU (사업부)", "global": "🌐 Global"}.get,
            key="upload_kb_tier",
            help="GLOBAL: 전사 공통 | BU: 사업부별 (organization_id 필수) | TEAM: 팀별 비공개",
        )

        # BU tier 선택 시 organization_id 필수 입력
        file_org_id = None
        if file_tier == "bu":
            file_org_id = st.text_input("Organization ID", placeholder="cvs, sm, hs 등",
                                        key="upload_org_id", help="BU tier 필수: 사업부 식별자")

        create_new_kb = True

    file_vision = st.checkbox("Vision 분석 (문서 내 이미지)", value=False, key="upload_vision",
                              help="PPTX/PDF/DOCX 내 이미지를 CV Pipeline으로 분석하여 그래프 추출")

    if st.button("인제스천 시작", type="primary", key="btn_upload_ingest"):
        if not uploaded_files or not file_kb_id:
            st.error("파일과 KB ID는 필수입니다.")
        elif create_new_kb and file_tier == "bu" and not file_org_id:
            st.error("BU tier는 Organization ID가 필수입니다.")
        else:
            file_label = ", ".join(f.name for f in uploaded_files)
            with st.spinner(f"'{file_label}' 업로드 및 인제스천 시작 중..."):
                result = api_client.upload_and_ingest_multi(
                    files=[(f.name, f.getvalue()) for f in uploaded_files],
                    kb_id=file_kb_id,
                    kb_name=file_kb_name if file_kb_name else None,
                    enable_vision=file_vision,
                    create_new_kb=create_new_kb,
                    tier=file_tier if create_new_kb else None,
                    organization_id=file_org_id if create_new_kb else None,
                )
                if api_failed(result):
                    if result.get("_conflict"):
                        st.error("이미 존재하는 KB ID입니다. '기존 KB에 동기화'를 선택하거나 다른 ID를 사용하세요.")
                    else:
                        st.error(f"실패: {result.get('error', '')}")
                else:
                    # 단일 파일 응답 (workflow_id 직접) 또는 다중 파일 응답 (files 배열)
                    file_results = result.get("files", [result])
                    for fr in file_results:
                        wf_id = fr.get("workflow_id", "")
                        s3_uri = fr.get("s3_uri", "")
                        kb_action = fr.get("kb_action", "")
                        fname = fr.get("filename", "")
                        action_label = {"created": "신규 생성", "synced": "동기화"}.get(kb_action, kb_action)
                        st.success(f"인제스천 시작: {wf_id} (KB: {action_label}) {fname}")
                        if s3_uri:
                            st.caption(f"S3: {s3_uri}")

# =============================================================================
# 데이터소스 추가 폼
# =============================================================================
with st.expander("➕ 새 데이터소스 추가", expanded=False):
    with st.form("add_data_source_form"):
        ds_type = st.selectbox(
            "커넥터 타입",
            options=["file_upload", "crawl_result", "git"],
            format_func=lambda x: {
                "file_upload": "📄 파일 업로드",
                "crawl_result": "🔍 크롤 결과 (JSON/JSONL)",
                "git": "🐙 Git 저장소",
            }.get(x, x),
        )
        ds_kb_id = st.text_input("KB ID", placeholder="기존 또는 신규 KB ID")
        ds_url = st.text_input(
            "연결 URL (선택)",
            placeholder="예: https://wiki.example.com (git 타입은 아래 저장소 설정 사용)",
        )
        ds_description = st.text_area("설명 (선택)", placeholder="데이터소스에 대한 설명")

        st.markdown("**🐙 Git 저장소 설정** (커넥터 타입이 `git`일 때만 사용)")
        git_repo_url = st.text_input(
            "Repository URL",
            placeholder="https://github.com/owner/repo 또는 git@github.com:owner/repo.git",
            key="ds_git_repo_url",
        )
        git_col1, git_col2 = st.columns(2)
        with git_col1:
            git_branch = st.text_input(
                "Branch / Tag (선택)", placeholder="main",
                key="ds_git_branch",
            )
        with git_col2:
            git_subdir = st.text_input(
                "Subdir (선택)", placeholder="예: kr",
                help="저장소 내 특정 하위 경로만 수집",
                key="ds_git_subdir",
            )
        git_include = st.text_area(
            "Include globs (줄 단위)",
            value="**/*.md",
            height=80,
            key="ds_git_include",
            help="한 줄에 하나씩. 예: kr/**/*.md",
        )
        git_exclude = st.text_area(
            "Exclude globs (줄 단위)",
            value=".git/**\nnode_modules/**\nREADME.md",
            height=80,
            key="ds_git_exclude",
        )
        git_auth_token_env = st.text_input(
            "Private repo 인증 환경변수 (선택)",
            placeholder="예: GITHUB_PAT",
            help="지정한 환경변수의 PAT를 HTTPS URL에 주입합니다. Public repo면 비워두세요.",
            key="ds_git_auth_env",
        )

        submitted = st.form_submit_button("추가", type="primary")
        if submitted:
            if not ds_type or not ds_kb_id:
                st.error("커넥터 타입과 KB ID는 필수입니다.")
            elif ds_type == "git" and not git_repo_url:
                st.error("Git 커넥터는 Repository URL이 필수입니다.")
            else:
                ds_name = f"{ds_type}_{ds_kb_id}"
                body: dict = {
                    "name": ds_name,
                    "source_type": ds_type,
                    "kb_id": ds_kb_id,
                    "metadata": {
                        "url": ds_url if ds_url else None,
                        "description": ds_description if ds_description else None,
                    },
                }
                if ds_type == "git":
                    def _split_globs(text: str) -> list[str]:
                        return [ln.strip() for ln in (text or "").splitlines() if ln.strip()]

                    crawl_config: dict = {
                        "repo_url": git_repo_url.strip(),
                        "include_globs": _split_globs(git_include) or ["**/*.md"],
                        "exclude_globs": _split_globs(git_exclude),
                    }
                    if git_branch.strip():
                        crawl_config["branch"] = git_branch.strip()
                    if git_subdir.strip():
                        crawl_config["subdir"] = git_subdir.strip()
                    if git_auth_token_env.strip():
                        crawl_config["auth_token_env"] = git_auth_token_env.strip()
                    body["crawl_config"] = crawl_config

                result = api_client.create_data_source(body)
                if api_failed(result):
                    st.error("데이터소스 추가 실패")
                else:
                    st.success(f"데이터소스 '{ds_name}'이(가) 추가되었습니다.")
                    st.cache_data.clear()
                    st.rerun()
