"""IngestionPipeline embed retry — PR-2 (D).

- 일시 실패 후 성공 → success
- 영구 실패 → 마지막 예외 raise
- 정책은 PipelineSettings.embed_max_retries 로 override 가능
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_pipeline():
    from src.pipelines.ingestion import IngestionPipeline

    p = IngestionPipeline(
        embedder=None, sparse_embedder=None,
        vector_store=None, graph_store=None,
    )
    return p


class TestEmbedDenseRetry:
    @pytest.mark.asyncio
    async def test_succeeds_after_transient_failures(self, monkeypatch):
        async def _instant_sleep(_):
            return None
        monkeypatch.setattr(
            "src.pipelines._retry.asyncio.sleep", _instant_sleep
        )

        p = _make_pipeline()
        # encode 가 2회 실패 후 3회차에 dense_vecs 반환
        encode = MagicMock(
            side_effect=[
                RuntimeError("flaky 1"),
                RuntimeError("flaky 2"),
                {"dense_vecs": [[0.1, 0.2]]},
            ],
        )
        p.embedder = MagicMock(encode=encode)

        # 정책을 짧게
        with patch.object(p, "_get_retry_policy",
                          return_value=(3, 0.01, 1.0)):
            result = await p._embed_dense(["t"])
        assert result == [[0.1, 0.2]]
        assert encode.call_count == 3

    @pytest.mark.asyncio
    async def test_raises_after_all_attempts(self, monkeypatch):
        async def _instant_sleep(_):
            return None
        monkeypatch.setattr(
            "src.pipelines._retry.asyncio.sleep", _instant_sleep
        )

        p = _make_pipeline()
        encode = MagicMock(side_effect=RuntimeError("dead"))
        p.embedder = MagicMock(encode=encode)

        with patch.object(p, "_get_retry_policy",
                          return_value=(2, 0.01, 1.0)):
            with pytest.raises(RuntimeError, match="dead"):
                await p._embed_dense(["t"])
        assert encode.call_count == 2


class TestEmbedSparseRetry:
    @pytest.mark.asyncio
    async def test_sparse_succeeds_after_failure(self, monkeypatch):
        async def _instant_sleep(_):
            return None
        monkeypatch.setattr(
            "src.pipelines._retry.asyncio.sleep", _instant_sleep
        )

        p = _make_pipeline()
        sparse = MagicMock()
        sparse.embed_sparse = AsyncMock(
            side_effect=[OSError("net"), [{"a": [1]}]],
        )
        p.sparse_embedder = sparse

        with patch.object(p, "_get_retry_policy",
                          return_value=(3, 0.01, 1.0)):
            out = await p._embed_sparse_with_retry(["t"])
        assert out == [{"a": [1]}]
        assert sparse.embed_sparse.call_count == 2


class TestPolicyOverride:
    def test_get_retry_policy_returns_settings(self):
        p = _make_pipeline()
        # default settings 가 새 필드 가지는지 확인
        max_retries, initial, max_delay = p._get_retry_policy()
        assert isinstance(max_retries, int)
        assert max_retries >= 1
        assert initial > 0
        assert max_delay >= initial
