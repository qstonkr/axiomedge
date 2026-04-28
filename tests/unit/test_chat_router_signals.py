"""derive_signals adapter against the real QueryClassifier.

The PR1 unit suite (test_chat_router_logic.py) only exercises route_query
with hand-rolled RoutingSignals, never the adapter that bridges the live
classifier into them. That gap masked a method-name typo (``analyze`` vs
``classify``) until production. This test calls the real classifier so a
future signature drift breaks here, not in 500-error logs.
"""

from __future__ import annotations

import pytest

from src.api.routes.chat_helpers import derive_signals, route_query
from src.search.query_classifier import QueryClassifier


@pytest.fixture
def classifier() -> QueryClassifier:
    return QueryClassifier()


def test_derive_signals_factual_question_routes_search(classifier):
    sig = derive_signals("담당자 누구야?", classifier)
    assert route_query(sig, force_mode=None) == "search"


def test_derive_signals_multi_hop_routes_agentic(classifier):
    sig = derive_signals("먼저 검색하고 다음에 비교해줘", classifier)
    assert sig.intent_count >= 2
    assert route_query(sig, force_mode=None) == "agentic"


def test_derive_signals_comparative_routes_agentic(classifier):
    sig = derive_signals("A 와 B 차이 뭐야", classifier)
    assert route_query(sig, force_mode=None) == "agentic"


def test_derive_signals_handles_none_classifier():
    """Adapter never blows up on a None classifier — graceful degradation."""
    sig = derive_signals("아무거나", None)
    assert route_query(sig, force_mode=None) == "search"


def test_derive_signals_handles_classifier_exception():
    """Adapter tolerates classifier failures and falls back to search."""

    class BadClassifier:
        def classify(self, query: str):
            raise RuntimeError("boom")

    sig = derive_signals("hi", BadClassifier())
    assert route_query(sig, force_mode=None) == "search"
