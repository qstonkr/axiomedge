# GraphRAG Schema Evolution — Phase 5 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans.

**Goal:** Re-extract existing documents against a newer schema version (Q5 on-demand pattern), plus developer CLI for scaffolding and dry-running schema flows.

**Architecture:** `ReextractJobRepo` manages `graph_schema_reextract_jobs` rows. `schema_reextract_run` arq task iterates a KB's Qdrant collection in pages, loads the raw doc text via payload, calls `GraphRAGExtractor.extract(schema=<current>)`, and writes the graph back. Progress and failure counts live on the job row. Admin trigger endpoint `/reextract/{kb_id}/run` enqueues the job. CLI: `make graph-schema-scaffold` (create a D-layer YAML from template) and `make graph-schema-dry-run` (preview what bootstrap *would* discover).

**Spec reference:** `docs/superpowers/specs/2026-04-24-graph-schema-evolution-design.md` §5.4 (end-user transparency), §6.1 (schema_reextract file), Q5 (forward-only + on-demand).

**Out of scope (Phase 5b / later):** Slack notifications, ops dashboard UI, per-job cancel button UI, E2E `test_kb_onboarding.py`. This phase is backend + CLI only.

---

## File Structure

### New files

| Path | Responsibility |
|---|---|
| `src/stores/postgres/repositories/reextract_job_repo.py` | `queue / start / progress / complete / has_active` |
| `src/jobs/schema_reextract.py` | arq task that drives the actual re-extract loop |
| `src/cli/graph_schema_cli.py` | `scaffold` + `dry-run` subcommands |
| `tests/unit/test_reextract_job_repo.py` | CRUD happy path |
| `tests/unit/test_schema_reextract_job.py` | arq task — mocked Qdrant + extractor |
| `tests/unit/test_graph_schema_cli.py` | scaffold + dry-run CLI |

### Modified

| Path | Change |
|---|---|
| `src/api/routes/graph_schema.py` | `POST /reextract/{kb_id}/run` + `GET /reextract/{job_id}` |
| `src/jobs/tasks.py` | register `schema_reextract_run` |
| `Makefile` | `graph-schema-scaffold`, `graph-schema-dry-run` targets |

---

## Task 1: ReextractJobRepo

**Files:** `src/stores/postgres/repositories/reextract_job_repo.py` + test.

- [ ] **Step 1: Write failing test**

Create `tests/unit/test_reextract_job_repo.py`:

```python
"""ReextractJobRepo — queue/start/progress/complete/has_active."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.stores.postgres.repositories.reextract_job_repo import ReextractJobRepo


def _make_session_maker():
    session = MagicMock()
    session.scalar = AsyncMock(return_value=None)
    session.add = MagicMock()
    session.execute = AsyncMock()
    session.flush = AsyncMock()

    begin_ctx = AsyncMock()
    begin_ctx.__aenter__ = AsyncMock(return_value=None)
    begin_ctx.__aexit__ = AsyncMock(return_value=None)
    session.begin = MagicMock(return_value=begin_ctx)

    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)

    return MagicMock(return_value=session), session


class TestQueue:
    @pytest.mark.asyncio
    async def test_queue_adds_row_and_returns_id(self):
        maker, session = _make_session_maker()
        fake_id = uuid4()

        def _capture(obj):
            obj.id = fake_id

        session.add = MagicMock(side_effect=_capture)
        repo = ReextractJobRepo(maker)
        job_id = await repo.queue(
            kb_id="test",
            triggered_by_user="admin@test",
            schema_version_from=1,
            schema_version_to=2,
        )
        assert job_id == fake_id


class TestStartAndProgress:
    @pytest.mark.asyncio
    async def test_start_sets_running(self):
        maker, session = _make_session_maker()
        repo = ReextractJobRepo(maker)
        await repo.start(uuid4(), docs_total=42)
        session.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_progress_updates_counters(self):
        maker, session = _make_session_maker()
        repo = ReextractJobRepo(maker)
        await repo.progress(uuid4(), docs_processed=10, docs_failed=1)
        session.execute.assert_awaited_once()


class TestComplete:
    @pytest.mark.asyncio
    async def test_complete_marks_done(self):
        maker, session = _make_session_maker()
        repo = ReextractJobRepo(maker)
        await repo.complete(uuid4(), status="completed")
        session.execute.assert_awaited_once()


class TestHasActive:
    @pytest.mark.asyncio
    async def test_has_active_true_when_row_found(self):
        maker, session = _make_session_maker()
        session.scalar = AsyncMock(return_value=uuid4())
        repo = ReextractJobRepo(maker)
        assert await repo.has_active("test") is True

    @pytest.mark.asyncio
    async def test_has_active_false_when_none(self):
        maker, session = _make_session_maker()
        session.scalar = AsyncMock(return_value=None)
        repo = ReextractJobRepo(maker)
        assert await repo.has_active("test") is False
```

