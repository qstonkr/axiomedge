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

    Returns (candidate_repo, run_repo, reextract_repo). Production wiring
    binds via the app_state session_maker; kept lazy to avoid app-startup
    order issues.
    """
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


class ReextractRunRequest(BaseModel):
    triggered_by_user: str


async def _enqueue_reextract(
    job_id: str, kb_id: str,
) -> dict[str, Any]:
    """Late-bound enqueue — easy to patch in tests."""
    from src.jobs.queue import enqueue_job

    return await enqueue_job("schema_reextract_run", job_id, kb_id)


# --- Routes ----------------------------------------------------------------


@router.get("/candidates")
async def list_candidates(
    kb_id: str = Query(..., description="Knowledge base id"),
    repos=Depends(_get_repos),
) -> dict[str, Any]:
    candidate_repo, _, _ = repos
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
    candidate_repo, _, _ = repos
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
    candidate_repo, _, _ = repos
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
    candidate_repo, _, _ = repos
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
    candidate_repo, _, _ = repos
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
    return {
        "status": "ok",
        "new_label": req.new_label,
        "yaml_path": str(yaml_path),
    }


@router.post("/bootstrap/{kb_id}/run")
async def trigger_bootstrap(
    kb_id: str,
    req: BootstrapRunRequest,
    repos=Depends(_get_repos),
) -> dict[str, Any]:
    _, run_repo, _ = repos
    if await run_repo.has_running(kb_id):
        raise HTTPException(
            status_code=409,
            detail=f"Bootstrap already running for kb_id={kb_id}",
        )
    result = await _enqueue_bootstrap(kb_id, req.triggered_by_user)
    return {"status": "queued", **result}


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


__all__ = ["router"]
