"""Bulk upload arq jobs — S3 → ingest pipeline + orphan cleanup.

1. ``ingest_from_object_storage(session_id, failed_indices)`` — finalize 시
   enqueue. session 의 모든 파일 (실패 idx 제외) 을 S3 download → parse →
   pipeline.ingest. partial failure 허용 (실패 카운트 누적).
2. ``cleanup_orphan_uploads()`` — arq cron, 매일 1회. 24h 이상 status='pending'
   세션의 S3 prefix 삭제 + DB status='failed' mark. orphan S3 비용 + DB row
   누적 차단.

DB 가 SSOT — increment_processed 가 status 자동 전이 (모두 처리 시 completed/
failed). API 재시작 무관, worker 가 retry (max_tries=3 from WorkerSettings).
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

# Orphan cleanup TTL — 24h. env 로 override (테스트/긴급 시).
_ORPHAN_TTL_HOURS = int(os.getenv("UPLOADS_ORPHAN_TTL_HOURS", "24"))


def _get_state() -> Any:
    from src.api.app import _get_state as _inner

    return _inner()


def _get_repo() -> Any:
    state = _get_state()
    repo = state.get("bulk_upload_repo")
    if repo is None:
        raise RuntimeError("bulk_upload_repo not initialized")
    return repo


def _build_pipeline(state: Any) -> Any:
    """ingest endpoint (`upload_file`) 와 같은 IngestionPipeline 구성."""
    from src.api.routes.ingest import _OnnxSparseEmbedder
    from src.pipelines.ingestion import IngestionPipeline

    embedder = state.get("embedder")
    store = state.get("qdrant_store")
    if not embedder or not store:
        raise RuntimeError("embedder/qdrant_store not initialized")

    sparse_embedder = _OnnxSparseEmbedder(embedder)
    return IngestionPipeline(
        embedder=embedder,
        sparse_embedder=sparse_embedder,
        vector_store=store,
        graph_store=state.get("graph_repo"),
        dedup_cache=state.get("dedup_cache"),
        dedup_pipeline=state.get("dedup_pipeline"),
        enable_ingestion_gate=True,
        enable_term_extraction=True,
        enable_graphrag=True,
        term_extractor=state.get("term_extractor"),
        graphrag_extractor=state.get("graphrag_extractor"),
    )


async def ingest_from_object_storage(
    ctx: dict[str, Any],
    session_id: str,
    failed_indices: list[int] | None = None,
) -> dict[str, Any]:
    """1 session 의 모든 파일을 S3 → ingest. partial failure 허용."""
    job_id = ctx.get("job_id", "?")
    logger.info(
        "ingest_from_object_storage[%s] session=%s skip=%s",
        job_id, session_id, failed_indices,
    )
    repo = _get_repo()
    sess = await repo.get_for_worker(session_id)
    if sess is None:
        logger.error("session %s not found — skip", session_id)
        return {"status": "session_missing"}

    state = _get_state()
    pipeline = _build_pipeline(state)

    from src.config import get_settings
    from src.core.models import RawDocument
    from src.pipelines.document_parser import parse_file_enhanced
    from src.storage import S3StorageError, delete_object, download_to_tempfile

    bucket = get_settings().aws.s3_uploads_bucket
    skip = set(failed_indices or [])
    files: list[dict[str, Any]] = sess.get("files") or []
    kb_count = 0
    chunks_total = 0

    # KB collection 사전 생성 (ingest endpoint 와 동일 패턴).
    collections = state.get("qdrant_collections")
    if collections:
        await collections.ensure_collection(sess["kb_id"])

    for entry in files:
        idx = int(entry.get("file_idx", -1))
        if idx in skip:
            await repo.increment_processed(
                session_id, success=False,
                filename=entry.get("filename"),
                error="browser PUT failed",
            )
            continue
        s3_key = entry.get("s3_key")
        filename = entry.get("filename") or f"file-{idx}"
        if not s3_key:
            await repo.increment_processed(
                session_id, success=False,
                filename=filename, error="missing s3_key in session",
            )
            continue

        suffix = ""
        if "." in filename:
            suffix = "." + filename.rsplit(".", 1)[-1]

        try:
            tmp_path = await asyncio.to_thread(
                download_to_tempfile, bucket=bucket, key=s3_key, suffix=suffix,
            )
        except S3StorageError as e:
            logger.warning("S3 download 실패 (%s): %s", s3_key, e)
            await repo.increment_processed(
                session_id, success=False, filename=filename, error=str(e),
            )
            continue

        try:
            parse_result = await asyncio.to_thread(parse_file_enhanced, str(tmp_path))
            text = (
                parse_result.full_text
                if hasattr(parse_result, "full_text") else str(parse_result)
            )
            if not text:
                raise ValueError(f"빈 본문: {filename}")

            raw = RawDocument(
                doc_id=RawDocument.sha256(s3_key),
                title=filename,
                content=text,
                source_uri=f"s3://{bucket}/{s3_key}",
            )
            ingest_result = await pipeline.ingest(raw, collection_name=sess["kb_id"])
            chunks_total += int(ingest_result.chunks_stored or 0)
            kb_count += 1 if (ingest_result.chunks_stored or 0) > 0 else 0
            await repo.increment_processed(
                session_id, success=True, filename=filename,
            )
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.warning("ingest 실패 (%s): %s", filename, e)
            await repo.increment_processed(
                session_id, success=False, filename=filename, error=str(e),
            )
        finally:
            # tempfile 정리.
            try:
                tmp_path.unlink()
            except OSError:
                pass
            # S3 cleanup — 성공/실패 무관 삭제 (orphan 방지). 재시도 필요하면
            # 사용자가 다시 업로드 (ingest 결과는 DB 에 이미 누적됨).
            await asyncio.to_thread(delete_object, bucket=bucket, key=s3_key)

    # KB registry counts 업데이트 — ingest endpoint 와 동일.
    if kb_count > 0:
        kb_registry = state.get("kb_registry")
        if kb_registry is not None:
            try:
                await kb_registry.update_counts(sess["kb_id"], kb_count, chunks_total)
            except (OSError, ValueError, RuntimeError) as e:
                logger.warning("KB count update 실패: %s", e)

    final = await repo.get_for_worker(session_id)
    return {
        "status": (final or {}).get("status", "unknown"),
        "processed": (final or {}).get("processed_files", 0),
        "failed": (final or {}).get("failed_files", 0),
    }


async def cleanup_orphan_uploads(ctx: dict[str, Any]) -> dict[str, int]:
    """매일 1회 — 24h 이상 status='pending' session 의 S3 prefix + DB row 정리.

    원인: 사용자가 init 후 finalize 안 하고 abort (브라우저 종료 등). presigned
    URL 은 1h 후 만료라 더 PUT 못 하고, S3 object 만 남음 (비용 + 보안 부담).

    동작:
    1. status='pending' AND created_at < now - 24h 인 session list
    2. 각 session 의 s3_prefix 아래 모든 object list + 삭제
    3. DB status='failed' + error_message='orphan cleanup' mark

    idempotent — 다시 실행해도 동일 session 두 번 처리 안 됨 (status 가
    'failed' 로 바뀌어 list 에서 제외).
    """
    job_id = ctx.get("job_id", "?")
    repo = _get_repo()
    cutoff = datetime.now(UTC) - timedelta(hours=_ORPHAN_TTL_HOURS)

    # repo 에 list_orphan(cutoff) 메서드는 별도 — 일단 raw query 로 처리.
    sessions = await repo.list_orphan_pending(cutoff=cutoff)
    if not sessions:
        return {"scanned": 0, "cleaned": 0, "errors": 0}

    from src.config import get_settings
    from src.storage import S3StorageError, get_s3_client

    bucket = get_settings().aws.s3_uploads_bucket
    s3 = get_s3_client()

    cleaned = 0
    errors = 0
    for sess in sessions:
        sid = sess["id"]
        prefix = sess["s3_prefix"]
        try:
            # list_objects_v2 paged
            paginator = s3.get_paginator("list_objects_v2")
            keys_to_delete: list[dict[str, str]] = []
            for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
                for obj in page.get("Contents") or []:
                    keys_to_delete.append({"Key": obj["Key"]})
                    if len(keys_to_delete) >= 1000:
                        # delete_objects 는 한 번에 최대 1000개
                        await asyncio.to_thread(
                            s3.delete_objects,
                            Bucket=bucket, Delete={"Objects": keys_to_delete},
                        )
                        keys_to_delete = []
            if keys_to_delete:
                await asyncio.to_thread(
                    s3.delete_objects,
                    Bucket=bucket, Delete={"Objects": keys_to_delete},
                )
            await repo.set_status(sid, "failed")
            await repo.update_error(
                sid, error_message=(
                    f"orphan cleanup — {_ORPHAN_TTL_HOURS}h 이상 finalize 안 됨"
                ),
            )
            cleaned += 1
            logger.info(
                "cleanup_orphan_uploads[%s] session=%s prefix=%s cleaned",
                job_id, sid, prefix,
            )
        except S3StorageError as e:
            logger.warning("orphan cleanup S3 실패 (sess=%s): %s", sid, e)
            errors += 1
        except Exception as e:  # noqa: BLE001 — boto/DB 예외 통합
            logger.warning("orphan cleanup 실패 (sess=%s): %s", sid, e)
            errors += 1

    return {"scanned": len(sessions), "cleaned": cleaned, "errors": errors}
