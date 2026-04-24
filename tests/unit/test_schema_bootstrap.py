"""SchemaBootstrapper — orchestration contract tests (mocked deps)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.pipelines.graphrag.schema_bootstrap import (
    BootstrapAlreadyRunning,
    BootstrapConfig,
    SchemaBootstrapper,
)


@pytest.fixture
def mock_llm():
    llm = MagicMock()
    llm.invoke = MagicMock(
        return_value='{"new_node_types":[],"new_relation_types":[]}',
    )
    return llm


@pytest.fixture
def mock_candidate_repo():
    repo = AsyncMock()
    repo.upsert = AsyncMock()
    repo.list_approved_labels = AsyncMock(return_value=[])
    return repo


@pytest.fixture
def mock_run_repo():
    repo = AsyncMock()
    repo.has_running = AsyncMock(return_value=False)
    repo.create = AsyncMock(return_value=uuid4())
    repo.complete = AsyncMock()
    return repo


@pytest.fixture
def mock_sampler():
    sampler = AsyncMock()
    sampler.sample = AsyncMock(return_value=[
        {"doc_id": "d1", "content": "doc 1 content", "source_type": "confluence"},
        {"doc_id": "d2", "content": "doc 2 content", "source_type": "confluence"},
    ])
    return sampler


class TestConcurrentGuard:
    @pytest.mark.asyncio
    async def test_already_running_raises(
        self, mock_llm, mock_candidate_repo, mock_run_repo, mock_sampler,
    ):
        mock_run_repo.has_running = AsyncMock(return_value=True)
        bs = SchemaBootstrapper(
            llm=mock_llm,
            candidate_repo=mock_candidate_repo,
            run_repo=mock_run_repo,
            sampler=mock_sampler,
        )
        with pytest.raises(BootstrapAlreadyRunning):
            await bs.run(kb_id="test", triggered_by="manual")
        mock_run_repo.create.assert_not_called()


class TestHappyPath:
    @pytest.mark.asyncio
    async def test_discovered_node_upserted(
        self, mock_llm, mock_candidate_repo, mock_run_repo, mock_sampler,
    ):
        mock_llm.invoke = MagicMock(return_value=(
            '{"new_node_types":['
            '{"label":"Meeting","confidence":0.9,"examples":["sample"]}'
            '],"new_relation_types":[]}'
        ))
        bs = SchemaBootstrapper(
            llm=mock_llm,
            candidate_repo=mock_candidate_repo,
            run_repo=mock_run_repo,
            sampler=mock_sampler,
        )
        run_id = await bs.run(kb_id="test", triggered_by="manual")
        assert run_id is not None
        mock_candidate_repo.upsert.assert_awaited()
        upsert_kwargs = mock_candidate_repo.upsert.await_args.kwargs
        assert upsert_kwargs["label"] == "Meeting"
        assert upsert_kwargs["kb_id"] == "test"
        assert upsert_kwargs["candidate_type"] == "node"

    @pytest.mark.asyncio
    async def test_below_threshold_skipped(
        self, mock_llm, mock_candidate_repo, mock_run_repo, mock_sampler,
    ):
        mock_llm.invoke = MagicMock(return_value=(
            '{"new_node_types":['
            '{"label":"Weak","confidence":0.5,"examples":[]}'
            '],"new_relation_types":[]}'
        ))
        cfg = BootstrapConfig(confidence_threshold=0.8)
        bs = SchemaBootstrapper(
            llm=mock_llm,
            candidate_repo=mock_candidate_repo,
            run_repo=mock_run_repo,
            sampler=mock_sampler,
        )
        await bs.run(kb_id="test", triggered_by="manual", config=cfg)
        mock_candidate_repo.upsert.assert_not_called()

    @pytest.mark.asyncio
    async def test_relationship_candidate_upserted(
        self, mock_llm, mock_candidate_repo, mock_run_repo, mock_sampler,
    ):
        mock_llm.invoke = MagicMock(return_value=(
            '{"new_node_types":[],"new_relation_types":['
            '{"label":"ATTENDED","source":"Person","target":"Meeting",'
            '"confidence":0.9,"examples":[]}'
            ']}'
        ))
        bs = SchemaBootstrapper(
            llm=mock_llm,
            candidate_repo=mock_candidate_repo,
            run_repo=mock_run_repo,
            sampler=mock_sampler,
        )
        await bs.run(kb_id="test", triggered_by="manual")
        call = mock_candidate_repo.upsert.await_args
        assert call.kwargs["candidate_type"] == "relationship"
        assert call.kwargs["label"] == "ATTENDED"
        assert call.kwargs["source_label"] == "Person"
        assert call.kwargs["target_label"] == "Meeting"


class TestEmptyDocs:
    @pytest.mark.asyncio
    async def test_empty_docs_completes_immediately(
        self, mock_llm, mock_candidate_repo, mock_run_repo, mock_sampler,
    ):
        mock_sampler.sample = AsyncMock(return_value=[])
        bs = SchemaBootstrapper(
            llm=mock_llm,
            candidate_repo=mock_candidate_repo,
            run_repo=mock_run_repo,
            sampler=mock_sampler,
        )
        await bs.run(kb_id="test", triggered_by="manual")
        mock_llm.invoke.assert_not_called()
        complete_call = mock_run_repo.complete.await_args
        assert complete_call.kwargs["status"] == "completed"
        assert complete_call.kwargs["candidates_found"] == 0


class TestRunCompletion:
    @pytest.mark.asyncio
    async def test_failure_marks_run_failed(
        self, mock_llm, mock_candidate_repo, mock_run_repo, mock_sampler,
    ):
        mock_sampler.sample = AsyncMock(side_effect=RuntimeError("no docs"))
        bs = SchemaBootstrapper(
            llm=mock_llm,
            candidate_repo=mock_candidate_repo,
            run_repo=mock_run_repo,
            sampler=mock_sampler,
        )
        with pytest.raises(RuntimeError):
            await bs.run(kb_id="test", triggered_by="manual")
        complete_call = mock_run_repo.complete.await_args
        assert complete_call.kwargs["status"] == "failed"
        assert "no docs" in complete_call.kwargs["error_message"]


class TestBatchLLMFailure:
    @pytest.mark.asyncio
    async def test_batch_parse_error_continues_to_next_batch(
        self, mock_llm, mock_candidate_repo, mock_run_repo, mock_sampler,
    ):
        """LLM 1회 실패해도 다음 batch 계속 진행."""
        # First call: malformed; second call: valid candidate
        mock_llm.invoke = MagicMock(side_effect=[
            "not json",
            '{"new_node_types":[{"label":"Ok","confidence":0.9,"examples":[]}],'
            '"new_relation_types":[]}',
        ])
        # 20 docs → 2 batches of 10
        mock_sampler.sample = AsyncMock(return_value=[
            {"doc_id": f"d{i}", "content": f"doc {i}", "source_type": "x"}
            for i in range(20)
        ])
        bs = SchemaBootstrapper(
            llm=mock_llm,
            candidate_repo=mock_candidate_repo,
            run_repo=mock_run_repo,
            sampler=mock_sampler,
        )
        await bs.run(kb_id="test", triggered_by="manual")
        # Second batch's candidate still made it through
        assert mock_candidate_repo.upsert.await_count == 1
        assert mock_candidate_repo.upsert.await_args.kwargs["label"] == "Ok"
