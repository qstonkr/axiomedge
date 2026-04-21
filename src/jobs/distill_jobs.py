"""Distill 파이프라인 arq jobs — async sweeper 패턴.

3개 job:
1. ``distill_pipeline_pre_train`` — API 가 enqueue. generate + train 시작.
2. ``distill_pipeline_post_train`` — sweeper 가 enqueue. evaluate/quantize/deploy.
3. ``distill_sweep_training`` — arq cron, 매 60s. status='training' 빌드 스캔.

DB 가 SSOT — API 재시작 무관. arq worker 가 안 떠있으면 build 가 status="pending"
또는 "training" 으로 멈춰있을 뿐 (사용자가 worker 시작하면 자동 진행).
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

# 24h SLA — 환경변수 override 가능. 자동 fail X — 알림만.
_SWEEP_ALERT_HOURS = int(os.getenv("DISTILL_SWEEP_ALERT_HOURS", "24"))


def _get_distill_service() -> Any:
    """AppState 에서 distill_service singleton 가져옴 — API/worker 양쪽에서 동작."""
    from src.api.app import _get_state

    state = _get_state()
    svc = state.get("distill_service")
    if svc is None:
        raise RuntimeError(
            "distill_service not initialized — check AppState lifespan setup",
        )
    return svc


def _get_distill_repo() -> Any:
    from src.api.app import _get_state

    state = _get_state()
    repo = state.get("distill_repo")
    if repo is None:
        raise RuntimeError("distill_repo not initialized")
    return repo


# ---------------------------------------------------------------------------
# 1) Pre-train: generate + train 시작
# ---------------------------------------------------------------------------


async def distill_pipeline_pre_train(
    ctx: dict[str, Any],
    build_id: str,
    profile_name: str,
    steps: list[str] | None = None,
    use_curated_data: bool = False,
) -> dict[str, Any]:
    """API 가 enqueue. generate + train (start_gpu_training 까지).

    GPU 모드면 awaiting_gpu 반환 — sweeper 가 result.json detect 후
    distill_pipeline_post_train enqueue. local 모드면 즉시 post_train 실행.
    """
    job_id = ctx.get("job_id", "?")
    logger.info(
        "distill_pipeline_pre_train[%s] build=%s profile=%s",
        job_id, build_id, profile_name,
    )
    svc = _get_distill_service()
    result = await svc.run_pipeline_pre_train(
        build_id=build_id, profile_name=profile_name,
        steps=steps, use_curated_data=use_curated_data,
    )

    # Local 모드 — post_train 즉시 실행 (별도 enqueue 안 함, 동일 job 안에서).
    if result.get("phase") == "post_train_ready":
        await svc.run_pipeline_post_train(
            build_id=build_id, profile_name=profile_name,
            train_result=result["train_result"], steps=steps,
        )
    return result


# ---------------------------------------------------------------------------
# 2) Post-train: evaluate + quantize + deploy
# ---------------------------------------------------------------------------


async def distill_pipeline_post_train(
    ctx: dict[str, Any],
    build_id: str,
    profile_name: str,
    train_result: dict[str, Any],
    steps: list[str] | None = None,
) -> None:
    """Sweeper 가 enqueue (GPU 학습 완료 detect 후) 또는 pre_train 가 직접 호출
    (local 모드)."""
    job_id = ctx.get("job_id", "?")
    logger.info(
        "distill_pipeline_post_train[%s] build=%s gpu_trained=%s",
        job_id, build_id, train_result.get("gpu_trained"),
    )
    svc = _get_distill_service()
    await svc.run_pipeline_post_train(
        build_id=build_id, profile_name=profile_name,
        train_result=train_result, steps=steps,
    )


# ---------------------------------------------------------------------------
# 3) Sweeper: status='training' 빌드 스캔 (매 60s)
# ---------------------------------------------------------------------------


async def distill_sweep_training(ctx: dict[str, Any]) -> dict[str, int]:
    """매 60s — status='training' AND gpu_instance_id IS NOT NULL 빌드 스캔.

    각 build:
    1. claim_for_sweep — 다른 worker 가 같은 build 처리 중이면 skip.
    2. check_gpu_training — S3 result.json + EC2 state 확인.
    3. success → train metrics update + post_train enqueue.
    4. failed  → status='failed' + error_message.
    5. running → 24h SLA 초과 시 logger.warning (자동 fail X).
    """
    repo = _get_distill_repo()
    builds = await repo.list_in_progress_training()
    if not builds:
        return {"scanned": 0, "completed": 0, "failed": 0, "running": 0}

    from src.connectors._google import resolve_access_token  # noqa: F401  # ensure imports OK
    from src.distill.gpu_trainer import check_gpu_training

    counts = {"scanned": 0, "completed": 0, "failed": 0, "running": 0, "skipped": 0}
    now = datetime.now(UTC)
    alert_threshold = timedelta(hours=_SWEEP_ALERT_HOURS)

    for build in builds:
        build_id = build["id"]
        # Atomic claim — 다른 worker 가 30s 안에 sweep 했으면 skip.
        if not await repo.claim_for_sweep(build_id):
            counts["skipped"] += 1
            continue

        counts["scanned"] += 1

        # Profile 에서 s3_bucket 가져옴 (sweeper 는 caller context 없음).
        profile = await repo.get_profile(build["profile_name"])
        if not profile:
            logger.warning(
                "sweeper: build %s 의 profile %s 사라짐 — failed 처리",
                build_id, build["profile_name"],
            )
            await repo.update_build(
                build_id, status="failed",
                error_message="profile deleted during training",
                error_step="train", gpu_finished_at=now,
            )
            counts["failed"] += 1
            continue

        from src.distill.config import dict_to_profile
        try:
            profile_obj = dict_to_profile(profile)
            s3_bucket = profile_obj.deploy.s3_bucket
        except (KeyError, AttributeError, ValueError) as e:
            logger.warning(
                "sweeper: build %s — profile parse 실패: %s", build_id, e,
            )
            await repo.update_build(
                build_id, status="failed",
                error_message=f"profile parse error: {e}",
                error_step="train", gpu_finished_at=now,
            )
            counts["failed"] += 1
            continue

        # check_gpu_training: 1회 check (sweeper 가 polling 담당).
        check = await check_gpu_training(
            gpu_instance_id=build["gpu_instance_id"],
            s3_bucket=s3_bucket,
            s3_result_key=build["s3_result_key"],
        )

        cstatus = check.get("status")
        if cstatus == "success":
            # train metrics 를 result.json 에서 추출 → DB update.
            await repo.update_build(
                build_id,
                train_loss=check.get("train_loss"),
                training_duration_sec=check.get("duration_sec"),
                gguf_size_mb=check.get("gguf_size_mb"),
                gguf_sha256=check.get("gguf_sha256"),
                quantize_method=check.get("quantize_method"),
                gpu_finished_at=now,
            )
            # post_train enqueue — evaluate + (skip quantize for GPU) + deploy.
            from src.jobs.queue import enqueue_job

            await enqueue_job(
                "distill_pipeline_post_train",
                build_id, build["profile_name"],
                {"gpu_trained": True, "result_json": check},
            )
            counts["completed"] += 1
            logger.info("sweeper: build %s training success → post_train enqueued", build_id)

        elif cstatus == "failed":
            await repo.update_build(
                build_id, status="failed",
                error_message=check.get("error", "unknown"),
                error_step="train", gpu_finished_at=now,
            )
            counts["failed"] += 1
            logger.error("sweeper: build %s training failed: %s", build_id, check.get("error"))

        else:  # "running"
            counts["running"] += 1
            # 24h SLA 알림 — 자동 fail X.
            started_at = build.get("gpu_started_at")
            if started_at:
                if isinstance(started_at, str):
                    try:
                        started_dt = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
                    except ValueError:
                        started_dt = None
                else:
                    started_dt = started_at
                if started_dt and (now - started_dt) > alert_threshold:
                    logger.warning(
                        "sweeper: build %s training elapsed %s (>%dh) — manual review 권장",
                        build_id, now - started_dt, _SWEEP_ALERT_HOURS,
                    )

    return counts
