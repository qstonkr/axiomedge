"""지식 검색 (Hub Search API)

Architecture: Dashboard -> POST /api/v1/search/hub -> FederatedSearchService
             -> HubSearchAnswerService -> EXAONE 3.5

채팅 인터페이스로 지식 검색 및 답변 생성.
2-phase search: 1) 문서 검색 (빠름) 2) AI 답변 생성 (선택)

Created: 2026-02-20
Updated: 2026-03-14 - 2-phase 검색, 검색 모드 선택, 검색 시간 최적화, 세션 영속성
"""

import time
import uuid

import streamlit as st

st.set_page_config(page_title="지식 검색", page_icon="💬", layout="wide")


from components.constants import TIER_ICONS
from components.sidebar import render_sidebar
from components.metric_cards import get_confidence_badge
from services import api_client
from services.api_client import api_failed
from services.metrics import metrics
from services.session_store import get_session_store
from services.validators import sanitize_input, validate_query

render_sidebar()

# ---------------------------------------------------------------------------
# 세션 초기화 + 영속성 복원
# ---------------------------------------------------------------------------
if "chat_session_id" not in st.session_state:
    st.session_state.chat_session_id = str(uuid.uuid4())
if "chat_messages" not in st.session_state:
    st.session_state.chat_messages = []
if "feedback_submitted" not in st.session_state:
    st.session_state.feedback_submitted = {}
if "show_error_report" not in st.session_state:
    st.session_state.show_error_report = None
if "session_restored" not in st.session_state:
    st.session_state.session_restored = False


def _get_user_id() -> str:
    """Return authenticated user ID, or empty string if unavailable."""
    try:
        from services.auth import get_authenticated_user

        user = get_authenticated_user()
        if user and hasattr(user, "user_id") and user.user_id:
            return user.user_id
    except Exception:
        pass
    return ""


def _try_restore_session() -> None:
    """Attempt to restore chat messages from persistent storage on first load."""
    if st.session_state.session_restored:
        return
    st.session_state.session_restored = True

    user_id = _get_user_id()
    if not user_id:
        return

    store = get_session_store()
    messages = store.load_messages(st.session_state.chat_session_id, user_id)
    if messages and not st.session_state.chat_messages:
        st.session_state.chat_messages = messages


def _persist_messages() -> None:
    """Save current chat messages to persistent storage (fire-and-forget)."""
    user_id = _get_user_id()
    if not user_id:
        return
    store = get_session_store()
    store.save_messages(
        st.session_state.chat_session_id,
        user_id,
        st.session_state.chat_messages,
    )


# Restore on first page load
_try_restore_session()


