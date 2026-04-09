"""GPU EC2 인스턴스 기반 원격 학습.

PaddleOCR와 동일 패턴: EC2 시작 → IP로 HTTP 학습 트리거 → 완료 폴링 → 자동 중지.
SSM 미사용 — SCP 제약 우회.

Usage:
    학습 요청 시:
    1. EC2 GPU 인스턴스 시작 (g4dn.xlarge)
    2. 학습 데이터(JSONL) + 설정을 S3에 업로드
    3. EC2의 학습 API 서버에 HTTP로 학습 트리거 (/train)
    4. 학습 완료 폴링 (/status) → 모델 S3 업로드 확인
    5. EC2 인스턴스 자동 중지

환경변수:
    DISTILL_GPU_INSTANCE_ID: EC2 인스턴스 ID (g4dn.xlarge)
    DISTILL_GPU_PORT: 학습 API 서버 포트 (기본 8080)
    AWS_REGION: ap-northeast-2
"""

from __future__ import annotations

import asyncio
import json
import logging
import os

import httpx

logger = logging.getLogger(__name__)

_GPU_INSTANCE_ID = os.getenv("DISTILL_GPU_INSTANCE_ID", "")
_GPU_PORT = int(os.getenv("DISTILL_GPU_PORT", "8080"))
_AWS_REGION = os.getenv("AWS_REGION", "ap-northeast-2")
_AWS_PROFILE = os.getenv("AWS_PROFILE", "jeongbeomkim")


# ---------------------------------------------------------------------------
# EC2 lifecycle helpers (PaddleOCR 동일 패턴)
# ---------------------------------------------------------------------------

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


async def _get_instance_ip(instance_id: str) -> str | None:
    """EC2 인스턴스 Public IP 조회."""
    proc = await asyncio.create_subprocess_shell(
        f"aws ec2 describe-instances --instance-ids {instance_id} "
        f"--region {_AWS_REGION} --profile {_AWS_PROFILE} "
        f"--query 'Reservations[0].Instances[0].PublicIpAddress' --output text",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    ip = stdout.decode().strip()
    return ip if ip and ip != "None" else None


async def _start_instance(instance_id: str) -> bool:
    """EC2 인스턴스 시작 → running 대기."""
    state = await _get_instance_state(instance_id)

    if state == "stopping":
        logger.info("Instance stopping, waiting for stopped first...")
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

    # running 대기 (최대 5분)
    for _ in range(60):
        if await _get_instance_state(instance_id) == "running":
            logger.info("GPU instance running")
            return True
        await asyncio.sleep(5)
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


# ---------------------------------------------------------------------------
# HTTP API 기반 학습 (SSM 대체)
# ---------------------------------------------------------------------------

async def _wait_for_health(base_url: str, max_wait: int = 300) -> bool:
    """학습 API 서버 health check 대기."""
    deadline = asyncio.get_event_loop().time() + max_wait
    async with httpx.AsyncClient(timeout=5) as client:
        while asyncio.get_event_loop().time() < deadline:
            try:
                resp = await client.get(f"{base_url}/health")
                if resp.status_code == 200:
                    logger.info("GPU training API ready at %s", base_url)
                    return True
            except (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout):
                pass
            await asyncio.sleep(10)
    return False


async def _trigger_training(base_url: str, s3_train_path: str, build_id: str) -> bool:
    """HTTP POST로 학습 트리거."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{base_url}/train",
            json={"s3_path": s3_train_path, "build_id": build_id},
        )
        if resp.status_code == 200:
            logger.info("Training triggered: %s", resp.json())
            return True
        logger.error("Training trigger failed: %s %s", resp.status_code, resp.text[:200])
        return False


async def _poll_training_status(
    base_url: str, build_id: str, timeout: int = 7200,
) -> dict:
    """학습 완료 폴링 (최대 2시간)."""
    async with httpx.AsyncClient(timeout=10) as client:
        for _ in range(timeout // 10):
            try:
                resp = await client.get(f"{base_url}/status/{build_id}")
                if resp.status_code == 200:
                    data = resp.json()
                    status = data.get("status", "")
                    if status == "completed":
                        logger.info("Remote training completed: %s", data)
                        return {"status": "success", **data}
                    if status == "failed":
                        error = data.get("error", "unknown")
                        logger.error("Remote training failed: %s", error[:200])
                        return {"status": "failed", "error": error}
                    # still training...
            except (httpx.ConnectError, httpx.ReadTimeout):
                pass
            await asyncio.sleep(10)

    return {"status": "timeout"}


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
    """GPU EC2에서 학습 실행 (전체 흐름).

    1. EC2 시작 (stopping 상태면 stopped 될 때까지 대기)
    2. 데이터 S3 업로드
    3. HTTP로 학습 트리거 + 완료 폴링
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

        # 2. IP 조회 + health check
        ip = await _get_instance_ip(instance_id)
        if not ip:
            return {"status": "error", "error": "GPU instance has no public IP"}

        base_url = f"http://{ip}:{_GPU_PORT}"
        if not await _wait_for_health(base_url):
            return {"status": "error", "error": f"GPU training API not ready at {base_url}"}

        # 3. 학습 데이터 S3 업로드
        s3_train_path = await asyncio.to_thread(
            _upload_training_data_sync,
            s3_bucket, s3_prefix, build_id, jsonl_path, config,
        )

        # 4. HTTP로 학습 트리거
        if not await _trigger_training(base_url, s3_train_path, build_id):
            return {"status": "error", "error": "Failed to trigger training via HTTP"}

        # 5. 완료 폴링
        return await _poll_training_status(base_url, build_id)

    finally:
        # 6. 인스턴스 중지 (항상)
        try:
            await _stop_instance(instance_id)
        except Exception as e:
            logger.error("Failed to stop GPU instance: %s", e)


def _upload_training_data_sync(
    s3_bucket: str, s3_prefix: str, build_id: str,
    jsonl_path: str, config: dict,
) -> str:
    """동기 버전 — asyncio.to_thread에서 호출."""
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
