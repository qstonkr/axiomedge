"""Registered Arq job tasks.

Each task receives ``ctx: dict`` (Arq context — has redis pool, job_id, etc.)
plus task-specific args. Tasks must be idempotent (Arq retries on failure).

Add new tasks here and register them in ``REGISTERED_TASKS`` so the worker
knows about them.
"""

from __future__ import annotations

import logging
from typing import Any

from src.jobs.distill_jobs import (
    distill_pipeline_post_train,
    distill_pipeline_pre_train,
    distill_sweep_post_train,
    distill_sweep_training,
)
from src.jobs.schema_bootstrap_jobs import (
    schema_bootstrap_cleanup,
    schema_bootstrap_run,
)
from src.jobs.upload_jobs import (
    cleanup_orphan_uploads,
    ingest_from_object_storage,
)

logger = logging.getLogger(__name__)


async def example_task(ctx: dict[str, Any], message: str) -> str:
    """Sample task — proves the pipeline works.

    Real tasks (ingest_kb, graphrag_extract, distill_build) follow this signature:
    accept ``ctx`` plus typed args; return a serializable result.
    """
    job_id = ctx.get("job_id", "?")
    logger.info("example_task[%s] received: %s", job_id, message)
    return f"processed: {message}"


# Authoritative list — referenced by WorkerSettings.functions
REGISTERED_TASKS = [
    example_task,
    distill_pipeline_pre_train,
    distill_pipeline_post_train,
    distill_sweep_training,
    distill_sweep_post_train,
    ingest_from_object_storage,
    cleanup_orphan_uploads,
    schema_bootstrap_run,
    schema_bootstrap_cleanup,
]
