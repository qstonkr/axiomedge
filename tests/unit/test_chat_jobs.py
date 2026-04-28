import uuid
from unittest.mock import AsyncMock

import pytest

from src.jobs.chat_jobs import auto_title_for_conversation


@pytest.mark.asyncio
async def test_auto_title_writes_short_title():
    repo = AsyncMock()
    llm = AsyncMock()
    llm.ainvoke.return_value = "신촌 점검"
    ctx = {
        "chat_repo": repo,
        "llm": llm,
        "auto_title_max_tokens": 20,
        "auto_title_fallback_chars": 30,
    }
    await auto_title_for_conversation(
        ctx, str(uuid.uuid4()), "신촌점 차주 점검 일정 알려줘",
    )
    repo.set_title_if_empty.assert_awaited()
    args = repo.set_title_if_empty.await_args.args
    assert args[1] == "신촌 점검"


@pytest.mark.asyncio
async def test_auto_title_fallback_on_llm_failure():
    repo = AsyncMock()
    llm = AsyncMock()
    llm.ainvoke.side_effect = RuntimeError("llm down")
    ctx = {
        "chat_repo": repo,
        "llm": llm,
        "auto_title_max_tokens": 20,
        "auto_title_fallback_chars": 30,
    }
    await auto_title_for_conversation(
        ctx, str(uuid.uuid4()), "신촌점 차주 점검 일정 알려줘",
    )
    repo.set_title_if_empty.assert_awaited()
    args = repo.set_title_if_empty.await_args.args
    assert args[1] == "신촌점 차주 점검 일정 알려줘"  # full input ≤30 chars
