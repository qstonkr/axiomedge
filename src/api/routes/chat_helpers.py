"""Helpers for /chat routes.

route_query: server-side mode router. Replaces user-facing ModeToggle.
Threshold values are explicit constants — adjust via config in v2 if needed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

AMBIGUITY_AGENTIC_THRESHOLD = 0.6


@dataclass
class RoutingSignals:
    intent_count: int
    requires_followup: bool
    ambiguity_score: float


def route_query(
    signals: RoutingSignals,
    force_mode: Literal["quick", "deep", None] = None,
) -> Literal["search", "agentic"]:
    if force_mode == "quick":
        return "search"
    if force_mode == "deep":
        return "agentic"
    if signals.intent_count > 1:
        return "agentic"
    if signals.requires_followup:
        return "agentic"
    if signals.ambiguity_score > AMBIGUITY_AGENTIC_THRESHOLD:
        return "agentic"
    return "search"


async def derive_signals(query: str, classifier) -> RoutingSignals:
    """Adapter — pull existing classifier outputs into RoutingSignals.

    `classifier` is `QueryClassifier` from src.search. We tolerate missing
    fields (older classifier versions) and default conservatively.
    """
    if classifier is None:
        return RoutingSignals(intent_count=1, requires_followup=False, ambiguity_score=0.0)
    out = await classifier.analyze(query)
    return RoutingSignals(
        intent_count=int(getattr(out, "intent_count", 1) or 1),
        requires_followup=bool(getattr(out, "requires_followup", False)),
        ambiguity_score=float(getattr(out, "ambiguity_score", 0.0) or 0.0),
    )