- [ ] **Step 2: Fail → implement**

Create `src/stores/postgres/repositories/reextract_job_repo.py`:

```python
"""ReextractJobRepo — graph_schema_reextract_jobs lifecycle."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.stores.postgres.models import ReextractJobModel
from src.stores.postgres.repositories.base import BaseRepository


class ReextractJobRepo(BaseRepository):
    def __init__(self, session_maker: async_sessionmaker) -> None:
        super().__init__(session_maker)

    async def queue(
        self,
        *,
        kb_id: str,
        triggered_by_user: str,
        schema_version_from: int,
        schema_version_to: int,
    ) -> UUID:
        async with self._session_maker() as session:
            async with session.begin():
                job = ReextractJobModel(
                    kb_id=kb_id,
                    triggered_by_user=triggered_by_user,
                    schema_version_from=schema_version_from,
                    schema_version_to=schema_version_to,
                    status="queued",
                )
                session.add(job)
                await session.flush()
                return job.id

    async def start(self, job_id: UUID, *, docs_total: int) -> None:
        async with self._session_maker() as session:
            async with session.begin():
                await session.execute(
                    update(ReextractJobModel).where(
                        ReextractJobModel.id == job_id,
                    ).values(
                        status="running",
                        docs_total=docs_total,
                        started_at=datetime.now(UTC),
                    ),
                )

    async def progress(
        self,
        job_id: UUID,
        *,
        docs_processed: int,
        docs_failed: int,
    ) -> None:
        async with self._session_maker() as session:
            async with session.begin():
                await session.execute(
                    update(ReextractJobModel).where(
                        ReextractJobModel.id == job_id,
                    ).values(
                        docs_processed=docs_processed,
                        docs_failed=docs_failed,
                    ),
                )

    async def complete(
        self,
        job_id: UUID,
        *,
        status: str,
        error_message: str | None = None,
    ) -> None:
        async with self._session_maker() as session:
            async with session.begin():
                await session.execute(
                    update(ReextractJobModel).where(
                        ReextractJobModel.id == job_id,
                    ).values(
                        status=status,
                        error_message=error_message,
                        completed_at=datetime.now(UTC),
                    ),
                )

    async def has_active(self, kb_id: str) -> bool:
        async with self._session_maker() as session:
            row = await session.scalar(
                select(ReextractJobModel.id).where(
                    ReextractJobModel.kb_id == kb_id,
                    ReextractJobModel.status.in_(["queued", "running"]),
                ).limit(1),
            )
            return row is not None


__all__ = ["ReextractJobRepo"]
```

- [ ] **Step 3: Run + commit**

```bash
uv run pytest tests/unit/test_reextract_job_repo.py -v --no-cov
uvx ruff check src/stores/postgres/repositories/reextract_job_repo.py tests/unit/test_reextract_job_repo.py
git add src/stores/postgres/repositories/reextract_job_repo.py tests/unit/test_reextract_job_repo.py
git commit -m "feat(postgres): ReextractJobRepo — Phase 5 job lifecycle

queue/start/progress/complete/has_active. has_active guards the admin
trigger endpoint from concurrent re-extracts on the same KB.

Spec §6.1."
```

