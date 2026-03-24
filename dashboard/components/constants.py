"""Dashboard 공용 상수 (SSOT)

여러 페이지에서 중복 사용되는 상수를 한 곳에 모음.
변경 시 이 파일만 수정하면 전체 반영.

Created: 2026-02-20
"""

# ── 10-Step 인제스천 파이프라인 (Neo4j 그래프 빌딩 포함) ──
PIPELINE_STEPS: list[tuple[str, str]] = [
    ("preprocess", "전처리"),
    ("korean", "한국어 처리"),
    ("chunk", "청킹"),
    ("dedup", "중복 제거"),
    ("hierarchical", "계층 구조"),
    ("contextual", "컨텍스트"),
    ("owner", "담당자 추출"),
    ("terms", "용어 추출"),
    ("embed", "임베딩 + 벡터 저장"),
    ("graph", "Neo4j 그래프 빌딩"),
]

PIPELINE_STEP_KEYS = [k for k, _ in PIPELINE_STEPS]
PIPELINE_STEP_LABELS = dict(PIPELINE_STEPS)

# ── 파이프라인 단계 상태 아이콘 ──
STEP_STATUS_ICONS: dict[str, str] = {
    "completed": "✅",
    "running": "🔄",
    "failed": "❌",
    "idle": "⏸️",
    "pending": "⏸️",
}

# ── KB 상태 아이콘 (대소문자 모두 지원) ──
KB_STATUS_ICONS: dict[str, str] = {
    "ACTIVE": "🟢", "active": "🟢",
    "INACTIVE": "⚪", "inactive": "⚪",
    "SYNCING": "🔵", "syncing": "🔵",
    "ERROR": "🔴", "error": "🔴",
    "ARCHIVED": "📦", "archived": "📦",
    "PENDING": "🟡", "pending": "🟡",
}

# ── KB 티어 아이콘 (대소문자 모두 지원) ──
TIER_ICONS: dict[str, str] = {
    "GLOBAL": "🌐", "global": "🌐",
    "BU": "🏢", "bu": "🏢",
    "TEAM": "👥", "team": "👥",
    "PERSONAL": "👤", "personal": "👤",
}

# ── 인제스천 실행 상태 아이콘 (대소문자 모두 지원) ──
RUN_STATUS_ICONS: dict[str, str] = {
    "PENDING": "🟡 대기", "pending": "🟡 대기",
    "RUNNING": "🔵 실행중", "running": "🔵 실행중",
    "COMPLETED": "🟢 완료", "completed": "🟢 완료",
    "FAILED": "🔴 실패", "failed": "🔴 실패",
}
