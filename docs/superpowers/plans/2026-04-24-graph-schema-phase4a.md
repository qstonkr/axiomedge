# GraphRAG Schema Evolution — Phase 4a Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans.

**Goal:** Admin API that lets a human review Phase-3 candidates and approve → YAML auto-commit. Backend only; the Next.js UI ships in Phase 4b.

**Architecture:** New `/api/v1/admin/graph-schema/*` route family. Approve flow: read candidate row → merge label into `deploy/config/graph_schemas/<kb_id>.yaml` (atomic temp-file write) → commit to a new branch via `GitPython` (installed dep) → create PR via `gh` CLI (optional — guarded by env). Resolver cache invalidation piggybacks on YAML mtime.

**Spec reference:** `docs/superpowers/specs/2026-04-24-graph-schema-evolution-design.md` §5.2 (admin UI), §5.3 (YAML auto-PR flow), §6.1 (graph_schema.py), §6.6 (merge semantics).

**Out of scope (later):** Next.js UI (Phase 4b), SlackBot notifications, re-extract job trigger button (Phase 5).

---

## File Structure

### New files

| Path | Responsibility |
|---|---|
| `src/api/routes/graph_schema_helpers.py` | YAML atomic writer + git commit wrapper |
| `src/api/routes/graph_schema.py` | 5 endpoints: list, approve, reject, merge, rename, bootstrap trigger |
| `tests/unit/test_graph_schema_helpers.py` | YAML merge + git stub |
| `tests/unit/test_graph_schema_routes.py` | Route handlers (TestClient + mocked repo + helpers) |

### Modified

| Path | Change |
|---|---|
| `src/api/app.py` (or route registration point) | include_router for new module |

---

## Task 1: YAML writer helper

**Files:** `src/api/routes/graph_schema_helpers.py` + `tests/unit/test_graph_schema_helpers.py`

- [ ] **Step 1: Write failing test**

Create `tests/unit/test_graph_schema_helpers.py`:

```python
"""Tests for YAML writer + label merge + (stubbed) git commit."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from src.api.routes.graph_schema_helpers import (
    merge_label_into_yaml,
)


@pytest.fixture
def kb_yaml_dir(tmp_path, monkeypatch):
    d = tmp_path / "graph_schemas"
    d.mkdir()
    monkeypatch.setattr(
        "src.api.routes.graph_schema_helpers._SCHEMA_DIR", d,
    )
    return d


class TestMergeLabelIntoYaml:
    def test_adds_new_node_label(self, kb_yaml_dir: Path):
        (kb_yaml_dir / "test.yaml").write_text(
            "version: 1\nprompt_focus: x\n"
            "nodes: [Person]\nrelationships: [MEMBER_OF]\n"
            "options:\n  disable_bootstrap: false\n"
        )
        path = merge_label_into_yaml(
            kb_id="test", candidate_type="node", label="Meeting",
            approved_by="admin@test",
        )
        assert path.name == "test.yaml"
        data = yaml.safe_load(path.read_text())
        assert "Meeting" in data["nodes"]
        assert "Person" in data["nodes"]  # preserved
        assert data["version"] == 2
        # _metadata appended
        assert data["_metadata"]["last_approved_by"] == "admin@test"
        assert any(
            e["label"] == "Meeting" and e["type"] == "node"
            for e in data["_metadata"]["approved_candidates"]
        )

    def test_adds_new_relationship(self, kb_yaml_dir: Path):
        (kb_yaml_dir / "test.yaml").write_text(
            "version: 1\nprompt_focus: x\n"
            "nodes: [Person]\nrelationships: [MEMBER_OF]\n"
        )
        path = merge_label_into_yaml(
            kb_id="test", candidate_type="relationship", label="ATTENDED",
            approved_by="admin@test",
        )
        data = yaml.safe_load(path.read_text())
        assert "ATTENDED" in data["relationships"]

    def test_idempotent_when_label_already_present(self, kb_yaml_dir: Path):
        (kb_yaml_dir / "test.yaml").write_text(
            "version: 3\nprompt_focus: x\n"
            "nodes: [Person, Meeting]\nrelationships: []\n"
        )
        merge_label_into_yaml(
            kb_id="test", candidate_type="node", label="Meeting",
            approved_by="admin@test",
        )
        data = yaml.safe_load((kb_yaml_dir / "test.yaml").read_text())
        # nodes still have Meeting exactly once
        assert data["nodes"].count("Meeting") == 1

    def test_creates_new_yaml_for_unknown_kb(self, kb_yaml_dir: Path):
        """If a KB has no YAML yet, create it with the new label as seed."""
        path = merge_label_into_yaml(
            kb_id="brand-new", candidate_type="node", label="Topic",
            approved_by="admin@test",
        )
        assert path.exists()
        data = yaml.safe_load(path.read_text())
        assert data["kb_id"] == "brand-new"
        assert "Topic" in data["nodes"]
        assert data["version"] == 1
```

