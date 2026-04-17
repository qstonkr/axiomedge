"""Distill Plugin API — 엣지 모델 프로필/빌드/로그/재학습 관리.

Created: 2026-04-06
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.distill.config import (
    DataQualityConfig as _DataQualityConfig,
    DeployConfig as _DeployConfig,
    LoRAConfig as _LoRAConfig,
    QAStyleConfig as _QAStyleConfig,
    TrainingConfig as _TrainingConfig,
)

# NOTE: `from src.api.app import _get_state` 는 deferred (함수 내부) import.
# Module-level 로 두면 `from src.api.routes.distill import router` 가 app.py
# 를 강제 로드해서 test 환경에서 circular import 가 발생한다. 런타임 route
# 호출 시에는 이미 app.py 가 로드돼 있으므로 지연 import 성능 영향 없음.

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/distill", tags=["Distill"])

_background_tasks: set[asyncio.Task] = set()


# ---------------------------------------------------------------------------
# Request / Response Models
# ---------------------------------------------------------------------------

class ProfileCreateRequest(BaseModel):
    name: str = Field(..., max_length=100)
    search_group: str
    # 필수. 디폴트 문자열 금지 — distill_base_models 레지스트리에서 선택한
    # hf_id 를 클라이언트(대시보드)가 반드시 지정해야 한다.
    base_model: str = Field(..., min_length=1, max_length=200)
    description: str = ""
    enabled: bool = True
    lora: _LoRAConfig | None = None
    training: _TrainingConfig | None = None
    qa_style: _QAStyleConfig | None = None
    data_quality: _DataQualityConfig | None = None
    deploy: _DeployConfig | None = None


class ProfileUpdateRequest(BaseModel):
    description: str | None = None
    enabled: bool | None = None
    base_model: str | None = None
    search_group: str | None = None
    lora: _LoRAConfig | None = None
    training: _TrainingConfig | None = None
    qa_style: _QAStyleConfig | None = None
    data_quality: _DataQualityConfig | None = None
    deploy: _DeployConfig | None = None


# NOTE: BuildTriggerRequest, RetrainRequest → distill_builds.py 로 이동
# NOTE: Training data 관련 request model 은 PR9 에서 `distill_training_data.py`
# 로 이동됨 (GenerateDataRequest, GenerateTestDataRequest, AugmentRequest,
# GenerateTermQARequest, TrainingDataUpdateItem/EditReviewRequest,
# TrainingDataAddRequest, TrainingDataReviewRequest).


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_state():
    """Deferred import wrapper for src.api.app._get_state.

    Module-level import of `src.api.app` creates a circular dependency when
    test code imports `from src.api.routes.distill import router` directly
    (distill.py → app.py → include_router(distill) → partially initialized).
    Calling the app helper only when the route handler runs breaks the cycle.
    """
    from src.api.app import _get_state as _inner
    return _inner()


def _get_distill_repo():
    repo = _get_state().get("distill_repo")
    if not repo:
        raise HTTPException(status_code=503, detail="Distill plugin not initialized")
    return repo


# ---------------------------------------------------------------------------
# Profiles CRUD
# ---------------------------------------------------------------------------

@router.get("/profiles")
async def list_profiles():
    """모든 프로필 조회."""
    repo = _get_distill_repo()
    profiles = await repo.list_profiles()
    return {"profiles": {p["name"]: p for p in profiles}}


@router.get("/profiles/{name}")
async def get_profile(name: str):
    """프로필 상세 조회."""
    repo = _get_distill_repo()
    profile = await repo.get_profile(name)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    return profile


async def _validate_base_model(repo, hf_id: str) -> None:
    """base_model 이 distill_base_models 레지스트리에 존재하고 enabled 인지 검증.

    하드코딩 fallback 을 제거한 뒤 방어막 — 대시보드를 우회해 curl/CLI 로
    직접 POST 하는 케이스에서도 잘못된 모델명이 저장되지 않도록 한다.
    """
    entry = await repo.get_base_model(hf_id)
    if entry is None:
        raise HTTPException(
            status_code=400,
            detail=(
                f"base_model '{hf_id}' not found in registry. "
                "Choose one from GET /api/v1/distill/base-models."
            ),
        )
    if not entry.get("enabled", True):
        raise HTTPException(
            status_code=400,
            detail=f"base_model '{hf_id}' is disabled in registry.",
        )


@router.post("/profiles", status_code=201)
async def create_profile(request: ProfileCreateRequest):
    """프로필 생성."""
    repo = _get_distill_repo()
    existing = await repo.get_profile(request.name)
    if existing:
        raise HTTPException(status_code=409, detail="Profile already exists")

    # 검색 그룹 유효성 확인
    group_repo = _get_state().get("search_group_repo")
    if group_repo:
        kb_ids = await group_repo.resolve_kb_ids(group_name=request.search_group)
        if not kb_ids:
            raise HTTPException(status_code=400, detail=f"Search group '{request.search_group}' not found or empty")

    await _validate_base_model(repo, request.base_model)

    data = request.model_dump(exclude_none=True)
    return await repo.create_profile(data)


@router.put("/profiles/{name}")
async def update_profile(name: str, request: ProfileUpdateRequest):
    """프로필 수정."""
    repo = _get_distill_repo()
    if request.base_model is not None:
        await _validate_base_model(repo, request.base_model)
    data = request.model_dump(exclude_none=True)
    result = await repo.update_profile(name, data)
    if not result:
        raise HTTPException(status_code=404, detail="Profile not found")
    return result


@router.delete("/profiles/{name}")
async def delete_profile(name: str):
    """프로필 삭제."""
    repo = _get_distill_repo()
    deleted = await repo.delete_profile(name)
    if not deleted:
        raise HTTPException(status_code=404, detail="Profile not found")
    return {"success": True, "message": f"Profile '{name}' deleted"}


@router.get("/search-groups")
async def list_search_groups():
    """프로필 생성 시 선택 가능한 검색 그룹 목록."""
    group_repo = _get_state().get("search_group_repo")
    if not group_repo:
        return {"groups": []}
    groups = await group_repo.list_all()
    return {"groups": groups}


# ---------------------------------------------------------------------------
# Base Model Registry — 대시보드 드롭다운 SSOT
# ---------------------------------------------------------------------------

class BaseModelUpsertRequest(BaseModel):
    """베이스 모델 레지스트리 upsert 요청.

    hf_id 가 이미 있으면 갱신, 없으면 추가. DB 의 distill_base_models 와 1:1.
    """
    hf_id: str = Field(..., min_length=1, max_length=200)
    display_name: str = Field(..., min_length=1, max_length=200)
    params: str | None = Field(None, max_length=20)
    license: str | None = Field(None, max_length=100)
    commercial_use: bool = False
    verified: bool = False
    notes: str = ""
    enabled: bool = True
    sort_order: int = 0


@router.get("/base-models")
async def list_base_models(enabled_only: bool = True):
    """선택 가능한 베이스 모델 목록. 대시보드 드롭다운에서 사용.

    admin 화면은 disabled 행도 봐야 하므로 ``enabled_only=false`` 로 호출.
    """
    repo = _get_distill_repo()
    models = await repo.list_base_models(enabled_only=enabled_only)
    return {"models": models}


@router.post("/base-models", status_code=201)
async def upsert_base_model_endpoint(request: BaseModelUpsertRequest):
    """베이스 모델 레지스트리 추가/갱신. Admin UI 에서 호출."""
    repo = _get_distill_repo()
    data = request.model_dump()
    return await repo.upsert_base_model(data)


@router.delete("/base-models/{hf_id:path}")
async def delete_base_model_endpoint(hf_id: str):
    """베이스 모델 레지스트리 삭제.

    ``hf_id`` 는 ``google/gemma-3-4b-it`` 처럼 슬래시가 있어 FastAPI ``:path``
    converter 로 캡처한다.

    이미 이 모델을 참조 중인 distill_profiles 가 있으면 FK 제약은 없지만
    드롭다운에서 legacy 라벨로 표시된다. 실제 삭제 대신 ``enabled=False`` 토글
    을 권장.
    """
    repo = _get_distill_repo()
    deleted = await repo.delete_base_model(hf_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Base model not found")
    return {"success": True, "hf_id": hf_id}


# ---------------------------------------------------------------------------

@router.post("/edge-logs/collect")
async def collect_edge_logs(profile_name: str | None = None):
    """S3에서 엣지 로그 수집."""
    repo = _get_distill_repo()
    profiles = await repo.list_profiles()
    if not profiles:
        return {"collected": 0}

    total = 0
    target = [p for p in profiles if p.get("enabled")]
    if profile_name:
        target = [p for p in target if p["name"] == profile_name]

    for profile in target:
        try:
            from src.distill.config import dict_to_profile
            from src.distill.edge_log_collector import EdgeLogCollector
            dp = dict_to_profile(profile)
            collector = EdgeLogCollector(dp)
            count = await collector.collect(repo, profile["name"])
            total += count
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.warning("Log collection failed for %s: %s", profile["name"], e)

    return {"collected": total}


@router.get("/edge-logs")
async def list_edge_logs(
    profile_name: str,
    store_id: str | None = None,
    success: bool | None = None,
    limit: int = 50,
    offset: int = 0,
):
    """엣지 로그 목록."""
    repo = _get_distill_repo()
    return await repo.list_edge_logs(
        profile_name=profile_name, store_id=store_id,
        success=success, limit=limit, offset=offset,
    )


@router.get("/edge-logs/analytics")
async def edge_analytics(profile_name: str, days: int = 7):
    """엣지 사용 통계."""
    repo = _get_distill_repo()
    return await repo.get_edge_analytics(profile_name, days=days)


@router.get("/edge-logs/failed")
async def failed_edge_queries(profile_name: str, limit: int = 50):
    """실패 질의 목록."""
    repo = _get_distill_repo()
    items = await repo.list_failed_queries(profile_name, limit=limit)
    return {"items": items}


