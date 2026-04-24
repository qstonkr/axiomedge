"""arq task: re-extract a KB's docs against the current SchemaProfile.

Spec §6.1 (``schema_reextract.py``) + Q5 (on-demand re-extract).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

logger = logging.getLogger(__name__)


class JobRepoProto(Protocol):
    async def start(self, job_id: UUID, *, docs_total: int) -> None: ...
    async def progress(
        self, job_id: UUID, *, docs_processed: int, docs_failed: int,
    ) -> None: ...
    async def complete(
        self, job_id: UUID, *, status: str, error_message: str | None = None,
    ) -> None: ...


class DocIteratorProto(Protocol):
    def __call__(
        self, *, kb_id: str,
    ) -> AsyncIterator[dict[str, Any]]: ...


@dataclass
class ReextractDeps:
    job_repo: JobRepoProto
    extractor: Any
    schema_resolver: Any
    doc_iterator: Callable[..., AsyncIterator[dict[str, Any]]]


async def run_reextract(
    *,
    job_id: UUID,
    kb_id: str,
    deps: ReextractDeps,
    progress_every: int = 10,
) -> None:
    """Iterate the KB's docs and re-run GraphRAG extraction.

    Per-doc failures are counted but do not abort the loop. Top-level
    failures (iterator blows up, DB unavailable) mark the job 'failed'
    before re-raising.
    """
    processed = 0
    failed = 0

    await deps.job_repo.start(job_id, docs_total=0)

    try:
        async for doc in deps.doc_iterator(kb_id=kb_id):
            try:
                schema = deps.schema_resolver.resolve(
                    kb_id=kb_id,
                    source_type=doc.get("source_type"),
                )
                result = deps.extractor.extract(
                    document=doc["content"],
                    source_title=doc.get("title"),
                    source_page_id=doc["doc_id"],
                    source_updated_at=doc.get("updated_at"),
                    kb_id=kb_id,
                    source_type=doc.get("source_type"),
                    schema=schema,
                )
                if result.node_count or result.relationship_count:
                    deps.extractor.save_to_neo4j(result, schema=schema)
                processed += 1
            except (RuntimeError, OSError, ValueError) as exc:
                failed += 1
                logger.warning(
                    "Reextract per-doc failure (job=%s doc=%s): %s",
                    job_id, doc.get("doc_id"), exc,
                )

            if (processed + failed) % progress_every == 0:
                await deps.job_repo.progress(
                    job_id, docs_processed=processed, docs_failed=failed,
                )

        await deps.job_repo.progress(
            job_id, docs_processed=processed, docs_failed=failed,
        )
        await deps.job_repo.complete(job_id, status="completed")
    except Exception as exc:  # noqa: BLE001
        logger.exception("Reextract job failed (kb=%s)", kb_id)
        await deps.job_repo.complete(
            job_id, status="failed", error_message=str(exc),
        )
        raise


async def schema_reextract_run(
    ctx: dict[str, Any],
    job_id: str,
    kb_id: str,
) -> dict[str, Any]:
    """arq entrypoint. Resolves deps from app_state; validates via run_reextract."""
    from uuid import UUID as _UUID

    from src.pipelines.graphrag import GraphRAGExtractor, SchemaResolver
    from src.stores.postgres.repositories.reextract_job_repo import (
        ReextractJobRepo,
    )

    app = ctx.get("app_state")
    if app is None or not all(
        hasattr(app, attr)
        for attr in ("session_maker", "doc_sampler")
    ):
        raise NotImplementedError(
            "schema_reextract_run requires ctx['app_state'] with "
            "session_maker + doc_sampler (iterator adapter). "
            "Wire via worker startup.",
        )

    deps = ReextractDeps(
        job_repo=ReextractJobRepo(app.session_maker),
        extractor=GraphRAGExtractor(),
        schema_resolver=SchemaResolver,
        doc_iterator=app.doc_sampler.iterate_kb,
    )
    await run_reextract(job_id=_UUID(job_id), kb_id=kb_id, deps=deps)
    return {"status": "ok"}


__all__ = [
    "DocIteratorProto",
    "JobRepoProto",
    "ReextractDeps",
    "run_reextract",
    "schema_reextract_run",
]