- [ ] **Step 2: Fail → implement**

Create `src/api/routes/graph_schema_helpers.py`:

```python
"""Helpers for graph_schema admin API: atomic YAML writer + git wrapper.

Spec §5.3 (YAML auto-PR flow).
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import yaml

logger = logging.getLogger(__name__)

_SCHEMA_DIR = Path("deploy/config/graph_schemas")
_DATE_ISO_FMT = "%Y-%m-%dT%H:%M:%SZ"


def merge_label_into_yaml(
    *,
    kb_id: str,
    candidate_type: Literal["node", "relationship"],
    label: str,
    approved_by: str,
) -> Path:
    """Add ``label`` to the KB's YAML, bump version, record metadata.

    - Atomic: write to temp file, then rename.
    - Idempotent: re-approving an existing label is a no-op for the list.
    - If the KB has no YAML file yet, create one seeded with this label.

    Returns the path to the updated YAML.
    """
    _SCHEMA_DIR.mkdir(parents=True, exist_ok=True)
    path = _SCHEMA_DIR / f"{kb_id}.yaml"

    if path.exists():
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    else:
        data = {
            "version": 0,  # bumped to 1 below
            "kb_id": kb_id,
            "prompt_focus": "",
            "nodes": [],
            "relationships": [],
            "options": {
                "disable_bootstrap": False,
                "schema_evolution": "batch",
                "bootstrap_sample_size": 100,
            },
        }

    key = "nodes" if candidate_type == "node" else "relationships"
    labels: list[str] = list(data.get(key) or [])
    if label not in labels:
        labels.append(label)
        labels.sort()
        data[key] = labels

    data["version"] = int(data.get("version", 0)) + 1

    meta = data.setdefault("_metadata", {})
    meta["last_approved_at"] = datetime.now(UTC).strftime(_DATE_ISO_FMT)
    meta["last_approved_by"] = approved_by
    approved_list = meta.setdefault("approved_candidates", [])
    entry = {
        "label": label,
        "type": candidate_type,
        "version_added": data["version"],
    }
    # Avoid duplicating history entries for idempotent re-approves
    if entry not in approved_list:
        approved_list.append(entry)

    _atomic_write_yaml(path, data)
    return path


def _atomic_write_yaml(path: Path, data: dict[str, Any]) -> None:
    """Write YAML via temp file + rename to avoid partial-write races."""
    with tempfile.NamedTemporaryFile(
        mode="w", dir=path.parent, delete=False, suffix=".tmp",
        encoding="utf-8",
    ) as tmp:
        yaml.dump(
            data, tmp, allow_unicode=True,
            sort_keys=False, default_flow_style=False,
        )
        tmp_path = Path(tmp.name)
    os.replace(tmp_path, path)


def git_commit_and_push(
    *,
    path: Path,
    branch: str,
    message: str,
    bot_name: str = "axiomedge-schema-bot",
    bot_email: str = "schema-bot@axiomedge.local",
    push: bool | None = None,
) -> dict[str, Any]:
    """Create a branch, commit ``path``, optionally push.

    ``push`` default: controlled by ``GRAPH_SCHEMA_AUTO_PUSH`` env (set to
    ``"1"``/``"true"`` to enable). Default off so CI/tests don't accidentally
    push. Returns a dict with ``{branch, commit_sha, pushed}``.
    """
    if push is None:
        push = os.getenv("GRAPH_SCHEMA_AUTO_PUSH", "").lower() in ("1", "true", "yes")

    def _run(*args: str) -> str:
        r = subprocess.run(
            list(args), capture_output=True, text=True, check=False,
        )
        if r.returncode != 0:
            raise RuntimeError(f"git {' '.join(args[1:])}: {r.stderr.strip()}")
        return r.stdout.strip()

    _run("git", "checkout", "-B", branch)
    _run("git", "add", str(path))
    _run(
        "git", "-c", f"user.name={bot_name}", "-c", f"user.email={bot_email}",
        "commit", "-m", message,
    )
    sha = _run("git", "rev-parse", "HEAD")

    pushed = False
    if push:
        _run("git", "push", "-u", "origin", branch)
        pushed = True

    return {"branch": branch, "commit_sha": sha, "pushed": pushed}


__all__ = ["git_commit_and_push", "merge_label_into_yaml"]
```

