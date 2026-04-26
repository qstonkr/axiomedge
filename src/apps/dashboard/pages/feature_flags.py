"""Feature flag admin 페이지 — C4.

``knowledge_feature_flags`` 의 (name, scope) 쌍을 토글하는 UI. 모든 변경은
audit log 에 기록 (``feature_flag.update`` event). FeatureFlagRepository.upsert
가 Redis ``feature_flags:invalidate`` 채널로 publish 하여 다중 worker 캐시
즉시 무효화 (P1-6).
"""

import json

import streamlit as st

st.set_page_config(
    page_title="Feature Flags", page_icon="🚦", layout="wide",
)

from components.sidebar import hide_default_nav, render_sidebar  # noqa: E402
from services import api_client  # noqa: E402

hide_default_nav()
render_sidebar(show_admin=True)

st.title("🚦 Feature Flags")
st.caption(
    "PR-11 (N) — 코드 재배포 없이 위험 기능 (병렬 ingest, GraphRAG batch 등) "
    "kill switch. APP_ENV=production 에서는 ENV override 가 "
    "FF_ALLOW_ENV_OVERRIDE allowlist 로 제한 (P0-4)."
)


# =============================================================================
# Listing
# =============================================================================
@st.cache_data(ttl=30)  # M-cleanup: 30s 로 트래픽 완화 (toggle 후 새로고침 권장)
def _fetch_flags() -> list[dict]:
    try:
        return api_client.get(
            "/api/v1/admin/feature-flags",
            cache_key="feature_flags",
        ) or []
    except Exception as e:  # noqa: BLE001
        st.error(f"Failed to fetch feature flags: {e}")
        return []


col_top1, col_top2 = st.columns([3, 1])
with col_top1:
    st.markdown(
        "**Active flag** (실제 코드 분기에서 사용):\n"
        "- `ENABLE_INGESTION_FILE_PARALLEL` — `_resolve_file_parallel` 의 "
        "병렬 ingest kill switch (CLI/Crawl).\n\n"
        "**Future work** (정의됐지만 코드 분기 대기 중):\n"
        "- `ENABLE_GRAPHRAG_BATCH_NEO4J` — GraphRAG persistence batch (계획)\n"
        "- `ENABLE_OTEL_NEO4J_INSTRUMENTATION` — Neo4j manual span 토글 (계획)",
    )
with col_top2:
    if st.button("🔄 새로고침"):
        st.cache_data.clear()
        st.rerun()

flags = _fetch_flags()

if not flags:
    st.info(
        "No flags yet. Use 'New / Upsert' below to create the first one.",
    )
else:
    st.subheader(f"현재 등록 {len(flags)}개")
    for flag in flags:
        name = flag.get("name", "?")
        scope = flag.get("scope", "_global")
        enabled = bool(flag.get("enabled"))
        updated_by = flag.get("updated_by", "")
        updated_at = flag.get("updated_at", "")
        payload = flag.get("payload") or {}

        with st.expander(
            f"{'🟢' if enabled else '⚫'} `{name}` @ `{scope}` "
            f"— updated by {updated_by} ({updated_at})",
        ):
            c1, c2 = st.columns([1, 3])
            with c1:
                new_state = st.toggle(
                    "Enabled", value=enabled, key=f"toggle_{name}_{scope}",
                )
                if new_state != enabled:
                    if st.button(
                        "Save", key=f"save_{name}_{scope}",
                        type="primary",
                    ):
                        try:
                            api_client.post(
                                "/api/v1/admin/feature-flags",
                                json={
                                    "name": name, "scope": scope,
                                    "enabled": new_state,
                                    "payload": payload,
                                },
                            )
                            st.success(
                                f"Updated: {name}@{scope} = {new_state}"
                            )
                            st.cache_data.clear()
                            st.rerun()
                        except Exception as e:  # noqa: BLE001
                            st.error(f"Update failed: {e}")
            with c2:
                st.markdown("**Payload**")
                st.json(payload)


# =============================================================================
# Upsert form
# =============================================================================
st.subheader("New / Upsert")
with st.form("upsert_flag"):
    name_input = st.text_input(
        "name", placeholder="ENABLE_INGESTION_FILE_PARALLEL",
    )
    scope_input = st.text_input(
        "scope", value="_global",
        help="kb:<id> / org:<id> / _global. Precedence: kb > org > global.",
    )
    enabled_input = st.checkbox("enabled", value=True)
    payload_input = st.text_area(
        "payload (JSON)", value="{}",
        help="Optional JSON config (e.g. {\"workers\": 8}).",
    )

    if st.form_submit_button("Save"):
        if not name_input:
            st.error("name is required")
        else:
            try:
                payload_dict = json.loads(payload_input or "{}")
            except json.JSONDecodeError as e:
                st.error(f"Invalid JSON: {e}")
                payload_dict = None
            if payload_dict is not None:
                try:
                    api_client.post(
                        "/api/v1/admin/feature-flags",
                        json={
                            "name": name_input.strip(),
                            "scope": scope_input.strip() or "_global",
                            "enabled": enabled_input,
                            "payload": payload_dict,
                        },
                    )
                    st.success(
                        f"Saved: {name_input}@{scope_input} = {enabled_input}"
                    )
                    st.cache_data.clear()
                    st.rerun()
                except Exception as e:  # noqa: BLE001
                    st.error(f"Save failed: {e}")
