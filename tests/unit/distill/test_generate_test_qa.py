"""generate_test_qa — existing_questions 의무화 (train/test 누수 차단)."""

from unittest.mock import AsyncMock

import pytest

from src.distill.data_gen.test_data_templates import generate_test_qa


@pytest.mark.asyncio
async def test_raises_when_existing_questions_is_none() -> None:
    """기존엔 None default 였음 — 우리 변경으로 의무화."""
    with pytest.raises(ValueError, match="existing_questions is required"):
        await generate_test_qa(
            llm_client=AsyncMock(),
            qdrant_url="http://localhost:6333",
            kb_ids=["kb-x"],
            count=10,
            existing_questions=None,  # type: ignore[arg-type]
        )