---

## Task 2: schema_reextract arq task

**Files:** `src/jobs/schema_reextract.py` + test.

The task's core job: iterate the KB's Qdrant collection, call the extractor with the current schema, save the resulting graph to Neo4j. Real Qdrant integration happens at task-run time through `ctx['app_state']`; tests inject a stub iterator.

- [ ] **Step 1: Write failing test**

Create `tests/unit/test_schema_reextract_job.py`:

```python
"""Contract tests for schema_reextract_run arq task."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.jobs.schema_reextract import (
    ReextractDeps,
    run_reextract,
)


@pytest.fixture
def mock_deps():
    deps = ReextractDeps(
        job_repo=AsyncMock(),
        extractor=MagicMock(),
        schema_resolver=MagicMock(),
        doc_iterator=AsyncMock(),
    )
    deps.job_repo.start = AsyncMock()
    deps.job_repo.progress = AsyncMock()
    deps.job_repo.complete = AsyncMock()
    # schema_resolver returns a static SchemaProfile-like object
    deps.schema_resolver.resolve = MagicMock(return_value=MagicMock(version=2))
    return deps


class TestRunReextract:
    @pytest.mark.asyncio
    async def test_happy_path_marks_completed(self, mock_deps):
        # doc_iterator yields 3 docs
        async def _iter(**_kw):
            for i in range(3):
                yield {
                    "doc_id": f"d{i}", "content": f"doc {i} content",
                    "kb_id": "test", "source_type": "confluence",
                }
        mock_deps.doc_iterator = _iter

        # Extractor returns a trivially non-empty result
        result = MagicMock()
        result.node_count = 1
        result.relationship_count = 1
        mock_deps.extractor.extract = MagicMock(return_value=result)
        mock_deps.extractor.save_to_neo4j = MagicMock(
            return_value={"nodes_created": 1},
        )

        job_id = uuid4()
        await run_reextract(
            job_id=job_id, kb_id="test", deps=mock_deps,
        )
        mock_deps.job_repo.start.assert_awaited_once()
        mock_deps.job_repo.complete.assert_awaited_once()
        complete_kwargs = mock_deps.job_repo.complete.await_args.kwargs
        assert complete_kwargs["status"] == "completed"

    @pytest.mark.asyncio
    async def test_per_doc_failure_counted_but_continues(self, mock_deps):
        async def _iter(**_kw):
            for i in range(3):
                yield {
                    "doc_id": f"d{i}", "content": f"doc {i}",
                    "kb_id": "test", "source_type": "x",
                }
        mock_deps.doc_iterator = _iter

        # 2nd extract call raises
        result_ok = MagicMock()
        result_ok.node_count = 1
        result_ok.relationship_count = 0
        call_count = {"n": 0}

        def _flaky(**_kw):
            call_count["n"] += 1
            if call_count["n"] == 2:
                raise RuntimeError("LLM hiccup")
            return result_ok

        mock_deps.extractor.extract = MagicMock(side_effect=_flaky)
        mock_deps.extractor.save_to_neo4j = MagicMock(return_value={})

        await run_reextract(
            job_id=uuid4(), kb_id="test", deps=mock_deps,
        )
        # Job completes despite 1 per-doc failure
        complete_kwargs = mock_deps.job_repo.complete.await_args.kwargs
        assert complete_kwargs["status"] == "completed"
        # Progress ran at least once with docs_failed > 0
        progress_calls = mock_deps.job_repo.progress.await_args_list
        assert any(c.kwargs.get("docs_failed", 0) >= 1 for c in progress_calls)

    @pytest.mark.asyncio
    async def test_top_level_failure_marks_failed(self, mock_deps):
        async def _iter(**_kw):
            raise RuntimeError("Qdrant down")
            yield  # pragma: no cover

        mock_deps.doc_iterator = _iter
        with pytest.raises(RuntimeError):
            await run_reextract(
                job_id=uuid4(), kb_id="test", deps=mock_deps,
            )
        complete_kwargs = mock_deps.job_repo.complete.await_args.kwargs
        assert complete_kwargs["status"] == "failed"
        assert "Qdrant down" in complete_kwargs["error_message"]
```

