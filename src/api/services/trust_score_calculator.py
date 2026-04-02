"""Trust score calculation service — compute KTS from Qdrant metadata."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime as dt, timezone as tz
from typing import Any

import httpx

from src.config_weights import weights as _w

logger = logging.getLogger(__name__)


async def _scroll_qdrant_docs(
    collection_name: str, qdrant_url: str,
) -> dict[str, dict]:
    """Scroll through Qdrant collection and return unique docs keyed by doc_id."""
    docs: dict[str, dict] = {}
    offset = None

    async with httpx.AsyncClient(timeout=15.0) as client:
        while True:
            body: dict[str, Any] = {
                "limit": 100,
                "with_payload": [
                    "doc_id", "quality_score", "owner", "l1_category",
                    "source_uri", "ingested_at", "source_type",
                ],
                "with_vector": False,
            }
            if offset:
                body["offset"] = offset
            resp = await client.post(
                f"{qdrant_url}/collections/{collection_name}/points/scroll",
                json=body,
            )
            if resp.status_code != 200:
                break
            data = resp.json().get("result", {})
            points = data.get("points", [])
            if not points:
                break
            for p in points:
                pay = p["payload"]
                did = pay.get("doc_id", "")
                if did and did not in docs:
                    docs[did] = pay
            offset = data.get("next_page_offset")
            if not offset:
                break

    return docs


def _compute_freshness(doc_date: str, now: dt) -> float:
    """Compute freshness score from a document date string."""
    _qc = _w.quality
    if not doc_date:
        return _qc.kts_freshness_default
    try:
        ing_dt = dt.fromisoformat(doc_date.replace("Z", "+00:00"))
        days = (now - ing_dt).days
        if days < 30:
            return _qc.kts_freshness_30d
        if days < 90:
            return _qc.kts_freshness_90d
        if days < 180:
            return _qc.kts_freshness_180d
        return _qc.kts_freshness_old
    except (ValueError, TypeError):
        return _qc.kts_freshness_default


def _compute_kts(pay: dict[str, Any], now: dt) -> tuple[float, str]:
    """Compute KTS score and confidence tier for a single document."""
    _qc = _w.quality
    quality = pay.get("quality_score", 50) / 100
    has_source = 1.0 if pay.get("source_uri") else 0.0
    has_category = (
        _qc.kts_has_metadata_high
        if pay.get("l1_category") and pay.get("l1_category") != "기타"
        else _qc.kts_has_metadata_low
    )
    has_owner = _qc.kts_has_metadata_high if pay.get("owner") else _qc.kts_has_metadata_low

    doc_date = pay.get("last_modified", pay.get("ingested_at", ""))
    freshness = _compute_freshness(doc_date, now)

    kts = (
        0.25 * quality
        + 0.20 * has_source
        + 0.20 * freshness
        + 0.15 * has_category
        + 0.10 * 0.5
        + 0.10 * has_owner
    )

    if kts >= _qc.kts_tier_high:
        tier = "high"
    elif kts >= _qc.kts_tier_medium:
        tier = "medium"
    else:
        tier = "low"

    return kts, tier


async def _save_trust_score(
    trust_repo: Any, doc_id: str, kb_id: str, pay: dict[str, Any],
    kts: float, tier: str, now: dt,
) -> bool:
    """Save a single trust score entry. Returns True on success."""
    _qc = _w.quality
    quality = pay.get("quality_score", 50) / 100
    has_source = 1.0 if pay.get("source_uri") else 0.0
    has_category = (
        _qc.kts_has_metadata_high
        if pay.get("l1_category") and pay.get("l1_category") != "기타"
        else _qc.kts_has_metadata_low
    )
    has_owner = _qc.kts_has_metadata_high if pay.get("owner") else _qc.kts_has_metadata_low
    doc_date = pay.get("last_modified", pay.get("ingested_at", ""))
    freshness = _compute_freshness(doc_date, now)

    try:
        await trust_repo.save({
            "id": str(uuid.uuid4()),
            "entry_id": doc_id,
            "kb_id": kb_id,
            "kts_score": round(kts, 3),
            "confidence_tier": tier,
            "source_credibility": round(has_source, 2),
            "freshness_score": round(freshness, 2),
            "hallucination_score": round(quality, 2),
            "consistency_score": round(has_category, 2),
            "usage_score": 0.5,
            "user_validation_score": round(has_owner, 2),
            "source_type": pay.get("source_type", "file"),
            "last_evaluated_at": now,
        })
        return True
    except Exception:
        return False


async def calculate_kb_trust_scores(
    kb_id: str,
    trust_repo: Any,
    collection_name: str,
    qdrant_url: str = "http://localhost:6333",
) -> dict[str, Any]:
    """Calculate KTS (Knowledge Trust Score) for all documents in a KB.

    Scrolls through Qdrant collection, computes 6-signal trust score,
    and saves to PostgreSQL via trust_repo.
    """
    now = dt.now(tz.utc)
    saved = 0
    errors = 0

    try:
        docs = await _scroll_qdrant_docs(collection_name, qdrant_url)

        for doc_id, pay in docs.items():
            kts, tier = _compute_kts(pay, now)
            if await _save_trust_score(trust_repo, doc_id, kb_id, pay, kts, tier, now):
                saved += 1
            else:
                errors += 1

        return {
            "success": True,
            "kb_id": kb_id,
            "documents_processed": len(docs),
            "scores_saved": saved,
            "errors": errors,
        }
    except Exception as e:
        logger.error("Trust score calculation failed: %s", e)
        from fastapi import HTTPException
        raise HTTPException(status_code=500, detail=f"Trust score calculation failed: {e}")
