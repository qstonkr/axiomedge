"""distill async sweeper — gpu_trainer split + arq sweeper job + repo claim."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# gpu_trainer.check_gpu_training — 3가지 분기
# ---------------------------------------------------------------------------


class TestCheckGpuTraining:
    @pytest.mark.asyncio
    async def test_result_completed_returns_success(self):
        from src.distill.gpu_trainer import check_gpu_training

        result_json = {
            "status": "completed", "train_loss": 0.5,
            "duration_sec": 3600, "gguf_size_mb": 512,
        }
        with patch(
            "src.distill.gpu_trainer._check_output_exists_sync",
            return_value=result_json,
        ):
            out = await check_gpu_training(
                gpu_instance_id="i-abc", s3_bucket="b",
                s3_result_key="train/x/output/result.json",
            )
        assert out["status"] == "success"
        assert out["train_loss"] == 0.5
        # status="completed" 가 wrapper "success" 를 덮어쓰지 않는지
        assert out["status"] != "completed"

    @pytest.mark.asyncio
    async def test_result_failed_returns_failed(self):
        from src.distill.gpu_trainer import check_gpu_training

        with patch(
            "src.distill.gpu_trainer._check_output_exists_sync",
            return_value={"status": "failed", "error": "OOM"},
        ):
            out = await check_gpu_training(
                gpu_instance_id="i-abc", s3_bucket="b",
                s3_result_key="train/x/output/result.json",
            )
        assert out["status"] == "failed"
        assert out["error"] == "OOM"

    @pytest.mark.asyncio
    async def test_no_result_ec2_running_returns_running(self):
        """result 없고 EC2 가 running 이면 학습 진행 중 — 다음 sweep tick 까지 대기."""
        from src.distill.gpu_trainer import check_gpu_training

        with patch(
            "src.distill.gpu_trainer._check_output_exists_sync",
            return_value=None,
        ), patch(
            "src.distill.gpu_trainer._get_instance_state",
            new=AsyncMock(return_value="running"),
        ):
            out = await check_gpu_training(
                gpu_instance_id="i-abc", s3_bucket="b",
                s3_result_key="train/x/output/result.json",
            )
        assert out["status"] == "running"

    @pytest.mark.asyncio
    async def test_no_result_ec2_stopped_returns_failed(self):
        """EC2 가 stopped 인데 result 없으면 부팅 스크립트 실패."""
        from src.distill.gpu_trainer import check_gpu_training

        with patch(
            "src.distill.gpu_trainer._check_output_exists_sync",
            return_value=None,
        ), patch(
            "src.distill.gpu_trainer._get_instance_state",
            new=AsyncMock(return_value="stopped"),
        ):
            out = await check_gpu_training(
                gpu_instance_id="i-abc", s3_bucket="b",
                s3_result_key="train/x/output/result.json",
            )
        assert out["status"] == "failed"
        assert "stopped" in out["error"]


# ---------------------------------------------------------------------------
# repo.claim_for_sweep — atomic idempotency
# ---------------------------------------------------------------------------


def _make_build_repo():
    from src.distill.repositories.build import DistillBuildRepository

    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.commit = AsyncMock()
    maker = MagicMock(return_value=session)
    repo = DistillBuildRepository.__new__(DistillBuildRepository)
    repo._session_maker = maker
    return repo, session


class TestClaimForSweep:
    def test_claim_succeeds_when_row_returned(self):
        repo, session = _make_build_repo()
        # UPDATE ... RETURNING id — first() returns row tuple (id present).
        result_mock = MagicMock()
        result_mock.first.return_value = ("build-1",)
        session.execute = AsyncMock(return_value=result_mock)

        out = _run(repo.claim_for_sweep("build-1"))
        assert out is True

    def test_claim_fails_when_no_row(self):
        repo, session = _make_build_repo()
        result_mock = MagicMock()
        result_mock.first.return_value = None  # 다른 worker 가 30s 안에 sweep 완료
        session.execute = AsyncMock(return_value=result_mock)

        out = _run(repo.claim_for_sweep("build-1"))
        assert out is False


# ---------------------------------------------------------------------------
# sweeper job — distill_sweep_training 종합 분기
# ---------------------------------------------------------------------------


class TestSweeperJob:
    @pytest.mark.asyncio
    async def test_no_in_progress_builds_returns_zero(self):
        from src.jobs.distill_jobs import distill_sweep_training

        repo = AsyncMock()
        repo.list_in_progress_training = AsyncMock(return_value=[])
        with patch("src.jobs.distill_jobs._get_distill_repo", return_value=repo):
            counts = await distill_sweep_training({"job_id": "test"})
        assert counts["scanned"] == 0

    @pytest.mark.asyncio
    async def test_skips_when_claim_fails(self):
        """다른 worker 가 같은 build sweep 중이면 skip."""
        from src.jobs.distill_jobs import distill_sweep_training

        repo = AsyncMock()
        repo.list_in_progress_training = AsyncMock(return_value=[
            {"id": "b1", "profile_name": "p1", "gpu_instance_id": "i-abc",
             "s3_result_key": "k", "gpu_started_at": datetime.now(UTC)},
        ])
        repo.claim_for_sweep = AsyncMock(return_value=False)  # 다른 worker 가 처리 중

        with patch("src.jobs.distill_jobs._get_distill_repo", return_value=repo):
            counts = await distill_sweep_training({"job_id": "test"})
        assert counts["skipped"] == 1
        assert counts["scanned"] == 0

    @pytest.mark.asyncio
    async def test_success_enqueues_post_train(self):
        """check 가 success 면 train metrics update + post_train enqueue."""
        from src.jobs.distill_jobs import distill_sweep_training

        repo = AsyncMock()
        repo.list_in_progress_training = AsyncMock(return_value=[
            {"id": "b1", "profile_name": "p1", "gpu_instance_id": "i-abc",
             "s3_result_key": "k", "gpu_started_at": datetime.now(UTC)},
        ])
        repo.claim_for_sweep = AsyncMock(return_value=True)
        repo.get_profile = AsyncMock(return_value={
            "name": "p1", "search_group": "sg", "base_model": "g/m",
            "deploy": {"s3_bucket": "test-bucket", "s3_prefix": "distill/"},
        })
        enqueue_mock = AsyncMock()

        # check_gpu_training mock — success
        success_result = {
            "status": "success", "train_loss": 0.5, "duration_sec": 1800,
            "gguf_size_mb": 512, "gguf_sha256": "abc", "quantize_method": "q4_k_m",
        }
        with patch(
            "src.jobs.distill_jobs._get_distill_repo", return_value=repo,
        ), patch(
            "src.distill.gpu_trainer.check_gpu_training",
            new=AsyncMock(return_value=success_result),
        ), patch(
            "src.jobs.queue.enqueue_job", new=enqueue_mock,
        ):
            counts = await distill_sweep_training({"job_id": "test"})

        assert counts["completed"] == 1
        # update_build 호출에 gguf_size_mb 등 메트릭 포함
        repo.update_build.assert_awaited()
        # post_train 으로 enqueue
        enqueue_mock.assert_awaited_once()
        assert enqueue_mock.await_args.args[0] == "distill_pipeline_post_train"
        # train_result 의 gpu_trained=True
        assert enqueue_mock.await_args.args[3]["gpu_trained"] is True

    @pytest.mark.asyncio
    async def test_failed_marks_build_failed(self):
        from src.jobs.distill_jobs import distill_sweep_training

        repo = AsyncMock()
        repo.list_in_progress_training = AsyncMock(return_value=[
            {"id": "b1", "profile_name": "p1", "gpu_instance_id": "i-abc",
             "s3_result_key": "k", "gpu_started_at": datetime.now(UTC)},
        ])
        repo.claim_for_sweep = AsyncMock(return_value=True)
        repo.get_profile = AsyncMock(return_value={
            "name": "p1", "search_group": "sg", "base_model": "g/m",
            "deploy": {"s3_bucket": "test-bucket", "s3_prefix": "distill/"},
        })

        with patch(
            "src.jobs.distill_jobs._get_distill_repo", return_value=repo,
        ), patch(
            "src.distill.gpu_trainer.check_gpu_training",
            new=AsyncMock(return_value={"status": "failed", "error": "OOM"}),
        ):
            counts = await distill_sweep_training({"job_id": "test"})

        assert counts["failed"] == 1
        # update_build 호출에 status='failed' + error_message
        update_kwargs = repo.update_build.await_args.kwargs
        assert update_kwargs.get("status") == "failed"
        assert "OOM" in update_kwargs.get("error_message", "")
        assert update_kwargs.get("error_step") == "train"

    @pytest.mark.asyncio
    async def test_running_just_updates_sweep_timestamp(self):
        """check 가 running 이면 status 변경 없이 다음 tick 대기."""
        from src.jobs.distill_jobs import distill_sweep_training

        repo = AsyncMock()
        repo.list_in_progress_training = AsyncMock(return_value=[
            {"id": "b1", "profile_name": "p1", "gpu_instance_id": "i-abc",
             "s3_result_key": "k", "gpu_started_at": datetime.now(UTC).isoformat()},
        ])
        repo.claim_for_sweep = AsyncMock(return_value=True)  # claim 자체가 last_sweep_at update
        repo.get_profile = AsyncMock(return_value={
            "name": "p1", "search_group": "sg", "base_model": "g/m",
            "deploy": {"s3_bucket": "b", "s3_prefix": "p"},
        })

        with patch(
            "src.jobs.distill_jobs._get_distill_repo", return_value=repo,
        ), patch(
            "src.distill.gpu_trainer.check_gpu_training",
            new=AsyncMock(return_value={"status": "running"}),
        ):
            counts = await distill_sweep_training({"job_id": "test"})

        assert counts["running"] == 1
        # status update 안 함 (claim_for_sweep 만 last_sweep_at 갱신)
        # 단, profile parse 실패 케이스가 아니므로 update_build 가 호출 안 됨.


# ---------------------------------------------------------------------------
# Catalog / model schema 점검
# ---------------------------------------------------------------------------


class TestModelColumnsPresent:
    def test_distill_build_model_has_5_new_columns(self):
        from src.distill.models import DistillBuildModel

        cols = {c.name for c in DistillBuildModel.__table__.columns}
        for c in ("gpu_instance_id", "gpu_started_at", "s3_result_key",
                  "last_sweep_at", "gpu_finished_at"):
            assert c in cols, f"{c} missing"

    def test_sweeper_index_exists(self):
        from src.distill.models import DistillBuildModel

        idx_names = {i.name for i in DistillBuildModel.__table__.indexes}
        assert "idx_distill_build_status_sweep" in idx_names