- [ ] **Step 2: Fail → implement**

Create `src/jobs/schema_reextract.py`:

```python
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
    extractor: Any          # GraphRAGExtractor
    schema_resolver: Any    # SchemaResolver-like (has .resolve(kb_id=, source_type=))
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

    # docs_total is unknown until the iterator walks the whole KB;
    # use 0 here and let UI rely on processed+failed for live progress.
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

        # Final progress + completion
        await deps.job_repo.progress(
            job_id, docs_processed=processed, docs_failed=failed,
        )
        await deps.job_repo.complete(job_id, status="completed")
    except Exception as exc:  # noqa: BLE001 — record-then-raise
        logger.exception("Reextract job failed (kb=%s)", kb_id)
        await deps.job_repo.complete(
            job_id, status="failed", error_message=str(exc),
        )
        raise


# ---------------------------------------------------------------------------
# arq task entry point (production wiring)
# ---------------------------------------------------------------------------


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
```

- [ ] **Step 3: Run + commit**

```bash
uv run pytest tests/unit/test_schema_reextract_job.py -v --no-cov
uvx ruff check src/jobs/schema_reextract.py tests/unit/test_schema_reextract_job.py
git add src/jobs/schema_reextract.py tests/unit/test_schema_reextract_job.py
git commit -m "feat(jobs): schema_reextract_run — on-demand KB re-extract

Dependency-injected orchestrator (JobRepoProto / DocIteratorProto /
extractor / resolver). Per-doc extract failures are counted + logged;
the loop continues so an LLM hiccup on one document doesn't abort the
whole job. Top-level failures (iterator crash, DB unavailable) mark
the job row 'failed' before re-raising so arq retry metadata kicks in.

Spec §6.1 + Q5."
```

---

## Task 3: Wire reextract endpoint + arq registration

**Files:** modify `src/api/routes/graph_schema.py` + `src/jobs/tasks.py`.

- [ ] **Step 1: Append reextract endpoint test**

Append to `tests/unit/test_graph_schema_routes.py`:

```python
class TestReextractTrigger:
    def test_trigger_returns_409_when_active(self, app, mock_candidate_repo):
        # Need the reextract_job_repo mocked — extend _get_repos
        from src.api.routes.graph_schema import _get_repos
        mock_reextract_repo = AsyncMock()
        mock_reextract_repo.has_active = AsyncMock(return_value=True)

        app.dependency_overrides[_get_repos] = lambda: (
            mock_candidate_repo,
            AsyncMock(has_running=AsyncMock(return_value=False)),
            mock_reextract_repo,
        )
        client = TestClient(app)
        resp = client.post(
            "/api/v1/admin/graph-schema/reextract/test/run",
            json={"triggered_by_user": "admin@test"},
        )
        assert resp.status_code == 409

    def test_trigger_enqueues_job(self, app, mock_candidate_repo):
        from src.api.routes.graph_schema import _get_repos

        fake_id = uuid4()
        mock_reextract_repo = AsyncMock()
        mock_reextract_repo.has_active = AsyncMock(return_value=False)
        mock_reextract_repo.queue = AsyncMock(return_value=fake_id)

        app.dependency_overrides[_get_repos] = lambda: (
            mock_candidate_repo,
            AsyncMock(has_running=AsyncMock(return_value=False)),
            mock_reextract_repo,
        )
        with patch(
            "src.api.routes.graph_schema._enqueue_reextract",
            new=AsyncMock(return_value={"job_id": "arq-j1"}),
        ) as enq:
            client = TestClient(app)
            resp = client.post(
                "/api/v1/admin/graph-schema/reextract/test/run",
                json={"triggered_by_user": "admin@test"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["reextract_job_id"] == str(fake_id)
        enq.assert_awaited_once()
```

