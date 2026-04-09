"""GPU EC2 인스턴스 기반 원격 학습.

EC2 시작 → S3에서 작업 자동 감지 → 학습 → 결과 업로드 → 자동 중지.
SSM/SSH 미사용 — EC2 부팅 스크립트가 S3 작업을 자동 처리.

흐름:
    [우리 API]
    1. S3에 train/{build_id}/train.jsonl + config.json 업로드
    2. EC2 GPU 인스턴스 시작
    3. S3에서 output/{build_id}/ 폴링 → 완료 확인

    [EC2 부팅 스크립트 - systemd]
    1. S3 전체 프로필 스캔 → 미완료 작업(output 없는 train/) 탐색
    2. 학습 실행 (순차)
    3. 결과 S3 업로드
    4. shutdown -h now

환경변수:
    DISTILL_GPU_INSTANCE_ID: EC2 인스턴스 ID (g4dn.xlarge)
    AWS_REGION: ap-northeast-2
"""

from __future__ import annotations

import asyncio
import json
import logging
import os

logger = logging.getLogger(__name__)

_GPU_INSTANCE_ID = os.getenv("DISTILL_GPU_INSTANCE_ID", "")
_AWS_REGION = os.getenv("AWS_REGION", "ap-northeast-2")
_AWS_PROFILE = os.getenv("AWS_PROFILE", "jeongbeomkim")


# ---------------------------------------------------------------------------
# EC2 lifecycle
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


async def _stop_instance(instance_id: str) -> None:
    logger.info("Stopping GPU instance %s", instance_id)
    proc = await asyncio.create_subprocess_shell(
        f"aws ec2 stop-instances --instance-ids {instance_id} "
        f"--region {_AWS_REGION} --profile {_AWS_PROFILE}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()
    logger.info("GPU instance stop requested")


# ---------------------------------------------------------------------------
# S3 업로드 + 결과 폴링
# ---------------------------------------------------------------------------

def _upload_training_data_sync(
    s3_bucket: str, s3_prefix: str, build_id: str,
    jsonl_path: str, config: dict,
) -> str:
    """학습 데이터 + 설정을 S3에 업로드 (동기)."""
    import boto3

    s3 = boto3.Session(
        profile_name=_AWS_PROFILE, region_name=_AWS_REGION,
    ).client("s3")

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


def _check_output_exists(s3_bucket: str, s3_prefix: str, build_id: str) -> dict | None:
    """S3에 학습 결과가 있는지 확인."""
    import boto3

    s3 = boto3.Session(
        profile_name=_AWS_PROFILE, region_name=_AWS_REGION,
    ).client("s3")

    result_key = f"{s3_prefix}train/{build_id}/output/result.json"
    try:
        obj = s3.get_object(Bucket=s3_bucket, Key=result_key)
        return json.loads(obj["Body"].read().decode())
    except s3.exceptions.NoSuchKey:
        return None
    except Exception:
        return None


async def _poll_s3_output(
    s3_bucket: str, s3_prefix: str, build_id: str,
    timeout: int = 7200,
) -> dict:
    """S3에서 학습 결과 폴링 (최대 2시간)."""
    for _ in range(timeout // 15):
        result = await asyncio.to_thread(
            _check_output_exists, s3_bucket, s3_prefix, build_id,
        )
        if result is not None:
            status = result.get("status", "unknown")
            if status == "completed":
                logger.info("Training completed: %s", result)
                return {"status": "success", **result}
            if status == "failed":
                logger.error("Training failed: %s", result.get("error", ""))
                return {"status": "failed", "error": result.get("error", "unknown")}
        await asyncio.sleep(15)

    return {"status": "timeout"}


# ---------------------------------------------------------------------------
# 메인 엔트리 포인트
# ---------------------------------------------------------------------------

async def run_gpu_training(
    build_id: str,
    jsonl_path: str,
    config: dict,
    s3_bucket: str,
    s3_prefix: str,
) -> dict:
    """GPU EC2에서 학습 실행.

    1. S3에 학습 데이터 업로드
    2. EC2 시작 (부팅 스크립트가 S3 작업 자동 감지)
    3. S3 output 폴링으로 완료 확인
    4. EC2는 부팅 스크립트에서 자동 shutdown
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

        logger.info("GPU instance started, polling S3 for results...")

        # 3. S3 결과 폴링 (EC2 부팅 스크립트가 학습 완료 후 result.json 업로드)
        result = await _poll_s3_output(s3_bucket, s3_prefix, build_id)

        # EC2는 부팅 스크립트에서 자동 shutdown하지만, 안전망으로 중지 요청
        if await _get_instance_state(instance_id) == "running":
            await _stop_instance(instance_id)

        return result

    except Exception as e:
        logger.error("GPU training error: %s", e)
        # 에러 시에도 EC2 중지 시도
        try:
            await _stop_instance(instance_id)
        except Exception:
            pass
        return {"status": "error", "error": str(e)}
