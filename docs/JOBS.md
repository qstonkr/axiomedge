# Background Job Queue (Arq)

장기 실행 작업 (ingestion, GraphRAG 추출, distill build) 을 별도 worker
프로세스에서 실행. uvicorn 재시작 시 inflight job 손실 방지 + 자동 retry.

## 아키텍처

```
FastAPI handler  ──enqueue──▶  Redis (queue)  ◀──pop──  Arq Worker
                                                          ↓
                                                       task 실행
                                                          ↓
                                                       result → Redis (TTL)
```

- **Producer**: `await enqueue_job("task_name", arg1, arg2, ...)` — handler 안 어디서든
- **Worker**: `make worker` — 별도 프로세스 (production: K8s deployment)
- **State**: `ARQ_REDIS_URL` (default `REDIS_URL`)

## 빠른 사용

### 1. 새 task 정의

```python
# src/jobs/tasks.py
async def ingest_kb(ctx: dict, kb_id: str, file_paths: list[str]) -> dict:
    """Heavy ingestion task — runs in worker, not API process."""
    from src.pipelines.ingestion import ingest_files
    result = await ingest_files(kb_id, file_paths)
    return {"docs": result.docs_count, "chunks": result.chunks_count}

REGISTERED_TASKS = [example_task, ingest_kb]  # 추가
```

### 2. Handler 에서 enqueue

```python
# src/api/routes/ingest.py
from src.jobs import enqueue_job

@router.post("/api/v1/ingest")
async def trigger_ingest(req: IngestRequest):
    job = await enqueue_job(
        "ingest_kb",
        req.kb_id, req.file_paths,
        _job_id=str(uuid.uuid4()),  # idempotency key (선택)
    )
    return {"job_id": job.job_id, "status": "queued"}
```

### 3. 결과 조회

```python
@router.get("/api/v1/jobs/{job_id}")
async def get_job(job_id: str):
    from arq.jobs import Job
    pool = await get_pool()
    job = Job(job_id, pool)
    info = await job.info()
    if info is None:
        return {"status": "not_found"}
    return {"status": info.status.value, "result": await job.result(timeout=0)}
```

## Worker 운영

### 로컬 dev

```bash
# Terminal 1
make api

# Terminal 2
make worker
```

### Production (K8s)

별도 Deployment — `command: ["arq", "src.jobs.worker.WorkerSettings"]`.
HPA 로 큐 길이 (Redis LIST length) 따라 scaling 가능.

권장 secret/env: `ARQ_REDIS_URL`, `ARQ_MAX_JOBS`, `ARQ_JOB_TIMEOUT_SECONDS`.

## Retry 정책

- 기본 `max_tries=3` — 실패 시 exponential backoff
- 재시도 무한 방지: `max_tries` env 로 조정
- 멱등성 (idempotency) 필수 — task 가 같은 입력에 두 번 호출되도 안전해야 함

## Migration 가이드 (asyncio.create_task → Arq)

기존 패턴:
```python
asyncio.create_task(_long_running(args))  # uvicorn 재시작 시 사라짐
```

신규 패턴:
```python
await enqueue_job("task_name", args)  # worker 가 보장
```

장점:
- ✅ uvicorn 재시작 ↔ in-flight 작업 보존
- ✅ 자동 retry (transient failure 회복)
- ✅ Concurrency 제어 (`ARQ_MAX_JOBS`)
- ✅ Worker 수평 확장
- ✅ Job 상태 / 결과 표준 API (Job.info, Job.result)

단점 / 주의:
- ⚠️ 별도 worker 프로세스 필요 (배포 복잡도 ↑)
- ⚠️ Worker 와 API 동일 코드 버전 유지 (deploy 동기화 중요)
- ⚠️ Task signature 변경 시 in-flight 작업 호환성 고려

## 환경 변수

| 변수 | 기본값 | 설명 |
|---|---|---|
| `ARQ_REDIS_URL` | `$REDIS_URL` | 큐 전용 Redis (격리 권장) |
| `ARQ_MAX_JOBS` | 10 | worker 동시 실행 |
| `ARQ_MAX_TRIES` | 3 | 재시도 횟수 |
| `ARQ_JOB_TIMEOUT_SECONDS` | 300 | 단일 job timeout |
| `ARQ_KEEP_RESULT_SECONDS` | 3600 | result TTL |