- [ ] **Step 2: Extend `graph_schema.py`**

Change `_get_repos` to return a 3-tuple `(candidate, bootstrap_run, reextract)`:

```python
def _get_repos():
    from src.api.state import get_app_state
    from src.stores.postgres.repositories.bootstrap_run_repo import (
        BootstrapRunRepo,
    )
    from src.stores.postgres.repositories.reextract_job_repo import (
        ReextractJobRepo,
    )
    from src.stores.postgres.repositories.schema_candidate_repo import (
        SchemaCandidateRepo,
    )

    app_state = get_app_state()
    session_maker = app_state.session_maker
    return (
        SchemaCandidateRepo(session_maker),
        BootstrapRunRepo(session_maker),
        ReextractJobRepo(session_maker),
    )
```

Update every existing route that uses `repos` to unpack 3 elements — for `list_candidates` / `approve_candidate` / `reject_candidate` / `merge_candidate` / `rename_candidate` change `candidate_repo, _ = repos` → `candidate_repo, _, _ = repos`. For `trigger_bootstrap` change `_, run_repo = repos` → `_, run_repo, _ = repos`.

Add the new enqueue helper + endpoint at the end:

```python
async def _enqueue_reextract(
    job_id: str, kb_id: str,
) -> dict[str, Any]:
    """Late-bound enqueue — easy to patch in tests."""
    from src.jobs.queue import enqueue_job

    return await enqueue_job("schema_reextract_run", job_id, kb_id)


class ReextractRunRequest(BaseModel):
    triggered_by_user: str


@router.post("/reextract/{kb_id}/run")
async def trigger_reextract(
    kb_id: str,
    req: ReextractRunRequest,
    repos=Depends(_get_repos),
) -> dict[str, Any]:
    _, _, reextract_repo = repos
    if await reextract_repo.has_active(kb_id):
        raise HTTPException(
            status_code=409,
            detail=f"Re-extract already queued/running for kb_id={kb_id}",
        )

    # Schema versions for audit
    from src.pipelines.graphrag import SchemaResolver

    current = SchemaResolver.resolve(kb_id=kb_id, source_type=None)
    version_from = max(1, current.version - 1)
    version_to = current.version

    job_id = await reextract_repo.queue(
        kb_id=kb_id,
        triggered_by_user=req.triggered_by_user,
        schema_version_from=version_from,
        schema_version_to=version_to,
    )
    arq_result = await _enqueue_reextract(str(job_id), kb_id)

    return {
        "status": "queued",
        "reextract_job_id": str(job_id),
        "schema_version_from": version_from,
        "schema_version_to": version_to,
        **arq_result,
    }
```

Also append `_enqueue_reextract` and `ReextractRunRequest` to `__all__` if that export list is present.

- [ ] **Step 3: Update existing route's `repos` unpacking in the test fixtures**

Existing tests injected `_get_repos` to return a 2-tuple. Update the `app` fixture at the top of `tests/unit/test_graph_schema_routes.py` to return 3-tuple:

```python
@pytest.fixture
def app(mock_candidate_repo, mock_run_repo):
    from src.api.routes.graph_schema import _get_repos, router

    fast = FastAPI()
    mock_reextract_repo = AsyncMock()
    mock_reextract_repo.has_active = AsyncMock(return_value=False)
    fast.dependency_overrides[_get_repos] = lambda: (
        mock_candidate_repo, mock_run_repo, mock_reextract_repo,
    )
    fast.include_router(router)
    return fast
```

- [ ] **Step 4: Register arq task**

In `src/jobs/tasks.py`:

```python
from src.jobs.schema_reextract import schema_reextract_run
# ...
REGISTERED_TASKS = [
    # ...existing
    schema_reextract_run,
]
```

- [ ] **Step 5: Tests + lint + commit**

