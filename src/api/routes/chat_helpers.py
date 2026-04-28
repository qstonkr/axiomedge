"""Helpers for /chat routes.

route_query: server-side mode router. Replaces user-facing ModeToggle.
Threshold values are explicit constants — adjust via config in v2 if needed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

logger = logging.getLogger(__name__)

AMBIGUITY_AGENTIC_THRESHOLD = 0.6

# QueryType → mode mapping. The QueryClassifier returns one of:
#   CHITCHAT, FACTUAL, ANALYTICAL, ADVISORY, COMPARATIVE, MULTI_HOP, UNKNOWN
# Multi-step / analytical types want agentic; lookups stay on the fast path.
_AGENTIC_QUERY_TYPES = frozenset({"multi_hop", "comparative", "analytical", "advisory"})


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


def derive_signals(query: str, classifier) -> RoutingSignals:
    """Adapter — pull existing classifier outputs into RoutingSignals.

    `classifier` is `QueryClassifier` from src.search.query_classifier.
    Its real surface is the *sync* ``classify(query) -> ClassificationResult``
    with fields (query_type, confidence, matched_patterns). We map those into
    RoutingSignals so route_query can stay schema-stable.

    Failure modes (None classifier, unexpected shape, raise) all degrade to
    the conservative "search" defaults so chat send never 500s on routing.
    """
    if classifier is None:
        return RoutingSignals(intent_count=1, requires_followup=False, ambiguity_score=0.0)
    try:
        out = classifier.classify(query)
    except Exception as e:  # noqa: BLE001 — routing is best-effort, never block send
        logger.warning("query classifier failed — defaulting to search: %s", e)
        return RoutingSignals(intent_count=1, requires_followup=False, ambiguity_score=0.0)

    qtype = getattr(getattr(out, "query_type", None), "value", "factual")
    confidence = float(getattr(out, "confidence", 0.5) or 0.5)

    # MULTI_HOP signals multi-step → intent_count > 1; the rest use ambiguity_score.
    intent_count = 2 if qtype == "multi_hop" else 1
    # Other agentic types tip via ambiguity_score (1 - confidence). Low-confidence
    # FACTUAL is also nudged toward agentic when confidence is poor.
    if qtype in _AGENTIC_QUERY_TYPES:
        ambiguity_score = max(0.7, 1.0 - confidence)
    else:
        ambiguity_score = max(0.0, 1.0 - confidence) if confidence < 0.5 else 0.0
    return RoutingSignals(
        intent_count=intent_count,
        requires_followup=False,  # current classifier doesn't surface this signal
        ambiguity_score=ambiguity_score,
    )
