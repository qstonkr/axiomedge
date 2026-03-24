"""Freshness Ranker.

검색 결과에 최신성 가중치를 적용하는 모듈.
오래된 문서는 점수를 낮추고, 최신 문서는 점수를 높임.

Extracted from oreo-ecosystem freshness_ranker.py.
All oreo-specific imports removed. Core algorithm preserved exactly.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..config_weights import weights as _w

logger = logging.getLogger(__name__)


@dataclass
class FreshnessConfig:
    """최신성 가중치 설정."""

    # 기준 일수
    fresh_days: int = _w.quality.fresh_max_days  # 90일 이내: 최신
    stale_days: int = _w.quality.stale_max_days  # 1년 이상: 오래됨
    outdated_days: int = _w.quality.stale_threshold_days  # 2년 이상: 매우 오래됨

    # 가중치 (0.0 ~ 1.0)
    fresh_boost: float = _w.quality.fresh_boost  # 최신 문서 부스트
    stale_penalty: float = _w.quality.stale_penalty  # 오래된 문서 페널티
    outdated_penalty: float = _w.quality.outdated_penalty  # 매우 오래된 문서 페널티

    # 최신성 경고 표시 임계값
    warning_threshold_days: int = _w.quality.stale_max_days

    # 버전 수 기반 활동도 보너스
    version_bonus_mid_threshold: int = 5
    version_bonus_high_threshold: int = 10
    version_bonus_mid: float = 0.05
    version_bonus_high: float = 0.10


@dataclass
class RankedResult:
    """랭킹된 검색 결과."""

    content: str
    metadata: dict[str, Any]
    original_score: float
    adjusted_score: float
    freshness_warning: str | None
    days_since_update: int | None


class FreshnessRanker:
    """최신성 기반 랭커."""

    def __init__(self, config: FreshnessConfig | None = None):
        self.config = config or FreshnessConfig()

    def rank(
        self,
        results: list[dict[str, Any]],
        apply_penalty: bool = True,
    ) -> list[RankedResult]:
        """검색 결과에 최신성 가중치 적용.

        Args:
            results: 원본 검색 결과 목록
            apply_penalty: 페널티 적용 여부

        Returns:
            랭킹된 결과 목록 (점수 내림차순)
        """
        ranked_results: list[RankedResult] = []

        for result in results:
            metadata = result.get("metadata", {})
            original_score = result.get("similarity", 0.0)

            # 최신성 계산
            days_old = self._calculate_days_old(metadata.get("updated_at"))
            weight = self._calculate_weight(days_old) if apply_penalty else 1.0

            # 버전 수 기반 활동도 보너스 (최대 10%)
            version_count = self._to_int(metadata.get("version_count", 0))
            if version_count >= self.config.version_bonus_high_threshold:
                weight *= 1 + self.config.version_bonus_high
            elif version_count >= self.config.version_bonus_mid_threshold:
                weight *= 1 + self.config.version_bonus_mid

            warning = self._get_warning(days_old)

            adjusted_score = original_score * weight

            ranked_results.append(RankedResult(
                content=result.get("content", ""),
                metadata=metadata,
                original_score=original_score,
                adjusted_score=adjusted_score,
                freshness_warning=warning,
                days_since_update=days_old,
            ))

        # 조정된 점수로 정렬
        ranked_results.sort(key=lambda x: x.adjusted_score, reverse=True)

        return ranked_results

    def _calculate_days_old(self, updated_at: str | None) -> int | None:
        """문서 수정 후 경과 일수 계산."""
        if not updated_at:
            return None

        try:
            # YYYY-MM-DD 형식 파싱
            if len(updated_at) >= 10:
                update_date = datetime.fromisoformat(updated_at[:10])
                return (datetime.now() - update_date).days
        except (ValueError, TypeError):
            pass

        return None

    def _calculate_weight(self, days_old: int | None) -> float:
        """경과 일수에 따른 가중치 계산."""
        if days_old is None:
            return 1.0  # 날짜 정보 없으면 기본값

        if days_old <= self.config.fresh_days:
            return self.config.fresh_boost
        elif days_old <= self.config.stale_days:
            return 1.0  # 중간 범위는 가중치 없음
        elif days_old <= self.config.outdated_days:
            return self.config.stale_penalty
        else:
            return self.config.outdated_penalty

    @staticmethod
    def _to_int(value: Any, default: int = 0) -> int:
        """숫자형 메타데이터를 안전하게 int로 변환."""
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def _get_warning(self, days_old: int | None) -> str | None:
        """경과 일수에 따른 경고 메시지 생성."""
        if days_old is None:
            return None

        if days_old > self.config.outdated_days:
            years = days_old // 365
            return f"{years}년 이상 미수정 문서"
        elif days_old > self.config.warning_threshold_days:
            months = days_old // 30
            return f"{months}개월 전 수정"

        return None

    def filter_outdated(
        self,
        results: list[RankedResult],
        max_days: int | None = None,
    ) -> list[RankedResult]:
        """오래된 문서 필터링.

        Args:
            results: 랭킹된 결과 목록
            max_days: 최대 허용 일수 (None이면 설정값 사용)

        Returns:
            필터링된 결과 목록
        """
        threshold = max_days or self.config.outdated_days

        return [
            r for r in results
            if r.days_since_update is None or r.days_since_update <= threshold
        ]

    def format_result_with_warning(self, result: RankedResult) -> str:
        """경고가 포함된 결과 포맷팅."""
        output = result.content

        if result.freshness_warning:
            output = f"{result.freshness_warning}\n\n{output}"

        return output