# ---------------------------------------------------------------------------
# 사이드바: KB 선택 및 모드
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown("---")
    st.subheader("검색 설정")

    # 검색 모드 선택
    search_mode = st.radio(
        "검색 모드",
        ["AI 답변", "빠른 검색"],
        index=0,
        help="**AI 답변**: EXAONE 3.5가 문서 기반 답변 생성 (10~30초)\n\n"
             "**빠른 검색**: 관련 문서만 표시 (1~3초)",
        key="search_mode_radio",
    )

    # ================================================================
    # 검색 범위 선택 (그룹 기반 + 개별 KB 선택)
    # ================================================================
    st.markdown("**🔍 검색 범위**")
    selected_kb_ids: list[str] = []
    selected_group_id: str | None = None

    # 검색 그룹 로드
    groups_result = api_client._request("GET", "/api/v1/search-groups")
    groups_list = []
    if not api_failed(groups_result):
        groups_list = groups_result.get("groups", [])

    # KB 목록 로드
    kbs_result = api_client.get_searchable_kbs()
    kbs_list = []
    if not api_failed(kbs_result):
        kbs_list = kbs_result.get("kbs", kbs_result.get("items", []))

    # 선택 모드: 그룹 / 직접 선택
    scope_mode = st.radio(
        "범위 설정",
        options=["그룹 선택", "직접 선택"],
        horizontal=True,
        key="scope_mode_radio",
        label_visibility="collapsed",
    )

    if scope_mode == "그룹 선택":
        # 그룹 드롭다운
        if groups_list:
            group_names = ["전체"] + [g["name"] for g in groups_list]
            group_desc = {g["name"]: g.get("description", "") for g in groups_list}
            group_desc["전체"] = "모든 KB에서 검색"

            # Use group selected from main page if available
            _default_group = st.session_state.get("search_group_name", "전체")
            _default_idx = group_names.index(_default_group) if _default_group in group_names else 0

            selected_name = st.selectbox(
                "검색 그룹",
                options=group_names,
                index=_default_idx,
                key="search_group_select",
                help="관리자가 설정한 검색 그룹 또는 전체",
            )

            if selected_name != "전체":
                matched = [g for g in groups_list if g["name"] == selected_name]
                if matched:
                    selected_group_id = matched[0]["id"]
                    group_kb_ids = matched[0].get("kb_ids", [])
                    selected_kb_ids = group_kb_ids
                    st.session_state["_active_group_name"] = selected_name
                    desc = group_desc.get(selected_name, "")
                    if desc:
                        st.caption(f"📋 {desc}")
                    st.caption(f"KB {len(group_kb_ids)}개: {', '.join(group_kb_ids)}")
            else:
                st.session_state["_active_group_name"] = None
            # "전체" 선택 시 selected_kb_ids는 빈 리스트 (= 전체 검색)
        else:
            st.info("검색 그룹이 없습니다. 전체 KB에서 검색합니다.")

    else:
        # 직접 KB 선택 (기존 체크박스 방식)
        if kbs_list:
            kb_id_list = [kb.get("kb_id", kb.get("id", "")) for kb in kbs_list]

            for _kid in kb_id_list:
                if f"kb_cb_{_kid}" not in st.session_state:
                    st.session_state[f"kb_cb_{_kid}"] = True

            def _sync_all_cb():
                val = st.session_state.get("kb_select_all", True)
                for _kid in kb_id_list:
                    st.session_state[f"kb_cb_{_kid}"] = val

            st.checkbox(
                "전체 선택", value=True, key="kb_select_all", on_change=_sync_all_cb,
            )

            for kb in kbs_list:
                kb_name = kb.get("name", kb.get("kb_id", ""))
                kb_id = kb.get("kb_id", kb.get("id", ""))
                if st.checkbox(kb_name, key=f"kb_cb_{kb_id}"):
                    selected_kb_ids.append(kb_id)
        else:
            st.warning("KB 목록을 불러올 수 없습니다.")

    st.markdown("---")

    # ------------------------------------------------------------------
    # 세션 관리 (영속성 활성화 시)
    # ------------------------------------------------------------------
    _sidebar_user_id = _get_user_id()
    _store = get_session_store()

    if _sidebar_user_id:
        prev_sessions = _store.list_sessions(_sidebar_user_id)
        if prev_sessions:
            st.subheader("이전 대화")
            for sess in prev_sessions[:10]:
                sess_id = sess.get("session_id", "")
                preview = sess.get("preview", "")[:60] or "(내용 없음)"
                msg_count = sess.get("message_count", 0)
                label = f"{preview} ({msg_count}건)"
                col_load, col_del = st.columns([4, 1])
                with col_load:
                    if st.button(
                        label,
                        key=f"load_sess_{sess_id}",
                        use_container_width=True,
                    ):
                        loaded = _store.load_messages(sess_id, _sidebar_user_id)
                        if loaded:
                            st.session_state.chat_session_id = sess_id
                            st.session_state.chat_messages = loaded
                            st.session_state.session_restored = True
                            st.rerun()
                with col_del:
                    if st.button(
                        "X",
                        key=f"del_sess_{sess_id}",
                        help="삭제",
                    ):
                        _store.delete_session(sess_id, _sidebar_user_id)
                        st.rerun()
            st.markdown("---")

    # 대화 초기화 (항상 표시)
    if st.session_state.chat_messages:
        if st.button("대화 초기화", key="clear_chat_history", use_container_width=True):
            if _sidebar_user_id:
                _store.delete_session(
                    st.session_state.chat_session_id,
                    _sidebar_user_id,
                )
            st.session_state.chat_messages = []
            st.session_state.chat_session_id = str(uuid.uuid4())
            st.session_state.feedback_submitted = {}
            st.session_state.show_error_report = None
            st.session_state.session_restored = False
            st.rerun()


