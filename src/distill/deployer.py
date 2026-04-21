"""S3 배포 + 매니페스트 관리.

양자화된 GGUF 모델을 S3에 업로드하고 pre-signed URL이 포함된 manifest 생성.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from src.distill.config import DistillProfile

logger = logging.getLogger(__name__)


def _s3_client() -> Any:
    """V4 서명 + region/endpoint_url 통일된 S3 client.

    내부 helper ``src/storage/s3.py:get_s3_client`` 로 위임 — bulk upload 와 같은
    helper 사용 (MinIO endpoint_url 자동 적용). distill 모듈 caller 호환을 위해
    wrapper 유지.
    """
    from src.storage.s3 import get_s3_client  # noqa: PLC0415
    return get_s3_client()


def _parse_s3_uri(s3_uri: str) -> tuple[str, str]:
    """s3://bucket/key → (bucket, key). 유효하지 않으면 ValueError."""
    if not s3_uri.startswith("s3://"):
        raise ValueError(f"Invalid s3_uri: {s3_uri}")
    rest = s3_uri[len("s3://"):]
    if "/" not in rest:
        raise ValueError(f"Invalid s3_uri (no key): {s3_uri}")
    bucket, key = rest.split("/", 1)
    return bucket, key


class DistillDeployer:
    """S3 모델 배포 관리."""

    def __init__(self, profile: DistillProfile) -> None:
        self.profile = profile
        self.bucket = profile.deploy.s3_bucket
        self.prefix = profile.deploy.s3_prefix

    async def upload_to_s3(self, gguf_path: str, version: str) -> str:
        """GGUF 파일을 S3에 업로드."""
        import asyncio

        s3_key = f"{self.prefix}{version}/model.gguf"

        def _upload() -> str:
            s3 = _s3_client()
            logger.info("Uploading %s → s3://%s/%s", gguf_path, self.bucket, s3_key)
            s3.upload_file(gguf_path, self.bucket, s3_key)
            return f"s3://{self.bucket}/{s3_key}"

        s3_uri = await asyncio.to_thread(_upload)
        logger.info("Upload complete: %s", s3_uri)
        return s3_uri

    async def copy_in_s3(self, src_uri: str, version: str) -> str:
        """S3 내부 객체 복사 (GPU 학습 결과물을 버전 경로로 이동).

        대용량 GGUF(>5GB)를 대비해 `s3.copy()` high-level API 사용 —
        필요 시 multipart 자동 처리.
        """
        import asyncio

        src_bucket, src_key = _parse_s3_uri(src_uri)
        dst_key = f"{self.prefix}{version}/model.gguf"

        def _copy() -> str:
            s3 = _s3_client()
            logger.info("Copying s3://%s/%s → s3://%s/%s",
                        src_bucket, src_key, self.bucket, dst_key)
            s3.copy(
                CopySource={"Bucket": src_bucket, "Key": src_key},
                Bucket=self.bucket,
                Key=dst_key,
            )
            return f"s3://{self.bucket}/{dst_key}"

        dst_uri = await asyncio.to_thread(_copy)
        logger.info("Copy complete: %s", dst_uri)
        return dst_uri

    async def create_and_upload_manifest(
        self, s3_uri: str, version: str, build_info: dict,
    ) -> dict:
        """manifest.json 생성 + S3 업로드 (pre-signed download URL 포함).

        download_url 은 `s3_uri` 파라미터의 실제 위치로 서명한다
        (예전엔 {prefix}{version}/model.gguf 로 재조립했는데, GPU 학습 경로와
        어긋나서 NoSuchKey 버그가 있었음).
        """
        import asyncio

        sha256 = build_info.get("gguf_sha256", "")
        gguf_bucket, gguf_key = _parse_s3_uri(s3_uri)

        def _create_manifest() -> dict:
            s3 = _s3_client()

            # Pre-signed download URL (24시간 유효) — s3_uri에서 추출한 실제 위치로 서명
            download_url = s3.generate_presigned_url(
                "get_object",
                Params={"Bucket": gguf_bucket, "Key": gguf_key},
                ExpiresIn=86400,
            )

            # 기존 manifest에서 app 정보 유지
            existing_manifest = {}
            manifest_key = f"{self.prefix}manifest.json"
            try:
                resp = s3.get_object(Bucket=self.bucket, Key=manifest_key)
                existing_manifest = json.loads(resp["Body"].read())
            except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError):
                pass

            manifest = {
                "version": version,
                "sha256": sha256,
                "download_url": download_url,
                "s3_uri": s3_uri,
                "base_model": build_info.get("base_model", ""),
                "search_group": build_info.get("search_group", ""),
                "training_samples": build_info.get("training_samples", 0),
                "eval_faithfulness": build_info.get("eval_faithfulness"),
                "eval_relevancy": build_info.get("eval_relevancy"),
                "gguf_size_mb": build_info.get("gguf_size_mb"),
                "gguf_sha256": build_info.get("gguf_sha256", ""),
                "quantize_method": build_info.get("quantize_method"),
                "created_at": datetime.now(timezone.utc).isoformat(),
                "format_version": "2.0",
                # 앱 정보 유지 (build_edge_binary.py에서 업데이트)
                "app_version": existing_manifest.get("app_version", ""),
                "app_downloads": existing_manifest.get("app_downloads", {}),
            }

            # manifest 업로드
            manifest_key = f"{self.prefix}manifest.json"
            s3.put_object(
                Bucket=self.bucket,
                Key=manifest_key,
                Body=json.dumps(manifest, ensure_ascii=False, indent=2),
                ContentType="application/json",
            )
            logger.info("Manifest uploaded: s3://%s/%s", self.bucket, manifest_key)
            return manifest

        return await asyncio.to_thread(_create_manifest)

    async def create_force_update(self, version: str) -> None:
        """긴급 업데이트 트리거 파일 생성."""
        import asyncio

        def _create() -> None:
            s3 = _s3_client()
            force_key = f"{self.prefix}force_update.json"
            s3.put_object(
                Bucket=self.bucket,
                Key=force_key,
                Body=json.dumps({
                    "version": version,
                    "urgent": True,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }),
                ContentType="application/json",
            )
            logger.info("Force update created: %s", force_key)

        await asyncio.to_thread(_create)

    async def delete_s3_object(self, s3_uri: str) -> None:
        """S3 오브젝트 삭제 (best-effort)."""
        import asyncio

        try:
            bucket, key = _parse_s3_uri(s3_uri)
        except ValueError:
            return

        def _delete() -> None:
            s3 = _s3_client()
            s3.delete_object(Bucket=bucket, Key=key)
            logger.info("Deleted S3 object: %s", s3_uri)

        await asyncio.to_thread(_delete)