- [ ] **Step 3: Tests + lint + commit**

```bash
uv run pytest tests/unit/test_graph_schema_helpers.py -v --no-cov
uvx ruff check src/api/routes/graph_schema_helpers.py tests/unit/test_graph_schema_helpers.py
git add src/api/routes/graph_schema_helpers.py tests/unit/test_graph_schema_helpers.py
git commit -m "feat(api): graph_schema_helpers — atomic YAML writer + git wrapper

merge_label_into_yaml() is idempotent (re-approve is no-op for the list
but still bumps version and records metadata), creates the KB YAML if
absent, and writes atomically via temp file + rename.

git_commit_and_push() wraps subprocess git with a bot identity, branch
checkout, and an opt-in push (env GRAPH_SCHEMA_AUTO_PUSH).

Spec §5.3.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Admin API routes

**Files:** `src/api/routes/graph_schema.py` + `tests/unit/test_graph_schema_routes.py`

- [ ] **Step 1: Test first — list endpoint**

Create `tests/unit/test_graph_schema_routes.py`:

```python
"""Admin graph-schema API — list/approve/reject/merge/rename/bootstrap-run."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def mock_candidate_repo():
    repo = AsyncMock()
    repo.list_pending = AsyncMock(return_value=[])
    repo.decide = AsyncMock()
    return repo


@pytest.fixture
def mock_run_repo():
    repo = AsyncMock()
    repo.has_running = AsyncMock(return_value=False)
    return repo


@pytest.fixture
def app(mock_candidate_repo, mock_run_repo):
    from src.api.routes.graph_schema import router, _get_repos

    fast = FastAPI()

    # Dependency override
    fast.dependency_overrides[_get_repos] = lambda: (
        mock_candidate_repo, mock_run_repo,
    )
    fast.include_router(router)
    return fast


class TestListCandidates:
    def test_empty_list(self, app):
        client = TestClient(app)
        resp = client.get("/api/v1/admin/graph-schema/candidates?kb_id=test")
        assert resp.status_code == 200
        assert resp.json() == {"candidates": []}

    def test_returns_candidates(self, app, mock_candidate_repo):
        mock_row = MagicMock()
        mock_row.id = uuid4()
        mock_row.kb_id = "test"
        mock_row.candidate_type = "node"
        mock_row.label = "Meeting"
        mock_row.frequency = 10
        mock_row.confidence_avg = 0.9
        mock_row.confidence_min = 0.85
        mock_row.confidence_max = 0.95
        mock_row.source_label = None
        mock_row.target_label = None
        mock_row.examples = [{"sample": "회의"}]
        mock_row.similar_labels = []
        mock_row.first_seen_at = None
        mock_row.last_seen_at = None
        mock_candidate_repo.list_pending.return_value = [mock_row]

        client = TestClient(app)
        resp = client.get("/api/v1/admin/graph-schema/candidates?kb_id=test")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["candidates"]) == 1
        assert body["candidates"][0]["label"] == "Meeting"


class TestApprove:
    def test_approve_writes_yaml_and_marks_decided(
        self, app, mock_candidate_repo, tmp_path, monkeypatch,
    ):
        # Redirect YAML writer to tmp_path
        schema_dir = tmp_path / "graph_schemas"
        schema_dir.mkdir()
        (schema_dir / "test.yaml").write_text(
            "version: 1\nprompt_focus: x\nnodes: [Person]\nrelationships: []\n"
        )
        monkeypatch.setattr(
            "src.api.routes.graph_schema_helpers._SCHEMA_DIR", schema_dir,
        )
        # Stub the git helper so the test doesn't create real commits
        monkeypatch.setattr(
            "src.api.routes.graph_schema.git_commit_and_push",
            lambda **kw: {"branch": kw["branch"], "commit_sha": "abc", "pushed": False},
        )

        client = TestClient(app)
        resp = client.post(
            "/api/v1/admin/graph-schema/candidates/approve",
            json={
                "kb_id": "test",
                "candidate_type": "node",
                "label": "Meeting",
                "approved_by": "admin@test",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert "yaml_path" in body
        # Candidate row marked approved
        mock_candidate_repo.decide.assert_awaited_once()
        call_kwargs = mock_candidate_repo.decide.await_args.kwargs
        assert call_kwargs["status"] == "approved"


class TestReject:
    def test_reject_marks_decided(self, app, mock_candidate_repo):
        client = TestClient(app)
        resp = client.post(
            "/api/v1/admin/graph-schema/candidates/reject",
            json={
                "kb_id": "test",
                "candidate_type": "node",
                "label": "Junk",
                "decided_by": "admin@test",
                "reason": "not a real concept",
            },
        )
        assert resp.status_code == 200
        mock_candidate_repo.decide.assert_awaited_once()
        assert mock_candidate_repo.decide.await_args.kwargs["status"] == "rejected"


class TestBootstrapRunTrigger:
    def test_trigger_enqueues_job(self, app, mock_run_repo):
        with patch(
            "src.api.routes.graph_schema._enqueue_bootstrap",
            new=AsyncMock(return_value={"job_id": "j1"}),
        ) as enq:
            client = TestClient(app)
            resp = client.post(
                "/api/v1/admin/graph-schema/bootstrap/test/run",
                json={"triggered_by_user": "admin@test"},
            )
        assert resp.status_code == 200
        assert resp.json()["job_id"] == "j1"
        enq.assert_awaited_once()

    def test_trigger_returns_409_when_already_running(self, app, mock_run_repo):
        mock_run_repo.has_running = AsyncMock(return_value=True)
        client = TestClient(app)
        resp = client.post(
            "/api/v1/admin/graph-schema/bootstrap/test/run",
            json={"triggered_by_user": "admin@test"},
        )
        assert resp.status_code == 409
```

- [ ] **Step 2: Fail → implement**

Create `src/api/routes/graph_schema.py`:

```python
"""Admin graph-schema API — review Phase 3 candidates, approve to YAML.