# ---------------------------------------------------------------------------
# 메인 영역
# ---------------------------------------------------------------------------
st.title("💬 지식 검색")
mode_label = "EXAONE 3.5" if search_mode == "AI 답변" else "문서 검색"
st.caption(f"Hub Search API를 통해 지식을 검색하고 {mode_label}가 답변합니다.")


# ---------------------------------------------------------------------------
# 유틸리티 함수 (메시지 렌더링 전에 정의)
# ---------------------------------------------------------------------------

def _render_answer_metadata(meta: dict, msg_id: str) -> None:
    """답변 메타데이터 (티어 배지, 신뢰도, 투명성, 리랭킹 점수 등) 렌더링."""
    # 소스 정보
    sources = meta.get("sources", [])
    if sources:
        with st.expander(f"📚 소스 문서 ({len(sources)}건)", expanded=False):
            for src in sources:
                tier = src.get("tier", src.get("kb_tier", "-"))
                tier_badge = f"{TIER_ICONS.get(tier, '')} {tier}"

                trust_score = src.get("trust_score", src.get("kts_score", 0))
                conf_badge = get_confidence_badge(trust_score)

                # TransparencyFormatter 레이블
                transparency = src.get("transparency_label", src.get("source_type", "-"))
                trans_icons = {"Document": "📄", "Inference": "🤖", "General": "💡"}
                trans_badge = f"{trans_icons.get(transparency, '📄')} {transparency}"

                title = src.get("title", src.get("document_title", "-"))
                url = src.get("url", src.get("source_url", ""))
                rerank_score = src.get("rerank_score", src.get("composite_score", 0))

                st.markdown(f"**{title}**")
                trust_detail = conf_badge if trust_score == 0 else f"{conf_badge} ({trust_score:.2f})"
                st.caption(
                    f"{tier_badge} | 신뢰도: {trust_detail} | "
                    f"{trans_badge} | Rerank: {rerank_score:.3f}"
                )

                # 최신성 경고
                is_stale = src.get("is_stale", False)
                freshness_warning = src.get("freshness_warning", "")
                days_since = src.get("days_since_update")

                warning_parts: list[str] = []
                if is_stale:
                    warning_parts.append("⚠️ 오래된 문서")
                if days_since is not None:
                    warning_parts.append(f"📅 {days_since}일 전 업데이트")
                elif src.get("updated_at"):
                    warning_parts.append(f"📅 {src['updated_at']}")
                if freshness_warning:
                    warning_parts.append(freshness_warning)

                if warning_parts:
                    st.caption(" | ".join(warning_parts))

                # Preview button + original link
                import re as _re
                _slide_match = _re.search(r'\(Slide (\d+)\)', title)
                _page_match = _re.search(r'\(Page (\d+)\)', title)
                _page_num = int(_slide_match.group(1)) if _slide_match else (int(_page_match.group(1)) if _page_match else 0)

                _pcol1, _pcol2 = st.columns([1, 1])
                with _pcol1:
                    if url:
                        st.markdown(f"[원본 보기]({url})")
                with _pcol2:
                    # Extract base filename from title (remove slide/page suffix)
                    _base_title = _re.sub(r'\s*\((?:Slide|Page|§)\s*\d+\)', '', title).strip()
                    if _base_title and _page_num > 0:
                        _preview_key = f"preview_{hash(title)}_{_page_num}"
                        if st.button(f"🔍 Slide {_page_num} 미리보기", key=_preview_key):
                            _api_url = f"http://localhost:8000/api/v1/admin/preview?file={_base_title}&page={_page_num}"
                            try:
                                import requests as _rq
                                _resp = _rq.get(_api_url, timeout=10)
                                if _resp.status_code == 200:
                                    st.image(_resp.content, caption=f"{_base_title} — Slide {_page_num}")
                                else:
                                    st.warning(f"프리뷰 실패: {_resp.status_code}")
                            except Exception as _pe:
                                st.warning(f"프리뷰 오류: {_pe}")

                st.markdown("---")

    # ConfidenceLevel 배지
    confidence_level = meta.get("confidence_level", meta.get("confidence", ""))
    if confidence_level:
        level_badges = {
            "HIGH": "🟢 HIGH",
            "MEDIUM": "🟡 MEDIUM",
            "LOW": "🟠 LOW",
            "UNCERTAIN": "🔴 UNCERTAIN",
        }
        badge = level_badges.get(str(confidence_level).upper(), str(confidence_level))
        st.caption(f"답변 신뢰도: {badge}")

    # Composite Reranking Score 분해
    rerank_breakdown = meta.get("rerank_breakdown", meta.get("composite_rerank", {}))
    if rerank_breakdown:
        with st.expander("📊 Composite Reranking 점수 분해", expanded=False):
            cols = st.columns(4)
            factors = [
                ("Dense", rerank_breakdown.get("dense", 0)),
                ("Sparse", rerank_breakdown.get("sparse", 0)),
                ("ColBERT", rerank_breakdown.get("colbert", 0)),
                ("Cross-Enc", rerank_breakdown.get("cross_encoder", 0)),
            ]
            for i, (label, score) in enumerate(factors):
                with cols[i]:
                    st.metric(label, f"{score:.3f}")

    # Query Expansion 표시
    expanded_terms = meta.get("expanded_terms", meta.get("query_expansion", []))
    if expanded_terms:
        st.caption(f"🔍 쿼리 확장: {', '.join(expanded_terms)}")

    # Working Memory Probe 히트
    wm_hit = meta.get("working_memory_hit", meta.get("wm_probe_hit", False))
    if wm_hit:
        st.caption("🧠 Working Memory Probe 히트")

    # 쿼리 자동 교정 (P2)
    corrected_query = meta.get("corrected_query", "")
    original_query = meta.get("original_query", "")
    if corrected_query and original_query and corrected_query != original_query:
        st.caption(f"🔄 검색어 자동 교정: {original_query} → {corrected_query}")

    # Disclaimer 경고문 (P1)
    disclaimer = meta.get("disclaimer", "")
    if disclaimer:
        st.warning(disclaimer)

    # Quality Gate (P1)
    quality_gate = meta.get("quality_gate_passed")
    if quality_gate is not None:
        if quality_gate:
            st.caption("✅ 답변 품질 검증 통과")
        else:
            st.caption("⚠️ 답변 품질 검증 미통과 — 내용을 직접 확인하세요")

    # Cross-KB Conflict (P1)
    conflict = meta.get("cross_kb_conflict")
    if conflict:
        with st.expander("⚠️ KB 간 정보 충돌 감지", expanded=False):
            if isinstance(conflict, dict):
                for key, val in conflict.items():
                    st.markdown(f"- **{key}**: {val}")
            elif isinstance(conflict, list):
                for item in conflict:
                    st.markdown(f"- {item}")
            else:
                st.markdown(str(conflict))

    # CRAG 반복 횟수 (P2)
    crag_history = meta.get("crag_action_history", [])
    if crag_history:
        st.caption(f"🔁 CRAG 검증: {len(crag_history)}회 반복")

    # 오류 신고 바로가기
    if msg_id:
        if st.button("🚨 오류 신고", key=f"error_report_{msg_id}", type="secondary"):
            st.session_state.show_error_report = msg_id
            st.rerun()


