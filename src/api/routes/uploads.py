"""Bulk upload — presigned URL flow.

3 endpoint:
1. ``POST /api/v1/knowledge/uploads/init`` — N개 파일 metadata 받아 presigned
   PUT URL × N 발급 + bulk_upload_sessions row 생성.
2. ``POST /api/v1/knowledge/uploads/{session_id}/finalize`` — 브라우저가 모든
   PUT 완료 후 호출 → arq job enqueue (ingest_from_object_storage).
3. ``GET /api/v1/knowledge/uploads/{session_id}/status`` — 진행률 polling.
4. ``GET /api/v1/knowledge/uploads`` (옵션) — 사용자의 최근 업로드 목록.

권한 모델 (me_data_sources 와 동일 패턴):
- caller 의 ``kb.owner_id == user.sub`` 인 personal KB 에만 attach 가능
- session 의 ``owner_user_id == user.sub`` 검증 — cross-user 시 404
- S3 path 가 ``user/{uid}/uploads/{sid}/`` 로 prefix 격리

본 라우트는 파일 byte 를 안 봄. multipart parsing 부담 0.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src.api.app import _get_state
from src.auth.dependencies import OrgContext, get_current_org, get_current_user
from src.auth.providers import AuthUser
from src.config import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/knowledge/uploads",
    tags=["Bulk Upload"],
)

_KB_NOT_FOUND = "Personal KB not found"
_SESSION_NOT_FOUND = "Upload session not found"


class FileEntry(BaseModel):
    """init body 의 파일 1건 — 사용자가 선택한 모든 파일에 대해 entry 1개."""

    name: str = Field(..., min_length=1, max_length=512)
    size: int = Field(..., ge=0)


class InitUploadBody(BaseModel):
    kb_id: str = Field(..., min_length=1, max_length=100)
    files: list[FileEntry] = Field(..., min_length=1, max_length=10000)


class InitUploadEntry(BaseModel):
    file_idx: int
    filename: str
    s3_key: str
    presigned_url: str


class InitUploadResponse(BaseModel):
    session_id: str
    expires_in: int
    uploads: list[InitUploadEntry]


class FinalizeBody(BaseModel):
    """브라우저가 PUT 완료 후 보내는 결과 — 실패한 파일 idx list (옵션)."""

    failed_indices: list[int] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 공통 helpers — me_data_sources 의 패턴 재사용
# ---------------------------------------------------------------------------


async def _require_personal_kb_owner(
    kb_id: str, organization_id: str, owner_user_id: str,
) -> None:
    """KB 가 caller 의 personal KB 가 아니면 404 (존재 누설 X)."""
    state = _get_state()
    kb_registry = state.get("kb_registry")
    if kb_registry is None:
        raise HTTPException(status_code=503, detail="kb_registry not initialized")
    try:
        kb_row = await kb_registry.get_kb(
            kb_id, organization_id=organization_id, owner_id=owner_user_id,
        )
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
        raise HTTPException(status_code=500, detail=f"KB lookup failed: {e}") from e
    if kb_row is None:
        raise HTTPException(status_code=404, detail=_KB_NOT_FOUND)


def _get_repo() -> Any:
    state = _get_state()
    repo = state.get("bulk_upload_repo")
    if repo is None:
        raise HTTPException(
            status_code=503, detail="bulk_upload_repo not initialized",
        )
    return repo


# ---------------------------------------------------------------------------
# 라우트 핸들러
# ---------------------------------------------------------------------------


@router.post("/init", response_model=InitUploadResponse, status_code=201)
async def init_upload(
    body: InitUploadBody,
    user: AuthUser = Depends(get_current_user),
    org: OrgContext = Depends(get_current_org),
) -> InitUploadResponse:
    """N개 파일 metadata 받아 presigned PUT URL × N 발급.

    백엔드는 byte 를 안 받음 — 브라우저가 직접 S3/MinIO 로 PUT.
    """
    await _require_personal_kb_owner(body.kb_id, org.id, user.sub)
    repo = _get_repo()

    # max_file_size_mb 와 sync — 어차피 S3 ContentLength 강제로 차단되지만
    # init 시점에서 명시적 거부가 사용자 친화적 (presigned URL 생성 비용 절약).
    from src.config.weights import weights as _w
    max_bytes = int(_w.pipeline.max_file_size_mb) * 1024 * 1024
    for idx, f in enumerate(body.files):
        if f.size > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"file[{idx}] '{f.name}' size {f.size} exceeds limit "
                    f"{_w.pipeline.max_file_size_mb} MB"
                ),
            )

    settings = get_settings()
    bucket = settings.aws.s3_uploads_bucket
    prefix = settings.aws.s3_uploads_prefix
    ttl = settings.aws.s3_uploads_url_ttl
    if not bucket:
        raise HTTPException(
            status_code=503,
            detail="UPLOADS_S3_BUCKET 미설정 — bulk upload 비활성화 상태",
        )

    from src.storage import (
        S3StorageError,
        build_object_key,
        generate_presigned_put_url,
    )

    session_id = str(uuid.uuid4())
    files_meta: list[dict[str, Any]] = []
    uploads: list[InitUploadEntry] = []

    for idx, f in enumerate(body.files):
        s3_key = build_object_key(
            user_id=user.sub, session_id=session_id,
            file_idx=idx, filename=f.name, prefix=prefix,
        )
        try:
            url = generate_presigned_put_url(
                bucket=bucket, key=s3_key, ttl_seconds=ttl,
                content_length=f.size,
            )
        except S3StorageError as e:
            logger.exception("presigned URL 발급 실패 (file_idx=%d)", idx)
            raise HTTPException(
                status_code=500, detail=f"presigned URL 발급 실패: {e}",
            ) from e
        files_meta.append({
            "file_idx": idx, "filename": f.name,
            "s3_key": s3_key, "size": f.size,
        })
        uploads.append(InitUploadEntry(
            file_idx=idx, filename=f.name, s3_key=s3_key, presigned_url=url,
        ))

    s3_prefix = f"{prefix.rstrip('/')}/user/{user.sub}/uploads/{session_id}/"
    await repo.create(
        session_id=session_id,
        kb_id=body.kb_id, organization_id=org.id,
        owner_user_id=user.sub, s3_prefix=s3_prefix,
        files=files_meta,
    )

    return InitUploadResponse(
        session_id=session_id, expires_in=ttl, uploads=uploads,
    )


@router.post("/{session_id}/finalize")
async def finalize_upload(
    session_id: str,
    body: FinalizeBody,
    user: AuthUser = Depends(get_current_user),
    org: OrgContext = Depends(get_current_org),
) -> dict[str, Any]:
    """브라우저가 모든 PUT 완료 후 호출 → arq job enqueue."""
    repo = _get_repo()
    sess = await repo.get(
        session_id, organization_id=org.id, owner_user_id=user.sub,
    )
    if sess is None:
        raise HTTPException(status_code=404, detail=_SESSION_NOT_FOUND)
    if sess["status"] != "pending":
        # 이미 finalize 된 session — idempotent (재호출 OK)
        return {
            "session_id": session_id,
            "status": sess["status"],
            "message": "session already finalized",
        }

    # status 전이 + arq enqueue.
    await repo.set_status(session_id, "processing")

    from src.jobs.queue import enqueue_job

    try:
        await enqueue_job(
            "ingest_from_object_storage",
            session_id, list(body.failed_indices or []),
        )
    except Exception as e:  # noqa: BLE001 — Redis 다양한 예외 통합
        logger.exception("enqueue ingest_from_object_storage 실패")
        await repo.set_status(session_id, "failed")
        raise HTTPException(
            status_code=500, detail=f"Failed to enqueue ingest job: {e}",
        ) from e

    return {"session_id": session_id, "status": "processing"}


@router.get("/{session_id}/status")
async def get_upload_status(
    session_id: str,
    user: AuthUser = Depends(get_current_user),
    org: OrgContext = Depends(get_current_org),
) -> dict[str, Any]:
    """진행률 polling — 5s 주기 권장."""
    repo = _get_repo()
    sess = await repo.get(
        session_id, organization_id=org.id, owner_user_id=user.sub,
    )
    if sess is None:
        raise HTTPException(status_code=404, detail=_SESSION_NOT_FOUND)
    return {
        "session_id": sess["id"],
        "status": sess["status"],
        "total_files": sess["total_files"],
        "processed_files": sess["processed_files"],
        "failed_files": sess["failed_files"],
        "errors": sess["errors"],
    }


@router.get("")
async def list_recent_uploads(
    user: AuthUser = Depends(get_current_user),
    org: OrgContext = Depends(get_current_org),
) -> dict[str, Any]:
    """본인 최근 업로드 세션 (default 20건)."""
    repo = _get_repo()
    items = await repo.list_recent_for_user(
        organization_id=org.id, owner_user_id=user.sub,
    )
    return {"sessions": items, "total": len(items)}
