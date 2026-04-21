"""Admin shared-token CRUD — `/api/v1/admin/shared-tokens/{connector_id}`.

Slack 같은 organization-wide bot token 을 admin 이 1회 등록. 사용자가
self-service 로 ``slack`` source 를 등록하면 launcher 가 본 path 에서 token
fetch (per-source secret 없이도 동작).

SecretBox path: ``org/{org_id}/connector-shared/{connector_id}``.

API:
- ``PUT /api/v1/admin/shared-tokens/{connector_id}`` — token 등록/덮어쓰기
- ``GET /api/v1/admin/shared-tokens`` — 등록된 connector_id 목록 (token 값 X, 등록 여부만)
- ``DELETE /api/v1/admin/shared-tokens/{connector_id}`` — token 삭제

응답에 token 값은 절대 포함 X — 등록 여부 (``configured: true/false``) 만.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src.auth.dependencies import (
    OrgContext,
    get_current_org,
    get_current_user,
    require_role,
)
from src.auth.providers import AuthUser
from src.auth.secret_box import SecretBoxError, get_secret_box
from src.auth.secret_paths import shared_token_path
from src.connectors.catalog_meta import (
    SHARED_TOKEN_CONNECTORS,
    is_shared_token_connector,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/admin/shared-tokens",
    tags=["Shared Tokens"],
    dependencies=[Depends(require_role("admin"))],
)


class SharedTokenBody(BaseModel):
    token: str = Field(..., min_length=1, max_length=4096)


def _validate_connector(connector_id: str) -> None:
    if not is_shared_token_connector(connector_id):
        raise HTTPException(
            status_code=400,
            detail=(
                f"connector_id '{connector_id}' is not a shared-token connector. "
                f"Allowed: {sorted(SHARED_TOKEN_CONNECTORS)}"
            ),
        )


@router.get("")
async def list_shared_tokens(
    user: AuthUser = Depends(get_current_user),  # noqa: ARG001 — auth guard
    org: OrgContext = Depends(get_current_org),
) -> dict[str, Any]:
    """등록된 shared-token connector 목록 (token 값 X, 등록 여부만)."""
    box = get_secret_box()
    items: list[dict[str, Any]] = []
    for connector_id in sorted(SHARED_TOKEN_CONNECTORS):
        path = shared_token_path(org.id, connector_id)
        try:
            value = await box.get(path)
            configured = value is not None
        except SecretBoxError:
            configured = False
        items.append({"connector_id": connector_id, "configured": configured})
    return {"items": items, "total": len(items)}


@router.put("/{connector_id}", status_code=204)
async def upsert_shared_token(
    connector_id: str,
    body: SharedTokenBody,
    user: AuthUser = Depends(get_current_user),  # noqa: ARG001
    org: OrgContext = Depends(get_current_org),
) -> None:
    """Shared bot token 등록/덮어쓰기. 같은 connector_id 면 기존 token 교체."""
    _validate_connector(connector_id)
    path = shared_token_path(org.id, connector_id)
    try:
        box = get_secret_box()
        await box.put(path, body.token.strip())
    except SecretBoxError as e:
        logger.warning("shared-token put failed for %s: %s", connector_id, e)
        raise HTTPException(
            status_code=500, detail=f"SecretBox put failed: {e}",
        ) from e


@router.delete("/{connector_id}", status_code=204)
async def delete_shared_token(
    connector_id: str,
    user: AuthUser = Depends(get_current_user),  # noqa: ARG001
    org: OrgContext = Depends(get_current_org),
) -> None:
    """Shared bot token 삭제 — 이후 사용자 source 동기화는 명시적으로 실패."""
    _validate_connector(connector_id)
    path = shared_token_path(org.id, connector_id)
    try:
        box = get_secret_box()
        await box.delete(path)
    except SecretBoxError as e:
        logger.warning("shared-token delete skipped for %s: %s", connector_id, e)
        # idempotent — 미존재도 200.
