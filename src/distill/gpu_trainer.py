"""GPU EC2 인스턴스 기반 원격 학습 — async sweeper 패턴.

흐름:
    [API/worker — start_gpu_training()]
    1. S3 에 train/{build_id}/train.jsonl + config.json 업로드
    2. EC2 GPU 인스턴스 시작
    3. 즉시 return — DB 의 build row 에 (gpu_instance_id, gpu_started_at,
       s3_result_key) 기록 (caller 가 set_gpu_metadata 호출).

    [arq cron sweeper — check_gpu_training()]
    1. S3 의 result.json 존재 확인 → 있으면 status="success" 또는 "failed"
       (result.json 의 status 그대로) 반환.
    2. EC2 가 stopped/terminated 인데 result 없으면 → status="failed".
    3. 위 둘 다 아니면 → status="running" — sweeper 가 다음 tick 에 재시도.

    [EC2 부팅 스크립트 - systemd]
    1. S3 train/ 스캔 → output 없는 작업 탐색 → 학습.
    2. 결과 S3 업로드 (output/result.json + model.gguf 등).
    3. ``shutdown -h now`` — EC2 종료를 부팅 스크립트가 담당.

**EC2 stop 호출은 본 모듈에서 안 함**. 과거 패턴 (``run_gpu_training`` 의
timeout 후 강제 stop) 은 학습 중인 instance 도 죽여 GPU 비용 + 결과 손실
유발 — 부팅 스크립트의 ``shutdown -h now`` 에 위임.

환경변수:
    DISTILL_GPU_INSTANCE_ID: EC2 인스턴스 ID (g4dn.xlarge)
    AWS_REGION: ap-northeast-2
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

_GPU_INSTANCE_ID = os.getenv("DISTILL_GPU_INSTANCE_ID", "")
_AWS_REGION = os.getenv("AWS_REGION", "ap-northeast-2")
_AWS_PROFILE = os.getenv("AWS_PROFILE", "")


# ---------------------------------------------------------------------------
# EC2 lifecycle (start + state — stop 은 부팅 스크립트가 담당, 본 모듈 X)
# ---------------------------------------------------------------------------

async def _get_instance_state(instance_id: str) -> str:
    proc = await asyncio.create_subprocess_shell(
        f"aws ec2 describe-instances --instance-ids {instance_id} "
        f"--region {_AWS_REGION} --profile {_AWS_PROFILE} "
        f"--query 'Reservations[0].Instances[0].State.Name' --output text",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    return stdout.decode().strip()


async def _start_instance(instance_id: str) -> bool:
    state = await _get_instance_state(instance_id)

    # stopping 상태면 stopped 될 때까지 대기
    if state == "stopping":
        logger.info("Instance stopping, waiting for stopped...")
        for _ in range(30):
            await asyncio.sleep(5)
            if await _get_instance_state(instance_id) == "stopped":
                break

    logger.info("Starting GPU instance %s", instance_id)
    proc = await asyncio.create_subprocess_shell(
        f"aws ec2 start-instances --instance-ids {instance_id} "
        f"--region {_AWS_REGION} --profile {_AWS_PROFILE}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()

    for _ in range(60):
        if await _get_instance_state(instance_id) == "running":
            logger.info("GPU instance running")
            return True
        await asyncio.sleep(5)
    return False


# ---------------------------------------------------------------------------
# S3 업로드 + 결과 확인
# ---------------------------------------------------------------------------

def _upload_training_data_sync(
    s3_bucket: str, s3_prefix: str, build_id: str,
    jsonl_path: str, config: dict,
) -> str:
    """학습 데이터 + 설정을 S3에 업로드 (동기)."""
    from src.distill.deployer import _s3_client  # noqa: PLC0415
    s3 = _s3_client()

    train_key = f"{s3_prefix}train/{build_id}/train.jsonl"
    config_key = f"{s3_prefix}train/{build_id}/config.json"

    s3.upload_file(jsonl_path, s3_bucket, train_key)
    s3.put_object(
        Bucket=s3_bucket, Key=config_key,
        Body=json.dumps(config, ensure_ascii=False),
        ContentType="application/json",
    )
    logger.info("Uploaded training data: s3://%s/%s", s3_bucket, train_key)
    return f"s3://{s3_bucket}/{s3_prefix}train/{build_id}/"


def _build_result_key(s3_prefix: str, build_id: str) -> str:
    """sweeper 가 polling 할 결과 path (S3 key, bucket 제외)."""
    return f"{s3_prefix}train/{build_id}/output/result.json"


def _check_output_exists_sync(s3_bucket: str, result_key: str) -> dict | None:
    """result.json 한 번만 GET. None = 미존재."""
    from src.distill.deployer import _s3_client  # noqa: PLC0415
    s3 = _s3_client()
    try:
        obj = s3.get_object(Bucket=s3_bucket, Key=result_key)
        return json.loads(obj["Body"].read().decode())
    except s3.exceptions.NoSuchKey:
        return None
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
        logger.debug("S3 check failed for %s: %s", result_key, e)
        return None


# ---------------------------------------------------------------------------
# 외부 API — start (one-shot, polling X) + check (one-shot, sweeper 호출)
# ---------------------------------------------------------------------------

async def start_gpu_training(
    build_id: str,
    jsonl_path: str,
    config: dict,
    s3_bucket: str,
    s3_prefix: str,
) -> dict[str, Any]:
    """GPU EC2 학습 시작 — 즉시 return. polling X.

    Returns:
        {
            "status": "started",
            "gpu_instance_id": "i-...",
            "s3_result_key": "<prefix>train/<id>/output/result.json",
            "started_at": datetime (UTC),
        }
        또는 ``{"status": "error", "error": ...}``.

    Caller 가 ``DistillBuildRepository.set_gpu_metadata(build_id, ...)`` 호출
    해서 DB 에 sweeper marker 등록해야 함. 등록 안 하면 sweeper 가 본 build
    를 못 찾음 (gpu_instance_id IS NOT NULL 필터).
    """
    instance_id = _GPU_INSTANCE_ID
    if not instance_id:
        return {"status": "error", "error": "DISTILL_GPU_INSTANCE_ID not configured"}

    try:
        # 1. S3 업로드
        await asyncio.to_thread(
            _upload_training_data_sync,
            s3_bucket, s3_prefix, build_id, jsonl_path, config,
        )

        # 2. EC2 시작
        state = await _get_instance_state(instance_id)
        if state != "running":
            started = await _start_instance(instance_id)
            if not started:
                return {"status": "error", "error": "Failed to start GPU instance"}

        result_key = _build_result_key(s3_prefix, build_id)
        logger.info(
            "GPU training started: build=%s, instance=%s, result_key=%s",
            build_id, instance_id, result_key,
        )
        return {
            "status": "started",
            "gpu_instance_id": instance_id,
            "s3_result_key": result_key,
            "started_at": datetime.now(UTC),
        }

    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
        logger.exception("GPU start error for build %s", build_id)
        return {"status": "error", "error": str(e)}


async def check_gpu_training(
    *,
    gpu_instance_id: str,
    s3_bucket: str,
    s3_result_key: str,
) -> dict[str, Any]:
    """sweeper 가 호출 — 1회 check, polling X.

    반환:
        ``{"status": "success", **result_json}`` — result.json 발견 + status=completed
        ``{"status": "failed", "error": ...}`` — result.json 의 status=failed
                                                 또는 EC2 stopped + result 없음
        ``{"status": "running"}``                — 아직 진행 중
    """
    # 1) result.json 우선 확인 — 학습 종료가 EC2 stop 보다 먼저
    result = await asyncio.to_thread(
        _check_output_exists_sync, s3_bucket, s3_result_key,
    )
    if result is not None:
        rstatus = str(result.get("status", "unknown"))
        if rstatus == "completed":
            # result.json 의 ``status="completed"`` 가 wrapper 의 ``"success"`` 를
            # dict merge 에서 덮어쓰지 않도록 status 를 마지막에 재설정. 과거 bug
            # ({"status": "success", **result} 가 result 의 "completed" 가 이김).
            return {**result, "status": "success"}
        if rstatus == "failed":
            return {"status": "failed", "error": result.get("error", "unknown")}
        # unknown — caller 가 알아서 결정
        return {"status": "failed", "error": f"unknown result status: {rstatus}"}

    # 2) EC2 가 stopped/terminated 인데 result 없으면 부팅 스크립트 실패.
    if gpu_instance_id:
        ec2_state = await _get_instance_state(gpu_instance_id)
        if ec2_state in ("stopped", "terminated"):
            logger.error(
                "EC2 %s without result.json — boot script likely crashed",
                ec2_state,
            )
            return {
                "status": "failed",
                "error": f"EC2 {ec2_state} without result.json",
            }

    # 3) 아직 진행 중 — 다음 sweeper tick 에서 재확인
    return {"status": "running"}
