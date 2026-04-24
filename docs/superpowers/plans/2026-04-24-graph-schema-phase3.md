# GraphRAG Schema Evolution — Phase 3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans.

**Goal:** LLM-based schema discovery (B layer). Bootstrap periodically samples KB documents, asks LLM for candidate node/relationship types, stores findings as pending candidates in PostgreSQL. Admin approval (Phase 4) turns candidates into YAML commits — but that's later. Phase 3 is discovery + persistence only.

**Architecture:** SQLAlchemy-based schema (3 new tables) registered on `KnowledgeBase.metadata`. `SchemaBootstrapper` class orchestrates one run: stratified sampling → batch LLM discovery → candidate upsert. arq cron task invokes it daily; same task serves on-demand (admin manual trigger, wired later). Concurrent-safety via `has_running` DB check + arq queue per-KB.

**Spec reference:** `docs/superpowers/specs/2026-04-24-graph-schema-evolution-design.md` §4.3 (DB) + §6.3 (SchemaBootstrapper) + §6.5 (concurrency) + §6.6 (LLM prompt).

**Out of scope:** Admin UI (Phase 4), YAML auto-commit from approve (Phase 4), re-extract jobs (Phase 5), realtime mode (Phase 6). Phase 3 = backend discovery only.

---

## File Structure

### New files

| Path | Responsibility |
|---|---|
| `src/stores/postgres/models.py` (modify) | Add 3 SQLAlchemy models: SchemaCandidateModel, BootstrapRunModel, ReextractJobModel |
| `src/stores/postgres/repositories/schema_candidate_repo.py` | upsert / list_pending / list_approved_labels / decide |
| `src/stores/postgres/repositories/bootstrap_run_repo.py` | create / complete / has_running / cleanup_stale |
| `src/pipelines/graphrag/schema_prompts.py` | SCHEMA_DISCOVERY_PROMPT + strict parser |
| `src/pipelines/graphrag/schema_bootstrap.py` | SchemaBootstrapper core class |
| `src/jobs/schema_bootstrap_jobs.py` | arq task + cron registration |
| `tests/unit/test_schema_prompts.py` | Prompt shape + strict parser |
| `tests/unit/test_schema_bootstrap.py` | Sampling / LLM mock / candidate upsert / concurrent guard |

### Modified

| Path | Change |
|---|---|
| `src/stores/postgres/models.py` | +3 models |
| `src/jobs/worker.py` | Register schema_bootstrap_jobs tasks + cron entry |
| `src/jobs/tasks.py` | Register bootstrap task in `REGISTERED_TASKS` |

---

## Task 1: SQLAlchemy models

**Files:**
- Modify: `src/stores/postgres/models.py`
- Test: `tests/unit/test_schema_db_models.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_schema_db_models.py`:

```python
"""Smoke tests for Phase 3 DB models — column presence + constraints."""

from __future__ import annotations

from src.stores.postgres.models import (
    BootstrapRunModel,
    ReextractJobModel,
    SchemaCandidateModel,
)


class TestCandidateModel:
    def test_has_required_columns(self):
        cols = {c.name for c in SchemaCandidateModel.__table__.columns}
        assert {"id", "kb_id", "candidate_type", "label", "frequency",
                "confidence_avg", "confidence_min", "confidence_max",
                "source_label", "target_label", "examples", "status",
                "merged_into", "rejected_reason", "similar_labels",
                "first_seen_at", "last_seen_at", "decided_at", "decided_by"} <= cols

    def test_unique_constraint(self):
        idx = SchemaCandidateModel.__table__.indexes
        # (kb_id, candidate_type, label) unique — enforced via Index/constraint
        assert any(
            "kb_id" in [c.name for c in i.columns]
            and "label" in [c.name for c in i.columns]
            for i in idx
        ) or any(
            sorted([c.name for c in c.columns]) == sorted(["kb_id", "candidate_type", "label"])
            for c in SchemaCandidateModel.__table__.constraints
            if hasattr(c, "columns")
        )


class TestBootstrapRunModel:
    def test_has_required_columns(self):
        cols = {c.name for c in BootstrapRunModel.__table__.columns}
        assert {"id", "kb_id", "status", "triggered_by", "sample_size",
                "docs_scanned", "candidates_found", "started_at",
                "completed_at", "error_message"} <= cols


class TestReextractJobModel:
    def test_has_required_columns(self):
        cols = {c.name for c in ReextractJobModel.__table__.columns}
        assert {"id", "kb_id", "schema_version_from", "schema_version_to",
                "status", "docs_total", "docs_processed", "docs_failed",
                "queued_at"} <= cols
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/unit/test_schema_db_models.py -v --no-cov
```

Expected: `ImportError: cannot import name 'BootstrapRunModel' from 'src.stores.postgres.models'`

- [ ] **Step 3: Add models to `src/stores/postgres/models.py`**

Find where `KnowledgeBase` is imported (top of file) and where existing models are declared. Append new models (use existing style — `sa.Column`, `__tablename__`, etc.):

