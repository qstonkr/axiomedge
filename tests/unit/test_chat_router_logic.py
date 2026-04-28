import pytest
from src.api.routes.chat_helpers import RoutingSignals, route_query


@pytest.mark.parametrize("force,expected", [
    ("quick", "search"),
    ("deep", "agentic"),
])
def test_force_mode_overrides(force, expected):
    sig = RoutingSignals(intent_count=1, requires_followup=False, ambiguity_score=0.0)
    assert route_query(sig, force_mode=force) == expected


def test_multi_intent_routes_agentic():
    sig = RoutingSignals(intent_count=2, requires_followup=False, ambiguity_score=0.0)
    assert route_query(sig, force_mode=None) == "agentic"


def test_followup_routes_agentic():
    sig = RoutingSignals(intent_count=1, requires_followup=True, ambiguity_score=0.0)
    assert route_query(sig, force_mode=None) == "agentic"


def test_high_ambiguity_routes_agentic():
    sig = RoutingSignals(intent_count=1, requires_followup=False, ambiguity_score=0.7)
    assert route_query(sig, force_mode=None) == "agentic"


def test_simple_query_routes_search():
    sig = RoutingSignals(intent_count=1, requires_followup=False, ambiguity_score=0.2)
    assert route_query(sig, force_mode=None) == "search"
