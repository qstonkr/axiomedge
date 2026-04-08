"""GPU EC2 인스턴스 기반 원격 학습.

PaddleOCR와 동일 패턴: 필요할 때 EC2 시작 → 학습 → 자동 중지.

Usage:
    학습 요청 시:
    1. EC2 GPU 인스턴스 시작 (g4dn.xlarge)
    2. 학습 데이터(JSONL) + 설정을 S3에 업로드
    3. EC2에서 학습 스크립트 실행 (SSM Run Command)
    4. 학습 완료 → 모델을 S3에 업로드
    5. EC2 인스턴스 자동 중지

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


async def _get_instance_state(instance_id: str) -> str:
    """EC2 인스턴스 상태 조회."""
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
    """EC2 인스턴스 시작."""
    logger.info("Starting GPU instance %s", instance_id)
    proc = await asyncio.create_subprocess_shell(
        f"aws ec2 start-instances --instance-ids {instance_id} "
        f"--region {_AWS_REGION} --profile {_AWS_PROFILE}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()

    # running 될 때까지 대기 (최대 5분)
    for _ in range(30):
        state = await _get_instance_state(instance_id)
        if state == "running":
            logger.info("GPU instance running")
            return True
        await asyncio.sleep(10)
    return False


async def _stop_instance(instance_id: str) -> None:
    """EC2 인스턴스 중지 (비용 절감)."""
    logger.info("Stopping GPU instance %s", instance_id)
    proc = await asyncio.create_subprocess_shell(
        f"aws ec2 stop-instances --instance-ids {instance_id} "
        f"--region {_AWS_REGION} --profile {_AWS_PROFILE}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()
    logger.info("GPU instance stop requested")


async def _upload_training_data(
    s3_bucket: str, s3_prefix: str, build_id: str,
    jsonl_path: str, config: dict,
) -> str:
    """학습 데이터 + 설정을 S3에 업로드."""
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


async def _run_remote_training(instance_id: str, s3_train_path: str, build_id: str) -> dict:
    """SSM Run Command로 EC2에서 학습 실행."""
    import boto3

    ssm = boto3.Session(
        profile_name=_AWS_PROFILE, region_name=_AWS_REGION,
    ).client("ssm")

    # EC2에서 실행할 학습 스크립트
    command = f"""
#!/bin/bash
set -e
cd /opt/distill

# 학습 데이터 다운로드
aws s3 sync {s3_train_path} ./data/{build_id}/

# 학습 실행
python3 train_remote.py \
    --data-dir ./data/{build_id}/ \
    --output-dir ./output/{build_id}/ \
    --build-id {build_id}

# 결과 업로드
aws s3 sync ./output/{build_id}/ {s3_train_path}output/

# 완료 시그널
echo "TRAINING_COMPLETE" > /tmp/train_status_{build_id}
"""

    response = ssm.send_command(
        InstanceIds=[instance_id],
        DocumentName="AWS-RunShellScript",
        Parameters={"commands": [command]},
        TimeoutSeconds=7200,  # 2시간 타임아웃
    )
    command_id = response["Command"]["CommandId"]
    logger.info("SSM command sent: %s", command_id)

    # 완료 대기 (polling)
    for _ in range(720):  # 최대 2시간
        await asyncio.sleep(10)
        try:
            result = ssm.get_command_invocation(
                CommandId=command_id,
                InstanceId=instance_id,
            )
            status = result["Status"]
            if status == "Success":
                logger.info("Remote training completed")
                return {"status": "success", "command_id": command_id}
            if status in ("Failed", "TimedOut", "Cancelled"):
                error = result.get("StandardErrorContent", "")
                logger.error("Remote training failed: %s", error[:200])
                return {"status": "failed", "error": error[:500]}
        except Exception:
            pass

    return {"status": "timeout"}


async def run_gpu_training(
    build_id: str,
    jsonl_path: str,
    config: dict,
    s3_bucket: str,
    s3_prefix: str,
) -> dict:
    """GPU EC2에서 학습 실행 (전체 흐름).

    1. EC2 시작
    2. 데이터 S3 업로드
    3. SSM으로 학습 실행
    4. 완료 후 EC2 중지
    """
    instance_id = _GPU_INSTANCE_ID
    if not instance_id:
        return {"status": "error", "error": "DISTILL_GPU_INSTANCE_ID not configured"}

    try:
        # 1. 인스턴스 시작
        state = await _get_instance_state(instance_id)
        if state != "running":
            started = await _start_instance(instance_id)
            if not started:
                return {"status": "error", "error": "Failed to start GPU instance"}

        # 2. 학습 데이터 업로드
        s3_train_path = await asyncio.to_thread(
            lambda: asyncio.run(_upload_training_data(
                s3_bucket, s3_prefix, build_id, jsonl_path, config,
            ))
        )

        # 3. 원격 학습
        result = await _run_remote_training(instance_id, s3_train_path, build_id)

        return result

    finally:
        # 4. 인스턴스 중지 (항상)
        try:
            await _stop_instance(instance_id)
        except Exception as e:
            logger.error("Failed to stop GPU instance: %s", e)
