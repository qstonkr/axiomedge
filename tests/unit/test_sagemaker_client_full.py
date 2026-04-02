"""Comprehensive tests for src/llm/sagemaker_client.py."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.llm.sagemaker_client import SageMakerConfig, SageMakerLLMClient


# ===========================================================================
# SageMakerConfig
# ===========================================================================

class TestSageMakerConfig:
    def test_defaults(self):
        with patch.dict("os.environ", {
            "SAGEMAKER_ENDPOINT_NAME": "test-endpoint",
            "SAGEMAKER_REGION": "us-east-1",
            "AWS_PROFILE": "testprofile",
        }):
            config = SageMakerConfig()
        assert config.endpoint_name == "test-endpoint"
        assert config.region == "us-east-1"
        assert config.profile == "testprofile"
        assert config.max_tokens > 0
        assert config.temperature >= 0

    def test_model(self):
        config = SageMakerConfig()
        assert config.model == "sagemaker-exaone"


# ===========================================================================
# SageMakerLLMClient
# ===========================================================================

class TestSageMakerLLMClient:
    def setup_method(self):
        self.config = SageMakerConfig(
            endpoint_name="test-ep",
            region="ap-northeast-2",
            profile="test",
        )
        self.client = SageMakerLLMClient(config=self.config)

    def test_init_default_config(self):
        client = SageMakerLLMClient()
        assert client._config is not None

    def test_get_client(self):
        with patch("boto3.Session") as MockSession:
            session = MagicMock()
            MockSession.return_value = session
            sm_client = MagicMock()
            session.client.return_value = sm_client

            result = self.client._get_client()
            MockSession.assert_called_once_with(
                profile_name="test",
                region_name="ap-northeast-2",
            )
            session.client.assert_called_once_with("sagemaker-runtime")

    def test_invoke_sync(self):
        mock_client = MagicMock()
        response_body = MagicMock()
        response_body.read.return_value = json.dumps({
            "choices": [{"message": {"content": "Test answer"}}],
        }).encode()
        mock_client.invoke_endpoint.return_value = {"Body": response_body}

        with patch.object(self.client, "_get_client", return_value=mock_client):
            result = self.client._invoke_sync(
                [{"role": "user", "content": "Hello"}],
            )
        assert result == "Test answer"
        mock_client.invoke_endpoint.assert_called_once()

    @pytest.mark.asyncio
    async def test_invoke_async(self):
        with patch.object(self.client, "_invoke_sync", return_value="async answer"):
            result = await self.client._invoke(
                [{"role": "user", "content": "Hello"}],
            )
        assert result == "async answer"

    @pytest.mark.asyncio
    async def test_generate_response(self):
        with patch.object(self.client, "_invoke", new_callable=AsyncMock) as mock_invoke:
            mock_invoke.return_value = "RAG answer"
            result = await self.client.generate_response(
                "test query",
                [{"content": "context", "metadata": {"title": "Doc 1"}}],
            )
        assert result == "RAG answer"
        mock_invoke.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_generate(self):
        with patch.object(self.client, "_invoke", new_callable=AsyncMock) as mock_invoke:
            mock_invoke.return_value = "Generated text"
            result = await self.client.generate("Write something")
        assert result == "Generated text"

    @pytest.mark.asyncio
    async def test_generate_with_system_prompt(self):
        with patch.object(self.client, "_invoke", new_callable=AsyncMock) as mock_invoke:
            mock_invoke.return_value = "answer"
            await self.client.generate("prompt", system_prompt="You are helpful")
            call_args = mock_invoke.call_args[0][0]
            assert call_args[0]["role"] == "system"

    @pytest.mark.asyncio
    async def test_chat(self):
        with patch.object(self.client, "_invoke", new_callable=AsyncMock) as mock_invoke:
            mock_invoke.return_value = "Chat response"
            messages = [
                {"role": "system", "content": "You are a bot"},
                {"role": "user", "content": "Hello"},
            ]
            result = await self.client.chat(messages)
        assert result == "Chat response"

    @pytest.mark.asyncio
    async def test_classify_batch_empty(self):
        result = await self.client.classify_batch([])
        assert result == []

    @pytest.mark.asyncio
    async def test_classify_batch(self):
        with patch.object(self.client, "generate", new_callable=AsyncMock) as mock_gen:
            mock_gen.return_value = "category_a"
            results = await self.client.classify_batch(
                ["prompt1", "prompt2"],
                max_concurrency=2,
            )
        assert len(results) == 2
        assert all(r == "category_a" for r in results)

    @pytest.mark.asyncio
    async def test_generate_response_stream(self):
        with patch.object(self.client, "generate_response", new_callable=AsyncMock) as mock_gen:
            mock_gen.return_value = "Streaming fallback"
            chunks = []
            async for chunk in self.client.generate_response_stream("query", []):
                chunks.append(chunk)
        assert len(chunks) == 1
        assert chunks[0] == "Streaming fallback"

    @pytest.mark.asyncio
    async def test_generate_with_context(self):
        with patch.object(self.client, "_invoke", new_callable=AsyncMock) as mock_invoke:
            mock_invoke.return_value = "Context answer"
            result = await self.client.generate_with_context("query", "context text")
        assert result == "Context answer"

    @pytest.mark.asyncio
    async def test_generate_stream(self):
        with patch.object(self.client, "generate_with_context", new_callable=AsyncMock) as mock_gen:
            mock_gen.return_value = "Stream fallback"
            chunks = []
            async for chunk in self.client.generate_stream("query", "context"):
                chunks.append(chunk)
        assert chunks == ["Stream fallback"]

    @pytest.mark.asyncio
    async def test_check_health_success(self):
        with patch("boto3.Session") as MockSession:
            session = MagicMock()
            MockSession.return_value = session
            sm = MagicMock()
            session.client.return_value = sm
            sm.describe_endpoint.return_value = {"EndpointStatus": "InService"}

            result = await self.client.check_health()
        assert result["status"] == "healthy"
        assert result["backend"] == "sagemaker"

    @pytest.mark.asyncio
    async def test_check_health_unhealthy(self):
        with patch("boto3.Session") as MockSession:
            session = MagicMock()
            MockSession.return_value = session
            sm = MagicMock()
            session.client.return_value = sm
            sm.describe_endpoint.return_value = {"EndpointStatus": "Creating"}

            result = await self.client.check_health()
        assert result["status"] == "unhealthy"

    @pytest.mark.asyncio
    async def test_check_health_error(self):
        with patch("boto3.Session") as MockSession:
            MockSession.side_effect = Exception("No credentials")
            result = await self.client.check_health()
        assert result["status"] == "unhealthy"
        assert "error" in result

    def test_format_context_empty(self):
        result = self.client._format_context([])
        assert "찾지 못했습니다" in result

    def test_format_context_with_docs(self):
        context = [
            {
                "content": "Test content",
                "metadata": {"title": "Doc 1", "source": "wiki"},
                "similarity": 0.95,
            },
        ]
        result = self.client._format_context(context)
        assert "Doc 1" in result
        assert "Test content" in result

    def test_format_context_limits_to_5(self):
        context = [{"content": f"Content {i}", "metadata": {}, "similarity": 0.5} for i in range(10)]
        result = self.client._format_context(context)
        # Should only include first 5
        assert "Content 0" in result
        assert "Content 4" in result
        assert "Content 5" not in result

    def test_estimate_token_count(self):
        count = SageMakerLLMClient._estimate_token_count("Hello world test")
        assert count > 0
