"""엣지 로그 수집 — S3에서 매장 로그 JSONL 다운로드 + DB 저장."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from src.distill.config import DistillProfile
from src.distill.repository import DistillRepository

logger = logging.getLogger(__name__)


class EdgeLogCollector:
    """S3에서 엣지 서버 로그를 수집하여 DB에 저장."""

    def __init__(self, profile: DistillProfile):
        self.profile = profile
        self.bucket = profile.deploy.s3_bucket
        self.prefix = profile.deploy.s3_prefix

    async def collect(
        self,
        repo: DistillRepository,
        profile_name: str,
        since: datetime | None = None,
    ) -> int:
        """S3 로그 파일 수집 → DB 저장."""
        import asyncio

        import boto3

        def _list_and_download():
            s3 = boto3.client("s3")
            log_prefix = f"{self.prefix}logs/"
            all_logs: list[dict[str, Any]] = []

            # S3에서 로그 파일 목록
            paginator = s3.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self.bucket, Prefix=log_prefix):
                for obj in page.get("Contents", []):
                    key = obj["Key"]
                    if not key.endswith(".jsonl"):
                        continue

                    # 시간 필터
                    if since and obj["LastModified"].replace(tzinfo=timezone.utc) < since:
                        continue

                    # 다운로드 + 파싱
                    try:
                        response = s3.get_object(Bucket=self.bucket, Key=key)
                        body = response["Body"].read().decode("utf-8")
                        for line in body.strip().split("\n"):
                            if not line.strip():
                                continue
                            entry = json.loads(line)
                            all_logs.append({
                                "id": str(uuid.uuid4()),
                                "profile_name": profile_name,
                                "store_id": entry.get("store_id", "unknown"),
                                "query": entry.get("query", ""),
                                "answer": entry.get("answer", ""),
                                "latency_ms": entry.get("latency_ms"),
                                "success": entry.get("success", True),
                                "model_version": entry.get("model_version"),
                                "edge_timestamp": self._parse_timestamp(entry.get("ts")),
                            })
                    except Exception as e:
                        logger.warning("Failed to process %s: %s", key, e)

                    # 처리 완료된 파일 삭제 (또는 아카이브)
                    try:
                        s3.delete_object(Bucket=self.bucket, Key=key)
                    except Exception:
                        pass

            return all_logs

        logs = await asyncio.to_thread(_list_and_download)

        if not logs:
            logger.info("No new edge logs found")
            return 0

        # DB 저장 (배치)
        batch_size = 100
        total_saved = 0
        for i in range(0, len(logs), batch_size):
            batch = logs[i:i + batch_size]
            saved = await repo.save_edge_logs(batch)
            total_saved += saved

        logger.info("Collected %d edge logs from S3", total_saved)
        return total_saved

    @staticmethod
    def _parse_timestamp(ts_str: str | None) -> datetime:
        """타임스탬프 안전 파싱."""
        if not ts_str:
            return datetime.now(timezone.utc)
        try:
            return datetime.fromisoformat(ts_str)
        except (ValueError, TypeError):
            return datetime.now(timezone.utc)