Spec §5.2 + §5.3 + §6.1.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from src.api.routes.graph_schema_helpers import (
    git_commit_and_push,
    merge_label_into_yaml,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/admin/graph-schema",
    tags=["Admin / Graph Schema"],
)


# --- Dependency wiring ------------------------------------------------------


def _get_repos():
    """Late-bound — overridden in tests via dependency_overrides.

    Returns (candidate_repo, run_repo). Production wiring binds the real
    session_maker here; we keep the import lazy to avoid app-startup order
    issues.
    """
    from src.stores.postgres.repositories.bootstrap_run_repo import (
        BootstrapRunRepo,
    )
    from src.stores.postgres.repositories.schema_candidate_repo import (
        SchemaCandidateRepo,
    )
    from src.api.state import get_app_state

    app_state = get_app_state()
    session_maker = app_state.session_maker
    return (
        SchemaCandidateRepo(session_maker),
        BootstrapRunRepo(session_maker),
    )


async def _enqueue_bootstrap(
    kb_id: str, triggered_by_user: str | None,
) -> dict[str, Any]:
    """Late-bound enqueue — easy to patch in tests."""
    from src.jobs.queue import enqueue_job

    return await enqueue_job(
        "schema_bootstrap_run",
        kb_id, "manual", triggered_by_user,
    )


