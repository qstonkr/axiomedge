"""CLI: Ingest documents into knowledge base with incremental support.

Supports incremental ingestion: skips already-ingested documents by checking
content_hash against Qdrant metadata. Only new/changed documents are processed.

Usage:
    python -m cli.ingest --source ./docs/ --kb-id my-kb
    python -m cli.ingest --file report.pdf --kb-id my-kb
    python -m cli.ingest --crawl-dir ./crawl_results/ --kb-id confluence-kb
    python -m cli.ingest --source ./docs/ --kb-id my-kb --force  # Force re-ingest all
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import logging
import os
import sys
import traceback as _tb
import uuid as _uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv()
from src.core.logging import configure_logging  # noqa: E402

configure_logging(service="axiomedge-cli-ingest")
logger = logging.getLogger(__name__)


async def _init_run_tracking(
    kb_id: str, source_type: str, source_name: str
) -> tuple[str | None, Any, Any]:
    """Postgres 가용 시 run_id 발급 + failure_repo 준비.

    Returns:
        (run_id, run_repo, failure_repo) — DATABASE_URL 미설정이면 (None, None, None).
        호출자는 run_id 가 None 이면 run-tracking 건너뛰고 ingest 만 수행.
    """
    try:
        from src.stores.postgres.session import get_knowledge_session_maker
        from src.stores.postgres.repositories.ingestion_run import IngestionRunRepository
        from src.stores.postgres.repositories.ingestion_failures import (
            IngestionFailureRepository,
        )
    except ImportError as e:
        logger.debug("Run tracking modules unavailable: %s", e)
        return None, None, None

    session_maker = get_knowledge_session_maker()
    if session_maker is None:
        logger.info(
            "DATABASE_URL not set — skipping ingestion run tracking"
        )
        return None, None, None

    run_id = str(_uuid.uuid4())
    run_repo = IngestionRunRepository(session_maker)
    failure_repo = IngestionFailureRepository(session_maker)
    try:
        await run_repo.create({
            "id": run_id,
            "kb_id": kb_id,
            "source_type": source_type,
            "source_name": source_name[:255],
            "started_at": datetime.now(timezone.utc),
            "status": "running",
        })
    except (RuntimeError, OSError, ValueError, TypeError) as e:
        logger.warning("Failed to create ingestion run: %s", e)
        return None, None, None
    return run_id, run_repo, failure_repo


async def _finalize_run(
    run_id: str | None,
    run_repo: Any,
    *,
    status: str,
    docs_fetched: int = 0,
    docs_ingested: int = 0,
    chunks_stored: int = 0,
    errors: list[str] | None = None,
) -> None:
    """Run row complete 처리 — best-effort."""
    if run_id is None or run_repo is None:
        return
    try:
        await run_repo.complete(run_id, {
            "status": status,
            "documents_fetched": docs_fetched,
            "documents_ingested": docs_ingested,
            "chunks_stored": chunks_stored,
            "errors": errors or [],
        })
    except (RuntimeError, OSError, ValueError, TypeError) as e:
        logger.warning("Failed to complete ingestion run %s: %s", run_id, e)


async def _persist_failure(
    failure_repo: Any,
    *,
    run_id: str | None,
    kb_id: str,
    doc_id: str,
    source_uri: str | None,
    stage: str,
    reason: str,
    traceback: str | None = None,
) -> None:
    """failure_repo.record 의 graceful wrapper — None 일 때 no-op."""
    if failure_repo is None or run_id is None:
        return
    try:
        await failure_repo.record(
            run_id=run_id, kb_id=kb_id, doc_id=doc_id,
            source_uri=source_uri, stage=stage, reason=reason,
            traceback=traceback,
        )
    except (RuntimeError, OSError, ValueError, TypeError) as e:
        logger.warning("Failed to persist failure record: %s", e)


class OnnxSparseEmbedder:
    """Adapter that wraps OnnxBgeEmbeddingProvider to satisfy ISparseEmbedder."""

    def __init__(self, onnx_provider: Any) -> None:
        self._provider = onnx_provider

    async def embed_sparse(self, texts: list[str]) -> list[dict[str, Any]]:
        output = await asyncio.to_thread(
            self._provider.encode, texts, False, True, False
        )
        return output.get("lexical_weights", [{} for _ in texts])


async def _init_services() -> tuple:
    """Initialize required services."""
    from src.config import get_settings
    from src.nlp.embedding.onnx_provider import OnnxBgeEmbeddingProvider
    from src.stores.qdrant.client import QdrantConfig, QdrantClientProvider
    from src.stores.qdrant.collections import QdrantCollectionManager
    from src.stores.qdrant.store import QdrantStoreOperations

    settings = get_settings()

    # Embedding
    model_path = settings.embedding.onnx_model_path or os.getenv("KNOWLEDGE_BGE_ONNX_MODEL_PATH", "")
    embedder = OnnxBgeEmbeddingProvider(model_path=model_path)
    if not embedder.is_ready():
        logger.error("BGE-M3 ONNX model not ready. Set KNOWLEDGE_BGE_ONNX_MODEL_PATH")
        sys.exit(1)

    # Qdrant
    config = QdrantConfig.from_env()
    provider = QdrantClientProvider(config)
    await provider.ensure_client()
    cm = QdrantCollectionManager(provider)
    store = QdrantStoreOperations(provider, cm)

    # Graph (optional)
    graph_repo = None
    if settings.neo4j.enabled:
        try:
            from src.stores.neo4j.client import Neo4jClient
            from src.stores.neo4j.errors import NEO4J_FAILURE
            from src.stores.neo4j.repository import Neo4jGraphRepository

            neo4j = Neo4jClient(uri=settings.neo4j.uri)
            await neo4j.connect()
            graph_repo = Neo4jGraphRepository(neo4j)
        except NEO4J_FAILURE as e:
            logger.warning("Neo4j not available: %s", e)

    sparse_embedder = OnnxSparseEmbedder(embedder)
    return embedder, sparse_embedder, store, cm, graph_repo, provider


async def _get_ingested_hashes(kb_id: str, _provider) -> set[str]:
    """Get content hashes of already-ingested documents from Qdrant."""
    import httpx

    from src.config import get_settings
    qdrant_url = get_settings().qdrant.url
    collection = f"kb_{kb_id.replace('-', '_')}"
    hashes: set[str] = set()

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Check if collection exists
            resp = await client.get(f"{qdrant_url}/collections/{collection}")
            if resp.status_code != 200:
                return hashes

            # Scroll through all points to collect source_uri hashes
            offset = None
            while True:
                body: dict[str, Any] = {
                    "limit": 100,
                    "with_payload": {"include": ["content_hash", "source_uri"]},
                    "with_vector": False,
                }
                if offset:
                    body["offset"] = offset

                resp = await client.post(
                    f"{qdrant_url}/collections/{collection}/points/scroll",
                    json=body,
                )
                if resp.status_code != 200:
                    break

                data = resp.json().get("result", {})
                points = data.get("points", [])
                if not points:
                    break

                hashes.update(
                    h for pt in points
                    if (h := pt.get("payload", {}).get("content_hash", ""))
                )

                offset = data.get("next_page_offset")
                if not offset:
                    break

    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
        logger.warning("Could not fetch ingested hashes: %s", e)

    return hashes


async def _should_skip_file(
    fpath: str, force: bool, ingested_hashes: set[str],
) -> bool:
    """Check if file was already ingested (by content hash)."""
    if force or not ingested_hashes:
        return False
    _content = await asyncio.to_thread(Path(fpath).read_bytes)
    file_hash = hashlib.sha256(_content).hexdigest()[:32]
    return file_hash in ingested_hashes


async def _ingest_single_file(
    fpath: str,
    fname: str,
    kb_id: str,
    pipeline: Any,
    *,
    run_id: str | None = None,
    failure_repo: Any = None,
) -> int:
    """Parse and ingest a single file. Returns chunks_stored or 0 on skip.

    실패 시 (caller exception 또는 result.success=False) failure_repo 가
    전달돼 있으면 영속화한다.
    """
    from src.core.models import RawDocument
    from src.pipelines.document_parser import parse_file_enhanced

    doc_id = RawDocument.sha256(fpath)
    try:
        parse = parse_file_enhanced(fpath)
        text = parse.full_text if hasattr(parse, 'full_text') else str(parse)
        if not text:
            return 0
        raw = RawDocument(
            doc_id=doc_id, title=fname, content=text, source_uri=fpath,
        )
        result = await pipeline.ingest(raw, collection_name=kb_id)
        if not result.success:
            await _persist_failure(
                failure_repo, run_id=run_id, kb_id=kb_id, doc_id=doc_id,
                source_uri=fpath, stage=result.stage or "unknown",
                reason=result.reason or "(no reason)",
                traceback=result.traceback,
            )
        return result.chunks_stored
    except Exception as exc:  # noqa: BLE001
        # parse 단계 또는 pipeline 외부에서 raise 된 caller-level 예외
        await _persist_failure(
            failure_repo, run_id=run_id, kb_id=kb_id, doc_id=doc_id,
            source_uri=fpath, stage="caller", reason=str(exc),
            traceback=_tb.format_exc()[-4096:],
        )
        logger.warning("Caller-level ingest failure for %s: %s", fpath, exc)
        return 0


async def ingest_directory(source_dir: str, kb_id: str, force: bool = False) -> None:
    embedder, sparse_embedder, store, _, graph_repo, provider = await _init_services()

    from src.pipelines.ingestion import IngestionPipeline

    pipeline = IngestionPipeline(
        embedder=embedder, sparse_embedder=sparse_embedder,
        vector_store=store, graph_store=graph_repo,
    )

    # Run tracking — DATABASE_URL 미설정이면 graceful no-op
    run_id, run_repo, failure_repo = await _init_run_tracking(
        kb_id, source_type="file", source_name=source_dir,
    )
    if run_id:
        logger.info("Ingestion run started: %s", run_id)

    # Get already-ingested hashes for incremental mode
    ingested_hashes: set[str] = set()
    if not force:
        ingested_hashes = await _get_ingested_hashes(kb_id, provider)
        if ingested_hashes:
            logger.info("Incremental mode: %d documents already ingested", len(ingested_hashes))

    # PR-4 (C) — Settings 기반 파일 단위 병렬화. API의 Sem(4) 패턴을 동일하게 적용.
    parallel = await _resolve_file_parallel(kb_id=kb_id)
    semaphore = asyncio.Semaphore(parallel)
    logger.info("Parallel ingest workers: %d", parallel)

    total_docs = 0
    total_chunks = 0
    total_fetched = 0
    skipped = 0
    run_status = "completed"

    # 1) 파일 목록 수집 (동기 부분 — 빠름)
    file_paths: list[tuple[str, str]] = []
    for root, _dirs, files in os.walk(source_dir):
        for fname in sorted(files):
            file_paths.append((os.path.join(root, fname), fname))
    total_fetched = len(file_paths)

    # P0-5 (race fix): nonlocal counter 대신 (chunks, was_skipped) 튜플 반환
    # → asyncio.gather 결과를 단일 thread 에서 sequential 합산. yield point
    # 사이의 race window 제거.
    async def _one(fpath: str, fname: str) -> tuple[int, bool]:
        """파일 1개를 sem 보호 하에 ingest. (chunks_stored, was_skipped) 반환."""
        async with semaphore:
            try:
                if await _should_skip_file(fpath, force, ingested_hashes):
                    return 0, True
                chunks = await _ingest_single_file(
                    fpath, fname, kb_id, pipeline,
                    run_id=run_id, failure_repo=failure_repo,
                )
                return chunks, False
            except Exception:  # noqa: BLE001 — _ingest_single_file 이 swallow 하지만 안전망
                return 0, False

    try:
        outcomes = await asyncio.gather(
            *[_one(fp, fn) for fp, fn in file_paths],
            return_exceptions=False,
        )
        for chunks_stored, was_skipped in outcomes:
            if was_skipped:
                skipped += 1
            elif chunks_stored:
                total_docs += 1
                total_chunks += chunks_stored
    except Exception:
        run_status = "failed"
        raise
    finally:
        await _finalize_run(
            run_id, run_repo, status=run_status,
            docs_fetched=total_fetched,
            docs_ingested=total_docs,
            chunks_stored=total_chunks,
        )

    mode = "FORCE" if force else "INCREMENTAL"
    logger.info(
        "[%s] Ingestion complete (run=%s): %d docs ingested, %d chunks, %d skipped",
        mode, run_id or "-", total_docs, total_chunks, skipped,
    )
    await provider.close()


async def _resolve_file_parallel(kb_id: str | None = None) -> int:
    """Resolve effective parallelism — feature-flag aware.

    Decision precedence:
      1. ``ENABLE_INGESTION_FILE_PARALLEL`` feature flag (kb_id > _global).
         If disabled → force serial (returns 1, regardless of settings).
      2. Settings fallback (``PipelineSettings.file_parallel``, default 4).
      3. Hard fallback 1 on any error.
    """
    # 1. Feature flag check — kill switch for parallel ingest
    try:
        from src.core.feature_flags import get_flag
        enabled = await get_flag(
            "ENABLE_INGESTION_FILE_PARALLEL",
            kb_id=kb_id,
            default=True,  # backward-compat: parallel by default
        )
        if not enabled:
            logger.info("Parallel ingest disabled via feature flag")
            return 1
    except (ImportError, AttributeError, RuntimeError) as e:
        logger.debug("Feature flag check skipped: %s", e)

    # 2. Settings
    try:
        from src.config import get_settings
        return max(1, int(get_settings().pipeline.file_parallel))
    except (ImportError, AttributeError, ValueError, RuntimeError):
        return 1


async def ingest_file(file_path: str, kb_id: str) -> None:
    embedder, sparse_embedder, store, _, graph_repo, provider = await _init_services()

    from src.core.models import RawDocument
    from src.pipelines.document_parser import parse_file_enhanced
    from src.pipelines.ingestion import IngestionPipeline

    pipeline = IngestionPipeline(
        embedder=embedder, sparse_embedder=sparse_embedder,
        vector_store=store, graph_store=graph_repo,
    )

    result = parse_file_enhanced(file_path)
    text = result.full_text if hasattr(result, 'full_text') else str(result)
    if not text:
        logger.error("Could not parse file: %s", file_path)
        await provider.close()
        return

    fname = os.path.basename(file_path)
    raw = RawDocument(
        doc_id=RawDocument.sha256(file_path),
        title=fname,
        content=text,
        source_uri=file_path,
    )
    result = await pipeline.ingest(raw, collection_name=kb_id)

    logger.info("File ingestion complete: %d chunks stored", result.chunks_stored)
    await provider.close()


async def ingest_crawl(crawl_dir: str, kb_id: str, force: bool = False) -> None:
    embedder, sparse_embedder, store, _, graph_repo, provider = await _init_services()

    from src.connectors.crawl_result import CrawlResultConnector
    from src.pipelines.ingestion import IngestionPipeline

    connector = CrawlResultConnector(default_output_dir=crawl_dir)
    result = await connector.fetch({"entry_point": crawl_dir}, force=force)

    if not result.documents:
        logger.info("No documents found in %s", crawl_dir)
        await provider.close()
        return

    pipeline = IngestionPipeline(
        embedder=embedder, sparse_embedder=sparse_embedder,
        vector_store=store, graph_store=graph_repo,
    )

    # Run tracking — DATABASE_URL 미설정이면 graceful no-op
    run_id, run_repo, failure_repo = await _init_run_tracking(
        kb_id, source_type="crawl", source_name=crawl_dir,
    )
    if run_id:
        logger.info("Ingestion run started: %s", run_id)

    # Get already-ingested hashes for incremental mode
    ingested_hashes: set[str] = set()
    if not force:
        ingested_hashes = await _get_ingested_hashes(kb_id, provider)

    total_chunks = 0
    skipped = 0
    ingested = 0
    run_status = "completed"

    # PR-4 (C) — 문서 단위 동시 처리. crawl 결과는 메모리상 list 이므로 단순 gather.
    parallel = await _resolve_file_parallel(kb_id=kb_id)
    semaphore = asyncio.Semaphore(parallel)
    logger.info("Parallel crawl ingest workers: %d", parallel)

    async def _one_doc(doc) -> tuple[int, bool]:
        """문서 1개 ingest. 반환: (chunks_stored, was_skipped)."""
        if not force and ingested_hashes:
            doc_hash = hashlib.sha256(
                doc.content.lower().strip().encode()
            ).hexdigest()[:32]
            if doc_hash in ingested_hashes:
                return 0, True
        async with semaphore:
            try:
                r = await pipeline.ingest(doc, collection_name=kb_id)
                if not r.success:
                    await _persist_failure(
                        failure_repo, run_id=run_id, kb_id=kb_id,
                        doc_id=doc.doc_id, source_uri=doc.source_uri,
                        stage=r.stage or "unknown",
                        reason=r.reason or "(no reason)",
                        traceback=r.traceback,
                    )
                return r.chunks_stored, False
            except Exception as exc:  # noqa: BLE001
                await _persist_failure(
                    failure_repo, run_id=run_id, kb_id=kb_id,
                    doc_id=doc.doc_id, source_uri=doc.source_uri,
                    stage="caller", reason=str(exc),
                    traceback=_tb.format_exc()[-4096:],
                )
                logger.warning(
                    "Caller-level crawl ingest failure for %s: %s",
                    doc.doc_id, exc,
                )
                return 0, False

    try:
        outcomes = await asyncio.gather(
            *[_one_doc(d) for d in result.documents],
            return_exceptions=False,
        )
        for chunks, was_skipped in outcomes:
            if was_skipped:
                skipped += 1
            else:
                ingested += 1
                total_chunks += chunks
    except Exception:
        run_status = "failed"
        raise
    finally:
        await _finalize_run(
            run_id, run_repo, status=run_status,
            docs_fetched=len(result.documents),
            docs_ingested=ingested,
            chunks_stored=total_chunks,
        )

    mode = "FORCE" if force else "INCREMENTAL"
    logger.info(
        "[%s] Crawl ingestion complete (run=%s): %d/%d docs ingested, %d chunks, %d skipped",
        mode, run_id or "-", ingested, len(result.documents), total_chunks, skipped,
    )
    await provider.close()


async def retry_failed(target_run_id: str, kb_id: str | None = None) -> None:
    """failures 테이블의 해당 run_id 문서를 새 run 으로 재시도 (PR-5 B).

    - run_id 의 failures 에서 source_uri 가 있는 항목만 file 재처리
    - 파일이 없거나 접근 불가하면 skip + warning
    - 새 run 이 발급되어 새 failures 추적
    """
    try:
        from src.stores.postgres.session import get_knowledge_session_maker
        from src.stores.postgres.repositories.ingestion_failures import (
            IngestionFailureRepository,
        )
    except ImportError:
        logger.error("Postgres modules unavailable — cannot --retry-failed")
        return

    session_maker = get_knowledge_session_maker()
    if session_maker is None:
        logger.error("DATABASE_URL 미설정 — --retry-failed 불가")
        return

    repo = IngestionFailureRepository(session_maker)
    failures = await repo.list_by_run(target_run_id)
    if not failures:
        logger.info("No failures found for run %s — nothing to retry", target_run_id)
        return

    # kb_id 미지정 시 첫 실패의 kb_id 사용
    if kb_id is None:
        kb_id = failures[0].get("kb_id", "")
    if not kb_id:
        logger.error("Could not determine kb_id from run %s", target_run_id)
        return

    embedder, sparse_embedder, store, _, graph_repo, provider = await _init_services()
    from src.pipelines.ingestion import IngestionPipeline
    pipeline = IngestionPipeline(
        embedder=embedder, sparse_embedder=sparse_embedder,
        vector_store=store, graph_store=graph_repo,
    )

    new_run_id, run_repo, failure_repo = await _init_run_tracking(
        kb_id, source_type="retry",
        source_name=f"retry-of:{target_run_id}",
    )
    logger.info(
        "Retrying %d failures from run=%s (new run=%s)",
        len(failures), target_run_id, new_run_id or "-",
    )

    parallel = await _resolve_file_parallel(kb_id=kb_id)
    semaphore = asyncio.Semaphore(parallel)
    total_chunks = 0
    succeeded_doc_ids: list[str] = []
    succeeded_lock = asyncio.Lock()
    run_status = "completed"

    async def _retry_one(f: dict) -> int:
        nonlocal total_chunks
        src = f.get("source_uri")
        doc_id = f.get("doc_id", "")
        if not src or not os.path.isfile(src):
            logger.warning(
                "Skip retry — source missing for doc_id=%s src=%s",
                doc_id, src,
            )
            return 0
        async with semaphore:
            chunks = await _ingest_single_file(
                src, os.path.basename(src), kb_id, pipeline,
                run_id=new_run_id, failure_repo=failure_repo,
            )
            if chunks > 0 and doc_id:
                async with succeeded_lock:
                    succeeded_doc_ids.append(doc_id)
            return chunks

    try:
        results = await asyncio.gather(
            *[_retry_one(f) for f in failures],
            return_exceptions=False,
        )
        total_chunks = sum(results)
    except Exception:
        run_status = "failed"
        raise
    finally:
        await _finalize_run(
            new_run_id, run_repo, status=run_status,
            docs_fetched=len(failures),
            docs_ingested=sum(1 for r in results if r > 0)
                if "results" in locals() else 0,
            chunks_stored=total_chunks,
        )
        # 성공한 doc_id 의 이전 run failures 정리
        if succeeded_doc_ids:
            cleared = await repo.delete_by_run_and_docs(
                target_run_id, succeeded_doc_ids,
            )
            logger.info(
                "Cleared %d resolved failure rows from old run %s",
                cleared, target_run_id,
            )

    logger.info(
        "Retry complete (run=%s): %d/%d retried, %d chunks",
        new_run_id or "-",
        len(succeeded_doc_ids), len(failures), total_chunks,
    )
    await provider.close()


@asynccontextmanager
async def _ocr_lifecycle():
    """OCR EC2 자동 기동 + 종료 — CLI ingest 의 모든 entry 가 통과.

    PADDLEOCR_INSTANCE_ID 미설정 시 graceful skip (start 가 None 반환). 가동
    실패 시 warning + text-only ingest 진행 (PDF 의 image OCR 는 누락되지만
    text-extractable 부분은 정상 ingest). 정상 가동 시 종료 시점에 stop 호출
    — GPU 비용 절약. stop 실패는 warning 만 (EC2 자체 boot script 의 shutdown
    -h now 가 fallback).
    """
    from src.services.ocr_lifecycle import start_ocr_instance, stop_ocr_instance

    instance_id = os.getenv("PADDLEOCR_INSTANCE_ID", "")
    started = False
    if instance_id:
        url = await start_ocr_instance()
        started = bool(url) and bool(instance_id)
        if started:
            logger.info("OCR EC2 가동 완료: %s", url)
        else:
            logger.warning("OCR EC2 가동 실패 — text-only ingest 진행")
    try:
        yield
    finally:
        if started:
            try:
                await stop_ocr_instance()
                logger.info("OCR EC2 stop 요청 완료")
            except (RuntimeError, OSError, ValueError) as e:
                logger.warning("OCR EC2 stop 실패: %s", e)


def main() -> None:
    parser = argparse.ArgumentParser(description="Knowledge Ingestion CLI")
    parser.add_argument("--source", help="Source directory to ingest")
    parser.add_argument("--file", help="Single file to ingest")
    parser.add_argument("--crawl-dir", help="Crawl results directory (JSON/JSONL)")
    parser.add_argument("--kb-id", default="knowledge", help="Knowledge base ID")
    parser.add_argument("--force", action="store_true", help="Force re-ingest all (skip incremental check)")
    parser.add_argument(
        "--retry-failed",
        metavar="RUN_ID",
        help="(PR-5 B) Retry failed documents from a previous run",
    )

    args = parser.parse_args()

    async def _run() -> None:
        # OCR EC2 wrap — PADDLEOCR_INSTANCE_ID 있으면 자동 start/stop, 없으면 no-op.
        async with _ocr_lifecycle():
            if args.retry_failed:
                kb = args.kb_id if args.kb_id != "knowledge" else None
                await retry_failed(args.retry_failed, kb_id=kb)
            elif args.source:
                await ingest_directory(args.source, args.kb_id, args.force)
            elif args.file:
                await ingest_file(args.file, args.kb_id)
            elif args.crawl_dir:
                await ingest_crawl(args.crawl_dir, args.kb_id, args.force)
            else:
                parser.print_help()

    asyncio.run(_run())


if __name__ == "__main__":
    main()
