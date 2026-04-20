"""Deprecation banner — Streamlit admin 페이지가 Next.js (/admin/*) 로 이식됐음을 알림.

B-2 milestone 으로 17 admin 라우트가 Next.js 위로 이동. 운영자가 새 환경으로
자연스럽게 이동하도록 각 Streamlit admin 페이지 상단에 배너를 표시한다.

사용:
    from components.deprecate_banner import deprecated_for
    deprecated_for("/admin/sources", "데이터 소스")
"""

from __future__ import annotations

import os

import streamlit as st


def _admin_base_url() -> str:
    """Next.js admin base URL — env override 가능."""
    return os.environ.get("NEXT_ADMIN_BASE_URL", "http://localhost:3000")


def deprecated_for(next_path: str, page_label: str) -> None:
    """Render '곧 폐기 — Next.js 로 이동' 배너.

    Args:
        next_path: Next.js 의 새 경로 ("/admin/sources" 등). 절대 경로.
        page_label: 사용자에게 보여줄 새 페이지 이름.
    """
    base = _admin_base_url().rstrip("/")
    target = f"{base}{next_path}"
    st.warning(
        f"⚠️ **이 페이지는 곧 폐기됩니다.** "
        f"새 운영 환경에서 동일 기능을 사용하세요 → "
        f"[**{page_label}** 열기]({target})",
        icon="🚧",
    )
