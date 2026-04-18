"""KB Search Group API - 검색 스코프 그룹 관리.

KB를 그룹으로 묶어 스코프 검색을 지원.
같은 KB가 여러 그룹에 속할 수 있음 (뷰 개념).

사용 예:
  - "CVS팀" 그룹 = [cvs-kb, infra-kb, miso-faq] → 이 3개 KB에서만 크로스 검색
  - "IT운영" 그룹 = [itops-kb, infra-kb, dev-kb]
  - "전체" 그룹 = [모든 KB] (기본 그룹)
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.api.app import _get_state

_DB_NOT_INIT = "Database not initialized"
_GROUP_NOT_FOUND = "Group not found"

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/search-groups", tags=["Search Groups"])


class CreateGroupRequest(BaseModel):
    name: str = Field(..., max_length=100)
    description: str = Field(default="", max_length=500)
    kb_ids: list[str] = Field(..., min_length=1)
    is_default: bool = False


class UpdateGroupRequest(BaseModel):
    name: str | None = Field(default=None, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    kb_ids: list[str] | None = None
    is_default: bool | None = None


@router.get("")
async def list_groups() -> dict:
    """모든 검색 그룹 조회."""
    repo = _get_state().get("search_group_repo")
    if not repo:
        return {"groups": []}
    groups = await repo.list_all()
    return {"groups": groups}


@router.post("", responses={503: {"description": "Database not initialized"}})
async def create_group(request: CreateGroupRequest) -> Any:
    """검색 그룹 생성."""
    repo = _get_state().get("search_group_repo")
    if not repo:
        raise HTTPException(status_code=503, detail=_DB_NOT_INIT)

    group = await repo.create(
        name=request.name,
        kb_ids=request.kb_ids,
        description=request.description,
        is_default=request.is_default,
    )
    return group


@router.get("/{group_id}", responses={503: {"description": "Database not initialized"}, 404: {"description": "Group not found"}})
async def get_group(group_id: str) -> Any:
    """검색 그룹 상세 조회."""
    repo = _get_state().get("search_group_repo")
    if not repo:
        raise HTTPException(status_code=503, detail=_DB_NOT_INIT)

    group = await repo.get(group_id)
    if not group:
        raise HTTPException(status_code=404, detail=_GROUP_NOT_FOUND)
    return group


@router.put("/{group_id}", responses={503: {"description": "Database not initialized"}, 404: {"description": "Group not found"}, 400: {"description": "Invalid group ID"}})
async def update_group(group_id: str, request: UpdateGroupRequest) -> Any:
    """검색 그룹 수정 (KB 추가/제거)."""
    repo = _get_state().get("search_group_repo")
    if not repo:
        raise HTTPException(status_code=503, detail=_DB_NOT_INIT)

    try:
        group = await repo.update(
            group_id=group_id,
            name=request.name,
            kb_ids=request.kb_ids,
            description=request.description,
            is_default=request.is_default,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid group ID: {group_id}") from e
    if not group:
        raise HTTPException(status_code=404, detail=_GROUP_NOT_FOUND)
    return group


@router.delete("/{group_id}", responses={503: {"description": "Database not initialized"}, 404: {"description": "Group not found"}, 400: {"description": "Invalid group ID"}})
async def delete_group(group_id: str) -> dict:
    """검색 그룹 삭제."""
    repo = _get_state().get("search_group_repo")
    if not repo:
        raise HTTPException(status_code=503, detail=_DB_NOT_INIT)

    try:
        deleted = await repo.delete(group_id)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid group ID: {group_id}") from e
    if not deleted:
        raise HTTPException(status_code=404, detail=_GROUP_NOT_FOUND)
    return {"success": True, "message": f"Group {group_id} deleted"}


@router.get("/{group_id}/kbs", responses={503: {"description": "Database not initialized"}})
async def get_group_kbs(group_id: str) -> dict:
    """그룹에 속한 KB 목록 조회."""
    repo = _get_state().get("search_group_repo")
    if not repo:
        raise HTTPException(status_code=503, detail=_DB_NOT_INIT)

    kb_ids = await repo.resolve_kb_ids(group_id=group_id)
    return {"group_id": group_id, "kb_ids": kb_ids}