def _build_sources_from_chunks(chunks: list[dict]) -> list[dict]:
    """HubSearchResponse chunks를 소스 메타데이터로 변환.

    Backend fields mapping:
    - score: 검색 유사도 (vector similarity) → rerank_score
    - trust_score / kts_score: KB 신뢰도 → trust_score
    - rerank_score / composite_score: composite rerank → rerank_score (우선)
    - is_stale, freshness_warning, days_since_update, updated_at: 최신성 시그널
    """
    import re as _re
    results = []
    for c in chunks:
        doc_name = c.get("document_name", c.get("chunk_id", "-"))
        content = c.get("content", "")

        # Extract slide/page number from content
        location = ""
        slide_match = _re.search(r'\[Slide (\d+)', content)
        page_match = _re.search(r'\[Page (\d+)', content)
        if slide_match:
            location = f" (Slide {slide_match.group(1)})"
        elif page_match:
            location = f" (Page {page_match.group(1)})"
        elif c.get("metadata", {}).get("chunk_index") is not None:
            idx = c["metadata"]["chunk_index"]
            if idx >= 0:
                location = f" (§{idx + 1})"

        results.append({
            "title": f"{doc_name}{location}",
            "url": c.get("source_uri", ""),
            "tier": c.get("tier", c.get("metadata", {}).get("tier", "-")),
            "trust_score": c.get("trust_score", c.get("kts_score", 0)),
            "rerank_score": c.get("rerank_score", c.get("composite_score", c.get("score", 0))),
            "is_stale": c.get("is_stale", False),
            "freshness_warning": c.get("freshness_warning", ""),
            "days_since_update": c.get("days_since_update"),
            "updated_at": c.get("updated_at"),
        })
    return results


