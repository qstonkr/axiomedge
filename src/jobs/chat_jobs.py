"""Chat-related arq background tasks.

- auto_title_for_conversation: LLM-summarize first user query → set conv title
- chat_history_purge_sweep: hard-delete rows older than retention_days
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)


async def auto_title_for_conversation(
    ctx: dict[str, Any], conversation_id: str, first_user_query: str,
) -> None:
    repo = ctx["chat_repo"]
    llm = ctx.get("llm")
    max_tokens = int(ctx.get("auto_title_max_tokens", 20))
    fallback_chars = int(ctx.get("auto_title_fallback_chars", 30))

    title = ""
    if llm is not None:
        try:
            prompt = (
                "다음 질의를 한국어 짧은 제목 (10자 이내, 명사구) 으로 요약하라. "
                "출력은 제목만, 따옴표/구두점 없음.\n\n"
                f"질의: {first_user_query}"
            )
            raw = await llm.ainvoke(prompt, max_tokens=max_tokens)
            title = (raw or "").strip().strip('"“”')[:40]
        except Exception as e:  # noqa: BLE001 — LLM failure is best-effort
            logger.warning("auto_title LLM failed: %s — using fallback", e)

    if not title:
        title = first_user_query[:fallback_chars]

    await repo.set_title_if_empty(uuid.UUID(conversation_id), title)


async def chat_history_purge_sweep(ctx: dict[str, Any]) -> dict[str, int]:
    repo = ctx["chat_repo"]
    days = int(ctx.get("chat_retention_days", 90))
    deleted = await repo.purge_older_than(days=days)
    cutoff = datetime.now(UTC) - timedelta(days=days)
    logger.info(
        "chat_history_purge_sweep: deleted=%d cutoff=%s",
        deleted, cutoff.isoformat(),
    )
    return {"deleted": deleted}
