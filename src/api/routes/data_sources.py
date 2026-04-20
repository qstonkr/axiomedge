"""Data Sources API endpoints - wired to DataSourceRepository.

모든 핸들러가 ``OrgContext`` 를 주입받아 ``organization_id`` 를 repo 호출에
전달 — cross-tenant 누설 차단 (0005_data_source_org_required.py).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query

from src.api.app import _get_state
from src.auth.dependencies import OrgContext, get_current_org
from src.auth.secret_box import SecretBoxError, get_secret_box

logger = logging.getLogger(__name__)

_DS_NOT_FOUND = "Data source not found"
_SECRET_MASK_KEYS = ("auth_token", "pat", "password", "api_key", "token")
_background_tasks: set[asyncio.Task] = set()  # prevent premature GC of fire-and-forget tasks
router = APIRouter(prefix="/api/v1/admin/data-sources", tags=["Data Sources"])


# ---------------------------------------------------------------------------
# Secret helpers — Phase 2: SecretBox put/get/delete + DB path 동기화 +
# 응답 직전 mask. Phase 4 에서 backend (Vault 등) 만 교체하면 path 그대로 동작.
# ---------------------------------------------------------------------------

def _secret_path(organization_id: str, source_id: str) -> str:
    """Org-scoped immutable path."""
    return f"org/{organization_id}/data-source/{source_id}"


async def _store_secret(
    repo: Any, organization_id: str, source_id: str, value: str,
) -> None:
    """SecretBox.put + DB has_secret/secret_path 동기화. KEY 미설정 시 raise."""
    box = get_secret_box()  # SECRET_BOX_KEY 미설정 시 SecretBoxError → 500.
    path = _secret_path(organization_id, source_id)
    await box.put(path, value)
    await repo.set_secret_path(source_id, organization_id, path)


async def _delete_secret(
    repo: Any, organization_id: str, source_id: str,
) -> None:
    """SecretBox.delete + DB has_secret/secret_path 비우기. idempotent.

    SECRET_BOX_KEY 미설정 환경 (dev / test 일부) 에서도 DB cleanup 은 진행 —
    SecretBox 호출만 silently skip. 라우트가 secret 없는 source 도 삭제할 수 있어야 함.
    """
    path = _secret_path(organization_id, source_id)
    try:
        box = get_secret_box()
        await box.delete(path)
    except SecretBoxError as e:
        logger.warning("SecretBox.delete skipped for %s: %s", path, e)
    await repo.set_secret_path(source_id, organization_id, None)


def _mask_secret_fields(source: dict[str, Any]) -> dict[str, Any]:
    """응답 직전 호출. secret_path 제거, crawl_config 의 옛 평문 token 마스킹."""
    safe = dict(source)
    safe.pop("secret_path", None)
    cfg = safe.get("crawl_config")
    if isinstance(cfg, dict):
        masked = {
            k: ("***" if k in _SECRET_MASK_KEYS else v) for k, v in cfg.items()
        }
        safe["crawl_config"] = masked
    return safe


# ---------------------------------------------------------------------------
# GET /api/v1/admin/data-sources
# ---------------------------------------------------------------------------
@router.get("")
async def list_data_sources(
    org: OrgContext = Depends(get_current_org),
) -> dict[str, Any]:
    """List data sources — caller's org scope only. secret 마스킹."""
    state = _get_state()
    repo = state.get("data_source_repo")
    if repo:
        try:
            sources = await repo.list(organization_id=org.id)
            masked = [_mask_secret_fields(s) for s in sources]
            return {"sources": masked, "total": len(masked)}
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.warning("Data source repo list failed: %s", e)
    return {"sources": [], "total": 0}


