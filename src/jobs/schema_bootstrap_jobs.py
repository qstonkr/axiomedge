"""arq tasks for GraphRAG schema bootstrap (Phase 3).

Tasks:
- ``schema_bootstrap_run(ctx, kb_id, triggered_by='cron')`` — one-shot bootstrap
- ``schema_bootstrap_cleanup(ctx)`` — daily cron to clear stale 'running' rows

Dependencies (LLM / DocSampler / session_maker) are expected on
``ctx['app_state']``; worker startup is responsible for binding them.
Phase 3 ships task skeletons — full production wiring lands alongside the
Phase 4 admin trigger endpoint so both use the same injection.

Spec §6.5.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def schema_bootstrap_run(
    ctx: dict[str, Any],
    kb_id: str,
    triggered_by: str = "cron",
    triggered_by_user: str | None = None,
) -> dict[str, Any]:
    """Run one bootstrap iteration for the given KB."""
    from src.pipelines.graphrag.schema_bootstrap import (
        BootstrapAlreadyRunning,
        SchemaBootstrapper,
    )
    from src.stores.postgres.repositories.bootstrap_run_repo import (
        BootstrapRunRepo,
    )
    from src.stores.postgres.repositories.schema_candidate_repo import (
        SchemaCandidateRepo,
    )

    app = ctx.get("app_state")
    if app is None or not all(
        hasattr(app, attr)
        for attr in ("llm", "session_maker", "doc_sampler")
    ):
        raise NotImplementedError(
            "schema_bootstrap_run requires ctx['app_state'] with llm + "
            "session_maker + doc_sampler. Wire via worker startup.",
        )

    candidate_repo = SchemaCandidateRepo(app.session_maker)
    run_repo = BootstrapRunRepo(app.session_maker)
    bootstrapper = SchemaBootstrapper(
        llm=app.llm,
        candidate_repo=candidate_repo,
        run_repo=run_repo,
        sampler=app.doc_sampler,
    )

    try:
        run_id = await bootstrapper.run(
            kb_id=kb_id,
            triggered_by=triggered_by,
            triggered_by_user=triggered_by_user,
        )
        return {"status": "ok", "run_id": str(run_id)}
    except BootstrapAlreadyRunning:
        logger.info("Bootstrap skip — already running: kb_id=%s", kb_id)
        return {"status": "skip", "reason": "already_running"}


async def schema_bootstrap_cleanup(ctx: dict[str, Any]) -> dict[str, Any]:
    """Daily cron — mark stale 'running' rows as 'failed'."""
    from src.stores.postgres.repositories.bootstrap_run_repo import (
        BootstrapRunRepo,
    )

    app = ctx.get("app_state")
    if app is None or not hasattr(app, "session_maker"):
        raise NotImplementedError(
            "schema_bootstrap_cleanup requires ctx['app_state'] with "
            "session_maker. Wire via worker startup.",
        )

    run_repo = BootstrapRunRepo(app.session_maker)
    cleared = await run_repo.cleanup_stale()
    logger.info("Schema bootstrap stale cleanup: cleared %d rows", cleared)
    return {"status": "ok", "cleared": cleared}


__all__ = ["schema_bootstrap_cleanup", "schema_bootstrap_run"]