# --- Request / response models ---------------------------------------------


class CandidateView(BaseModel):
    id: str
    kb_id: str
    candidate_type: str
    label: str
    frequency: int
    confidence_avg: float
    confidence_min: float
    confidence_max: float
    source_label: str | None = None
    target_label: str | None = None
    examples: list[dict[str, Any]] = Field(default_factory=list)
    similar_labels: list[dict[str, Any]] = Field(default_factory=list)


class ApproveRequest(BaseModel):
    kb_id: str
    candidate_type: Literal["node", "relationship"]
    label: str
    approved_by: str


class RejectRequest(BaseModel):
    kb_id: str
    candidate_type: Literal["node", "relationship"]
    label: str
    decided_by: str
    reason: str | None = None


class MergeRequest(BaseModel):
    kb_id: str
    candidate_type: Literal["node", "relationship"]
    label: str
    merge_into: str
    decided_by: str


class RenameRequest(BaseModel):
    kb_id: str
    candidate_type: Literal["node", "relationship"]
    label: str
    new_label: str
    approved_by: str


class BootstrapRunRequest(BaseModel):
    triggered_by_user: str | None = None


# --- Routes ----------------------------------------------------------------


@router.get("/candidates")
async def list_candidates(
    kb_id: str = Query(..., description="Knowledge base id"),
    repos=Depends(_get_repos),
) -> dict[str, Any]:
    candidate_repo, _ = repos
    rows = await candidate_repo.list_pending(kb_id)
    return {
        "candidates": [
            CandidateView(
                id=str(r.id),
                kb_id=r.kb_id,
                candidate_type=r.candidate_type,
                label=r.label,
                frequency=r.frequency,
                confidence_avg=r.confidence_avg,
                confidence_min=r.confidence_min,
                confidence_max=r.confidence_max,
                source_label=r.source_label,
                target_label=r.target_label,
                examples=list(r.examples or []),
                similar_labels=list(r.similar_labels or []),
            ).model_dump()
            for r in rows
        ],
    }


@router.post("/candidates/approve")
async def approve_candidate(
    req: ApproveRequest,
    repos=Depends(_get_repos),
) -> dict[str, Any]:
    candidate_repo, _ = repos
    yaml_path = merge_label_into_yaml(
        kb_id=req.kb_id,
        candidate_type=req.candidate_type,
        label=req.label,
        approved_by=req.approved_by,
    )

    ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    branch = f"schema/{req.kb_id}-{ts}"
    try:
        git_info = git_commit_and_push(
            path=yaml_path,
            branch=branch,
            message=(
                f"feat(schema): {req.kb_id} — approve {req.candidate_type} "
                f"'{req.label}' (by {req.approved_by})"
            ),
        )
    except RuntimeError as exc:
        logger.warning("git commit failed — YAML saved locally: %s", exc)
        git_info = {"error": str(exc)}

    await candidate_repo.decide(
        kb_id=req.kb_id,
        candidate_type=req.candidate_type,
        label=req.label,
        status="approved",
        decided_by=req.approved_by,
    )

    return {
        "status": "ok",
        "yaml_path": str(yaml_path),
        "git": git_info,
    }


@router.post("/candidates/reject")
async def reject_candidate(
    req: RejectRequest,
    repos=Depends(_get_repos),
) -> dict[str, str]:
    candidate_repo, _ = repos
    await candidate_repo.decide(
        kb_id=req.kb_id,
        candidate_type=req.candidate_type,
        label=req.label,
        status="rejected",
        decided_by=req.decided_by,
        rejected_reason=req.reason,
    )
    return {"status": "ok"}


@router.post("/candidates/merge")
async def merge_candidate(
    req: MergeRequest,
    repos=Depends(_get_repos),
) -> dict[str, str]:
    candidate_repo, _ = repos
    await candidate_repo.decide(
        kb_id=req.kb_id,
        candidate_type=req.candidate_type,
        label=req.label,
        status="merged",
        decided_by=req.decided_by,
        merged_into=req.merge_into,
    )
    return {"status": "ok", "merged_into": req.merge_into}


