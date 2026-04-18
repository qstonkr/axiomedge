# pyright: reportGeneralTypeIssues=false
"""Ingestion pipeline — dedup, quality check, ingestion gate, term/synonym extraction.

Extracted from ingestion.py for module size management.
"""

from __future__ import annotations

import logging
from typing import Any

from src.core.models import IngestionResult, RawDocument
from .quality_processor import (
    QualityTier,
    QualityMetrics,
    _calculate_metrics,
    _determine_quality_tier,
)

logger = logging.getLogger(__name__)


async def check_dedup(
    raw: RawDocument,
    collection_name: str,
    content_hash: str,
    dedup_pipeline: Any | None,
    dedup_cache: Any | None,
) -> tuple[IngestionResult | None, dict[str, Any]]:
    """Run dedup checks. Returns (failure_result, dedup_info) or (None, dedup_info)."""
    dedup_result_info: dict[str, Any] = {}

    if raw.metadata.get("force_rebuild", False):
        return None, dedup_result_info

    if dedup_pipeline is not None:
        try:
            from src.pipelines.dedup import Document as DedupDoc, DedupStatus
            dedup_doc = DedupDoc(
                doc_id=raw.doc_id, title=raw.title, content=raw.content,
                url=raw.source_uri, updated_at=raw.updated_at,
            )
            dedup_result = await dedup_pipeline.add(dedup_doc)
            dedup_result_info = dedup_result.to_dict()
            if dedup_result.status == DedupStatus.EXACT_DUPLICATE:
                logger.info(
                    "Dedup(4-stage): exact duplicate doc_id=%s dup_of=%s (%.1fms)",
                    raw.doc_id, dedup_result.duplicate_of, dedup_result.processing_time_ms,
                )
                return IngestionResult.failure_result(
                    reason=(
                        f"Exact duplicate of {dedup_result.duplicate_of}"
                        " (dedup pipeline Stage 1)"
                    ),
                    stage="dedup",
                ), dedup_result_info
            if dedup_result.status in (DedupStatus.NEAR_DUPLICATE, DedupStatus.SEMANTIC_DUPLICATE):
                logger.info(
                    "Dedup(4-stage): %s doc_id=%s dup_of=%s score=%.3f (%.1fms) - proceeding",
                    dedup_result.status.value, raw.doc_id,
                    dedup_result.duplicate_of, dedup_result.similarity_score,
                    dedup_result.processing_time_ms,
                )
        except (  # noqa: BLE001
        RuntimeError, OSError, ValueError, TypeError,
        KeyError, AttributeError, ImportError,
    ) as _dedup_err:
            logger.warning("Dedup pipeline check failed, proceeding: %s", _dedup_err)
    elif dedup_cache is not None:
        try:
            if await dedup_cache.exists(collection_name, content_hash):
                logger.info(
                    "Dedup: skipping duplicate doc_id=%s in %s (hash=%s)",
                    raw.doc_id, collection_name, content_hash[:12],
                )
                return IngestionResult.failure_result(
                    reason="Duplicate content (dedup cache hit)", stage="dedup",
                ), dedup_result_info
        except (  # noqa: BLE001
        RuntimeError, OSError, ValueError, TypeError,
        KeyError, AttributeError, ImportError,
    ) as _dedup_err:
            logger.warning("Dedup cache check failed, proceeding: %s", _dedup_err)

    return None, dedup_result_info


def check_ingestion_gate(
    raw: RawDocument,
    collection_name: str,
    ingestion_gate: Any | None,
) -> IngestionResult | None:
    """Check ingestion gate. Returns failure result if blocked, None if allowed."""
    if ingestion_gate is None:
        return None
    gate_result = ingestion_gate.run_gates(raw, collection_name)
    if gate_result.is_blocked:
        logger.info(
            "Ingestion gate blocked doc_id=%s: action=%s, failed=%d",
            raw.doc_id, gate_result.action.value, gate_result.failed_count,
        )
        return IngestionResult.failure_result(
            reason=(
                f"Ingestion gate: {gate_result.action.value}"
                f" ({gate_result.failed_count} check(s) failed)"
            ),
            stage="ingestion_gate",
        )
    return None


