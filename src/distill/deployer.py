"""S3 배포 + 매니페스트 관리.

양자화된 GGUF 모델을 S3에 업로드하고 pre-signed URL이 포함된 manifest 생성.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from src.distill.config import DistillProfile

logger = logging.getLogger(__name__)


class DistillDeployer:
    """S3 모델 배포 관리."""

    def __init__(self, profile: DistillProfile):
        self.profile = profile
        self.bucket = profile.deploy.s3_bucket
        self.prefix = profile.deploy.s3_prefix

    async def upload_to_s3(self, gguf_path: str, version: str) -> str:
        """GGUF 파일을 S3에 업로드."""
        import asyncio

        import boto3

        s3_key = f"{self.prefix}{version}/model.gguf"

        def _upload():
            s3 = boto3.client("s3")
            logger.info("Uploading %s → s3://%s/%s", gguf_path, self.bucket, s3_key)
            s3.upload_file(gguf_path, self.bucket, s3_key)
            return f"s3://{self.bucket}/{s3_key}"

        s3_uri = await asyncio.to_thread(_upload)
        logger.info("Upload complete: %s", s3_uri)
        return s3_uri

    async def create_and_upload_manifest(
        self, s3_uri: str, version: str, build_info: dict,
    ) -> dict:
        """manifest.json 생성 + S3 업로드 (pre-signed download URL 포함)."""
        import asyncio

        import boto3

        # SHA256 계산 (로컬 파일이 있으면)
        sha256 = ""
        gguf_key = f"{self.prefix}{version}/model.gguf"

        def _create_manifest():
            s3 = boto3.client("s3")

            # Pre-signed download URL (24시간 유효)
            download_url = s3.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket, "Key": gguf_key},
                ExpiresIn=86400,
            )

            # 기존 manifest에서 app 정보 유지
            existing_manifest = {}
            manifest_key = f"{self.prefix}manifest.json"
            try:
                resp = s3.get_object(Bucket=self.bucket, Key=manifest_key)
                existing_manifest = json.loads(resp["Body"].read())
            except Exception:
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

        import boto3

        def _create():
            s3 = boto3.client("s3")
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

        import boto3

        if not s3_uri.startswith("s3://"):
            return
        parts = s3_uri.replace("s3://", "").split("/", 1)
        if len(parts) != 2:
            return
        bucket, key = parts

        def _delete():
            s3 = boto3.client("s3")
            s3.delete_object(Bucket=bucket, Key=key)
            logger.info("Deleted S3 object: %s", s3_uri)

        await asyncio.to_thread(_delete)