@router.post("/candidates/rename")
async def rename_candidate(
    req: RenameRequest,
    repos=Depends(_get_repos),
) -> dict[str, Any]:
    """Approve the candidate under a different label name.

    Writes ``new_label`` into the YAML (not the original candidate label)
    and marks the candidate row approved with ``merged_into = new_label``
    so the history trail shows the rename.
    """
    candidate_repo, _ = repos
    yaml_path = merge_label_into_yaml(
        kb_id=req.kb_id,
        candidate_type=req.candidate_type,
        label=req.new_label,
        approved_by=req.approved_by,
    )
    await candidate_repo.decide(
        kb_id=req.kb_id,
        candidate_type=req.candidate_type,
        label=req.label,
        status="approved",
        decided_by=req.approved_by,
        merged_into=req.new_label,
    )
    return {"status": "ok", "new_label": req.new_label, "yaml_path": str(yaml_path)}


@router.post("/bootstrap/{kb_id}/run")
async def trigger_bootstrap(
    kb_id: str,
    req: BootstrapRunRequest,
    repos=Depends(_get_repos),
) -> dict[str, Any]:
    _, run_repo = repos
    if await run_repo.has_running(kb_id):
        raise HTTPException(
            status_code=409,
            detail=f"Bootstrap already running for kb_id={kb_id}",
        )
    result = await _enqueue_bootstrap(kb_id, req.triggered_by_user)
    return {"status": "queued", **result}


__all__ = ["router"]
```

- [ ] **Step 3: Register the router**

Find where other routes register. Usually `src/api/app.py` or a route-discovery module:

```bash
grep -rn "include_router\|route_discovery" src/api/app.py src/api/route_discovery.py 2>/dev/null | head -5
```

If the project uses auto-discovery (`route_discovery.py`), the new file is auto-picked. Otherwise add explicit `app.include_router(graph_schema.router)`.

- [ ] **Step 4: Tests + lint + commit**

```bash
uv run pytest tests/unit/test_graph_schema_routes.py -v --no-cov
uvx ruff check src/api/routes/graph_schema.py tests/unit/test_graph_schema_routes.py
git add src/api/routes/graph_schema.py tests/unit/test_graph_schema_routes.py
git commit -m "feat(api): admin graph-schema candidate review endpoints

5 endpoints under /api/v1/admin/graph-schema/*: list candidates,
approve (merge label into YAML + git commit + mark decided), reject,
merge (candidate → existing label), rename (approve under new label).
Plus POST /bootstrap/{kb_id}/run to trigger a Phase-3 bootstrap run
with a 409 guard when one is already in flight.

git push is opt-in via GRAPH_SCHEMA_AUTO_PUSH; default commits locally
so tests + dev don't accidentally push.

Spec §5.2 + §5.3 + §6.1.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Full regression

- [ ] **Step 1: Phase 4a + full Phase 1~3 suite**

```bash
uv run pytest \
  tests/unit/test_graph_schema_helpers.py \
  tests/unit/test_graph_schema_routes.py \
  tests/unit/test_schema_db_models.py \
  tests/unit/test_schema_candidate_repo.py \
  tests/unit/test_bootstrap_run_repo.py \
  tests/unit/test_schema_discovery_prompt.py \
  tests/unit/test_schema_bootstrap.py \
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
  -q --no-cov 2>&1 | tail -3
```

- [ ] **Step 2: Coverage for new modules**

```bash
uv run pytest \
  tests/unit/test_graph_schema_helpers.py \
  tests/unit/test_graph_schema_routes.py \
  --cov=src.api.routes.graph_schema_helpers \
  --cov=src.api.routes.graph_schema \
  --cov-report=term-missing --no-cov-on-fail 2>&1 | tail -10
```

---

## Spec coverage

| Spec | Task |
|---|---|
| §5.2 admin UI backend | 2 |
| §5.3 YAML auto-PR flow | 1, 2 |
| §6.1 graph_schema.py | 2 |

Deferred: Next.js UI (Phase 4b), PR creation via gh CLI (opt-in env in Task 1 handles git push — full PR can be added when needed), re-extract trigger endpoint (Phase 5).