```python
# ============================================================================
# Graph schema evolution (Phase 3)
# ============================================================================

class SchemaCandidateModel(KnowledgeBase):
    """LLM-discovered entity/relationship type candidates (pre-approval).

    Spec §4.3. Unique on (kb_id, candidate_type, label).
    """

    __tablename__ = "graph_schema_candidates"
    __table_args__ = (
        sa.UniqueConstraint(
            "kb_id", "candidate_type", "label",
            name="uq_schema_candidates_kb_type_label",
        ),
        sa.Index("ix_schema_candidates_kb_status", "kb_id", "status", "frequency"),
        sa.Index(
            "ix_schema_candidates_pending", "status", "last_seen_at",
            postgresql_where=sa.text("status = 'pending'"),
        ),
    )

    id = sa.Column(
        postgresql.UUID(as_uuid=True),
        primary_key=True, server_default=sa.text("gen_random_uuid()"),
    )
    kb_id = sa.Column(sa.String(64), nullable=False)
    candidate_type = sa.Column(sa.String(16), nullable=False)  # 'node'|'relationship'
    label = sa.Column(sa.String(64), nullable=False)
    frequency = sa.Column(sa.Integer, nullable=False, default=1)
    confidence_avg = sa.Column(sa.Float, nullable=False)
    confidence_min = sa.Column(sa.Float, nullable=False)
    confidence_max = sa.Column(sa.Float, nullable=False)
    source_label = sa.Column(sa.String(64), nullable=True)
    target_label = sa.Column(sa.String(64), nullable=True)
    examples = sa.Column(postgresql.JSONB, nullable=False, default=list)
    status = sa.Column(sa.String(16), nullable=False, default="pending")
    merged_into = sa.Column(sa.String(64), nullable=True)
    rejected_reason = sa.Column(sa.Text, nullable=True)
    similar_labels = sa.Column(postgresql.JSONB, nullable=False, default=list)
    first_seen_at = sa.Column(sa.DateTime(timezone=True), server_default=sa.func.now())
    last_seen_at = sa.Column(sa.DateTime(timezone=True), server_default=sa.func.now())
    decided_at = sa.Column(sa.DateTime(timezone=True), nullable=True)
    decided_by = sa.Column(sa.String(128), nullable=True)


class BootstrapRunModel(KnowledgeBase):
    """Audit log + concurrent-safety anchor for schema bootstrap runs.

    Spec §4.3 + §6.5. `status='running'` + `started_at > now() - 1h` rows
    block concurrent runs for the same kb_id.
    """

    __tablename__ = "graph_schema_bootstrap_runs"
    __table_args__ = (
        sa.Index("ix_bootstrap_runs_kb_time", "kb_id", "started_at"),
        sa.Index(
            "ix_bootstrap_runs_running", "kb_id",
            postgresql_where=sa.text("status = 'running'"),
        ),
    )

    id = sa.Column(
        postgresql.UUID(as_uuid=True),
        primary_key=True, server_default=sa.text("gen_random_uuid()"),
    )
    kb_id = sa.Column(sa.String(64), nullable=False)
    status = sa.Column(sa.String(16), nullable=False)  # running|completed|failed|cancelled
    triggered_by = sa.Column(sa.String(32), nullable=False)  # cron|kb_create|manual|volume_threshold
    triggered_by_user = sa.Column(sa.String(128), nullable=True)
    sample_size = sa.Column(sa.Integer, nullable=False)
    sample_strategy = sa.Column(sa.String(16), nullable=False)  # stratified|random
    docs_scanned = sa.Column(sa.Integer, default=0)
    candidates_found = sa.Column(sa.Integer, default=0)
    llm_calls = sa.Column(sa.Integer, default=0)
    error_message = sa.Column(sa.Text, nullable=True)
    started_at = sa.Column(sa.DateTime(timezone=True), server_default=sa.func.now())
    completed_at = sa.Column(sa.DateTime(timezone=True), nullable=True)
    duration_ms = sa.Column(sa.Integer, nullable=True)


class ReextractJobModel(KnowledgeBase):
    """On-demand re-extract job queue (Phase 5 consumer).

    Phase 3 only creates the table so Phase 5 can push rows without a
    second DB migration.
    """

    __tablename__ = "graph_schema_reextract_jobs"
    __table_args__ = (
        sa.Index("ix_reextract_jobs_kb", "kb_id", "queued_at"),
    )

    id = sa.Column(
        postgresql.UUID(as_uuid=True),
        primary_key=True, server_default=sa.text("gen_random_uuid()"),
    )
    kb_id = sa.Column(sa.String(64), nullable=False)
    triggered_by_user = sa.Column(sa.String(128), nullable=False)
    schema_version_from = sa.Column(sa.Integer, nullable=False)
    schema_version_to = sa.Column(sa.Integer, nullable=False)
    status = sa.Column(sa.String(16), nullable=False, default="queued")
    docs_total = sa.Column(sa.Integer, nullable=True)
    docs_processed = sa.Column(sa.Integer, default=0)
    docs_failed = sa.Column(sa.Integer, default=0)
    error_message = sa.Column(sa.Text, nullable=True)
    started_at = sa.Column(sa.DateTime(timezone=True), nullable=True)
    completed_at = sa.Column(sa.DateTime(timezone=True), nullable=True)
    queued_at = sa.Column(sa.DateTime(timezone=True), server_default=sa.func.now())
```

Ensure `import sqlalchemy as sa` and `from sqlalchemy.dialects import postgresql` are present at the top of `models.py`.

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/unit/test_schema_db_models.py -v --no-cov
```

Expected: 4 passed.

- [ ] **Step 5: Lint**

```bash
uvx ruff check src/stores/postgres/models.py tests/unit/test_schema_db_models.py
```

- [ ] **Step 6: Commit**

```bash
git add src/stores/postgres/models.py tests/unit/test_schema_db_models.py
git commit -m "feat(postgres): Phase 3 schema evolution DB models

Adds SchemaCandidateModel, BootstrapRunModel, ReextractJobModel on
KnowledgeBase.metadata. Unique constraint (kb_id, candidate_type, label)
on candidates; partial index on status='running' on bootstrap runs for
cheap concurrent-safety check. JSONB for examples/similar_labels.

Spec §4.3.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: `SchemaCandidateRepo`

**Files:**
- Create: `src/stores/postgres/repositories/schema_candidate_repo.py`
- Test: `tests/unit/test_schema_candidate_repo.py`

- [ ] **Step 1: Write failing test (with in-memory-ish mock — unit scope)**

Create `tests/unit/test_schema_candidate_repo.py`:

```python
"""Unit tests for SchemaCandidateRepo — upsert + list_pending + decide.

Tests use a real SQLAlchemy engine pointed at SQLite in-memory; Phase 3
repo doesn't use PG-only features at the query level (JSONB is stored
but not queried by type).
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.stores.postgres.models import KnowledgeBase
from src.stores.postgres.repositories.schema_candidate_repo import (
    SchemaCandidateRepo,
)


@pytest.fixture
async def session_maker():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        # Only the three Phase-3 tables — avoid JSONB-incompatible defaults elsewhere
        from src.stores.postgres.models import SchemaCandidateModel
        await conn.run_sync(SchemaCandidateModel.__table__.create)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


class TestUpsert:
    @pytest.mark.asyncio
    async def test_insert_new_candidate(self, session_maker):
        repo = SchemaCandidateRepo(session_maker)
        await repo.upsert(
            kb_id="test",
            candidate_type="node",
            label="Meeting",
            confidence=0.9,
            examples=[{"doc_id": "d1", "sample": "월간 회의"}],
        )
        rows = await repo.list_pending("test")
        assert len(rows) == 1
        assert rows[0].label == "Meeting"
        assert rows[0].frequency == 1
        assert rows[0].confidence_avg == 0.9

    @pytest.mark.asyncio
    async def test_upsert_same_label_increments_frequency(self, session_maker):
        repo = SchemaCandidateRepo(session_maker)
        await repo.upsert(kb_id="test", candidate_type="node", label="Meeting",
                          confidence=0.8, examples=[])
        await repo.upsert(kb_id="test", candidate_type="node", label="Meeting",
                          confidence=0.9, examples=[])
        rows = await repo.list_pending("test")
        assert len(rows) == 1
        assert rows[0].frequency == 2
        assert rows[0].confidence_min == 0.8
        assert rows[0].confidence_max == 0.9
        assert abs(rows[0].confidence_avg - 0.85) < 0.01


class TestListApprovedLabels:
    @pytest.mark.asyncio
    async def test_lists_only_approved(self, session_maker):
        repo = SchemaCandidateRepo(session_maker)
        await repo.upsert(kb_id="test", candidate_type="node", label="Meeting",
                          confidence=0.9, examples=[])
        await repo.upsert(kb_id="test", candidate_type="node", label="Room",
                          confidence=0.9, examples=[])
        # Approve only Meeting
        await repo.decide(
            kb_id="test", candidate_type="node", label="Meeting",
            status="approved", decided_by="admin@test",
        )
        labels = await repo.list_approved_labels("test", "node")
        assert labels == ["Meeting"]
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/unit/test_schema_candidate_repo.py -v --no-cov
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Write the repo**

Create `src/stores/postgres/repositories/schema_candidate_repo.py`:

```python
"""CRUD + query helpers for graph_schema_candidates (Phase 3)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.stores.postgres.models import SchemaCandidateModel
from src.stores.postgres.repositories.base import BaseRepository


class SchemaCandidateRepo(BaseRepository):
    async def upsert(
        self,
        *,
        kb_id: str,
        candidate_type: str,
        label: str,
        confidence: float,
        examples: list[dict[str, Any]],
        source_label: str | None = None,
        target_label: str | None = None,
        similar_labels: list[dict[str, Any]] | None = None,
    ) -> None:
        """Insert or increment-frequency update keyed on (kb, type, label)."""
        async with self._session_maker() as session:
            async with session.begin():
                existing = await session.scalar(
                    select(SchemaCandidateModel).where(
                        SchemaCandidateModel.kb_id == kb_id,
                        SchemaCandidateModel.candidate_type == candidate_type,
                        SchemaCandidateModel.label == label,
                    ),
                )
                if existing is None:
                    session.add(SchemaCandidateModel(
                        kb_id=kb_id, candidate_type=candidate_type, label=label,
                        frequency=1,
                        confidence_avg=confidence,
                        confidence_min=confidence,
                        confidence_max=confidence,
                        source_label=source_label,
                        target_label=target_label,
                        examples=examples,
                        similar_labels=similar_labels or [],
                        status="pending",
                    ))
                else:
                    new_freq = existing.frequency + 1
                    new_avg = (
                        (existing.confidence_avg * existing.frequency + confidence)
                        / new_freq
                    )
                    existing.frequency = new_freq
                    existing.confidence_avg = new_avg
                    existing.confidence_min = min(existing.confidence_min, confidence)
                    existing.confidence_max = max(existing.confidence_max, confidence)
                    existing.last_seen_at = datetime.now(UTC)
                    # Keep at most 5 latest examples
                    existing.examples = (examples + list(existing.examples or []))[:5]

    async def list_pending(self, kb_id: str) -> list[SchemaCandidateModel]:
        async with self._session_maker() as session:
            result = await session.execute(
                select(SchemaCandidateModel).where(
                    SchemaCandidateModel.kb_id == kb_id,
                    SchemaCandidateModel.status == "pending",
                ).order_by(SchemaCandidateModel.frequency.desc()),
            )
            return list(result.scalars().all())

    async def list_approved_labels(
        self, kb_id: str, candidate_type: str,
    ) -> list[str]:
        async with self._session_maker() as session:
            result = await session.execute(
                select(SchemaCandidateModel.label).where(
                    SchemaCandidateModel.kb_id == kb_id,
                    SchemaCandidateModel.candidate_type == candidate_type,
                    SchemaCandidateModel.status == "approved",
                ),
            )
            return [row[0] for row in result.all()]

    async def decide(
        self,
        *,
        kb_id: str,
        candidate_type: str,
        label: str,
        status: str,  # approved|rejected|merged
        decided_by: str,
        merged_into: str | None = None,
        rejected_reason: str | None = None,
    ) -> None:
        async with self._session_maker() as session:
            async with session.begin():
                await session.execute(
                    update(SchemaCandidateModel).where(
                        SchemaCandidateModel.kb_id == kb_id,
                        SchemaCandidateModel.candidate_type == candidate_type,
                        SchemaCandidateModel.label == label,
                    ).values(
                        status=status,
                        decided_at=datetime.now(UTC),
                        decided_by=decided_by,
                        merged_into=merged_into,
                        rejected_reason=rejected_reason,
                    ),
                )


__all__ = ["SchemaCandidateRepo"]
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/unit/test_schema_candidate_repo.py -v --no-cov
```

Expected: 3 passed.

- [ ] **Step 5: Lint + commit**

```bash
uvx ruff check src/stores/postgres/repositories/schema_candidate_repo.py tests/unit/test_schema_candidate_repo.py
git add src/stores/postgres/repositories/schema_candidate_repo.py tests/unit/test_schema_candidate_repo.py
git commit -m "feat(postgres): SchemaCandidateRepo — upsert/list/decide

Upsert merges frequency + confidence running stats (min/avg/max);
list_pending sorts by frequency desc for admin review prioritization;
list_approved_labels feeds the bootstrap similarity check (avoid
re-proposing labels already merged into the schema); decide handles
all 3 terminal states (approved/rejected/merged).

Spec §6.3.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: `BootstrapRunRepo` + concurrent safety

**Files:**
- Create: `src/stores/postgres/repositories/bootstrap_run_repo.py`
- Test: `tests/unit/test_bootstrap_run_repo.py`

- [ ] **Step 1: Test first**

Create `tests/unit/test_bootstrap_run_repo.py`:

```python
"""BootstrapRunRepo — create / complete / has_running / cleanup_stale."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.stores.postgres.repositories.bootstrap_run_repo import BootstrapRunRepo


@pytest.fixture
async def session_maker():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        from src.stores.postgres.models import BootstrapRunModel
        await conn.run_sync(BootstrapRunModel.__table__.create)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


