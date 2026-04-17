"""Search answer generation, CRAG evaluation, conflict detection, and logging steps.

Extracted from _search_steps.py for module size management.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from src.config.weights import weights
from src.search.crag_evaluator import RetrievalAction
from src.search.transparency_formatter import SourceType, TransparencyFormatter

logger = logging.getLogger(__name__)


async def _step_generate_answer(
    display_query: str,
    all_chunks: list[dict[str, Any]],
    crag_evaluation: Any,
    include_answer: bool,
    state: dict[str, Any],
) -> tuple[str | None, str, str]:
    """Step 8: Generate answer. Returns (answer, query_type, confidence)."""
    if crag_evaluation and crag_evaluation.action == RetrievalAction.INCORRECT:
        return crag_evaluation.recommendation, "", crag_evaluation.confidence_level.value

    if crag_evaluation and crag_evaluation.confidence_score < weights.search.crag_block_threshold:
        return (
            "검색 결과의 신뢰도가 낮아 정확한 답변을 제공하기 어렵습니다. "
            "질문을 더 구체적으로 해주세요.",
            "", "낮음",
        )

    if not include_answer or not all_chunks:
        return None, "", ""

    answer = await _try_tiered_generation(display_query, all_chunks, state)
    if answer is not None:
        return answer

    # Fallback to AnswerService
    answer_service = state.get("answer_service")
    if answer_service:
        result = await answer_service.enrich(display_query, all_chunks)
        return result.answer, result.query_type, result.confidence_indicator

    return None, "", ""


async def _try_tiered_generation(
    display_query: str,
    all_chunks: list[dict[str, Any]],
    state: dict[str, Any],
) -> tuple[str, str, str] | None:
    """Try TieredResponseGenerator. Returns (answer, query_type, confidence) or None."""
    tiered_gen = state.get("tiered_response_generator")
    llm = state.get("llm")
    if not tiered_gen or not llm:
        return None

    try:
        from src.search.query_classifier import QueryClassifier
        from src.search.tiered_response import RAGContext

        classifier = state.get("query_classifier") or QueryClassifier()
        classification = classifier.classify(display_query)

        rag_context = RAGContext(
            query=display_query,
            retrieved_chunks=[c.get("content", "") for c in all_chunks],
            chunk_sources=[
                {
                    "document_name": c.get("document_name", ""),
                    "source_uri": c.get("source_uri", ""),
                    "score": c.get("score", 0),
                    "metadata": c.get("metadata", {}),
                }
                for c in all_chunks
            ],
            relevance_scores=[c.get("score", 0.0) for c in all_chunks],
        )

        tiered_result = await tiered_gen.generate(
            query_type=classification.query_type, context=rag_context,
        )

        if tiered_result.confidence >= weights.search.confidence_display_high:
            confidence = "높음"
        elif tiered_result.confidence >= weights.search.confidence_display_medium:
            confidence = "보통"
        else:
            confidence = "낮음"
        return tiered_result.content, tiered_result.query_type.value, confidence
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
        logger.warning("TieredResponseGenerator failed, falling back to AnswerService: %s", e)
        return None


def _check_kb_pair_conflict(
    kb_a: str, kb_b: str,
    kb_answers: dict[str, list[str]],
    threshold: float,
) -> dict[str, Any] | None:
    """Check if two KBs have conflicting answers based on word overlap."""
    words_a = set(" ".join(kb_answers[kb_a][:3]).split())
    words_b = set(" ".join(kb_answers[kb_b][:3]).split())
    if not words_a or not words_b:
        return None
    overlap = len(words_a & words_b) / min(len(words_a), len(words_b))
    if overlap >= threshold:
        return None
    return {
        "kb_a": kb_a, "kb_b": kb_b,
        "overlap_ratio": round(overlap, 3),
        "warning": f"KB '{kb_a}'와 '{kb_b}'의 답변이 상이할 수 있습니다.",
    }


def _step_detect_conflicts(
    all_chunks: list[dict[str, Any]],
    searched_kbs: list[str],
) -> list[dict[str, Any]]:
    """Step 9.5: Detect contradictory answers from different KBs."""
    if len(searched_kbs) <= 1 or not all_chunks:
        return []

    kb_answers: dict[str, list[str]] = {}
    for c in all_chunks[:10]:
        kb = c.get("kb_id", "")
        content = c.get("content", "")[:200]
        if kb and content:
            kb_answers.setdefault(kb, []).append(content)

    if len(kb_answers) <= 1:
        return []

    conflicts: list[dict[str, Any]] = []
    kb_list = list(kb_answers.keys())
    threshold = weights.search.conflict_overlap_threshold
    for i in range(len(kb_list)):
        for j in range(i + 1, len(kb_list)):
            conflict = _check_kb_pair_conflict(
                kb_list[i], kb_list[j], kb_answers, threshold,
            )
            if conflict:
                conflicts.append(conflict)
    return conflicts


async def _step_follow_ups(
    display_query: str,
    answer: str | None,
    all_chunks: list[dict[str, Any]],
    include_answer: bool,
    state: dict[str, Any],
) -> list[str]:
    """Step 9.6: Generate follow-up questions."""
    if not include_answer or not answer or not all_chunks:
        return []
    try:
        llm = state.get("llm")
        if not llm:
            return []
        prompt = (
            "다음 질문과 답변을 바탕으로, "
            "사용자가 추가로 궁금해할 수 있는 후속 질문 3개를 "
            "생성하세요.\n"
            "각 질문은 한 줄씩, 번호 없이 작성하세요.\n\n"
            f"질문: {display_query}\n"
            f"답변: {answer[:500]}\n\n후속 질문:"
        )
        result = await llm.generate(
            prompt, temperature=0.3, max_tokens=200,
        )
        if result:
            lines = result.strip().split("\n")
            return [
                q.strip().lstrip("- ·•123.")
                for q in lines if q.strip()
            ][:3]
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
        logger.debug("Follow-up generation skipped: %s", e)
    return []


# Map hub_search query_type -> TransparencyFormatter SourceType
_QUERY_TYPE_TO_SOURCE: dict[str, SourceType] = {
    "factual": SourceType.DOCUMENT,
    "analytical": SourceType.INFERENCE,
    "advisory": SourceType.GENERAL,
}
# Map Korean confidence labels -> TransparencyFormatter confidence keys
_CONFIDENCE_KO_TO_EN: dict[str, str] = {
    "높음": "high",
    "보통": "medium",
    "낮음": "low",
}


def _step_build_transparency(
    answer: str | None,
    query_type: str,
    confidence: str,
) -> dict[str, Any] | None:
    """Step 9.5: Build transparency metadata for the response.

    Feature-gated via SEARCH_TRANSPARENCY_ENABLED (default: true).
    Reuses TransparencyFormatter constants for label consistency.
    """
    if os.environ.get("SEARCH_TRANSPARENCY_ENABLED", "true").lower() == "false":
        return None
    if not answer:
        return None

    source_type = _QUERY_TYPE_TO_SOURCE.get(query_type, SourceType.DOCUMENT)
    source_label = TransparencyFormatter.SOURCE_LABELS.get(source_type, "")
    confidence_key = _CONFIDENCE_KO_TO_EN.get(confidence, "")
    confidence_indicator = TransparencyFormatter.CONFIDENCE_INDICATORS.get(confidence_key, "")

    return {
        "source_type": source_type.value,
        "source_label": source_label,
        "confidence_indicator": confidence_indicator,
    }


async def _step_crag_evaluate(
    display_query: str,
    all_chunks: list[dict[str, Any]],
    start: float,
    state: dict[str, Any],
) -> Any:
    """Step 7: CRAG evaluation."""
    crag_evaluator = state.get("crag_evaluator")
    if not crag_evaluator or not all_chunks:
        return None
    try:
        crag_evaluation = await crag_evaluator.evaluate(
            display_query, all_chunks, search_time_ms=(time.time() - start) * 1000,
        )
        logger.info(
            "CRAG evaluation: action=%s confidence=%.3f level=%s",
            crag_evaluation.action.value,
            crag_evaluation.confidence_score,
            crag_evaluation.confidence_level.value,
        )
        return crag_evaluation
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
        logger.warning("CRAG evaluation failed: %s", e)
        return None


async def _step_log_usage(
    query: str,
    display_query: str,
    all_chunks: list[dict[str, Any]],
    elapsed: float,
    collections: list[str],
    request: Any,
    answer: str | None,
    follow_ups: list[str],
    rerank_applied: bool,
    state: dict[str, Any],
    crag_evaluation: Any = None,
) -> None:
    """Log search usage to repository."""
    usage_repo = state.get("usage_log_repo")
    if not usage_repo:
        return
    try:
        context: dict[str, Any] = {
            "query": query, "display_query": display_query,
            "total_chunks": len(all_chunks), "search_time_ms": elapsed,
            "mode": request.mode, "group_name": request.group_name,
            "embed_calls": 1, "llm_calls": 1 if answer else 0,
            "cross_encoder_calls": 1 if all_chunks else 0,
            "follow_up_generated": len(follow_ups) > 0,
            "rerank_applied": rerank_applied,
        }
        # CRAG 평가 결과 (distill 학습 데이터 품질 필터용)
        if crag_evaluation:
            context["crag_action"] = crag_evaluation.action.value
            context["crag_confidence"] = crag_evaluation.confidence_score

        # Distill 학습 데이터용 (answer + chunks)
        from src.config import get_settings
        if get_settings().distill.log_full_context:
            context["answer"] = answer
            context["chunks"] = [
                {
                    "content": c.get("content", "")[:500],
                    "document_name": c.get("document_name", ""),
                    "score": round(c.get("score", 0), 4),
                }
                for c in all_chunks[:5]
            ]
        await usage_repo.log_search(
            knowledge_id=query, kb_id=",".join(collections),
            user_id="local-user", usage_type="hub_search",
            context=context,
        )
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
        logger.warning("Failed to log hub_search usage: %s", e, exc_info=True)
