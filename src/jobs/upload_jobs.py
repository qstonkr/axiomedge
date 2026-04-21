"""Bulk upload arq jobs — S3 → ingest pipeline.

1. ``ingest_from_object_storage(session_id, failed_indices)`` — finalize 시
   enqueue. session 의 모든 파일 (실패 idx 제외) 을 S3 download → parse →
   pipeline.ingest. partial failure 허용 (실패 카운트 누적).

DB 가 SSOT — increment_processed 가 status 자동 전이 (모두 처리 시 completed/
failed). API 재시작 무관, worker 가 retry (max_tries=3 from WorkerSettings).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


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