class TestCreateAndComplete:
    @pytest.mark.asyncio
    async def test_create_returns_id_and_status_running(self, session_maker):
        repo = BootstrapRunRepo(session_maker)
        run_id = await repo.create(
            kb_id="test", triggered_by="cron",
            sample_size=50, sample_strategy="stratified",
        )
        assert run_id is not None
        assert await repo.has_running("test") is True

    @pytest.mark.asyncio
    async def test_complete_clears_running(self, session_maker):
        repo = BootstrapRunRepo(session_maker)
        run_id = await repo.create(
            kb_id="test", triggered_by="cron",
            sample_size=50, sample_strategy="stratified",
        )
        await repo.complete(
            run_id, status="completed",
            docs_scanned=50, candidates_found=3, llm_calls=5,
        )
        assert await repo.has_running("test") is False


class TestStale:
    @pytest.mark.asyncio
    async def test_stale_running_not_counted(self, session_maker):
        """1h 지난 running row 는 concurrent 체크에서 무시."""
        repo = BootstrapRunRepo(session_maker)
        # Create + immediately override started_at to 2h ago
        run_id = await repo.create(
            kb_id="test", triggered_by="cron",
            sample_size=50, sample_strategy="stratified",
        )
        async with session_maker() as s:
            from sqlalchemy import update
            from src.stores.postgres.models import BootstrapRunModel
            old = datetime.now(UTC) - timedelta(hours=2)
            await s.execute(
                update(BootstrapRunModel).where(
                    BootstrapRunModel.id == run_id,
                ).values(started_at=old),
            )
            await s.commit()
        # Stale — should NOT block a new run
        assert await repo.has_running("test") is False
```

- [ ] **Step 2: Fail**

```bash
uv run pytest tests/unit/test_bootstrap_run_repo.py -v --no-cov
```

- [ ] **Step 3: Implementation**

Create `src/stores/postgres/repositories/bootstrap_run_repo.py`:

```python
"""BootstrapRunRepo — manage graph_schema_bootstrap_runs lifecycle."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.stores.postgres.models import BootstrapRunModel
from src.stores.postgres.repositories.base import BaseRepository


class BootstrapRunRepo(BaseRepository):
    async def create(
        self,
        *,
        kb_id: str,
        triggered_by: str,
        sample_size: int,
        sample_strategy: str,
        triggered_by_user: str | None = None,
    ) -> UUID:
        async with self._session_maker() as session:
            async with session.begin():
                run = BootstrapRunModel(
                    kb_id=kb_id, triggered_by=triggered_by,
                    sample_size=sample_size, sample_strategy=sample_strategy,
                    triggered_by_user=triggered_by_user,
                    status="running",
                )
                session.add(run)
                await session.flush()
                return run.id

    async def complete(
        self,
        run_id: UUID,
        *,
        status: str,  # completed|failed|cancelled
        docs_scanned: int = 0,
        candidates_found: int = 0,
        llm_calls: int = 0,
        error_message: str | None = None,
    ) -> None:
        async with self._session_maker() as session:
            async with session.begin():
                await session.execute(
                    update(BootstrapRunModel).where(
                        BootstrapRunModel.id == run_id,
                    ).values(
                        status=status,
                        docs_scanned=docs_scanned,
                        candidates_found=candidates_found,
                        llm_calls=llm_calls,
                        error_message=error_message,
                        completed_at=datetime.now(UTC),
                    ),
                )

    async def has_running(self, kb_id: str) -> bool:
        """Return True iff there's a non-stale 'running' row for this KB.

        Stale = started_at older than 1h — we assume those are crashed
        workers and don't block new attempts.
        """
        threshold = datetime.now(UTC) - timedelta(hours=1)
        async with self._session_maker() as session:
            row = await session.scalar(
                select(BootstrapRunModel.id).where(
                    BootstrapRunModel.kb_id == kb_id,
                    BootstrapRunModel.status == "running",
                    BootstrapRunModel.started_at > threshold,
                ).limit(1),
            )
            return row is not None

    async def cleanup_stale(self) -> int:
        """Mark stale 'running' rows (>1h) as 'failed'. Run daily."""
        threshold = datetime.now(UTC) - timedelta(hours=1)
        async with self._session_maker() as session:
            async with session.begin():
                result = await session.execute(
                    update(BootstrapRunModel).where(
                        BootstrapRunModel.status == "running",
                        BootstrapRunModel.started_at <= threshold,
                    ).values(
                        status="failed",
                        error_message="stale — exceeded 1h timeout",
                        completed_at=datetime.now(UTC),
                    ),
                )
                return result.rowcount or 0


__all__ = ["BootstrapRunRepo"]
```

- [ ] **Step 4: Tests + lint + commit**

```bash
uv run pytest tests/unit/test_bootstrap_run_repo.py -v --no-cov
uvx ruff check src/stores/postgres/repositories/bootstrap_run_repo.py tests/unit/test_bootstrap_run_repo.py
git add src/stores/postgres/repositories/bootstrap_run_repo.py tests/unit/test_bootstrap_run_repo.py
git commit -m "feat(postgres): BootstrapRunRepo + stale-lock cleanup

create/complete/has_running/cleanup_stale. has_running honors a 1h
stale threshold so a crashed worker doesn't permanently block the KB.
cleanup_stale() runs daily via cron to flip leaked 'running' rows to
'failed'.

Spec §6.5.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: LLM discovery prompt + strict parser

**Files:**
- Create: `src/pipelines/graphrag/schema_prompts.py`
- Test: `tests/unit/test_schema_discovery_prompt.py`

- [ ] **Step 1: Test first**

Create `tests/unit/test_schema_discovery_prompt.py`:

```python
"""Schema discovery prompt + strict JSON parser."""

from __future__ import annotations

import pytest

from src.pipelines.graphrag.schema_prompts import (
    SCHEMA_DISCOVERY_PROMPT,
    parse_discovery_response,
)


class TestPromptShape:
    def test_prompt_contains_all_placeholders(self):
        # .format() must accept all 5 named placeholders
        filled = SCHEMA_DISCOVERY_PROMPT.format(
            kb_id="test",
            n=3,
            existing_nodes="Person, Team",
            existing_rels="MEMBER_OF",
            docs="[doc 1] content",
        )
        assert "test" in filled
        assert "Person, Team" in filled


class TestParser:
    def test_valid_json_parsed(self):
        raw = '''
        {"new_node_types":[
           {"label":"Meeting","reason":"x","confidence":0.9,"examples":["sample"]}
        ],"new_relation_types":[]}
        '''
        out = parse_discovery_response(raw)
        assert len(out.node_candidates) == 1
        assert out.node_candidates[0].label == "Meeting"
        assert out.node_candidates[0].confidence == 0.9
        assert out.relation_candidates == []

    def test_code_fence_stripped(self):
        raw = '```json\n{"new_node_types":[],"new_relation_types":[]}\n```'
        out = parse_discovery_response(raw)
        assert out.node_candidates == []

    def test_malformed_raises_valueerror(self):
        with pytest.raises(ValueError):
            parse_discovery_response("not json")

    def test_missing_label_rejected(self):
        raw = '{"new_node_types":[{"confidence":0.9}],"new_relation_types":[]}'
        out = parse_discovery_response(raw)
        # Silently drop malformed entries — don't raise on individual bad items
        assert out.node_candidates == []
```

- [ ] **Step 2: Fail → implement**

Create `src/pipelines/graphrag/schema_prompts.py`:

```python
"""LLM prompt + strict parser for schema discovery (Phase 3 bootstrap).