def _render_chunks_as_results(chunks: list[dict]) -> None:
    """빠른 검색 모드: 검색된 문서 목록을 카드로 표시."""
    if not chunks:
        st.info("관련 문서를 찾지 못했습니다.")
        return

    for i, chunk in enumerate(chunks[:10]):
        title = chunk.get("document_name", chunk.get("chunk_id", "-"))
        content = chunk.get("content", "")
        score = chunk.get("score", 0)
        kb_name = chunk.get("metadata", {}).get("kb_name", chunk.get("kb_id", "-"))
        url = chunk.get("source_uri", "")
        tier = chunk.get("metadata", {}).get("tier", "-")

        with st.container(border=True):
            col_title, col_score = st.columns([4, 1])
            with col_title:
                st.markdown(f"**{TIER_ICONS.get(tier, '📄')} {title}**")
                st.caption(f"KB: {kb_name} | {tier}")
            with col_score:
                st.metric("관련도", f"{score:.2f}", label_visibility="collapsed")

            # 내용 미리보기
            if content:
                preview = content[:300] + "..." if len(content) > 300 else content
                st.markdown(preview)

            if url:
                st.link_button("📄 원본 보기", url)


# ---------------------------------------------------------------------------
# 기존 메시지 렌더링
# ---------------------------------------------------------------------------
for msg in st.session_state.chat_messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

        # 답변 메타데이터 (assistant만)
        if msg["role"] == "assistant" and msg.get("metadata"):
            _render_answer_metadata(msg["metadata"], msg.get("msg_id", ""))


