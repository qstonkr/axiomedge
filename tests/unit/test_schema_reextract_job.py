"""Contract tests for schema_reextract_run arq task."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.jobs.schema_reextract import (
    ReextractDeps,
    run_reextract,
)


@pytest.fixture
def mock_deps():
    deps = ReextractDeps(
        job_repo=AsyncMock(),
        extractor=MagicMock(),
        schema_resolver=MagicMock(),
        doc_iterator=AsyncMock(),
    )
    deps.job_repo.start = AsyncMock()
    deps.job_repo.progress = AsyncMock()
    deps.job_repo.complete = AsyncMock()
    deps.schema_resolver.resolve = MagicMock(return_value=MagicMock(version=2))
    return deps


class TestRunReextract:
    @pytest.mark.asyncio
    async def test_happy_path_marks_completed(self, mock_deps):
        async def _iter(**_kw):
            for i in range(3):
                yield {
                    "doc_id": f"d{i}", "content": f"doc {i} content",
                    "kb_id": "test", "source_type": "confluence",
                }
        mock_deps.doc_iterator = _iter

        result = MagicMock()
        result.node_count = 1
        result.relationship_count = 1
        mock_deps.extractor.extract = MagicMock(return_value=result)
        mock_deps.extractor.save_to_neo4j = MagicMock(
            return_value={"nodes_created": 1},
        )

        job_id = uuid4()
        await run_reextract(
            job_id=job_id, kb_id="test", deps=mock_deps,
        )
        mock_deps.job_repo.start.assert_awaited_once()
        mock_deps.job_repo.complete.assert_awaited_once()
        complete_kwargs = mock_deps.job_repo.complete.await_args.kwargs
        assert complete_kwargs["status"] == "completed"

    @pytest.mark.asyncio
    async def test_per_doc_failure_counted_but_continues(self, mock_deps):
        async def _iter(**_kw):
            for i in range(3):
                yield {
                    "doc_id": f"d{i}", "content": f"doc {i}",
                    "kb_id": "test", "source_type": "x",
                }
        mock_deps.doc_iterator = _iter

        result_ok = MagicMock()
        result_ok.node_count = 1
        result_ok.relationship_count = 0
        call_count = {"n": 0}

        def _flaky(**_kw):
            call_count["n"] += 1
            if call_count["n"] == 2:
                raise RuntimeError("LLM hiccup")
            return result_ok

        mock_deps.extractor.extract = MagicMock(side_effect=_flaky)
        mock_deps.extractor.save_to_neo4j = MagicMock(return_value={})

        await run_reextract(
            job_id=uuid4(), kb_id="test", deps=mock_deps,
            progress_every=1,
        )
        complete_kwargs = mock_deps.job_repo.complete.await_args.kwargs
        assert complete_kwargs["status"] == "completed"
        progress_calls = mock_deps.job_repo.progress.await_args_list
        assert any(c.kwargs.get("docs_failed", 0) >= 1 for c in progress_calls)

    @pytest.mark.asyncio
    async def test_top_level_failure_marks_failed(self, mock_deps):
        async def _iter(**_kw):
            raise RuntimeError("Qdrant down")
            yield  # pragma: no cover

        mock_deps.doc_iterator = _iter
        with pytest.raises(RuntimeError):
            await run_reextract(
                job_id=uuid4(), kb_id="test", deps=mock_deps,
            )
        complete_kwargs = mock_deps.job_repo.complete.await_args.kwargs
        assert complete_kwargs["status"] == "failed"
        assert "Qdrant down" in complete_kwargs["error_message"]