Spec §6.6.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)


SCHEMA_DISCOVERY_PROMPT = """다음은 KB "{kb_id}" 의 샘플 문서 {n}개입니다.
이 도메인의 지식 그래프에 적합한 신규 entity/relationship 타입을 제안하세요.

### 이미 확정된 타입 (중복 제안 금지, 영문 label)
- Entity: {existing_nodes}
- Relationship: {existing_rels}

### 판단 기준
- 문서 2개 이상에 등장하는 개념만 제안
- 기존 타입으로 충분히 커버되면 신규 제안 금지
- Confidence 0.0~1.0:
    0.95 = 여러 문서에 일관되게 등장
    0.85 = 등장은 하나 약간 모호
    0.70 = 1~2 문서만 언급

### 샘플 문서
{docs}

### 출력 (JSON 만, 다른 텍스트 금지)
{{"new_node_types":[{{"label":"<CamelCase>","reason":"<한 문장>","confidence":0.92,"examples":["<원문 구절>"]}}],"new_relation_types":[{{"label":"<SCREAMING_SNAKE>","source":"<Entity>","target":"<Entity>","reason":"<한 문장>","confidence":0.9,"examples":["<원문 구절>"]}}]}}
"""


_LABEL_NODE_RE = re.compile(r"^[A-Z][a-zA-Z0-9_]{0,63}$")
_LABEL_REL_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")


@dataclass(frozen=True)
class NodeCandidate:
    label: str
    confidence: float
    examples: tuple[str, ...]
    reason: str = ""


@dataclass(frozen=True)
class RelationCandidate:
    label: str
    source: str
    target: str
    confidence: float
    examples: tuple[str, ...]
    reason: str = ""


@dataclass(frozen=True)
class DiscoveryResponse:
    node_candidates: list[NodeCandidate]
    relation_candidates: list[RelationCandidate]


def parse_discovery_response(raw: str) -> DiscoveryResponse:
    """Strict parse. Strips code fences; drops malformed individual entries.

    Raises ValueError if the top-level JSON is unparseable.
    """
    # Strip code fences if present
    if "```" in raw:
        m = re.search(r"```(?:json)?\s*\n?(.*?)```", raw, re.DOTALL)
        if m:
            raw = m.group(1).strip()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Discovery response not JSON: {exc}") from exc

    node_cands: list[NodeCandidate] = []
    for item in data.get("new_node_types") or []:
        try:
            label = str(item["label"])
            if not _LABEL_NODE_RE.match(label):
                continue
            conf = float(item.get("confidence", 0.0))
            examples = tuple(str(e) for e in (item.get("examples") or []))
            node_cands.append(NodeCandidate(
                label=label, confidence=conf, examples=examples,
                reason=str(item.get("reason", "")),
            ))
        except (KeyError, ValueError, TypeError):
            continue

    rel_cands: list[RelationCandidate] = []
    for item in data.get("new_relation_types") or []:
        try:
            label = str(item["label"])
            if not _LABEL_REL_RE.match(label):
                continue
            conf = float(item.get("confidence", 0.0))
            examples = tuple(str(e) for e in (item.get("examples") or []))
            rel_cands.append(RelationCandidate(
                label=label,
                source=str(item.get("source", "")),
                target=str(item.get("target", "")),
                confidence=conf, examples=examples,
                reason=str(item.get("reason", "")),
            ))
        except (KeyError, ValueError, TypeError):
            continue

    return DiscoveryResponse(
        node_candidates=node_cands,
        relation_candidates=rel_cands,
    )


__all__ = [
    "DiscoveryResponse",
    "NodeCandidate",
    "RelationCandidate",
    "SCHEMA_DISCOVERY_PROMPT",
    "parse_discovery_response",
]
```

- [ ] **Step 3: Tests + lint + commit**

```bash
uv run pytest tests/unit/test_schema_discovery_prompt.py -v --no-cov
uvx ruff check src/pipelines/graphrag/schema_prompts.py tests/unit/test_schema_discovery_prompt.py
git add src/pipelines/graphrag/schema_prompts.py tests/unit/test_schema_discovery_prompt.py
git commit -m "feat(graphrag): SCHEMA_DISCOVERY_PROMPT + strict parser

LLM prompt template (5 placeholders) + DiscoveryResponse dataclass +
strict JSON parser that tolerates code-fence wrappers and silently
drops malformed individual entries while raising on top-level JSON
decode errors. Label regexes enforce Cypher-identifier safety at
parse time.

Spec §6.6.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: `SchemaBootstrapper` — orchestration

**Files:**
- Create: `src/pipelines/graphrag/schema_bootstrap.py`
- Test: `tests/unit/test_schema_bootstrap.py`

- [ ] **Step 1: Test first** — focus on the orchestration contract, not the LLM or DB specifics.

Create `tests/unit/test_schema_bootstrap.py`:

```python
"""SchemaBootstrapper — orchestration contract tests (mocked deps)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.pipelines.graphrag.schema_bootstrap import (
    BootstrapAlreadyRunning,
    BootstrapConfig,
    SchemaBootstrapper,
)


@pytest.fixture
def mock_llm():
    llm = MagicMock()
    # Default: empty discovery response
    llm.invoke = MagicMock(return_value='{"new_node_types":[],"new_relation_types":[]}')
    return llm


@pytest.fixture
def mock_candidate_repo():
    repo = AsyncMock()
    repo.upsert = AsyncMock()
    repo.list_approved_labels = AsyncMock(return_value=[])
    return repo


@pytest.fixture
def mock_run_repo():
    repo = AsyncMock()
    repo.has_running = AsyncMock(return_value=False)
    repo.create = AsyncMock(return_value=uuid4())
    repo.complete = AsyncMock()
    return repo


@pytest.fixture
def mock_sampler():
    sampler = AsyncMock()
    sampler.sample = AsyncMock(return_value=[
        {"doc_id": "d1", "content": "doc 1 content", "source_type": "confluence"},
        {"doc_id": "d2", "content": "doc 2 content", "source_type": "confluence"},
    ])
    return sampler


class TestConcurrentGuard:
    @pytest.mark.asyncio
    async def test_already_running_raises(
        self, mock_llm, mock_candidate_repo, mock_run_repo, mock_sampler,
    ):
        mock_run_repo.has_running = AsyncMock(return_value=True)
        bs = SchemaBootstrapper(
            llm=mock_llm,
            candidate_repo=mock_candidate_repo,
            run_repo=mock_run_repo,
            sampler=mock_sampler,
        )
        with pytest.raises(BootstrapAlreadyRunning):
            await bs.run(kb_id="test", triggered_by="manual")
        mock_run_repo.create.assert_not_called()


class TestHappyPath:
    @pytest.mark.asyncio
    async def test_discovered_node_upserted(
        self, mock_llm, mock_candidate_repo, mock_run_repo, mock_sampler,
    ):
        mock_llm.invoke = MagicMock(return_value=(
            '{"new_node_types":['
            '{"label":"Meeting","confidence":0.9,"examples":["sample"]}'
            '],"new_relation_types":[]}'
        ))
        bs = SchemaBootstrapper(
            llm=mock_llm,
            candidate_repo=mock_candidate_repo,
            run_repo=mock_run_repo,
            sampler=mock_sampler,
        )
        run_id = await bs.run(kb_id="test", triggered_by="manual")
        assert run_id is not None
        mock_candidate_repo.upsert.assert_awaited()
        upsert_kwargs = mock_candidate_repo.upsert.await_args.kwargs
        assert upsert_kwargs["label"] == "Meeting"
        assert upsert_kwargs["kb_id"] == "test"

    @pytest.mark.asyncio
    async def test_below_threshold_skipped(
        self, mock_llm, mock_candidate_repo, mock_run_repo, mock_sampler,
    ):
        mock_llm.invoke = MagicMock(return_value=(
            '{"new_node_types":['
            '{"label":"Weak","confidence":0.5,"examples":[]}'
            '],"new_relation_types":[]}'
        ))
        cfg = BootstrapConfig(confidence_threshold=0.8)
        bs = SchemaBootstrapper(
            llm=mock_llm,
            candidate_repo=mock_candidate_repo,
            run_repo=mock_run_repo,
            sampler=mock_sampler,
        )
        await bs.run(kb_id="test", triggered_by="manual", config=cfg)
        mock_candidate_repo.upsert.assert_not_called()


class TestRunCompletion:
    @pytest.mark.asyncio
    async def test_failure_marks_run_failed(
        self, mock_llm, mock_candidate_repo, mock_run_repo, mock_sampler,
    ):
        # Force the sampler to blow up
        mock_sampler.sample = AsyncMock(side_effect=RuntimeError("no docs"))
        bs = SchemaBootstrapper(
            llm=mock_llm,
            candidate_repo=mock_candidate_repo,
            run_repo=mock_run_repo,
            sampler=mock_sampler,
        )
        with pytest.raises(RuntimeError):
            await bs.run(kb_id="test", triggered_by="manual")
        complete_call = mock_run_repo.complete.await_args
        assert complete_call.kwargs["status"] == "failed"
```

- [ ] **Step 2: Fail → implement**

Create `src/pipelines/graphrag/schema_bootstrap.py`:

```python
"""Schema bootstrap — LLM-based discovery of new entity/relation types.

Contract: inject dependencies (llm / candidate_repo / run_repo / sampler)
so unit tests can mock each. Real wiring happens in the arq job.

Spec §6.3.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from src.pipelines.graphrag.schema_prompts import (
    SCHEMA_DISCOVERY_PROMPT,
    DiscoveryResponse,
    parse_discovery_response,
)

logger = logging.getLogger(__name__)


@dataclass
class BootstrapConfig:
    sample_size: int = 100
    min_per_source: int = 5
    confidence_threshold: float = 0.8
    similarity_threshold: float = 0.7
    batch_size: int = 10
    doc_preview_chars: int = 1500


class DocSampler(Protocol):
    async def sample(
        self, *, kb_id: str, sample_size: int,
    ) -> list[dict[str, Any]]:
        """Return [{doc_id, content, source_type}, ...]."""


class LLMClient(Protocol):
    def invoke(self, *, document: str, prompt_template: str) -> str:
        ...


class CandidateRepoProto(Protocol):
    async def upsert(
        self, *,
        kb_id: str, candidate_type: str, label: str,
        confidence: float, examples: list[dict[str, Any]],
        source_label: str | None = None, target_label: str | None = None,
        similar_labels: list[dict[str, Any]] | None = None,
    ) -> None: ...

    async def list_approved_labels(
        self, kb_id: str, candidate_type: str,
    ) -> list[str]: ...


class RunRepoProto(Protocol):
    async def has_running(self, kb_id: str) -> bool: ...
    async def create(
        self, *,
        kb_id: str, triggered_by: str,
        sample_size: int, sample_strategy: str,
        triggered_by_user: str | None = None,
    ) -> UUID: ...
    async def complete(
        self, run_id: UUID, *,
        status: str,
        docs_scanned: int = 0, candidates_found: int = 0,
        llm_calls: int = 0, error_message: str | None = None,
    ) -> None: ...


class BootstrapAlreadyRunning(RuntimeError):
    pass


class SchemaBootstrapper:
    def __init__(
        self,
        *,
        llm: LLMClient,
        candidate_repo: CandidateRepoProto,
        run_repo: RunRepoProto,
        sampler: DocSampler,
    ) -> None:
        self.llm = llm
        self.candidates = candidate_repo
        self.runs = run_repo
        self.sampler = sampler

    async def run(
        self,
        *,
        kb_id: str,
        triggered_by: str,
        triggered_by_user: str | None = None,
        config: BootstrapConfig | None = None,
    ) -> UUID:
        cfg = config or BootstrapConfig()

        if await self.runs.has_running(kb_id):
            raise BootstrapAlreadyRunning(
                f"Bootstrap already running for kb_id={kb_id}",
            )

        run_id = await self.runs.create(
            kb_id=kb_id, triggered_by=triggered_by,
            sample_size=cfg.sample_size, sample_strategy="stratified",
            triggered_by_user=triggered_by_user,
        )

        try:
            docs = await self.sampler.sample(
                kb_id=kb_id, sample_size=cfg.sample_size,
            )
            if not docs:
                await self.runs.complete(
                    run_id, status="completed",
                    docs_scanned=0, candidates_found=0, llm_calls=0,
                )
                return run_id

            existing_nodes = await self.candidates.list_approved_labels(
                kb_id, "node",
            )
            existing_rels = await self.candidates.list_approved_labels(
                kb_id, "relationship",
            )

            candidates_found = 0
            llm_calls = 0
            for i in range(0, len(docs), cfg.batch_size):
                batch = docs[i : i + cfg.batch_size]
                prompt = SCHEMA_DISCOVERY_PROMPT.format(
                    kb_id=kb_id,
                    n=len(batch),
                    existing_nodes=", ".join(existing_nodes) or "(none)",
                    existing_rels=", ".join(existing_rels) or "(none)",
                    docs="\n\n---\n\n".join(
                        f"[doc {j+1}] {d['content'][:cfg.doc_preview_chars]}"
                        for j, d in enumerate(batch)
                    ),
                )
                try:
                    raw = self.llm.invoke(document="", prompt_template=prompt)
                    llm_calls += 1
                    response = parse_discovery_response(raw)
                except (RuntimeError, ValueError) as exc:
                    logger.warning(
                        "Bootstrap batch %d failed (kb=%s): %s",
                        i // cfg.batch_size, kb_id, exc,
                    )
                    continue

                candidates_found += await self._upsert_candidates(
                    kb_id, response, cfg,
                )

            await self.runs.complete(
                run_id, status="completed",
                docs_scanned=len(docs),
                candidates_found=candidates_found,
                llm_calls=llm_calls,
            )
            return run_id

        except Exception as exc:
            logger.exception("Bootstrap failed for %s", kb_id)
            await self.runs.complete(
                run_id, status="failed", error_message=str(exc),
            )
            raise

    async def _upsert_candidates(
        self,
        kb_id: str,
        response: DiscoveryResponse,
        cfg: BootstrapConfig,
    ) -> int:
        count = 0
        for cand in response.node_candidates:
            if cand.confidence < cfg.confidence_threshold:
                continue
            await self.candidates.upsert(
                kb_id=kb_id, candidate_type="node", label=cand.label,
                confidence=cand.confidence,
                examples=[{"sample": ex} for ex in cand.examples],
            )
            count += 1
        for cand in response.relation_candidates:
            if cand.confidence < cfg.confidence_threshold:
                continue
            await self.candidates.upsert(
                kb_id=kb_id, candidate_type="relationship",
                label=cand.label, confidence=cand.confidence,
                source_label=cand.source or None,
                target_label=cand.target or None,
                examples=[{"sample": ex} for ex in cand.examples],
            )
            count += 1
        return count


__all__ = [
    "BootstrapAlreadyRunning",
    "BootstrapConfig",
    "CandidateRepoProto",
    "DocSampler",
    "LLMClient",
    "RunRepoProto",
    "SchemaBootstrapper",
]
```

- [ ] **Step 3: Tests + lint + commit**