# ---------------------------------------------------------------------------
# 오류 신고 다이얼로그
# ---------------------------------------------------------------------------
if st.session_state.show_error_report:
    with st.expander("🚨 오류 신고", expanded=True):
        error_desc = st.text_area("오류 내용을 설명해주세요", key="error_report_desc", max_chars=1000)
        error_type = st.selectbox(
            "오류 유형",
            ["잘못된 정보", "불완전한 답변", "관련 없는 답변", "출처 오류", "기타"],
            key="error_report_type",
        )
        ecol1, ecol2 = st.columns(2)
        with ecol1:
            if st.button("제출", type="primary", key="submit_error"):
                if error_desc:
                    result = api_client.create_error_report({
                        "description": sanitize_input(error_desc, max_length=1000),
                        "error_type": error_type,
                        "session_id": st.session_state.chat_session_id,
                        "message_id": st.session_state.show_error_report,
                    })
                    if not api_failed(result):
                        st.success("오류 신고가 접수되었습니다.")
                        st.session_state.show_error_report = None
                        st.rerun()
                    else:
                        st.error("신고 접수 실패. 재시도해 주세요.")
                else:
                    st.warning("오류 내용을 입력해주세요.")
        with ecol2:
            if st.button("취소", key="cancel_error"):
                st.session_state.show_error_report = None
                st.rerun()


