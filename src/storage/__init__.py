"""Storage backends — S3/MinIO 통합 client.

``get_s3_client()`` 가 SSOT — boto3 client 생성 + endpoint_url override
(MinIO on-prem vs AWS cloud) 통일 처리.
"""

from .s3 import (
    S3StorageError,
    build_object_key,
    delete_object,
    download_to_tempfile,
    generate_presigned_put_url,
    get_s3_client,
)

__all__ = [
    "S3StorageError",
    "build_object_key",
    "delete_object",
    "download_to_tempfile",
    "generate_presigned_put_url",
    "get_s3_client",
]
