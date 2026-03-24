"""Ingestion API endpoints."""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from src.api.app import _get_state

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


@router.post("/ingest", response_model=IngestResponse)
async def ingest_directory(request: IngestRequest):
    """Ingest documents from a directory."""
    state = _get_state()
    store = state.get("qdrant_store")
    embedder = state.get("embedder")
    if not store or not embedder:
        raise HTTPException(status_code=503, detail="Ingestion services not initialized")

    if not os.path.isdir(request.source_dir):
        raise HTTPException(status_code=400, detail=f"Directory not found: {request.source_dir}")

    try:
        from src.domain.models import RawDocument
        from src.pipeline.document_parser import parse_file
        from src.pipeline.ingestion import IngestionPipeline

        sparse_embedder = _OnnxSparseEmbedder(embedder)
        pipeline = IngestionPipeline(
            embedder=embedder,
            sparse_embedder=sparse_embedder,
            vector_store=store,
            graph_store=state.get("graph_repo"),
        )

        documents_processed = 0
        chunks_created = 0
        errors: list[str] = []

        for root, _dirs, files in os.walk(request.source_dir):
            for fname in files:
                fpath = os.path.join(root, fname)
                try:
                    text = parse_file(fpath)
                    if not text:
                        continue
                    raw = RawDocument(
                        doc_id=RawDocument.sha256(fpath),
                        title=fname,
                        content=text,
                        source_uri=fpath,
                    )
                    result = await pipeline.ingest(raw, collection_name=request.kb_id)
                    documents_processed += 1
                    chunks_created += result.chunks_stored
                except Exception as file_err:
                    errors.append(f"{fname}: {file_err}")

        return IngestResponse(
            success=True,
            kb_id=request.kb_id,
            documents_processed=documents_processed,
            chunks_created=chunks_created,
            errors=errors,
        )
    except Exception as e:
        logger.error("Ingestion failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    kb_id: str = Form(default="knowledge"),
):
    """Upload and ingest a single file."""
    state = _get_state()
    store = state.get("qdrant_store")
    embedder = state.get("embedder")
    if not store or not embedder:
        raise HTTPException(status_code=503, detail="Ingestion services not initialized")

    # Save uploaded file to temp
    suffix = os.path.splitext(file.filename or "")[1]
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name

    try:
        from src.domain.models import RawDocument
        from src.pipeline.document_parser import parse_file
        from src.pipeline.ingestion import IngestionPipeline

        sparse_embedder = _OnnxSparseEmbedder(embedder)
        pipeline = IngestionPipeline(
            embedder=embedder,
            sparse_embedder=sparse_embedder,
            vector_store=store,
            graph_store=state.get("graph_repo"),
        )

        text = parse_file(tmp_path)
        if not text:
            raise ValueError(f"Could not parse file: {file.filename}")

        doc_name = file.filename or "uploaded_file"
        raw = RawDocument(
            doc_id=RawDocument.sha256(tmp_path),
            title=doc_name,
            content=text,
            source_uri=doc_name,
        )
        result = await pipeline.ingest(raw, collection_name=kb_id)
        return {
            "success": True,
            "filename": file.filename,
            "kb_id": kb_id,
            "chunks_created": result.chunks_stored,
        }
    except Exception as e:
        logger.error("Upload ingestion failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        os.unlink(tmp_path)
