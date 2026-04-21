"""S3/MinIO 통합 helper — bulk upload + distill deploy 등에서 공유.

설계 원칙:
- ``boto3`` 기반 — MinIO 도 S3 호환 API 라 같은 코드.
- ``endpoint_url`` override 로 MinIO/AWS 자동 전환 (env 만 다름).
- presigned PUT URL 발급 (브라우저 직접 업로드용).
- tempfile 다운로드 helper (arq job 에서 사용).

기존 ``src/distill/deployer.py:_s3_client`` 도 본 helper 로 위임 — 코드 중복
제거 (P1).
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Any

import boto3
from botocore.client import Config

from src.config import get_settings

logger = logging.getLogger(__name__)


class S3StorageError(RuntimeError):
    """S3/MinIO 호출 실패."""


def get_s3_client() -> Any:
    """Boto3 S3 client — MinIO endpoint_url 자동 적용.

    AwsSettings.s3_endpoint_url 이 비어있으면 AWS 표준 endpoint (현 동작).
    값이 있으면 (예: ``http://minio:9000``) MinIO 등 S3 호환 endpoint 로 라우팅.
    """
    settings = get_settings()
    aws = settings.aws

    session_kwargs: dict[str, Any] = {"region_name": aws.region}
    if aws.profile:
        session_kwargs["profile_name"] = aws.profile

    client_kwargs: dict[str, Any] = {
        "config": Config(signature_version="s3v4"),
    }
    if aws.s3_endpoint_url:
        # MinIO — path-style addressing 강제 (default virtual-host 가 MinIO 호환 X)
        client_kwargs["endpoint_url"] = aws.s3_endpoint_url
        client_kwargs["config"] = Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
        )

    return boto3.Session(**session_kwargs).client("s3", **client_kwargs)


def build_object_key(
    *, user_id: str, session_id: str, file_idx: int, filename: str,
    prefix: str = "",
) -> str:
    """Bulk upload S3 key — user prefix 강제 (cross-user 격리).

    형식: ``{prefix}user/{user_id}/uploads/{session_id}/{file_idx}/{filename}``
    예: ``uploads/user/u-42/uploads/sess-abc/0/report.pdf``

    file_idx 가 path 에 들어가서 같은 session 안에서 이름 중복 파일도 분리.
    """
    safe_name = filename.replace("/", "_").replace("\\", "_")
    base = prefix.rstrip("/") + "/" if prefix else ""
    return f"{base}user/{user_id}/uploads/{session_id}/{file_idx}/{safe_name}"


def generate_presigned_put_url(
    *, bucket: str, key: str, ttl_seconds: int = 3600,
    content_length: int | None = None,
) -> str:
    """브라우저가 직접 PUT 할 presigned URL.

    content_length 가 주어지면 ContentLength 강제 — 사용자가 더 큰 파일을
    선언된 사이즈와 다르게 PUT 못 하게 막음 (security).
    """
    s3 = get_s3_client()
    params: dict[str, Any] = {"Bucket": bucket, "Key": key}
    if content_length is not None:
        params["ContentLength"] = content_length
    try:
        return s3.generate_presigned_url(
            "put_object", Params=params, ExpiresIn=ttl_seconds,
        )
    except Exception as e:  # noqa: BLE001 — boto/botocore 다양한 예외 통합
        raise S3StorageError(f"presigned URL 발급 실패: {e}") from e


def create_multipart_upload(*, bucket: str, key: str) -> str:
    """Multipart upload 시작 — 5GB+ 파일용. 반환 ``upload_id``.

    chunk 별 PUT 후 ``complete_multipart_upload`` 로 종료. abort 시 ``abort_
    multipart_upload`` (S3 자동 cleanup 안 함 — orphan 방지 위해 명시 호출 필요).
    """
    s3 = get_s3_client()
    try:
        resp = s3.create_multipart_upload(Bucket=bucket, Key=key)
    except Exception as e:  # noqa: BLE001
        raise S3StorageError(f"multipart upload init 실패: {e}") from e
    return str(resp["UploadId"])


def generate_presigned_part_url(
    *, bucket: str, key: str, upload_id: str, part_number: int,
    ttl_seconds: int = 3600,
) -> str:
    """Multipart upload 의 1개 part 에 대한 presigned URL.

    part_number 는 1-based (S3 API 규약). 각 part 5MB+ (마지막 제외).
    """
    s3 = get_s3_client()
    try:
        return s3.generate_presigned_url(
            "upload_part",
            Params={
                "Bucket": bucket, "Key": key,
                "UploadId": upload_id, "PartNumber": part_number,
            },
            ExpiresIn=ttl_seconds,
        )
    except Exception as e:  # noqa: BLE001
        raise S3StorageError(
            f"multipart presigned URL 발급 실패 (part={part_number}): {e}",
        ) from e


def complete_multipart_upload(
    *, bucket: str, key: str, upload_id: str,
    parts: list[dict[str, Any]],
) -> None:
    """모든 part PUT 완료 후 호출. ``parts`` = [{"PartNumber": 1, "ETag": "..."}, ...].

    S3 가 part 들을 합쳐 최종 object 생성. 이후 일반 GetObject 가능.
    """
    s3 = get_s3_client()
    try:
        s3.complete_multipart_upload(
            Bucket=bucket, Key=key, UploadId=upload_id,
            MultipartUpload={"Parts": parts},
        )
    except Exception as e:  # noqa: BLE001
        raise S3StorageError(f"multipart upload complete 실패: {e}") from e


def abort_multipart_upload(*, bucket: str, key: str, upload_id: str) -> None:
    """Multipart upload 중단 — orphan part 정리. idempotent."""
    s3 = get_s3_client()
    try:
        s3.abort_multipart_upload(
            Bucket=bucket, Key=key, UploadId=upload_id,
        )
    except Exception as e:  # noqa: BLE001 — 이미 abort 된 경우도 OK
        logger.warning(
            "multipart abort 실패 (key=%s, upload_id=%s): %s",
            key, upload_id, e,
        )


def download_to_tempfile(*, bucket: str, key: str, suffix: str = "") -> Path:
    """S3 object → tempfile path 반환. caller 가 처리 후 unlink 책임.

    arq ingest job 에서 사용 — 기존 ingest pipeline 의 file path 입력과 호환.
    """
    s3 = get_s3_client()
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    tmp_path = Path(tmp.name)
    try:
        s3.download_fileobj(bucket, key, tmp)
    except Exception as e:  # noqa: BLE001
        tmp.close()
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise S3StorageError(f"S3 download 실패 ({key}): {e}") from e
    finally:
        tmp.close()
    return tmp_path


def delete_object(*, bucket: str, key: str) -> None:
    """S3 object 삭제 — ingest 완료 후 cleanup. 미존재여도 idempotent."""
    s3 = get_s3_client()
    try:
        s3.delete_object(Bucket=bucket, Key=key)
    except Exception as e:  # noqa: BLE001 — 삭제 실패는 warning 만 (이미 없으면 OK)
        logger.warning("S3 delete failed (key=%s): %s", key, e)
