"""LLMHelper.call — tenacity retry 검증.

distill teacher LLM 호출의 transient failure (httpx 일시 오류 / asyncio
TimeoutError / RuntimeError) 를 자동 재시도. ValueError 같은 의미 변환
실패는 retry 안 함 (재시도해도 같은 결과).
"""

from __future__ import annotations

import asyncio
import logging as _logging

import httpx
import pytest


class _FakeLLM:
    """attempts 횟수 만큼 raise 후 success 반환하는 mock."""

    def __init__(self, *, fail_n: int, exc: Exception, success: str = "OK") -> None:
        self.calls = 0
        self.fail_n = fail_n
        self.exc = exc
        self.success = success

    async def generate(self, prompt: str, temperature: float = 0.7) -> str:
        self.calls += 1
        if self.calls <= self.fail_n:
            raise self.exc
        return self.success


@pytest.mark.asyncio
async def test_call_retries_transient_httpx_error() -> None:
    """httpx.ConnectError 같은 transient → 2회 retry 후 성공."""
    from src.distill.data_gen.llm_helper import LLMHelper

    fake = _FakeLLM(
        fail_n=2,
        exc=httpx.ConnectError("connection refused"),
    )
    helper = LLMHelper(fake, qdrant_url="", concurrency=2, timeout_sec=10)
    result = await helper.call("hi")

    assert result == "OK"
    assert fake.calls == 3, f"expected 3 attempts (2 retry + success), got {fake.calls}"


@pytest.mark.asyncio
async def test_call_retries_transient_runtime_error() -> None:
    """RuntimeError (e.g. SageMaker invoke transient) → retry."""
    from src.distill.data_gen.llm_helper import LLMHelper

    fake = _FakeLLM(fail_n=2, exc=RuntimeError("SageMaker invoke failed"))
    helper = LLMHelper(fake, qdrant_url="", concurrency=2, timeout_sec=10)
    result = await helper.call("hi")

    assert result == "OK"
    assert fake.calls == 3


@pytest.mark.asyncio
async def test_call_retries_timeout_error() -> None:
    """asyncio.TimeoutError → retry."""
    from src.distill.data_gen.llm_helper import LLMHelper

    fake = _FakeLLM(fail_n=1, exc=asyncio.TimeoutError())
    helper = LLMHelper(fake, qdrant_url="", concurrency=2, timeout_sec=10)
    result = await helper.call("hi")

    assert result == "OK"
    assert fake.calls == 2


@pytest.mark.asyncio
async def test_call_does_not_retry_value_error() -> None:
    """ValueError 같은 의미 변환 실패는 retry 무의미 — 즉시 빈 결과."""
    from src.distill.data_gen.llm_helper import LLMHelper

    fake = _FakeLLM(fail_n=1, exc=ValueError("bad output"))
    helper = LLMHelper(fake, qdrant_url="", concurrency=2, timeout_sec=10)
    result = await helper.call("hi")

    assert result == ""
    assert fake.calls == 1


@pytest.mark.asyncio
async def test_call_gives_up_after_max_attempts() -> None:
    """3회 모두 실패 → 빈 string + 호출 정확히 3번."""
    from src.distill.data_gen.llm_helper import LLMHelper

    fake = _FakeLLM(fail_n=99, exc=httpx.ConnectError("down"))
    helper = LLMHelper(fake, qdrant_url="", concurrency=2, timeout_sec=10)
    result = await helper.call("hi")

    assert result == ""
    assert fake.calls == 3, f"expected exactly 3 attempts, got {fake.calls}"


@pytest.mark.asyncio
async def test_call_success_first_try_no_retry() -> None:
    """첫 시도 성공 시 retry 없음."""
    from src.distill.data_gen.llm_helper import LLMHelper

    fake = _FakeLLM(fail_n=0, exc=RuntimeError("never"))
    helper = LLMHelper(fake, qdrant_url="", concurrency=2, timeout_sec=10)
    result = await helper.call("hi")

    assert result == "OK"
    assert fake.calls == 1


@pytest.mark.asyncio
async def test_retry_emits_metric_log(caplog) -> None:
    """retry 발생 시 _RETRY_LOGGER 에 INFO 로그 (운영 dashboard grep 추적용)."""
    from src.distill.data_gen.llm_helper import LLMHelper

    fake = _FakeLLM(fail_n=2, exc=httpx.ConnectError("transient"))
    helper = LLMHelper(fake, qdrant_url="", concurrency=2, timeout_sec=10)

    with caplog.at_level(_logging.INFO, logger="src.distill.data_gen.llm_helper.retry"):
        result = await helper.call("hi")

    assert result == "OK"
    metric_records = [
        r for r in caplog.records
        if r.name == "src.distill.data_gen.llm_helper.retry"
    ]
    # 2회 retry → 2개 metric log (before_sleep 은 retry 직전 호출).
    assert len(metric_records) >= 2, (
        f"expected 2+ retry metric logs, got {len(metric_records)}: "
        f"{[r.getMessage() for r in metric_records]}"
    )


@pytest.mark.asyncio
async def test_no_retry_no_metric_log(caplog) -> None:
    """첫 시도 성공 시 metric 로그 없음 — retry rate baseline 0 검증."""
    from src.distill.data_gen.llm_helper import LLMHelper

    fake = _FakeLLM(fail_n=0, exc=RuntimeError("never"))
    helper = LLMHelper(fake, qdrant_url="", concurrency=2, timeout_sec=10)

    with caplog.at_level(_logging.INFO, logger="src.distill.data_gen.llm_helper.retry"):
        await helper.call("hi")

    metric_records = [
        r for r in caplog.records
        if r.name == "src.distill.data_gen.llm_helper.retry"
    ]
    assert len(metric_records) == 0


@pytest.mark.asyncio
async def test_call_catches_aws_client_error() -> None:
    """SageMaker invoke leak (ClientError) → fail-soft 빈 결과."""
    from botocore.exceptions import ClientError

    from src.distill.data_gen.llm_helper import LLMHelper

    err = ClientError(
        {"Error": {"Code": "ValidationException", "Message": "bad input"}},
        "InvokeEndpoint",
    )
    fake = _FakeLLM(fail_n=1, exc=err)
    helper = LLMHelper(fake, qdrant_url="", concurrency=2, timeout_sec=10)
    result = await helper.call("hi")

    # ClientError 는 LLMHelper retry 대상 아님 — 1회만 호출 + fail-soft.
    assert result == ""
    assert fake.calls == 1
