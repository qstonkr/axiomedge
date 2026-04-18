"""Background job queue (Arq + Redis).

Replaces ad-hoc ``asyncio.create_task`` for long-running work (ingestion,
GraphRAG extraction, distill builds) so jobs survive uvicorn restarts and
get retry on failure.

Architecture:
- Producer (FastAPI handler): ``ctx = await pool.enqueue_job("ingest_kb", kb_id, ...)``
- Worker (separate process): ``arq src.jobs.worker.WorkerSettings``
- Queue / state: Redis ``ARQ_REDIS_URL`` (default $REDIS_URL)

See docs/JOBS.md for full migration guide.
"""

from src.jobs.queue import enqueue_job, get_pool, redis_settings_from_env
from src.jobs.tasks import REGISTERED_TASKS

__all__ = [
    "enqueue_job",
    "get_pool",
    "redis_settings_from_env",
    "REGISTERED_TASKS",
]