```bash
uv run pytest tests/unit/test_schema_bootstrap.py -v --no-cov
uvx ruff check src/pipelines/graphrag/schema_bootstrap.py tests/unit/test_schema_bootstrap.py
git add src/pipelines/graphrag/schema_bootstrap.py tests/unit/test_schema_bootstrap.py
git commit -m "feat(graphrag): SchemaBootstrapper — LLM-driven discovery

Dependency-injected orchestrator: takes an LLMClient, CandidateRepo,
RunRepo, DocSampler (all Protocol-typed). run() flow:
  1. Bail on concurrent run (BootstrapAlreadyRunning)
  2. Create run row
  3. Sample docs (delegated to sampler)
  4. Batch LLM prompt + parse + threshold filter + upsert
  5. Complete the run row (success or fail with error_message)

Failure path always marks the run 'failed' before re-raising so
monitoring can alert on repeat failures.

Spec §6.3.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: arq job wiring + cron registration

**Files:**
- Create: `src/jobs/schema_bootstrap_jobs.py`
- Modify: `src/jobs/worker.py` (register cron)
- Modify: `src/jobs/tasks.py` (register task)

The real DocSampler isn't available yet (it would need Qdrant integration);
Phase 3 ships the job plumbing and a stub sampler that reads from Qdrant
via the existing infra. Since wiring Qdrant+PG here is beyond the unit-test
scope, we'll expose the task and mock the wiring for a smoke test.

- [ ] **Step 1: Create the job module**

Create `src/jobs/schema_bootstrap_jobs.py`:

```python
"""arq tasks for GraphRAG schema bootstrap (Phase 3).

Tasks:
- schema_bootstrap_run(ctx, kb_id, triggered_by='cron'): one-shot bootstrap
- schema_bootstrap_cleanup(ctx): daily cron to clear stale 'running' rows
- schema_bootstrap_cron_all_kbs(ctx): daily cron — iterate active KBs
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def schema_bootstrap_run(
    _ctx: dict[str, Any],
    kb_id: str,
    triggered_by: str = "cron",
    triggered_by_user: str | None = None,
) -> dict[str, Any]:
    """Run one bootstrap iteration for the given KB.

    Real implementation wires SchemaBootstrapper with:
    - LLM client from app state
    - CandidateRepo / BootstrapRunRepo from session_maker
    - DocSampler that queries Qdrant by kb collection

    Phase 3 ships the task skeleton; full wiring lands alongside the
    Phase 4 admin trigger endpoint so both use the same injection.
    """
    from src.pipelines.graphrag.schema_bootstrap import (
        BootstrapAlreadyRunning,
        SchemaBootstrapper,
    )
    from src.stores.postgres.repositories.bootstrap_run_repo import BootstrapRunRepo
    from src.stores.postgres.repositories.schema_candidate_repo import (
        SchemaCandidateRepo,
    )

    # Resolve dependencies from process-level registry. The actual LLM + sampler
    # come from the app-state; for Phase 3 we raise NotImplementedError if the
    # production bindings aren't present — CI can still verify the import
    # graph and the code path above compiles.
    app = _ctx.get("app_state")
    if app is None or not hasattr(app, "llm") or not hasattr(app, "session_maker"):
        raise NotImplementedError(
            "schema_bootstrap_run requires ctx['app_state'] with llm + "
            "session_maker + doc_sampler bindings. Wire via worker startup.",
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
            kb_id=kb_id, triggered_by=triggered_by,
            triggered_by_user=triggered_by_user,
        )
        return {"status": "ok", "run_id": str(run_id)}
    except BootstrapAlreadyRunning:
        logger.info("Bootstrap skip — already running: kb_id=%s", kb_id)
        return {"status": "skip", "reason": "already_running"}


async def schema_bootstrap_cleanup(_ctx: dict[str, Any]) -> dict[str, Any]:
    """Daily cron — mark stale 'running' rows as 'failed'."""
    from src.stores.postgres.repositories.bootstrap_run_repo import BootstrapRunRepo

    app = _ctx.get("app_state")
    if app is None or not hasattr(app, "session_maker"):
        raise NotImplementedError("cleanup requires app_state.session_maker")

    run_repo = BootstrapRunRepo(app.session_maker)
    cleared = await run_repo.cleanup_stale()
    return {"status": "ok", "cleared": cleared}


__all__ = ["schema_bootstrap_run", "schema_bootstrap_cleanup"]
```

- [ ] **Step 2: Register task in `src/jobs/tasks.py`**

```bash
grep -n "REGISTERED_TASKS" src/jobs/tasks.py
```

Add the import at the top of `tasks.py`:

```python
from src.jobs.schema_bootstrap_jobs import (
    schema_bootstrap_cleanup,
    schema_bootstrap_run,
)
```

And extend `REGISTERED_TASKS`:

```python
REGISTERED_TASKS = [
    # ... existing tasks
    schema_bootstrap_run,
    schema_bootstrap_cleanup,
]
```

- [ ] **Step 3: Register cron in `src/jobs/worker.py`**

Find existing `cron_jobs` list (or create one if absent). Add:

```python
cron(
    schema_bootstrap_cleanup,
    hour={3},  # daily 03:00 UTC
    minute={0},
    run_at_startup=False,
),
```

If multi-KB cron orchestration is needed here, defer to a thin wrapper that iterates `kb_registry` — for Phase 3 we only ship the cleanup cron; bootstrap is manual-trigger.

- [ ] **Step 4: Smoke test — registration only**

```bash
uv run python -c "
from src.jobs.schema_bootstrap_jobs import schema_bootstrap_run, schema_bootstrap_cleanup
print('schema_bootstrap_run:', schema_bootstrap_run.__name__)
print('schema_bootstrap_cleanup:', schema_bootstrap_cleanup.__name__)
"
```

- [ ] **Step 5: Lint + commit**

```bash
uvx ruff check src/jobs/schema_bootstrap_jobs.py src/jobs/worker.py src/jobs/tasks.py
git add src/jobs/schema_bootstrap_jobs.py src/jobs/worker.py src/jobs/tasks.py
git commit -m "feat(jobs): register schema bootstrap arq tasks + cleanup cron

schema_bootstrap_run — one-shot bootstrap per kb_id (triggered_by=
'cron'|'manual'|'kb_create'). Wires SchemaBootstrapper with deps from
ctx.app_state; raises NotImplementedError if bindings missing so the
Phase 4 admin endpoint work can fill the gap explicitly.

schema_bootstrap_cleanup — daily 03:00 UTC cron to clear stale 'running'
rows older than 1h.

Spec §6.5.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Full regression + coverage

- [ ] **Step 1: Full Phase 3 suite**

```bash
uv run pytest \
  tests/unit/test_schema_db_models.py \
  tests/unit/test_schema_candidate_repo.py \
  tests/unit/test_bootstrap_run_repo.py \
  tests/unit/test_schema_discovery_prompt.py \
  tests/unit/test_schema_bootstrap.py \
  -q --no-cov 2>&1 | tail -5
```

- [ ] **Step 2: Regression (Phase 1 + 2 tests)**

```bash
uv run pytest \
  tests/unit/test_schema_types.py \
  tests/unit/test_schema_resolver.py \
  tests/unit/test_source_defaults.py \
  tests/unit/test_schema_migration.py \
  tests/unit/test_graphrag_prompts_facade.py \
  tests/unit/test_dynamic_schema.py \
  tests/unit/test_extractor_schema_integration.py \
  tests/unit/test_graphrag_full.py \
  tests/unit/test_graphrag_coverage.py \
  tests/unit/test_graphrag_extractor_backfill.py \
  -q --no-cov 2>&1 | tail -5
```

Both should pass cleanly.

- [ ] **Step 3: Coverage for Phase 3 new modules ≥ 80%**

```bash
uv run pytest \
  tests/unit/test_schema_candidate_repo.py \
  tests/unit/test_bootstrap_run_repo.py \
  tests/unit/test_schema_discovery_prompt.py \
  tests/unit/test_schema_bootstrap.py \
  --cov=src.stores.postgres.repositories.schema_candidate_repo \
  --cov=src.stores.postgres.repositories.bootstrap_run_repo \
  --cov=src.pipelines.graphrag.schema_prompts \
  --cov=src.pipelines.graphrag.schema_bootstrap \
  --cov-report=term-missing --no-cov-on-fail 2>&1 | tail -10
```

Target: each module ≥ 80%.

- [ ] **Step 4: Ruff clean on every file touched by Phase 3**

```bash
uvx ruff check \
  src/stores/postgres/models.py \
  src/stores/postgres/repositories/schema_candidate_repo.py \
  src/stores/postgres/repositories/bootstrap_run_repo.py \
  src/pipelines/graphrag/schema_prompts.py \
  src/pipelines/graphrag/schema_bootstrap.py \
  src/jobs/schema_bootstrap_jobs.py \
  src/jobs/worker.py src/jobs/tasks.py \
  tests/unit/test_schema_db_models.py \
  tests/unit/test_schema_candidate_repo.py \
  tests/unit/test_bootstrap_run_repo.py \
  tests/unit/test_schema_discovery_prompt.py \
  tests/unit/test_schema_bootstrap.py
```

---

## Spec coverage

| Spec | Task |
|---|---|
| §4.3 DB tables | 1 |
| §6.3 SchemaBootstrapper | 5 |
| §6.5 concurrency | 3, 5 |
| §6.6 discovery prompt | 4 |
| §10 Phase 3 | All tasks |

**Deferred to Phase 4+**: admin API endpoints (approve/reject/merge/rename);
admin UI; YAML auto-commit on approve; `DocSampler` production impl (Qdrant
query); re-extract job consumer.
