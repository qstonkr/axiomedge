"""Quality route helpers — DB queries extracted from quality.py."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def _get_db_engine():
    """Create a disposable async engine from settings."""
    from sqlalchemy.ext.asyncio import create_async_engine
    from src.config import get_settings

    return create_async_engine(get_settings().database.database_url)


# ── Golden Set ──────────────────────────────────────────────────────────────


async def query_golden_set(
    *,
    kb_id: str | None,
    status: str | None,
    page: int,
    page_size: int,
) -> dict[str, Any]:
    """List golden set Q&A pairs with optional filters."""
    from sqlalchemy import text

    engine = await _get_db_engine()
    try:
        async with engine.begin() as conn:
            conditions: list[str] = []
            params: dict[str, Any] = {
                "limit": page_size,
                "offset": (page - 1) * page_size,
            }
            if kb_id:
                conditions.append("kb_id = :kb_id")
                params["kb_id"] = kb_id
            if status:
                conditions.append("status = :status")
                params["status"] = status

            where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

            count_row = await conn.execute(
                text(f"SELECT count(*) FROM rag_golden_set {where}"), params
            )
            total = count_row.scalar() or 0

            rows = await conn.execute(
                text(
                    f"SELECT id, kb_id, question, expected_answer, source_document, "
                    f"status, created_at "
                    f"FROM rag_golden_set {where} "
                    f"ORDER BY kb_id, created_at "
                    f"LIMIT :limit OFFSET :offset"
                ),
                params,
            )
            items = [
                {
                    "id": str(r[0]),
                    "kb_id": r[1],
                    "question": r[2],
                    "expected_answer": r[3],
                    "source_document": r[4],
                    "status": r[5],
                    "created_at": r[6].isoformat() if r[6] else None,
                }
                for r in rows.fetchall()
            ]
    finally:
        await engine.dispose()

    return {"items": items, "total": total, "page": page, "page_size": page_size}


_GOLDEN_SET_COLUMNS = frozenset({"status", "question", "expected_answer"})


async def update_golden_set(item_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """Update golden set item (status, question, expected_answer).

    Raises ``ValueError`` for invalid input.
    """
    from sqlalchemy import text

    unknown = set(body.keys()) - _GOLDEN_SET_COLUMNS
    if unknown:
        raise ValueError(
            f"Unknown columns: {sorted(unknown)}. Allowed: {sorted(_GOLDEN_SET_COLUMNS)}"
        )
    updates = {k: v for k, v in body.items() if k in _GOLDEN_SET_COLUMNS}
    if not updates:
        raise ValueError("No valid fields to update")

    set_clause = ", ".join(f"{k} = :{k}" for k in updates)
    updates["id"] = item_id

    engine = await _get_db_engine()
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(f"UPDATE rag_golden_set SET {set_clause} WHERE id = :id"), updates
            )
    finally:
        await engine.dispose()

    return {"ok": True, "id": item_id}


async def delete_golden_set(item_id: str) -> dict[str, Any]:
    """Delete a golden set item."""
    from sqlalchemy import text

    engine = await _get_db_engine()
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM rag_golden_set WHERE id = :id"), {"id": item_id}
            )
    finally:
        await engine.dispose()

    return {"ok": True, "id": item_id}


# ── Eval Results ────────────────────────────────────────────────────────────


async def query_eval_results(
    *,
    eval_id: str | None,
    kb_id: str | None,
    page: int,
    page_size: int,
) -> dict[str, Any]:
    """List evaluation results with optional filters."""
    from sqlalchemy import text

    engine = await _get_db_engine()
    try:
        async with engine.begin() as conn:
            check = await conn.execute(
                text(
                    "SELECT EXISTS (SELECT FROM information_schema.tables "
                    "WHERE table_name = 'rag_eval_results')"
                )
            )
            if not check.scalar():
                return {"items": [], "total": 0, "page": page, "page_size": page_size}

            conditions: list[str] = []
            params: dict[str, Any] = {
                "limit": page_size,
                "offset": (page - 1) * page_size,
            }
            if eval_id:
                conditions.append("eval_id = :eval_id")
                params["eval_id"] = eval_id
            if kb_id:
                conditions.append("kb_id = :kb_id")
                params["kb_id"] = kb_id

            where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

            count_row = await conn.execute(
                text(f"SELECT count(*) FROM rag_eval_results {where}"), params
            )
            total = count_row.scalar() or 0

            rows = await conn.execute(
                text(
                    f"SELECT id, eval_id, kb_id, golden_set_id, question, "
                    f"expected_answer, actual_answer, faithfulness, relevancy, "
                    f"completeness, search_time_ms, created_at, "
                    f"crag_action, crag_confidence, recall_hit "
                    f"FROM rag_eval_results {where} "
                    f"ORDER BY created_at DESC "
                    f"LIMIT :limit OFFSET :offset"
                ),
                params,
            )
            items = [
                {
                    "id": str(r[0]),
                    "eval_id": r[1],
                    "kb_id": r[2],
                    "golden_set_id": str(r[3]) if r[3] else None,
                    "question": r[4],
                    "expected_answer": r[5],
                    "actual_answer": r[6],
                    "faithfulness": r[7],
                    "relevancy": r[8],
                    "completeness": r[9],
                    "search_time_ms": r[10],
                    "created_at": r[11].isoformat() if r[11] else None,
                    "crag_action": r[12] or "",
                    "crag_confidence": float(r[13]) if r[13] else 0.0,
                    "recall_hit": bool(r[14]) if r[14] is not None else None,
                }
                for r in rows.fetchall()
            ]
    finally:
        await engine.dispose()

    return {"items": items, "total": total, "page": page, "page_size": page_size}


async def query_eval_results_summary() -> dict[str, Any]:
    """Get summary of all evaluation runs."""
    from sqlalchemy import text

    engine = await _get_db_engine()
    try:
        async with engine.begin() as conn:
            check = await conn.execute(
                text(
                    "SELECT EXISTS (SELECT FROM information_schema.tables "
                    "WHERE table_name = 'rag_eval_results')"
                )
            )
            if not check.scalar():
                return {"runs": []}

            rows = await conn.execute(
                text(
                    "SELECT eval_id, kb_id, count(*) as cnt, "
                    "round(avg(faithfulness)::numeric, 3) as avg_f, "
                    "round(avg(relevancy)::numeric, 3) as avg_r, "
                    "round(avg(completeness)::numeric, 3) as avg_c, "
                    "round(avg(search_time_ms)::numeric, 1) as avg_time, "
                    "min(created_at) as started_at, "
                    "round(avg(crag_confidence)::numeric, 3) as avg_crag_conf, "
                    "count(CASE WHEN crag_action = 'correct' THEN 1 END) as crag_correct, "
                    "count(CASE WHEN crag_action = 'ambiguous' THEN 1 END) as crag_ambiguous, "
                    "count(CASE WHEN crag_action = 'incorrect' THEN 1 END) as crag_incorrect, "
                    "count(CASE WHEN recall_hit = TRUE THEN 1 END) as recall_hits "
                    "FROM rag_eval_results "
                    "GROUP BY eval_id, kb_id "
                    "ORDER BY started_at DESC"
                )
            )
            runs = [
                {
                    "eval_id": r[0],
                    "kb_id": r[1],
                    "count": r[2],
                    "avg_faithfulness": float(r[3]) if r[3] else 0,
                    "avg_relevancy": float(r[4]) if r[4] else 0,
                    "avg_completeness": float(r[5]) if r[5] else 0,
                    "avg_search_time_ms": float(r[6]) if r[6] else 0,
                    "started_at": r[7].isoformat() if r[7] else None,
                    "avg_crag_confidence": float(r[8]) if r[8] else 0,
                    "crag_correct": r[9] or 0,
                    "crag_ambiguous": r[10] or 0,
                    "crag_incorrect": r[11] or 0,
                    "recall_hits": r[12] or 0,
                }
                for r in rows.fetchall()
            ]
    finally:
        await engine.dispose()

    return {"runs": runs}


# ── Transparency: document-owner count from PostgreSQL ──────────────────────


async def query_document_owner_count(session_factory: Any) -> int:
    """Return the count of document_owners rows."""
    from sqlalchemy import text

    try:
        async with session_factory() as session:
            r = await session.execute(text("SELECT count(*) FROM document_owners"))
            return r.scalar() or 0
    except Exception as e:
        logger.debug("Failed to query document_owners count: %s", e)
        return 0