# ---------------------------------------------------------------------------
# POST /api/v1/admin/data-sources
# ---------------------------------------------------------------------------
@router.post("", responses={500: {"description": "Failed to create data source"}})
async def create_data_source(
    body: dict[str, Any],
    org: OrgContext = Depends(get_current_org),
) -> dict[str, Any]:
    """Create a data source — automatically scoped to caller's org.

    body 가 ``secret_token`` 가지면 SecretBox 에 저장 + crawl_config 의 평문
    auth_token 자동 제거 (이중 저장 방지).
    """
    state = _get_state()
    repo = state.get("data_source_repo")
    source_id = body.get("id") or str(uuid.uuid4())
    secret_token = body.pop("secret_token", None)
    if repo:
        try:
            data = dict(body)
            data.setdefault("id", source_id)
            data.setdefault("status", "active")
            data.pop("organization_id", None)
            # crawl_config 의 평문 token 키는 절대 DB 평문 저장 X — 사용자가
            # body 에 넣었어도 SecretBox 로 redirect, plain 키는 strip.
            cfg = data.get("crawl_config")
            if isinstance(cfg, dict):
                fallback_token = next(
                    (cfg.get(k) for k in _SECRET_MASK_KEYS if cfg.get(k)),
                    None,
                )
                if fallback_token and not secret_token:
                    secret_token = str(fallback_token)
                data["crawl_config"] = {
                    k: v for k, v in cfg.items() if k not in _SECRET_MASK_KEYS
                }
            await repo.register(data, organization_id=org.id)
            if isinstance(secret_token, str) and secret_token.strip():
                await _store_secret(repo, org.id, source_id, secret_token.strip())
            return {"success": True, "source_id": source_id, "message": "Data source created"}
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.warning("Data source repo register failed: %s", e)
            raise HTTPException(status_code=500, detail=f"Failed to create data source: {e}")
    return {"success": True, "source_id": source_id, "message": "Data source created (stub - no DB)"}


# ---------------------------------------------------------------------------
# GET /api/v1/admin/data-sources/{source_id}
# ---------------------------------------------------------------------------
@router.get("/{source_id}", responses={404: {"description": "Data source not found"}})
async def get_data_source(
    source_id: str,
    org: OrgContext = Depends(get_current_org),
) -> dict[str, Any]:
    """Get data source — cross-org 시 404. secret 마스킹."""
    state = _get_state()
    repo = state.get("data_source_repo")
    if repo:
        try:
            source = await repo.get(source_id, organization_id=org.id)
            if source:
                return _mask_secret_fields(source)
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.warning("Data source repo get failed: %s", e)
    raise HTTPException(status_code=404, detail=_DS_NOT_FOUND)


# ---------------------------------------------------------------------------
# PUT /api/v1/admin/data-sources/{source_id}
# ---------------------------------------------------------------------------
@router.put("/{source_id}", responses={404: {"description": "Data source not found"}, 500: {"description": "Failed to update data source"}})  # noqa: E501
async def update_data_source(
    source_id: str,
    body: dict[str, Any],
    org: OrgContext = Depends(get_current_org),
) -> dict[str, Any]:
    """Update data source — cross-org 시 404.

    body.secret_token 처리:
      - 비어있는 string ("") → 변경 없음 (옛 token 유지)
      - 명시적 ``null`` → SecretBox 에서 삭제 (has_secret=false)
      - 일반 string → SecretBox 에 저장 (덮어쓰기)
    """
    state = _get_state()
    repo = state.get("data_source_repo")
    secret_token = body.get("secret_token", "")  # sentinel: missing key → ""
    has_secret_key = "secret_token" in body
    if repo:
        try:
            existing = await repo.get(source_id, organization_id=org.id)
            if not existing:
                raise HTTPException(status_code=404, detail=_DS_NOT_FOUND)
            status = body.get("status", existing.get("status", "active"))
            error_message = body.get("error_message")
            updated = await repo.update_status(
                source_id, status, organization_id=org.id, error_message=error_message,
            )
            if not updated:
                raise HTTPException(status_code=404, detail=_DS_NOT_FOUND)
            # secret 변경 처리.
            if has_secret_key:
                if secret_token is None:
                    await _delete_secret(repo, org.id, source_id)
                elif isinstance(secret_token, str) and secret_token.strip():
                    await _store_secret(
                        repo, org.id, source_id, secret_token.strip(),
                    )
                # 빈 string ("") 은 의도적 no-op — 옛 token 유지.
            return {"success": True, "source_id": source_id, "message": "Updated"}
        except HTTPException:
            raise
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.warning("Data source repo update failed: %s", e)
            raise HTTPException(status_code=500, detail=f"Failed to update data source: {e}")
    return {"success": True, "source_id": source_id, "message": "Updated (stub - no DB)"}


