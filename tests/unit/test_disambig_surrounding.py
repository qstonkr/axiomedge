"""Surrounding-token disambig 테스트."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from src.search.similarity._disambig import disambiguate_by_surrounding_tokens


@dataclass
class _T:
    term: str
    word_type: str | None = None
    confidence_score: float | None = None


def test_single_candidate_returned() -> None:
    c = _T("이전", "RELOC", 0.9)
    assert disambiguate_by_surrounding_tokens([c], []) is c


def test_majority_type_match() -> None:
    """주변 RELOC 다수 → RELOC candidate 선택."""
    cands = [_T("이전", "BFR", 0.5), _T("이전", "RELOC", 0.4)]
    types = ["RELOC", "RELOC", None, "BFR"]
    chosen = disambiguate_by_surrounding_tokens(cands, types)
    assert chosen.word_type == "RELOC"


def test_no_match_falls_back_to_confidence() -> None:
    """주변 type 이 candidates 와 안 맞으면 confidence 최고."""
    cands = [_T("이전", "BFR", 0.5), _T("이전", "RELOC", 0.9)]
    types = ["UNRELATED"]
    chosen = disambiguate_by_surrounding_tokens(cands, types)
    assert chosen.confidence_score == 0.9


def test_empty_surrounding_uses_fallback() -> None:
    cands = [_T("이전", "BFR", 0.5), _T("이전", "RELOC", 0.9)]
    chosen = disambiguate_by_surrounding_tokens(cands, [])
    assert chosen.confidence_score == 0.9


def test_all_none_surrounding_uses_fallback() -> None:
    cands = [_T("이전", "BFR", 0.5), _T("이전", "RELOC", 0.9)]
    chosen = disambiguate_by_surrounding_tokens(cands, [None, None])
    assert chosen.confidence_score == 0.9


def test_tie_picks_first_match() -> None:
    """type majority 동률 시 candidates 순서 우선."""
    cands = [_T("이전", "BFR", 0.5), _T("이전", "RELOC", 0.5)]
    types = ["BFR", "RELOC"]  # 1:1
    chosen = disambiguate_by_surrounding_tokens(cands, types)
    # candidates 순서대로 — BFR 먼저 매칭
    assert chosen.word_type == "BFR"


def test_empty_candidates_raises() -> None:
    with pytest.raises(ValueError):
        disambiguate_by_surrounding_tokens([], ["RELOC"])


def test_first_candidate_when_no_word_type_attr() -> None:
    """word_type 없는 candidate 들 → fallback (confidence_score 없으면 첫 후보)."""
    cands = [_T("a"), _T("b")]
    chosen = disambiguate_by_surrounding_tokens(cands, ["X"])
    assert chosen.term == "a"