# ---------------------------------------------------------------------------
# 검색 실행 함수
# ---------------------------------------------------------------------------
def _execute_search(query: str) -> None:
    """Hub Search API 호출 및 결과 표시.

    AI 답변 모드: hub_search_answer() — LLM 답변 생성 포함 (10~30초)
    빠른 검색 모드: hub_search() — 문서 검색만 (1~3초)
    """
    # 세션 활동 gauge
    metrics.session_active(len(st.session_state.chat_messages) // 2 + 1)

    # 사용자 메시지 추가
    st.session_state.chat_messages.append({"role": "user", "content": query})

    with st.chat_message("user"):
        st.markdown(query)

    with st.chat_message("assistant"):
        if search_mode == "빠른 검색":
            _execute_fast_search(query)
        else:
            _execute_ai_search(query)


def _execute_fast_search(query: str) -> None:
    """빠른 검색: 문서 결과만 표시 (LLM 답변 없음)."""
    t0 = time.monotonic()
    with st.status("문서 검색 중...", expanded=True) as status:
        st.write("📡 KB 검색 중...")
        result = api_client.hub_search(
            query,
            kb_ids=selected_kb_ids or None,
            group_name=st.session_state.get("_active_group_name"),
            top_k=10,
        )
        status.update(label="검색 완료", state="complete", expanded=True)
    duration_ms = round((time.monotonic() - t0) * 1000, 1)

    if api_failed(result):
        st.error(f"검색 실패: {result.get('error', '알 수 없는 오류')}")
        metrics.track_search_quality(
            mode="fast", has_results=False, source_count=0,
            duration_ms=duration_ms, timed_out="timeout" in str(result.get("error", "")).lower(),
        )
        return

    chunks = result.get("chunks", result.get("results", []))
    msg_id = str(uuid.uuid4())[:8]

    if chunks:
        st.markdown(f"**{len(chunks)}건의 관련 문서를 찾았습니다.**")
        _render_chunks_as_results(chunks)
    else:
        st.info("관련 문서를 찾지 못했습니다. 다른 검색어를 시도해 보세요.")

    sources = _build_sources_from_chunks(chunks)
    has_stale = any(s.get("is_stale", False) for s in sources)
    metadata = {
        "sources": sources,
        "confidence_level": "",
        "rerank_breakdown": {},
        "expanded_terms": [],
        "working_memory_hit": False,
    }

    metrics.search_executed(query=query, results=len(chunks), duration_ms=duration_ms)
    metrics.track_search_quality(
        mode="fast", has_results=bool(chunks), source_count=len(chunks),
        duration_ms=duration_ms, has_stale_docs=has_stale,
    )

    content = f"{len(chunks)}건의 관련 문서를 찾았습니다." if chunks else "관련 문서를 찾지 못했습니다."
    st.session_state.chat_messages.append({
        "role": "assistant",
        "content": content,
        "metadata": metadata,
        "msg_id": msg_id,
    })
    _persist_messages()


def _execute_ai_search(query: str) -> None:
    """AI 답변 모드: 문서 검색 + EXAONE 3.5 답변 생성."""
    t0 = time.monotonic()
    timed_out = False
    with st.status("AI 답변 생성 중...", expanded=True) as status:
        st.write("1/3 관련 KB 선택 및 문서 검색 중...")
        result = api_client.hub_search_answer(
            query,
            kb_ids=selected_kb_ids or None,
            group_name=st.session_state.get("_active_group_name"),
            mode="agentic",
        )
        duration_ms = round((time.monotonic() - t0) * 1000, 1)

        if api_failed(result):
            status.update(label="검색 실패", state="error")
            error_detail = result.get("error", "알 수 없는 오류")
            timed_out = "timeout" in str(error_detail).lower()
            st.error(f"검색 실패: {error_detail}")
            metrics.track_search_quality(
                mode="ai", has_results=False, source_count=0,
                duration_ms=duration_ms, timed_out=timed_out,
            )
            if st.button("재시도", key="retry_search"):
                st.cache_data.clear()
                st.rerun()
            return

        st.write("2/3 문서 리랭킹 및 답변 생성 완료")
        st.write(f"3/3 응답 수신 완료 ({duration_ms:.0f}ms)")
        status.update(label="답변 생성 완료", state="complete", expanded=False)

    answer = result.get("answer") or "답변을 생성할 수 없습니다."
    msg_id = str(uuid.uuid4())[:8]

    st.markdown(answer)

    # 메타데이터 구성
    chunks = result.get("chunks", [])
    sources = _build_sources_from_chunks(chunks)
    has_stale = any(s.get("is_stale", False) for s in sources)
    transparency = result.get("transparency") or {}
    query_preprocess = result.get("query_preprocess") or {}
    quality_gate_passed = result.get("quality_gate_passed")
    metadata = {
        "sources": sources,
        "confidence_level": transparency.get("confidence_indicator", ""),
        "rerank_breakdown": {},
        "expanded_terms": [],
        "working_memory_hit": False,
        # P1: 품질 시그널
        "quality_gate_passed": quality_gate_passed,
        "disclaimer": result.get("disclaimer", ""),
        "cross_kb_conflict": result.get("cross_kb_conflict"),
        # P2: 쿼리 전처리
        "corrected_query": query_preprocess.get("corrected_query", ""),
        "original_query": query_preprocess.get("original_query", ""),
        "crag_action_history": result.get("crag_action_history", []),
    }

    _render_answer_metadata(metadata, msg_id)

    metrics.search_executed(query=query, results=len(chunks), duration_ms=duration_ms)
    metrics.track_search_quality(
        mode="ai", has_results=bool(chunks), source_count=len(chunks),
        duration_ms=duration_ms, has_stale_docs=has_stale,
        quality_gate_passed=quality_gate_passed,
    )

    st.session_state.chat_messages.append({
        "role": "assistant",
        "content": answer,
        "metadata": metadata,
        "msg_id": msg_id,
    })
    _persist_messages()


# ---------------------------------------------------------------------------
# 홈페이지에서 넘어온 검색어 처리
# ---------------------------------------------------------------------------
pending = st.session_state.pop("pending_query", None)
if pending:
    try:
        validated_pending = validate_query(pending, max_length=500)
        _execute_search(validated_pending)
    except ValueError:
        pass  # Silently ignore invalid pending queries


# ---------------------------------------------------------------------------
# 채팅 입력
# ---------------------------------------------------------------------------
user_input = st.chat_input("궁금한 것을 물어보세요...", max_chars=500)
if user_input:
    try:
        validated = validate_query(user_input, max_length=500)
        _execute_search(validated)
    except ValueError:
        st.warning("검색어를 입력해주세요.")
