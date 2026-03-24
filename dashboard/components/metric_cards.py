"""Metric Cards Component

메트릭 카드 렌더링 유틸리티.

Created: 2026-02-04 (Sprint 10)
"""

from dataclasses import dataclass
from typing import Literal

import streamlit as st


@dataclass
class MetricData:
    """메트릭 데이터."""

    label: str
    value: str | int | float
    delta: str | int | float | None = None
    delta_color: Literal["normal", "inverse", "off"] = "normal"
    help_text: str | None = None


def render_metric_card(
    label: str,
    value: str | int | float,
    delta: str | int | float | None = None,
    delta_color: Literal["normal", "inverse", "off"] = "normal",
    help_text: str | None = None,
) -> None:
    """
    단일 메트릭 카드를 렌더링합니다.

    Args:
        label: 메트릭 레이블
        value: 메트릭 값
        delta: 변화량 (선택)
        delta_color: 델타 색상 ("normal", "inverse", "off")
        help_text: 도움말 텍스트 (선택)
    """
    st.metric(
        label=label,
        value=value,
        delta=delta,
        delta_color=delta_color,
        help=help_text,
    )


def render_metric_row(
    metrics: list[MetricData],
    columns: int | None = None,
) -> None:
    """
    메트릭 카드들을 행으로 렌더링합니다.

    Args:
        metrics: MetricData 목록
        columns: 열 수 (None이면 메트릭 수와 동일)
    """
    num_columns = columns if columns else len(metrics)
    cols = st.columns(num_columns)

    for i, metric in enumerate(metrics):
        col_index = i % num_columns
        with cols[col_index]:
            render_metric_card(
                label=metric.label,
                value=metric.value,
                delta=metric.delta,
                delta_color=metric.delta_color,
                help_text=metric.help_text,
            )


def render_quality_metrics(
    faithfulness: float,
    relevancy: float,
    precision: float,
    overall: float | None = None,
) -> None:
    """
    RAGAS 품질 메트릭을 렌더링합니다.

    Args:
        faithfulness: Faithfulness 점수 (0-1)
        relevancy: Relevancy 점수 (0-1)
        precision: Precision 점수 (0-1)
        overall: 종합 점수 (None이면 가중 평균 계산)
    """
    if overall is None:
        # RAGAS 가중치: Faithfulness 50%, Relevancy 30%, Precision 20%
        overall = faithfulness * 0.5 + relevancy * 0.3 + precision * 0.2

    metrics = [
        MetricData(
            label="Faithfulness",
            value=f"{faithfulness:.0%}",
            help_text="응답이 컨텍스트에 충실한가? (가중치: 50%)",
        ),
        MetricData(
            label="Relevancy",
            value=f"{relevancy:.0%}",
            help_text="응답이 질문에 관련있는가? (가중치: 30%)",
        ),
        MetricData(
            label="Precision",
            value=f"{precision:.0%}",
            help_text="검색된 컨텍스트가 정확한가? (가중치: 20%)",
        ),
        MetricData(
            label="종합 점수",
            value=f"{overall:.0%}",
            help_text="가중 평균 점수",
        ),
    ]

    render_metric_row(metrics)


def render_kb_summary_metrics(
    total_docs: int,
    stale_docs: int,
    glossary_terms: int,
    avg_freshness: float,
) -> None:
    """
    KB 요약 메트릭을 렌더링합니다.

    Args:
        total_docs: 총 문서 수
        stale_docs: Stale 문서 수
        glossary_terms: 용어집 용어 수
        avg_freshness: 평균 신선도 (0-1)
    """
    metrics = [
        MetricData(
            label="📄 총 문서 수",
            value=f"{total_docs:,}",
            help_text="지식베이스에 등록된 총 문서 수",
        ),
        MetricData(
            label="⚠️ Stale 문서",
            value=str(stale_docs),
            delta_color="inverse",
            help_text="90일 이상 미수정 문서",
        ),
        MetricData(
            label="📖 용어집 용어",
            value=str(glossary_terms),
            help_text="등록된 용어 수",
        ),
        MetricData(
            label="🎯 평균 Freshness",
            value=f"{avg_freshness:.0%}",
            help_text="문서 평균 신선도 점수",
        ),
    ]

    render_metric_row(metrics)


def get_confidence_badge(score: float) -> str:
    """
    점수에 따른 신뢰도 배지를 반환합니다.

    Args:
        score: 점수 (0-1). 0 means "not yet rated" (미산정).

    Returns:
        신뢰도 배지 문자열
    """
    if score == 0:
        return "➖ 미산정"
    elif score >= 0.85:
        return "🟢 High"
    elif score >= 0.70:
        return "🟡 Medium"
    elif score >= 0.50:
        return "🟠 Low"
    else:
        return "🔴 Uncertain"
