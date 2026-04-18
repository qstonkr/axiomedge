"""Background ingestion job tracking (Redis-backed).

Jobs are stored in Redis so all uvicorn workers share the same state.
Key format: ``job:{job_id}`` with a hash of job fields.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone

import logging

import redis.asyncio as aioredis
from fastapi import APIRouter, HTTPException

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/jobs", tags=["Jobs"])

def _get_redis_url() -> str:
    from src.config import get_settings
    return get_settings().redis.url
_KEY_PREFIX = "knowledge:job:"
_INDEX_KEY = "knowledge:jobs"
_MAX_JOBS = 1000
_TTL_SECONDS = 86400  # 24h

_redis: aioredis.Redis | None = None


async def _get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(_get_redis_url(), decode_responses=True)
    await asyncio.sleep(0)
    return _redis


def _job_key(job_id: str) -> str:
    return f"{_KEY_PREFIX}{job_id}"


async def create_job(kb_id: str, file_count: int) -> str:
    """Create a new ingestion job and return its ID."""
    r = await _get_redis()
    job_id = str(uuid.uuid4())[:8]
    now = datetime.now(timezone.utc).isoformat()
    job = {
        "id": job_id,
        "kb_id": kb_id,
        "status": "processing",
        "total_files": file_count,
        "processed": 0,
        "chunks": 0,
        "errors": "[]",
        "created_at": now,
        "updated_at": now,
        "completed_at": "",
    }
    pipe = r.pipeline()
    pipe.hset(_job_key(job_id), mapping=job)
    pipe.expire(_job_key(job_id), _TTL_SECONDS)
    pipe.rpush(_INDEX_KEY, job_id)
    await pipe.execute()

    # Evict oldest jobs beyond limit
    length = await r.llen(_INDEX_KEY)
    if length > _MAX_JOBS:
        to_remove = length - _MAX_JOBS
        for _ in range(to_remove):
            old_id = await r.lpop(_INDEX_KEY)
            if old_id:
                await r.delete(_job_key(old_id))

    return job_id


async def update_job(job_id: str, **kwargs) -> None:
    """Update job fields."""
    r = await _get_redis()
    key = _job_key(job_id)
    if not await r.exists(key):
        return
    now = datetime.now(timezone.utc).isoformat()
    updates = {"updated_at": now}
    status = kwargs.get("status")
    if status in ("completed", "failed", "cancelled"):
        updates["completed_at"] = now
    for k, v in kwargs.items():
        if isinstance(v, (list, dict)):
            updates[k] = json.dumps(v, ensure_ascii=False)
        else:
            updates[k] = v
    await r.hset(key, mapping=updates)


async def get_job(job_id: str) -> dict | None:
    """Get job by ID."""
    r = await _get_redis()
    raw = await r.hgetall(_job_key(job_id))
    if not raw:
        return None
    return _deserialize(raw)


async def get_active_job_count() -> int:
    """Return the number of jobs currently in 'processing' status."""
    try:
        r = await _get_redis()
        job_ids = await r.lrange(_INDEX_KEY, 0, -1)
        count = 0
        for jid in job_ids:
            status = await r.hget(_job_key(jid), "status")
            if status == "processing":
                count += 1
        return count
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError):
        return 0


async def is_cancelled(job_id: str) -> bool:
    """Check if a job has been cancelled."""
    r = await _get_redis()
    st = await r.hget(_job_key(job_id), "status")
    return st == "cancelled"


def _deserialize(raw: dict) -> dict:
    """Convert Redis hash values back to proper types."""
    job = dict(raw)
    for int_field in ("total_files", "processed", "chunks"):
        if int_field in job:
            try:
                job[int_field] = int(job[int_field])
            except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
                logger.debug("Failed to parse int field %s: %s", int_field, e)
    if "errors" in job:
        try:
            job["errors"] = json.loads(job["errors"])
        except (json.JSONDecodeError, TypeError):
            job["errors"] = []
    return job


@router.get("/{job_id}", responses={404: {"description": "Job not found"}})
async def get_job_status(job_id: str) -> dict:
    """Get status of an ingestion job."""
    job = await get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.post("/{job_id}/cancel", responses={404: {"description": "Job not found"}, 400: {"description": "Job is not in processing state"}})  # noqa: E501
async def cancel_job(job_id: str) -> dict:
    """Cancel a running ingestion job."""
    job = await get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["status"] != "processing":
        raise HTTPException(status_code=400, detail=f"Job is already {job['status']}")
    now = datetime.now(timezone.utc).isoformat()
    r = await _get_redis()
    await r.hset(_job_key(job_id), mapping={
        "status": "cancelled",
        "updated_at": now,
        "completed_at": now,
    })
    return {"id": job_id, "status": "cancelled"}


@router.get("")
async def list_jobs() -> dict:
    """List recent ingestion jobs (last 20)."""
    r = await _get_redis()
    job_ids = await r.lrange(_INDEX_KEY, -20, -1)
    jobs = []
    for jid in job_ids:
        raw = await r.hgetall(_job_key(jid))
        if raw:
            jobs.append(_deserialize(raw))
    return {"jobs": jobs}
