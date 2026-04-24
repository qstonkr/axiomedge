"""arq cron: sweep graph-schema ops thresholds and emit Slack alerts.

Spec §5.6 (Ops — alerts). Runs every 30 minutes.
"""

from __future__ import annotations

import logging
from typing import Any

from src.config import get_settings
from src.notifications.slack import (
    notify_bootstrap_failure_streak,
    notify_pending_threshold,
)

logger = logging.getLogger(__name__)


async def run_alerts_sweep(*, candidate_repo: Any, run_repo: Any) -> None:
    """Core sweep — dependency injected so it's unit-testable."""
    settings = get_settings()
    n = settings.notifications

    try:
        pending_by_kb = await candidate_repo.count_pending_by_kb()
    except Exception:  # noqa: BLE001
        logger.exception("alerts_sweep: count_pending_by_kb failed")
        pending_by_kb = []

    for kb_id, count in pending_by_kb:
        if count >= n.candidate_pending_threshold:
            await notify_pending_threshold(kb_id=kb_id, pending=count)

    try:
        streaks = await run_repo.recent_failure_streak()
    except Exception:  # noqa: BLE001
        logger.exception("alerts_sweep: recent_failure_streak failed")
        streaks = {}

    for kb_id, streak in streaks.items():
        if streak >= n.bootstrap_failure_streak:
            await notify_bootstrap_failure_streak(kb_id=kb_id, count=streak)


async def schema_alerts_sweep(ctx: dict[str, Any]) -> dict[str, Any]:
    """arq cron entrypoint. Resolves repos from ctx['app_state']."""
    from src.stores.postgres.repositories.bootstrap_run_repo import (
        BootstrapRunRepo,
    )
    from src.stores.postgres.repositories.schema_candidate_repo import (
        SchemaCandidateRepo,
    )

    app = ctx.get("app_state")
    if app is None or not hasattr(app, "session_maker"):
        logger.warning("schema_alerts_sweep: app_state missing — skipping")
        return {"status": "skipped"}

    await run_alerts_sweep(
        candidate_repo=SchemaCandidateRepo(app.session_maker),
        run_repo=BootstrapRunRepo(app.session_maker),
    )
    return {"status": "ok"}


__all__ = ["run_alerts_sweep", "schema_alerts_sweep"]