def check_quality(
    raw: RawDocument,
    enable_quality_filter: bool,
    min_quality_tier: QualityTier,
) -> tuple[QualityTier, QualityMetrics | None, IngestionResult | None]:
    """Run quality check. Returns (tier, metrics, failure_or_None)."""
    quality_tier = QualityTier.BRONZE
    quality_metrics: QualityMetrics | None = None
    if not enable_quality_filter:
        return quality_tier, quality_metrics, None
    quality_metrics = _calculate_metrics(raw.content)
    quality_tier = _determine_quality_tier(quality_metrics)
    tier_order = [QualityTier.NOISE, QualityTier.BRONZE, QualityTier.SILVER, QualityTier.GOLD]
    if tier_order.index(quality_tier) < tier_order.index(min_quality_tier):
        return quality_tier, quality_metrics, IngestionResult.failure_result(
            reason=f"Document quality {quality_tier.value} below minimum {min_quality_tier.value}",
            stage="quality_check",
        )
    return quality_tier, quality_metrics, None


async def run_term_extraction(
    raw: RawDocument,
    typed_chunks: list[tuple[str, str, str]],
    collection_name: str,
    enable_term_extraction: bool,
    term_extractor: Any | None,
) -> dict[str, Any]:
    """Run optional term extraction and synonym discovery."""
    stats: dict[str, Any] = {}
    if not enable_term_extraction or term_extractor is None:
        return stats

    try:
        chunk_texts = [ct for ct, _, _ in typed_chunks]
        extracted_terms = await term_extractor.extract_from_chunks(
            chunk_texts, kb_id=collection_name,
        )
        if extracted_terms:
            saved_count = await term_extractor.save_extracted_terms(
                extracted_terms, kb_id=collection_name,
            )
            stats = {"terms_extracted": len(extracted_terms), "terms_saved": saved_count}
            logger.info(
                "Term extraction completed for doc_id=%s: %d extracted, %d saved",
                raw.doc_id, len(extracted_terms), saved_count,
            )
    except (  # noqa: BLE001
        RuntimeError, OSError, ValueError, TypeError,
        KeyError, AttributeError, ImportError,
    ) as e:
        logger.warning("Term extraction failed for doc_id=%s: %s", raw.doc_id, e)
        stats = {"error": str(e)}
    return stats


async def run_synonym_discovery(
    raw: RawDocument,
    collection_name: str,
    enable_term_extraction: bool,
    term_extractor: Any | None,
) -> dict[str, Any]:
    """Run optional synonym discovery."""
    stats: dict[str, Any] = {}
    if not enable_term_extraction or term_extractor is None:
        return stats

    try:
        discover_fn = getattr(term_extractor, "discover_synonyms", None)
        save_syn_fn = getattr(term_extractor, "save_discovered_synonyms", None)
        if not discover_fn or not save_syn_fn:
            return stats

        glossary_repo = getattr(term_extractor, "_glossary_repo", None)
        known_terms: list[dict[str, Any]] = []
        if glossary_repo:
            list_fn = getattr(glossary_repo, "list_by_kb", None)
            if list_fn and callable(list_fn):
                try:
                    known_terms = await list_fn(
                        kb_id=collection_name, status="approved", limit=500, offset=0,
                    )
                except (  # noqa: BLE001
                    RuntimeError, OSError, ValueError,
                    TypeError, KeyError, AttributeError,
                    ImportError,
                ):
                    known_terms = []

        discoveries = await discover_fn(raw.content, known_terms)
        if discoveries:
            syn_saved = await save_syn_fn(discoveries, kb_id=collection_name)
            stats = {"synonyms_discovered": len(discoveries), "synonyms_saved": syn_saved}
            logger.info(
                "Synonym discovery completed for doc_id=%s: %d found, %d saved",
                raw.doc_id, len(discoveries), syn_saved,
            )
    except (  # noqa: BLE001
        RuntimeError, OSError, ValueError, TypeError,
        KeyError, AttributeError, ImportError,
    ) as e:
        logger.warning("Synonym discovery failed for doc_id=%s: %s", raw.doc_id, e)
        stats = {"error": str(e)}
    return stats
