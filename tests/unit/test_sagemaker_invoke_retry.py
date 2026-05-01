"""SageMakerLLMClient._invoke — tenacity retry on transient ClientError codes only."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.nlp.llm.sagemaker_client import SageMakerConfig, SageMakerLLMClient


class TestSageMakerInvokeRetry:
    """Verify _invoke retries transient SageMaker codes only."""

    def setup_method(self):
        self.client = SageMakerLLMClient(
            config=SageMakerConfig(endpoint_name="ep", region="ap-northeast-2", profile=""),
        )

    def _make_client_error(self, code: str):
        """Build a botocore ClientError with the given Error.Code."""
        from botocore.exceptions import ClientError
        return ClientError(
            {"Error": {"Code": code, "Message": f"{code} simulated"}},
            "InvokeEndpoint",
        )

    @pytest.mark.asyncio
    async def test_retries_throttling_then_succeeds(self, monkeypatch):
        """ThrottlingException → retry → success on 2nd attempt."""
        async def _no_sleep(_seconds):
            return None

        monkeypatch.setattr("tenacity.nap.time.sleep", lambda _s: None)
        import asyncio as _asyncio
        monkeypatch.setattr(_asyncio, "sleep", _no_sleep)

        calls = {"count": 0}

        def _flaky(messages, max_tokens, temperature):  # noqa: ARG001
            calls["count"] += 1
            if calls["count"] == 1:
                raise self._make_client_error("ThrottlingException")
            return "ok-on-retry"

        with patch.object(self.client, "_invoke_sync", side_effect=_flaky):
            result = await self.client._invoke([{"role": "user", "content": "x"}])
        assert result == "ok-on-retry"
        assert calls["count"] == 2

    @pytest.mark.asyncio
    async def test_validation_exception_does_not_retry(self, monkeypatch):
        """ValidationException → raise immediately (no retry)."""
        async def _no_sleep(_seconds):
            return None
        import asyncio as _asyncio
        monkeypatch.setattr(_asyncio, "sleep", _no_sleep)

        calls = {"count": 0}
        err = self._make_client_error("ValidationException")

        def _always_fail(messages, max_tokens, temperature):  # noqa: ARG001
            calls["count"] += 1
            raise err

        from botocore.exceptions import ClientError
        with patch.object(self.client, "_invoke_sync", side_effect=_always_fail):
            with pytest.raises(ClientError):
                await self.client._invoke([{"role": "user", "content": "x"}])
        assert calls["count"] == 1

    @pytest.mark.asyncio
    async def test_retries_max_3_then_raises(self, monkeypatch):
        """Persistent ServiceUnavailable → 3 attempts then raise."""
        async def _no_sleep(_seconds):
            return None
        import asyncio as _asyncio
        monkeypatch.setattr(_asyncio, "sleep", _no_sleep)

        calls = {"count": 0}
        err = self._make_client_error("ServiceUnavailable")

        def _always_fail(messages, max_tokens, temperature):  # noqa: ARG001
            calls["count"] += 1
            raise err

        from botocore.exceptions import ClientError
        with patch.object(self.client, "_invoke_sync", side_effect=_always_fail):
            with pytest.raises(ClientError):
                await self.client._invoke([{"role": "user", "content": "x"}])
        assert calls["count"] == 3
