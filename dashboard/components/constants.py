"""Dashboard 공용 상수 (SSOT)

여러 페이지에서 중복 사용되는 상수를 한 곳에 모음.
변경 시 이 파일만 수정하면 전체 반영.

Created: 2026-02-20

── 백엔드 config_weights.py 와의 관계 ──
백엔드에서 직접 import 가능한 임계값은 백엔드를 SSOT로 사용.
프론트 전용 표시 임계값(UI 등급 표시, 통과율 등)은 여기서 관리.
"""

from src.config_weights import (
    ConfidenceConfig,
    DedupConfig,
    TrustScoreWeights,
)

# ── 백엔드 SSOT 인스턴스 (프론트 전역 사용) ──
CONFIDENCE = ConfidenceConfig()
DEDUP = DedupConfig()
KTS_WEIGHTS = TrustScoreWeights()

# ── RAGAS 평가 가중치 ──
RAGAS_FAITHFULNESS_WEIGHT: float = 0.5
RAGAS_RELEVANCY_WEIGHT: float = 0.3
RAGAS_PRECISION_WEIGHT: float = 0.2

# ── 통과율 임계값 (Ingestion Gate) ──
PASS_RATE_GOOD: float = 0.9
PASS_RATE_WARN: float = 0.7
GATE_FAIL_RATE_WARN: float = 0.1

# ── 신선도 표시 임계값 (%) ──
FRESHNESS_GOOD_PCT: float = 70.0
FRESHNESS_WARN_PCT: float = 40.0

# ── 출처 커버리지 임계값 ──
SOURCE_COVERAGE_GOOD: float = 0.8
SOURCE_COVERAGE_WARN: float = 0.5

# ── L1 카테고리 기타 비율 임계값 ──
ETC_RATIO_WARN: float = 0.15

# ── KTS 6-Signal 정의 (가중치는 백엔드 TrustScoreWeights 참조) ──
KTS_SIGNALS: dict[str, dict] = {
    "accuracy": {
        "label": "정확도",
        "weight": KTS_WEIGHTS.hallucination_weight,
        "field": "hallucination_score",
    },
    "source_credibility": {
        "label": "출처 신뢰도",
        "weight": KTS_WEIGHTS.source_credibility_weight,
        "field": "source_credibility",
    },
    "freshness": {
        "label": "신선도",
        "weight": KTS_WEIGHTS.freshness_weight,
        "field": "freshness_score",
    },
    "consistency": {
        "label": "일관성",
        "weight": KTS_WEIGHTS.consistency_weight,
        "field": "consistency_score",
    },
    "usage_feedback": {
        "label": "사용자 피드백",
        "weight": KTS_WEIGHTS.usage_weight,
        "field": "usage_score",
    },
    "expert_validation": {
        "label": "전문가 검증",
        "weight": KTS_WEIGHTS.user_validation_weight,
        "field": "user_validation_score",
    },
}

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