```bash
uv run pytest tests/unit/test_graph_schema_routes.py -v --no-cov
uvx ruff check src/api/routes/graph_schema.py src/jobs/tasks.py tests/unit/test_graph_schema_routes.py
git add src/api/routes/graph_schema.py src/jobs/tasks.py tests/unit/test_graph_schema_routes.py
git commit -m "feat(api): /reextract/{kb_id}/run admin trigger + arq registration

ReextractRunRequest triggers a Phase-5 re-extract of all docs in the KB
against the current schema version. 409 guard when queued/running for
the same kb_id. Schema versions (from/to) captured at trigger time for
audit.

Spec §6.1."
```

---

## Task 4: CLI — scaffold + dry-run

**Files:** `src/cli/graph_schema_cli.py` + test + Makefile edits.

- [ ] **Step 1: Test first**

Create `tests/unit/test_graph_schema_cli.py`:

```python
"""CLI: scaffold + dry-run commands."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from src.cli.graph_schema_cli import scaffold_source_default


class TestScaffold:
    def test_scaffold_writes_template(self, tmp_path, monkeypatch):
        schemas = tmp_path / "graph_schemas"
        (schemas / "_defaults").mkdir(parents=True)
        monkeypatch.setattr(
            "src.cli.graph_schema_cli._DEFAULTS_DIR", schemas / "_defaults",
        )
        path = scaffold_source_default("jira")
        assert path.exists()
        data = yaml.safe_load(path.read_text())
        assert "nodes" in data
        assert "relationships" in data
        assert "prompt_focus" in data

    def test_scaffold_rejects_existing(self, tmp_path, monkeypatch):
        schemas = tmp_path / "graph_schemas"
        (schemas / "_defaults").mkdir(parents=True)
        (schemas / "_defaults" / "jira.yaml").write_text("existing: yes\n")
        monkeypatch.setattr(
            "src.cli.graph_schema_cli._DEFAULTS_DIR", schemas / "_defaults",
        )
        with pytest.raises(FileExistsError):
            scaffold_source_default("jira")

    def test_scaffold_rejects_unsafe_name(self, tmp_path, monkeypatch):
        schemas = tmp_path / "graph_schemas"
        (schemas / "_defaults").mkdir(parents=True)
        monkeypatch.setattr(
            "src.cli.graph_schema_cli._DEFAULTS_DIR", schemas / "_defaults",
        )
        with pytest.raises(ValueError):
            scaffold_source_default("../evil")
```

- [ ] **Step 2: Fail → implement**

Create `src/cli/graph_schema_cli.py`:

```python
"""CLI: graph-schema-* operator commands.

Invocations:
    uv run python -m src.cli.graph_schema_cli scaffold <source_type>
    uv run python -m src.cli.graph_schema_cli dry-run <kb_id>
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

_DEFAULTS_DIR = Path("deploy/config/graph_schemas/_defaults")
_SAFE_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

_TEMPLATE = {
    "version": 1,
    "prompt_focus": "TODO: describe the typical content of this source",
    "nodes": [
        "Person",
        "Document",
        "Topic",
    ],
    "relationships": [
        "AUTHORED",
        "MENTIONS",
        "RELATED_TO",
    ],
    "options": {
        "disable_bootstrap": False,
        "schema_evolution": "batch",
        "bootstrap_sample_size": 100,
    },
}


def scaffold_source_default(source_type: str) -> Path:
    """Create ``_defaults/<source_type>.yaml`` from a template.

    Rejects unsafe names (injection defense) and refuses to overwrite.
    """
    if not _SAFE_NAME.match(source_type):
        raise ValueError(
            f"unsafe source_type name: {source_type!r} — "
            "must match [a-z][a-z0-9_]*",
        )
    _DEFAULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = _DEFAULTS_DIR / f"{source_type}.yaml"
    if path.exists():
        raise FileExistsError(f"{path} already exists; delete first to regen")
    path.write_text(
        yaml.dump(_TEMPLATE, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return path


def dry_run(kb_id: str) -> dict:
    """Print what SchemaResolver would resolve for ``kb_id`` today.

    Useful when an operator wants to preview schema without running a
    real bootstrap.
    """
    from src.pipelines.graphrag import SchemaResolver

    schema = SchemaResolver.resolve(kb_id=kb_id, source_type=None)
    return {
        "kb_id": kb_id,
        "version": schema.version,
        "source_layers": list(schema.source_layers),
        "nodes": list(schema.nodes),
        "relationships": list(schema.relationships),
        "prompt_focus": schema.prompt_focus,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="graph-schema-cli")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_scaffold = sub.add_parser(
        "scaffold", help="Create _defaults/<source_type>.yaml from template",
    )
    p_scaffold.add_argument("source_type")

    p_dry = sub.add_parser(
        "dry-run", help="Preview SchemaResolver output for a kb_id",
    )
    p_dry.add_argument("kb_id")

    args = parser.parse_args(argv)

    if args.cmd == "scaffold":
        try:
            path = scaffold_source_default(args.source_type)
        except (ValueError, FileExistsError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(f"wrote {path}")
        return 0

    if args.cmd == "dry-run":
        import json

        info = dry_run(args.kb_id)
        print(json.dumps(info, indent=2, ensure_ascii=False))
        return 0

    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["dry_run", "main", "scaffold_source_default"]
```

