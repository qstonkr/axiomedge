"""섹션 관련 공유 유틸리티 — heading_path 파싱."""

from __future__ import annotations

HEADING_PATH_SEPARATOR = " > "


def get_top_section(heading_path: str) -> str:
    """heading_path에서 최상위 섹션 이름 추출.

    >>> get_top_section("설치 가이드 > 사전 요구사항 > Python")
    '설치 가이드'
    >>> get_top_section("")
    ''
    """
    if not heading_path:
        return ""
    return heading_path.split(HEADING_PATH_SEPARATOR)[0].strip()
