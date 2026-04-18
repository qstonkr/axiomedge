"""Ingestion API endpoints."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import tempfile
from typing import Annotated, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from src.api.state import AppState

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from src.api.app import _get_state
from src.api.routes.metrics import inc as metrics_inc

logger = logging.getLogger(__name__)


class _OnnxSparseEmbedder:
    """Adapter that wraps OnnxBgeEmbeddingProvider to satisfy ISparseEmbedder."""

    def __init__(self, onnx_provider: Any) -> None:
        self._provider = onnx_provider

    async def embed_sparse(self, texts: list[str]) -> list[dict[str, Any]]:
        output = await asyncio.to_thread(
            self._provider.encode, texts, False, True, False
        )
        return output.get("lexical_weights", [{} for _ in texts])


router = APIRouter(prefix="/api/v1/knowledge", tags=["Ingestion"])


class IngestRequest(BaseModel):
    kb_id: str = Field(default="knowledge", max_length=100)
    source_dir: str = Field(..., max_length=500)
    force_rebuild: bool = False


class IngestResponse(BaseModel):
    success: bool
    kb_id: str
    documents_processed: int = 0
    chunks_created: int = 0
    errors: list[str] = []


def _tally_results(
    results: list, file_names: list[str],
) -> tuple[int, int, list[str]]:
    """Tally ingestion results, returning (docs_processed, chunks_created, errors)."""
    documents_processed = 0
    chunks_created = 0
    errors: list[str] = []
    for i, r in enumerate(results):
        if isinstance(r, Exception):
            errors.append(f"{file_names[i]}: {r}")
            metrics_inc("errors")
        else:
            chunks, _ = r
            if chunks > 0:
                documents_processed += 1
                chunks_created += chunks
    return documents_processed, chunks_created, errors


def _collect_files(source_dir: str) -> list[tuple[str, str]]:
    """Collect all file paths and names from source directory."""
    result = []
    for root, _dirs, files in os.walk(source_dir):
        for fname in files:
            result.append((os.path.join(root, fname), fname))
    return result


async def _update_kb_counts(state: AppState, kb_id: str, docs: int, chunks: int) -> None:
    """Update KB registry counts if documents were processed."""
    if docs <= 0:
        return
    kb_registry = state.get("kb_registry")
    if not kb_registry:
        return
    try:
        await kb_registry.update_counts(kb_id, docs, chunks)
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as _e:
        logger.warning("KB count update failed: %s", _e)


@router.post("/ingest", response_model=IngestResponse, responses={503: {"description": "Ingestion services not initialized"}, 400: {"description": "Directory not found"}, 500: {"description": "Ingestion failed"}})  # noqa: E501
async def ingest_directory(request: IngestRequest) -> tuple:
    """Ingest documents from a directory."""
    state = _get_state()
    store = state.get("qdrant_store")
    embedder = state.get("embedder")
    if not store or not embedder:
        raise HTTPException(status_code=503, detail="Ingestion services not initialized")

    # Path traversal prevention: resolve symlinks and validate against allowed base paths
    _extra = os.environ.get("INGEST_ALLOWED_PATHS", "")
    allowed_bases = [
        os.path.realpath("/tmp"),
        os.path.realpath(tempfile.gettempdir()),
        os.path.realpath(os.path.expanduser("~/uploads")),
    ] + [os.path.realpath(p) for p in _extra.split(":") if p.strip()]
    real_path = os.path.realpath(request.source_dir)
    if not any(real_path.startswith(base) for base in allowed_bases):
        raise HTTPException(status_code=400, detail="Source directory is outside allowed paths")
    if not os.path.isdir(real_path):
        raise HTTPException(status_code=400, detail="Directory not found")

    collections = state.get("qdrant_collections")
    if collections:
        await collections.ensure_collection(request.kb_id)

    try:
        from src.core.models import RawDocument
        from src.pipelines.document_parser import parse_file_enhanced
        from src.pipelines.ingestion import IngestionPipeline

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

        semaphore = asyncio.Semaphore(4)

        async def _ingest_one(fpath: str, fname: str) -> tuple[int, str | None]:
            async with semaphore:
                parse_result = await asyncio.to_thread(parse_file_enhanced, fpath)
                text = parse_result.full_text if hasattr(parse_result, 'full_text') else str(parse_result)
                if not text:
                    return 0, None
                raw = RawDocument(
                    doc_id=RawDocument.sha256(fpath),
                    title=fname,
                    content=text,
                    source_uri=fpath,
                    metadata={"force_rebuild": request.force_rebuild},
                )
                result = await pipeline.ingest(raw, collection_name=request.kb_id)
                return result.chunks_stored, None

        file_paths = _collect_files(request.source_dir)
        tasks = [_ingest_one(fp, fn) for fp, fn in file_paths]
        file_names = [fn for _, fn in file_paths]

        results = await asyncio.gather(*tasks, return_exceptions=True)
        documents_processed, chunks_created, errors = _tally_results(results, file_names)

        metrics_inc("ingest_documents", documents_processed)
        metrics_inc("ingest_chunks", chunks_created)

        await _update_kb_counts(state, request.kb_id, documents_processed, chunks_created)

        return IngestResponse(
            success=True,
            kb_id=request.kb_id,
            documents_processed=documents_processed,
            chunks_created=chunks_created,
            errors=errors,
        )
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
        logger.error("Ingestion failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/upload", responses={503: {"description": "Ingestion services not initialized"}, 500: {"description": "Upload ingestion failed"}})  # noqa: E501
async def upload_file(
    file: Annotated[UploadFile, File()],
    kb_id: Annotated[str, Form()] = "knowledge",
) -> dict:
    """Upload and ingest a single file."""
    state = _get_state()
    store = state.get("qdrant_store")
    embedder = state.get("embedder")

    # Ensure collection exists
    collections = state.get("qdrant_collections")
    if collections:
        await collections.ensure_collection(kb_id)

    if not store or not embedder:
        raise HTTPException(status_code=503, detail="Ingestion services not initialized")

    # Save uploaded file to temp (use asyncio.to_thread for sync I/O)
    # Sanitize filename to prevent XSS and path traversal
    raw_name = file.filename or "uploaded_file"
    safe_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', raw_name)  # strip dangerous chars
    suffix = os.path.splitext(safe_name)[1]
    content = await file.read()
    tmp = await asyncio.to_thread(tempfile.NamedTemporaryFile, delete=False, suffix=suffix)
    try:
        await asyncio.to_thread(tmp.write, content)
        tmp_path = tmp.name
    finally:
        await asyncio.to_thread(tmp.close)

    try:
        from src.core.models import RawDocument
        from src.pipelines.document_parser import parse_file_enhanced
        from src.pipelines.ingestion import IngestionPipeline

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

        parse_result = await asyncio.to_thread(parse_file_enhanced, tmp_path)
        text = parse_result.full_text if hasattr(parse_result, 'full_text') else str(parse_result)
        if not text:
            raise ValueError(f"Could not parse file: {file.filename}")

        doc_name = file.filename or "uploaded_file"
        raw = RawDocument(
            doc_id=RawDocument.sha256(tmp_path),
            title=doc_name,
            content=text,
            source_uri=doc_name,
        )
        ingest_result = await pipeline.ingest(raw, collection_name=kb_id)

        # Update KB registry counts
        if ingest_result.chunks_stored > 0:
            kb_registry = state.get("kb_registry")
            if kb_registry:
                try:
                    await kb_registry.update_counts(kb_id, 1, ingest_result.chunks_stored)
                except (OSError, ValueError, RuntimeError) as _e:
                    logger.warning("KB count update failed: %s", _e)

        return {
            "success": True,
            "filename": file.filename,
            "kb_id": kb_id,
            "chunks_created": ingest_result.chunks_stored,
        }
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
        logger.error("Upload ingestion failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        os.unlink(tmp_path)
