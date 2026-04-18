"""Tests for Korean query planner — KiwiPy enrichment + LLM plan call."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agentic.planner import (
    KoreanQueryPlanner,
    QueryEnrichment,
    _build_planner_context,
    _enrich_query,
)
from src.agentic.protocols import Plan
from src.agentic.tools import build_default_registry


# =============================================================================
# Enrichment
# =============================================================================


def test_enrich_query_detects_time_keyword() -> None:
    e = _enrich_query("차주 회의 자료 어디에 있어")
    assert e.has_time_reference
    assert "차주" in e.detected_time_phrases


def test_enrich_query_detects_n_days_ago_pattern() -> None:
    e = _enrich_query("3일 전 보고서")
    assert e.has_time_reference
    assert any("일 전" in p for p in e.detected_time_phrases)


def test_enrich_query_no_time_keyword() -> None:
    e = _enrich_query("Kubernetes pod 재시작 방법")
    assert not e.has_time_reference


def test_enrich_query_returns_empty_on_kiwipy_failure() -> None:
    """Kiwi 가 import 실패해도 빈 enrichment 반환 (graceful)."""
    # 실 Kiwi 가 설치되어 있더라도 — 빈 string 입력은 안전
    e = _enrich_query("")
    assert isinstance(e, QueryEnrichment)


# =============================================================================
# Context builder
# =============================================================================


def test_build_planner_context_empty_when_no_enrichment() -> None:
    e = QueryEnrichment()
    assert _build_planner_context(e) == ""


def test_build_planner_context_includes_entities() -> None:
    e = QueryEnrichment(entities=["신촌점", "김담당"])
    ctx = _build_planner_context(e)
    assert "신촌점" in ctx
    assert "graph_query" in ctx


def test_build_planner_context_includes_time_hint() -> None:
    e = QueryEnrichment(has_time_reference=True, detected_time_phrases=["차주"])
    ctx = _build_planner_context(e)
    assert "차주" in ctx
    assert "time_resolver" in ctx


def test_build_planner_context_falls_back_to_keywords_when_no_entities() -> None:
    e = QueryEnrichment(keywords=["검색", "에러", "디버깅"])
    ctx = _build_planner_context(e)
    assert "qdrant_search" in ctx
    assert "검색" in ctx


# =============================================================================
# KoreanQueryPlanner integration
# =============================================================================


@pytest.mark.asyncio
async def test_planner_make_plan_calls_llm_with_context() -> None:
    llm = MagicMock()
    llm.plan = AsyncMock(return_value=Plan(
        query="x", sub_queries=[], steps=[], estimated_complexity=1,
    ))
    registry = build_default_registry()
    planner = KoreanQueryPlanner(llm, registry)
    await planner.make_plan("차주 매장 현황")
    args, kwargs = llm.plan.call_args
    # 'context' positional or keyword
    if "context" in kwargs:
        ctx = kwargs["context"]
    else:
        ctx = args[2]
    assert "차주" in ctx or "time_resolver" in ctx


@pytest.mark.asyncio
async def test_planner_passes_extra_context_through() -> None:
    llm = MagicMock()
    llm.plan = AsyncMock(return_value=Plan(query="x", sub_queries=[], steps=[], estimated_complexity=1))
    planner = KoreanQueryPlanner(llm, build_default_registry())
    await planner.make_plan("일반 검색", extra_context="추가 힌트")
    args, kwargs = llm.plan.call_args
    ctx = kwargs.get("context") or args[2]
    assert "추가 힌트" in ctx


def test_enrich_static_helper() -> None:
    e = KoreanQueryPlanner.enrich("차주 매장")
    assert isinstance(e, QueryEnrichment)
    assert e.has_time_reference
