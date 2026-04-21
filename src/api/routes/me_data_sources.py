"""User self-service data sources — `/api/v1/me/knowledge/{kb_id}/data-sources`.

Admin route ``/api/v1/admin/data-sources`` 와 별개. 사용자가 본인 personal KB
에 connector source 추가할 때 사용. 권한 모델:

- caller 가 ``kb.owner_id == user.sub`` 인 KB 에만 attach 가능 (``kb_registry``
  가 owner mismatch 시 None 반환 → 404 매핑, 존재 누설 X).
- 모든 source 는 ``owner_user_id = user.sub`` 로 등록 — admin 등록 source 와
  한 테이블 안에서 격리.
- SecretBox path 는 ``user/{user_id}/data-source/{source_id}`` (per-user 격리).
- shared-token connector (Slack) 는 admin 이 등록한 organization-wide token
  사용 — 사용자는 channel_ids 만 입력.
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
from src.auth.secret_box import SecretBoxError, get_secret_box
from src.auth.secret_paths import data_source_path
from src.connectors.catalog_meta import (
    is_shared_token_connector,
    is_user_self_service,
)

from .data_sources import _mask_secret_fields

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/me/knowledge",
    tags=["My Data Sources"],
)

_KB_NOT_FOUND = "Personal KB not found"
_DS_NOT_FOUND = "Data source not found"
_NOT_SELF_SERVICE = (
    "이 connector 는 사용자 self-service 가 지원되지 않습니다. "
    "관리자에게 등록을 요청해주세요."
)


class UserDataSourceBody(BaseModel):
    """사용자 self-service 등록/수정 body — admin body 와 별도 (권한 차이)."""

    name: str = Field(..., min_length=1, max_length=255)
    source_type: str = Field(..., min_length=1, max_length=50)
    schedule: str | None = Field(default=None, max_length=50)
    crawl_config: dict[str, Any] | None = None
    pipeline_config: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None
    # per-user token connector (Notion/Git/Confluence) 만 사용. shared-token
    # connector (Slack) 면 backend 가 무시 (admin 등록 token 사용).
    secret_token: str | None = None


# ---------------------------------------------------------------------------
# 권한 헬퍼
# ---------------------------------------------------------------------------


async def _require_personal_kb_owner(
    kb_id: str, organization_id: str, owner_user_id: str,
) -> None:
    """KB 가 caller 의 personal KB 가 아니면 404. (존재 누설 방지)"""
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
    repo = state.get("data_source_repo")
    if repo is None:
        raise HTTPException(status_code=503, detail="data_source_repo not initialized")
    return repo


# ---------------------------------------------------------------------------
# 라우트 핸들러
# ---------------------------------------------------------------------------


@router.get("/{kb_id}/data-sources")
async def list_user_sources(
    kb_id: str,
    user: AuthUser = Depends(get_current_user),
    org: OrgContext = Depends(get_current_org),
) -> dict[str, Any]:
    """본인 등록한 source 목록 (해당 KB + owner_user_id 매칭만)."""
    await _require_personal_kb_owner(kb_id, org.id, user.sub)
    repo = _get_repo()
    sources = await repo.list_for_user(
        organization_id=org.id, owner_user_id=user.sub,
    )
    # 같은 KB 안의 source 만 필터.
    filtered = [s for s in sources if s.get("kb_id") == kb_id]
    masked = [_mask_secret_fields(s) for s in filtered]
    return {"sources": masked, "total": len(masked)}


@router.post("/{kb_id}/data-sources", status_code=201)
async def create_user_source(
    kb_id: str,
    body: UserDataSourceBody,
    user: AuthUser = Depends(get_current_user),
    org: OrgContext = Depends(get_current_org),
) -> dict[str, Any]:
    """본인 personal KB 에 source 등록. owner_user_id = user.sub 강제."""
    await _require_personal_kb_owner(kb_id, org.id, user.sub)
    if not is_user_self_service(body.source_type):
        raise HTTPException(status_code=400, detail=_NOT_SELF_SERVICE)

    repo = _get_repo()
    source_id = str(uuid.uuid4())
    data: dict[str, Any] = {
        "id": source_id,
        "name": body.name.strip(),
        "source_type": body.source_type,
        "kb_id": kb_id,
        "schedule": body.schedule,
        "crawl_config": body.crawl_config or {},
        "pipeline_config": body.pipeline_config or {},
        "metadata": body.metadata or {},
        "status": "pending",
    }
    try:
        created = await repo.register(
            data, organization_id=org.id, owner_user_id=user.sub,
        )
    except Exception as e:
        logger.exception("user source register failed")
        raise HTTPException(status_code=500, detail=f"register failed: {e}") from e

    # Per-user token 등록 — shared-token connector 는 secret_token 무시.
    if (
        body.secret_token
        and not is_shared_token_connector(body.source_type)
    ):
        path = data_source_path(
            organization_id=org.id,
            source_id=source_id,
            owner_user_id=user.sub,
        )
        try:
            box = get_secret_box()
            await box.put(path, body.secret_token.strip())
            await repo.set_secret_path(source_id, org.id, path)
            created["secret_path"] = path
            created["has_secret"] = True
        except SecretBoxError as e:
            logger.warning("user source secret put failed: %s", e)
            # source 자체는 살리되 token 등록만 실패 표시.
            created["has_secret"] = False

    return _mask_secret_fields(created)


@router.delete("/{kb_id}/data-sources/{source_id}", status_code=204)
async def delete_user_source(
    kb_id: str,
    source_id: str,
    user: AuthUser = Depends(get_current_user),
    org: OrgContext = Depends(get_current_org),
) -> None:
    """본인 source 삭제 + SecretBox cascade. cross-user → 404."""
    await _require_personal_kb_owner(kb_id, org.id, user.sub)
    repo = _get_repo()
    src = await repo.get_for_user(
        source_id, organization_id=org.id, owner_user_id=user.sub,
    )
    if src is None or src.get("kb_id") != kb_id:
        raise HTTPException(status_code=404, detail=_DS_NOT_FOUND)

    # SecretBox 정리 (best-effort).
    if src.get("has_secret") and src.get("secret_path"):
        try:
            box = get_secret_box()
            await box.delete(src["secret_path"])
        except SecretBoxError as e:
            logger.warning("user source secret delete skipped: %s", e)

    ok = await repo.delete_for_user(
        source_id, organization_id=org.id, owner_user_id=user.sub,
    )
    if not ok:
        raise HTTPException(status_code=404, detail=_DS_NOT_FOUND)


@router.post("/{kb_id}/data-sources/{source_id}/trigger")
async def trigger_user_source(
    kb_id: str,
    source_id: str,
    user: AuthUser = Depends(get_current_user),
    org: OrgContext = Depends(get_current_org),
) -> dict[str, Any]:
    """본인 source 동기화 — admin 트리거와 동일 함수 dispatch."""
    await _require_personal_kb_owner(kb_id, org.id, user.sub)
    repo = _get_repo()
    src = await repo.get_for_user(
        source_id, organization_id=org.id, owner_user_id=user.sub,
    )
    if src is None or src.get("kb_id") != kb_id:
        raise HTTPException(status_code=404, detail=_DS_NOT_FOUND)

    # admin trigger 와 같은 background dispatch 재사용.
    from .data_source_sync import run_data_source_sync

    state = _get_state()
    import asyncio

    task = asyncio.create_task(
        run_data_source_sync(src, state, sync_mode="full"),
    )
    # background task GC 방지 — admin route 와 동일 패턴.
    from .data_sources import _background_tasks

    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    return {"success": True, "message": f"동기화를 시작했습니다 ({src.get('name')})"}
