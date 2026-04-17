"""Comprehensive tests for src/llm/ollama_client.py."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.nlp.llm.ollama_client import OllamaClient, OllamaConfig, OllamaLLMClient
from src.nlp.llm.utils import estimate_token_count, sanitize_text


# ---------------------------------------------------------------------------
# OllamaConfig
# ---------------------------------------------------------------------------


class TestOllamaConfig:
    def test_defaults(self):
        cfg = OllamaConfig()
        assert "localhost" in cfg.base_url or "11434" in cfg.base_url
        assert cfg.model  # non-empty
        assert cfg.timeout > 0
        assert cfg.max_tokens > 0
        assert cfg.context_length > 0
        assert isinstance(cfg.temperature, float)

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_BASE_URL", "http://my-host:9999")
        monkeypatch.setenv("OLLAMA_MODEL", "  my-model  ")
        cfg = OllamaConfig()
        assert cfg.base_url == "http://my-host:9999"
        assert cfg.model == "my-model"  # post_init strips

    def test_explicit_params(self):
        cfg = OllamaConfig(base_url="http://x:1234", model="testmodel")
        assert cfg.base_url == "http://x:1234"
        assert cfg.model == "testmodel"

    def test_post_init_strips_model(self):
        cfg = OllamaConfig(model="  spaced  ")
        assert cfg.model == "spaced"


# ---------------------------------------------------------------------------
# OllamaClient initialization
# ---------------------------------------------------------------------------


class TestOllamaClientInit:
    def test_default_init(self):
        client = OllamaClient()
        assert client._config is not None
        assert client._client is None

    def test_init_with_config(self):
        cfg = OllamaConfig(base_url="http://a:1", model="m")
        client = OllamaClient(config=cfg)
        assert client._config is cfg

    def test_init_with_kwargs(self):
        client = OllamaClient(base_url="http://b:2")
        assert client._config.base_url == "http://b:2"

    def test_init_with_model_only(self):
        client = OllamaClient(model="mymodel")
        assert client._config.model == "mymodel"

    def test_init_with_both_kwargs(self):
        client = OllamaClient(base_url="http://c:3", model="m2")
        assert client._config.base_url == "http://c:3"
        assert client._config.model == "m2"

    def test_backward_compat_alias(self):
        assert OllamaLLMClient is OllamaClient


# ---------------------------------------------------------------------------
# _estimate_token_count (via utils)
# ---------------------------------------------------------------------------


class TestEstimateTokenCount:
    def test_empty(self):
        assert estimate_token_count("") == 0

    def test_whitespace_only(self):
        assert estimate_token_count("   ") == 0

    def test_latin(self):
        result = estimate_token_count("hello world")
        assert result >= 2

    def test_korean(self):
        result = estimate_token_count("안녕하세요")
        assert result >= 5  # each char is a CJK token

    def test_mixed(self):
        result = estimate_token_count("hello 안녕")
        assert result >= 3

    def test_punctuation(self):
        result = estimate_token_count("!@#")
        assert result >= 3

    def test_static_method(self):
        # OllamaClient._estimate_token_count delegates to utils
        result = OllamaClient._estimate_token_count("test")
        assert result >= 1


# ---------------------------------------------------------------------------
# sanitize_text (via utils)
# ---------------------------------------------------------------------------


class TestSanitizeText:
    def test_empty(self):
        assert sanitize_text("") == ""

    def test_strips(self):
        assert sanitize_text("  hello  ") == "hello"

    def test_truncates(self):
        result = sanitize_text("a" * 100, max_length=10)
        assert len(result) == 10


# ---------------------------------------------------------------------------
# _format_context
# ---------------------------------------------------------------------------


class TestFormatContext:
    def test_empty_context(self):
        client = OllamaClient()
        result = client._format_context([])
        assert "찾지 못했습니다" in result

    def test_single_doc(self):
        client = OllamaClient()
        ctx = [
            {
                "content": "문서 내용",
                "metadata": {"title": "제목1", "source": "src1"},
                "similarity": 0.85,
            }
        ]
        result = client._format_context(ctx)
        assert "제목1" in result
        assert "src1" in result
        assert "85" in result  # 85% formatted

    def test_max_5_docs(self):
        client = OllamaClient()
        ctx = [
            {"content": f"doc{i}", "metadata": {"title": f"t{i}"}, "similarity": 0.5}
            for i in range(10)
        ]
        result = client._format_context(ctx)
        assert "문서 5" in result
        assert "문서 6" not in result

    def test_missing_metadata(self):
        client = OllamaClient()
        ctx = [{"content": "x", "similarity": 0.5}]
        result = client._format_context(ctx)
        assert "제목 없음" in result


# ---------------------------------------------------------------------------
# generate_response (mocked httpx)
# ---------------------------------------------------------------------------


class TestGenerateResponse:
    async def test_generate_response(self):
        client = OllamaClient(base_url="http://test:11434", model="test")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"response": "답변입니다."}

        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=mock_response)
        client._client = mock_http

        result = await client.generate_response(
            "질문", [{"content": "문서", "metadata": {}, "similarity": 0.9}]
        )
        assert result == "답변입니다."
        mock_http.post.assert_called_once()

    async def test_generate_response_custom_system_prompt(self):
        client = OllamaClient(base_url="http://test:11434", model="test")

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"response": "custom answer"}

        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=mock_response)
        client._client = mock_http

        result = await client.generate_response(
            "q", [], system_prompt="Custom system"
        )
        assert result == "custom answer"


# ---------------------------------------------------------------------------
# generate (generic)
# ---------------------------------------------------------------------------


class TestGenerate:
    async def test_generate_basic(self):
        client = OllamaClient()

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"response": "  result  "}

        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=mock_response)
        client._client = mock_http

        result = await client.generate("prompt text")
        assert result == "result"

    async def test_generate_with_system_prompt(self):
        client = OllamaClient()

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"response": "ok"}

        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=mock_response)
        client._client = mock_http

        result = await client.generate("p", system_prompt="sys")
        assert result == "ok"
        call_json = mock_http.post.call_args[1]["json"]
        assert "sys" in call_json["prompt"]

    async def test_generate_custom_params(self):
        client = OllamaClient()

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"response": "x"}

        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=mock_response)
        client._client = mock_http

        await client.generate("p", max_tokens=100, temperature=0.1)
        call_json = mock_http.post.call_args[1]["json"]
        assert call_json["options"]["num_predict"] == 100
        assert call_json["options"]["temperature"] == 0.1


# ---------------------------------------------------------------------------
# chat
# ---------------------------------------------------------------------------


class TestChat:
    async def test_chat_basic(self):
        client = OllamaClient()

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "message": {"content": "  assistant reply  "}
        }

        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=mock_response)
        client._client = mock_http

        result = await client.chat(
            [{"role": "user", "content": "hello"}]
        )
        assert result == "assistant reply"

    async def test_chat_empty_message(self):
        client = OllamaClient()

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"message": {}}

        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=mock_response)
        client._client = mock_http

        result = await client.chat([])
        assert result == ""


# ---------------------------------------------------------------------------
# classify_batch
# ---------------------------------------------------------------------------


class TestClassifyBatch:
    async def test_empty_prompts(self):
        client = OllamaClient()
        result = await client.classify_batch([])
        assert result == []

    async def test_batch(self):
        client = OllamaClient()

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"response": "classified"}

        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=mock_response)
        client._client = mock_http

        results = await client.classify_batch(["p1", "p2"])
        assert len(results) == 2
        assert all(r == "classified" for r in results)


# ---------------------------------------------------------------------------
# check_health
# ---------------------------------------------------------------------------


class TestCheckHealth:
    async def test_healthy(self):
        client = OllamaClient(model="test-model")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "models": [{"name": "test-model"}, {"name": "other"}]
        }

        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=mock_response)
        client._client = mock_http

        result = await client.check_health()
        assert result["status"] == "healthy"
        assert result["primary_model_ready"] is True

    async def test_unhealthy_status(self):
        client = OllamaClient()

        mock_response = MagicMock()
        mock_response.status_code = 500

        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=mock_response)
        client._client = mock_http

        result = await client.check_health()
        assert result["status"] == "unhealthy"

    async def test_exception(self):
        client = OllamaClient()

        mock_http = AsyncMock()
        mock_http.get = AsyncMock(side_effect=RuntimeError("connection refused"))
        client._client = mock_http

        result = await client.check_health()
        assert result["status"] == "unhealthy"
        assert "connection refused" in result["error"]

    async def test_model_partial_match(self):
        client = OllamaClient(model="exaone3.5")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "models": [{"name": "exaone3.5:7.8b"}]
        }

        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=mock_response)
        client._client = mock_http

        result = await client.check_health()
        assert result["primary_model_ready"] is True


# ---------------------------------------------------------------------------
# generate_with_context
# ---------------------------------------------------------------------------


class TestGenerateWithContext:
    async def test_basic(self):
        client = OllamaClient()

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"response": "answer"}

        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=mock_response)
        client._client = mock_http

        result = await client.generate_with_context("question", "context text")
        assert result == "answer"


# ---------------------------------------------------------------------------
# _get_client (lazy init)
# ---------------------------------------------------------------------------


class TestGetClient:
    async def test_creates_client_on_first_call(self):
        client = OllamaClient()
        assert client._client is None
        http = await client._get_client()
        assert http is not None
        assert isinstance(http, httpx.AsyncClient)
        await client.close()

    async def test_reuses_client(self):
        client = OllamaClient()
        c1 = await client._get_client()
        c2 = await client._get_client()
        assert c1 is c2
        await client.close()


# ---------------------------------------------------------------------------
# close / context manager
# ---------------------------------------------------------------------------


class TestClose:
    async def test_close_no_client(self):
        client = OllamaClient()
        await client.close()  # no error

    async def test_close_with_client(self):
        client = OllamaClient()
        await client._get_client()
        await client.close()
        assert client._client is None

    async def test_context_manager(self):
        async with OllamaClient() as client:
            assert isinstance(client, OllamaClient)
        assert client._client is None

    async def test_double_close(self):
        client = OllamaClient()
        await client._get_client()
        await client.close()
        await client.close()  # no error