# ---------------------------------------------------------------------------
# DELETE /api/v1/admin/data-sources/{source_id}
# ---------------------------------------------------------------------------
@router.delete("/{source_id}", responses={404: {"description": "Data source not found"}, 500: {"description": "Failed to delete data source"}})  # noqa: E501
async def delete_data_source(
    source_id: str,
    org: OrgContext = Depends(get_current_org),
) -> dict[str, bool | str]:
    """Delete data source — cross-org 시 404. SecretBox cascade delete."""
    state = _get_state()
    repo = state.get("data_source_repo")
    if repo:
        try:
            # secret 먼저 정리 (SecretBox.delete idempotent).
            await _delete_secret(repo, org.id, source_id)
            deleted = await repo.delete(source_id, organization_id=org.id)
            if not deleted:
                raise HTTPException(status_code=404, detail=_DS_NOT_FOUND)
            return {"success": True, "source_id": source_id}
        except HTTPException:
            raise
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.warning("Data source repo delete failed: %s", e)
            raise HTTPException(status_code=500, detail=f"Failed to delete data source: {e}")
    return {"success": True, "source_id": source_id}


# ---------------------------------------------------------------------------
# POST /api/v1/admin/data-sources/{source_id}/trigger
# ---------------------------------------------------------------------------
@router.post("/{source_id}/trigger", responses={404: {"description": "Data source not found"}, 500: {"description": "Failed to trigger sync"}})  # noqa: E501
async def trigger_data_source_sync(
    source_id: str,
    sync_mode: Annotated[str, Query()] = "resume",
    org: OrgContext = Depends(get_current_org),
) -> dict[str, Any]:
    """Trigger data source sync (crawl → ingest pipeline) — caller org scope."""
    state = _get_state()
    repo = state.get("data_source_repo")
    if repo:
        try:
            existing = await repo.get(source_id, organization_id=org.id)
            if not existing:
                raise HTTPException(status_code=404, detail=_DS_NOT_FOUND)
            await repo.update_status(source_id, "syncing", organization_id=org.id)

            # Launch background sync task — existing dict carries organization_id
            # so the sync runner can pass it through to repo.complete_sync().
            from src.api.routes.data_source_sync import run_data_source_sync
            task = asyncio.create_task(
                run_data_source_sync(existing, state, sync_mode=sync_mode),
                name=f"ds-sync-{source_id[:8]}",
            )
            _background_tasks.add(task)
            task.add_done_callback(_background_tasks.discard)

            return {
                "success": True,
                "source_id": source_id,
                "sync_mode": sync_mode,
                "message": "Sync triggered — crawling and ingestion started in background",
            }
        except HTTPException:
            raise
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.warning("Data source trigger sync failed: %s", e)
            raise HTTPException(status_code=500, detail=f"Failed to trigger sync: {e}")
    return {"success": True, "source_id": source_id, "sync_mode": sync_mode, "message": "Sync triggered (stub - no DB)"}


# ---------------------------------------------------------------------------
# GET /api/v1/admin/data-sources/{source_id}/status
# ---------------------------------------------------------------------------
@router.get("/{source_id}/status")
async def get_data_source_status(
    source_id: str,
    org: OrgContext = Depends(get_current_org),
) -> dict[str, Any]:
    """Get data source status — cross-org 시 idle/null fallback."""
    state = _get_state()
    repo = state.get("data_source_repo")
    if repo:
        try:
            source = await repo.get(source_id, organization_id=org.id)
            if source:
                return {
                    "source_id": source_id,
                    "status": source.get("status", "idle"),
                    "last_sync": source.get("last_sync_at"),
                    "documents_synced": source.get("last_sync_result", {}).get("documents_synced", 0),
                }
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.warning("Data source status query failed: %s", e)
    return {"source_id": source_id, "status": "idle", "last_sync": None, "documents_synced": 0}


# ---------------------------------------------------------------------------
# POST /api/v1/admin/data-sources/file-ingest
# ---------------------------------------------------------------------------
@router.post("/file-ingest")
async def trigger_file_ingest(
    body: dict[str, Any],
    org: OrgContext = Depends(get_current_org),
) -> dict[str, Any]:
    """Trigger file ingest — caller org scope."""
    state = _get_state()
    repo = state.get("data_source_repo")
    if repo:
        try:
            source_name = body.get("source_name", "file-upload")
            source = await repo.get_by_name(source_name, organization_id=org.id)
            if source:
                await repo.update_status(
                    source["id"], "syncing", organization_id=org.id,
                )
            return {"success": True, "message": "File ingest triggered"}
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.warning("File ingest trigger failed: %s", e)
    return {"success": True, "message": "File ingest triggered"}
