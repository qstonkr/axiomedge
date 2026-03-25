"""RAG API endpoints."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from src.api.routes.jobs import create_job, is_cancelled, update_job
from src.api.routes.metrics import inc as metrics_inc

logger = logging.getLogger(__name__)

# Knowledge RAG router
knowledge_router = APIRouter(prefix="/api/v1/knowledge", tags=["RAG"])

# RAG query alias router (dashboard compatibility)
rag_query_router = APIRouter(tags=["RAG"])


# ============================================================================
# Knowledge RAG
# ============================================================================

# ---------------------------------------------------------------------------
# POST /api/v1/knowledge/ask
# ---------------------------------------------------------------------------
@knowledge_router.post("/ask")
async def rag_query(body: dict[str, Any]):
    """RAG query."""
    from src.api.app import _get_state

    state = _get_state()
    rag = state.get("rag_pipeline")
    query = body.get("query", "")
    mode = body.get("mode", "classic")
    kb_ids = body.get("kb_ids")

    if rag:
        try:
            from src.search.rag_pipeline import RAGRequest

            kb_id = kb_ids[0] if kb_ids else None
            result = await rag.process(RAGRequest(query=query, kb_id=kb_id))
            return result.to_dict()
        except Exception as e:
            logger.warning("RAG query failed: %s", e)

    return {
        "query": query,
        "answer": None,
        "chunks": [],
        "mode": mode,
    }


# ---------------------------------------------------------------------------
# GET /api/v1/knowledge/rag/config
# ---------------------------------------------------------------------------
@knowledge_router.get("/rag/config")
async def get_rag_config():
    """Get RAG config."""
    return {
        "mode": "classic",
        "top_k": 5,
        "reranking": False,
        "graph_enabled": False,
    }


# ---------------------------------------------------------------------------
# GET /api/v1/knowledge/rag/stats
# ---------------------------------------------------------------------------
@knowledge_router.get("/rag/stats")
async def get_rag_stats():
    """Get RAG stats."""
    return {
        "total_queries": 0,
        "avg_response_time_ms": 0.0,
        "avg_chunks_returned": 0.0,
    }


# ============================================================================
# File Upload & Ingest (background processing with job ID)
# ============================================================================

async def _stage1_parse_to_jsonl(
    job_id: str,
    file_paths: list[tuple[str, str]],
    effective_kb_id: str,
) -> tuple[str, list[str]]:
    """Stage 1: Parse files and write results to JSONL checkpoint.

    Returns (jsonl_path, errors).
    """
    import hashlib
    import os
    import shutil

    from src.pipeline.document_parser import parse_file_enhanced
    from src.pipeline.jsonl_checkpoint import (
        JsonlCheckpointWriter,
        get_already_parsed_ids,
        get_jsonl_path,
        serialize_parse_result,
    )

    jsonl_path = get_jsonl_path(effective_kb_id)
    already_parsed = get_already_parsed_ids(jsonl_path)
    errors: list[str] = []
    parsed_count = 0

    with JsonlCheckpointWriter(jsonl_path) as writer:
        for fname, tmp_path in file_paths:
            if await is_cancelled(job_id):
                logger.info("Job %s cancelled during Stage 1", job_id)
                break

            try:
                # Compute content hash for dedup / doc_id
                with open(tmp_path, "rb") as f:
                    content_hash = hashlib.sha256(f.read()).hexdigest()[:16]

                # Skip if already parsed (resume after crash)
                if content_hash in already_parsed:
                    logger.info("Skipping already-parsed file: %s (%s)", fname, content_hash)
                    parsed_count += 1
                    continue

                # Save permanent copy
                uploads_dir = os.path.join(
                    os.getenv("KNOWLEDGE_PIPELINE_RUNTIME_BASE_DIR", "/tmp/knowledge-local"),
                    "uploads", effective_kb_id,
                )
                os.makedirs(uploads_dir, exist_ok=True)
                shutil.copy2(tmp_path, os.path.join(uploads_dir, fname))

                # Parse (OCR happens here)
                parse_result = await asyncio.to_thread(parse_file_enhanced, tmp_path)
                text = parse_result.full_text if hasattr(parse_result, "full_text") else str(parse_result)
                if not text:
                    errors.append(f"{fname}: empty content after parsing")
                    continue

                # LLM correction for OCR noise
                if parse_result.ocr_text:
                    try:
                        from src.api.app import _get_state
                        from src.pipeline.ocr_corrector import correct_ocr_chunks
                        llm = _get_state().get("llm")
                        if llm:
                            parse_result.ocr_text = await correct_ocr_chunks(
                                parse_result.ocr_text, llm,
                            )
                    except Exception as _corr_err:
                        logger.warning("OCR LLM correction skipped: %s", _corr_err)

                # Write to JSONL checkpoint
                json_line = serialize_parse_result(
                    doc_id=content_hash,
                    filename=fname,
                    source_path=tmp_path,
                    content_hash=content_hash,
                    parse_result=parse_result,
                )
                writer.write_record(json_line)
                parsed_count += 1
                logger.info("Stage 1: parsed %s -> %d chars (%s)", fname, len(text), content_hash)

            except Exception as e:
                errors.append(f"{fname}: {e}")
                metrics_inc("errors")

            await update_job(job_id, processed=0, chunks=0, errors=errors[:])

    logger.info("Stage 1 complete: %d parsed, %d errors, JSONL: %s", parsed_count, len(errors), jsonl_path)
    return str(jsonl_path), errors


async def _stage2_ingest_from_jsonl(
    job_id: str,
    jsonl_path: str,
    pipeline: Any,
    effective_kb_id: str,
) -> tuple[int, int, list[str]]:
    """Stage 2: Read JSONL checkpoint and ingest (chunk/embed/store).

    Returns (total_docs, total_chunks, errors).
    """
    from src.domain.models import RawDocument
    from src.pipeline.jsonl_checkpoint import JsonlCheckpointReader

    reader = JsonlCheckpointReader(jsonl_path)
    total_docs = 0
    total_chunks = 0
    errors: list[str] = []

    for record, parse_result in reader:
        if await is_cancelled(job_id):
            logger.info("Job %s cancelled during Stage 2", job_id)
            break

        try:
            raw = RawDocument(
                doc_id=record.doc_id,
                title=record.filename,
                content=parse_result.full_text,
                source_uri=record.filename,
            )
            ingest_result = await pipeline.ingest(
                raw, collection_name=effective_kb_id, parse_result=parse_result,
            )
            total_docs += 1
            total_chunks += ingest_result.chunks_stored
        except Exception as e:
            errors.append(f"{record.filename}: {e}")
            metrics_inc("errors")

        await update_job(job_id, processed=total_docs, chunks=total_chunks, errors=errors[:])

    logger.info("Stage 2 complete: %d docs, %d chunks, %d errors", total_docs, total_chunks, len(errors))
    return total_docs, total_chunks, errors


async def _process_files(
    job_id: str,
    file_paths: list[tuple[str, str]],
    pipeline: Any,
    effective_kb_id: str,
    save_dir: str = "",
) -> None:
    """Background task: two-stage ingestion with JSONL checkpoint.

    Stage 1: parse files + OCR -> JSONL (crash-safe, resumable)
    Stage 2: JSONL -> chunk + embed + store to Qdrant/Neo4j
    """
    # Stage 1: Parse to JSONL
    jsonl_path, stage1_errors = await _stage1_parse_to_jsonl(
        job_id, file_paths, effective_kb_id,
    )

    if await is_cancelled(job_id):
        await update_job(job_id, status="cancelled", errors=stage1_errors)
        return

    # Stage 2: Ingest from JSONL
    total_docs, total_chunks, stage2_errors = await _stage2_ingest_from_jsonl(
        job_id, jsonl_path, pipeline, effective_kb_id,
    )

    all_errors = stage1_errors + stage2_errors

    metrics_inc("ingest_documents", total_docs)
    metrics_inc("ingest_chunks", total_chunks)

    # Update KB registry counts
    if total_docs > 0:
        try:
            from src.api.app import _get_state
            kb_registry = _get_state().get("kb_registry")
            if kb_registry:
                await kb_registry.update_counts(effective_kb_id, total_docs, total_chunks)
        except Exception as _count_err:
            logger.warning("KB count update failed: %s", _count_err)

    if await is_cancelled(job_id):
        status = "cancelled"
    elif total_docs > 0:
        status = "completed"
    else:
        status = "failed"
    await update_job(
        job_id,
        status=status,
        processed=total_docs,
        chunks=total_chunks,
        errors=all_errors,
    )

    # Clean up temp directory (keep JSONL and permanent uploads)
    if save_dir:
        import shutil as _shutil
        _shutil.rmtree(save_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# POST /api/v1/knowledge/file-upload-ingest
# ---------------------------------------------------------------------------
@knowledge_router.post("/file-upload-ingest")
async def upload_and_ingest(
    file: UploadFile | None = File(default=None),
    files: list[UploadFile] | None = File(default=None),
    kb_id: str = Form(default=""),
    kb_name: str | None = Form(default=None),
    enable_vision: str = Form(default="false"),
    create_new_kb: str = Form(default="false"),
    tier: str | None = Form(default=None),
    organization_id: str | None = Form(default=None),
):
    """Upload and ingest files (returns job ID, processes in background)."""
    import os

    from src.api.app import _get_state

    state = _get_state()
    store = state.get("qdrant_store")
    embedder = state.get("embedder")

    if not store or not embedder:
        return {"success": False, "error": "Ingestion services not initialized"}

    # Determine effective kb_id
    effective_kb_id = kb_id or kb_name or "knowledge"

    # Collect uploaded files
    upload_files: list = []
    if file is not None:
        upload_files.append(file)
    if files is not None:
        if isinstance(files, list):
            upload_files.extend(files)
        else:
            upload_files.append(files)

    if not upload_files:
        return {"success": False, "error": "No files provided"}

    from src.pipeline.ingestion import IngestionPipeline

    # Ensure Qdrant collection exists before ingestion
    collections = state.get("qdrant_collections")
    if collections:
        try:
            await collections.ensure_collection(effective_kb_id)
        except Exception as _coll_err:
            # Fallback: create via REST API if SDK version mismatch
            logger.warning("ensure_collection via SDK failed: %s, trying REST", _coll_err)
            import httpx as _httpx
            qdrant_url = os.getenv("QDRANT_URL", "http://localhost:6333")
            coll_name = collections.get_collection_name(effective_kb_id)
            async with _httpx.AsyncClient() as _client:
                resp = await _client.put(
                    f"{qdrant_url}/collections/{coll_name}",
                    json={
                        "vectors": {"bge_dense": {"size": 1024, "distance": "Cosine"}},
                        "sparse_vectors": {"bge_sparse": {}},
                    },
                    timeout=30,
                )
                if resp.status_code in (200, 409):
                    logger.info("Collection %s created via REST API", coll_name)
                else:
                    logger.error("Collection creation failed: %s", resp.text)

    # Register KB in database if not exists
    kb_registry = state.get("kb_registry")
    if kb_registry:
        try:
            existing = await kb_registry.get_kb(effective_kb_id)
            if not existing:
                await kb_registry.create_kb({
                    "id": effective_kb_id,
                    "name": kb_name or effective_kb_id,
                    "tier": tier or "global",
                    "status": "active",
                })
        except Exception as e:
            logger.warning("KB registry auto-create failed: %s", e)

    # Sparse embedder for hybrid search (same pattern as ingest.py)
    from src.api.routes.ingest import _OnnxSparseEmbedder
    sparse_embedder = _OnnxSparseEmbedder(embedder)

    pipeline = IngestionPipeline(
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

    # Save uploads to disk before responding (UploadFile closes after response)
    import tempfile as _tmpmod
    save_dir = _tmpmod.mkdtemp(prefix="ingest_")
    file_paths: list[tuple[str, str]] = []  # (original_name, saved_path)
    for uf in upload_files:
        fname = getattr(uf, "filename", None) or "uploaded_file"
        suffix = os.path.splitext(fname)[1]
        tmp_fd, tmp_path = _tmpmod.mkstemp(suffix=suffix, dir=save_dir)
        content = await uf.read()
        os.write(tmp_fd, content)
        os.close(tmp_fd)
        file_paths.append((fname, tmp_path))

    # Create job and process in background (keep reference to avoid GC)
    job_id = await create_job(effective_kb_id, len(file_paths))
    task = asyncio.create_task(_process_files(job_id, file_paths, pipeline, effective_kb_id, save_dir))

    def _task_done_callback(t: asyncio.Task) -> None:
        if t.cancelled():
            logger.warning("Background ingest task %s was cancelled", job_id)
        elif t.exception():
            logger.error("Background ingest task %s failed: %s", job_id, t.exception())
    task.add_done_callback(_task_done_callback)
    # Store reference on the app state to prevent garbage collection
    bg_tasks: set = state.setdefault("_background_tasks", set())
    bg_tasks.add(task)
    task.add_done_callback(bg_tasks.discard)

    return {
        "success": True,
        "job_id": job_id,
        "kb_id": effective_kb_id,
        "message": f"Ingestion started for {len(upload_files)} file(s). Poll /api/v1/jobs/{job_id} for status.",
    }


# ============================================================================
# POST /api/v1/knowledge/reingest-from-jsonl
# ============================================================================

@knowledge_router.post("/reingest-from-jsonl")
async def reingest_from_jsonl(
    kb_id: str = Form(...),
    jsonl_path: str | None = Form(default=None),
):
    """Re-run Stage 2 (chunk/embed/store) from an existing JSONL checkpoint.

    Skips parsing/OCR entirely. Useful when Stage 1 succeeded but Stage 2 failed.
    """
    from src.pipeline.jsonl_checkpoint import get_jsonl_path, JsonlCheckpointReader
    from src.pipeline.ingestion import IngestionPipeline

    state = _get_state()
    store = state.get("qdrant_store")
    embedder = state.get("embedder")
    if not store or not embedder:
        raise HTTPException(status_code=503, detail="Ingestion services not initialized")

    path = jsonl_path or str(get_jsonl_path(kb_id))
    reader = JsonlCheckpointReader(path)
    record_count = reader.count()
    if record_count == 0:
        raise HTTPException(status_code=404, detail=f"No records in JSONL: {path}")

    from src.api.routes.ingest import _OnnxSparseEmbedder
    sparse_embedder = _OnnxSparseEmbedder(embedder)
    pipeline = IngestionPipeline(
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

    job_id = await create_job(kb_id, record_count)
    task = asyncio.create_task(_stage2_ingest_from_jsonl(job_id, path, pipeline, kb_id))

    async def _finalize(t: asyncio.Task) -> None:
        try:
            total_docs, total_chunks, errors = t.result()
            st = "completed" if total_docs > 0 else "failed"
            await update_job(job_id, status=st, processed=total_docs, chunks=total_chunks, errors=errors)
        except Exception as e:
            await update_job(job_id, status="failed", errors=[str(e)])

    task.add_done_callback(lambda t: asyncio.ensure_future(_finalize(t)))
    bg_tasks: set = state.setdefault("_background_tasks", set())
    bg_tasks.add(task)
    task.add_done_callback(bg_tasks.discard)

    return {
        "success": True,
        "job_id": job_id,
        "kb_id": kb_id,
        "jsonl_path": path,
        "records": record_count,
        "message": f"Re-ingestion started from JSONL ({record_count} records). Poll /api/v1/jobs/{job_id}.",
    }


# ============================================================================
# /rag-query alias (dashboard compatibility)
# ============================================================================

@rag_query_router.post("/api/v1/rag-query")
async def rag_query_alias(body: dict[str, Any]):
    """Alias for /api/v1/knowledge/ask - dashboard compatibility."""
    return await rag_query(body)