- [ ] **Step 3: Makefile targets**

Append to `Makefile`:

```makefile
# Graph schema operator commands (Phase 5)
graph-schema-scaffold:
	@uv run python -m src.cli.graph_schema_cli scaffold $(source)

graph-schema-dry-run:
	@uv run python -m src.cli.graph_schema_cli dry-run $(kb)
```

Usage: `make graph-schema-scaffold source=jira`.

- [ ] **Step 4: Tests + lint + commit**

```bash
uv run pytest tests/unit/test_graph_schema_cli.py -v --no-cov
uvx ruff check src/cli/graph_schema_cli.py tests/unit/test_graph_schema_cli.py
git add src/cli/graph_schema_cli.py tests/unit/test_graph_schema_cli.py Makefile
git commit -m "feat(cli): graph-schema scaffold + dry-run"
```

---

## Task 5: Full regression + coverage

- [ ] Run Phase 5 new tests + Phase 1-4a regression; confirm all pass and that Phase 5 new modules ≥ 80% coverage.

```bash
uv run pytest \
  tests/unit/test_reextract_job_repo.py \
  tests/unit/test_schema_reextract_job.py \
  tests/unit/test_graph_schema_cli.py \
  tests/unit/test_graph_schema_routes.py \
  tests/unit/test_graph_schema_helpers.py \
  tests/unit/test_schema_candidate_repo.py \
  tests/unit/test_bootstrap_run_repo.py \
  tests/unit/test_schema_bootstrap.py \
  tests/unit/test_schema_discovery_prompt.py \
  tests/unit/test_schema_resolver.py \
  tests/unit/test_schema_types.py \
  tests/unit/test_dynamic_schema.py \
  tests/unit/test_extractor_schema_integration.py \
  tests/unit/test_graphrag_prompts_facade.py \
  tests/unit/test_source_defaults.py \
  tests/unit/test_schema_migration.py \
  tests/unit/test_graphrag_full.py \
  tests/unit/test_graphrag_coverage.py \
  -q --no-cov 2>&1 | tail -3

uv run pytest \
  tests/unit/test_reextract_job_repo.py \
  tests/unit/test_schema_reextract_job.py \
  tests/unit/test_graph_schema_cli.py \
  --cov=src.stores.postgres.repositories.reextract_job_repo \
  --cov=src.jobs.schema_reextract \
  --cov=src.cli.graph_schema_cli \
  --cov-report=term-missing --no-cov-on-fail 2>&1 | tail -10
```

---

## Spec Coverage

| Spec | Task |
|---|---|
| Q5 on-demand re-extract | 2, 3 |
| §5.4 end-user transparency (reextract status) | 2 (docs_processed/failed) |
| §6.1 `schema_reextract.py` | 2 |
| Makefile CLI | 4 |

Deferred: Phase 5b = Slack webhook / ops dashboard UI / E2E kb onboarding test.
