"""Distill Plugin API — 엣지 모델 프로필/빌드/로그/재학습 관리.

Created: 2026-04-06
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.api.app import _get_state

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/distill", tags=["Distill"])

_background_tasks: set[asyncio.Task] = set()


# ---------------------------------------------------------------------------
# Request / Response Models
# ---------------------------------------------------------------------------

class ProfileCreateRequest(BaseModel):
    name: str = Field(..., max_length=100)
    search_group: str
    base_model: str = "Qwen/Qwen2.5-0.5B-Instruct"
    description: str = ""
    enabled: bool = True
    lora: dict | None = None
    training: dict | None = None
    qa_style: dict | None = None
    data_quality: dict | None = None
    deploy: dict | None = None


class ProfileUpdateRequest(BaseModel):
    description: str | None = None
    enabled: bool | None = None
    base_model: str | None = None
    search_group: str | None = None
    lora: dict | None = None
    training: dict | None = None
    qa_style: dict | None = None
    data_quality: dict | None = None
    deploy: dict | None = None


class BuildTriggerRequest(BaseModel):
    profile_name: str
    steps: list[str] | None = None  # None=전체


class TrainingDataAddRequest(BaseModel):
    profile_name: str
    question: str
    answer: str
    source_type: str = "manual"
    kb_id: str | None = None


class TrainingDataReviewRequest(BaseModel):
    ids: list[str]
    status: str  # approved | rejected


class RetrainRequest(BaseModel):
    profile_name: str
    edge_log_ids: list[str]
    generate_answers: bool = True
    corrected_answers: dict[str, str] | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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

    data = request.model_dump(exclude_none=True)
    return await repo.create_profile(data)


@router.put("/profiles/{name}")
async def update_profile(name: str, request: ProfileUpdateRequest):
    """프로필 수정."""
    repo = _get_distill_repo()
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
# Builds
# ---------------------------------------------------------------------------

@router.post("/builds")
async def trigger_build(request: BuildTriggerRequest):
    """빌드 시작 (백그라운드)."""
    repo = _get_distill_repo()
    profile = await repo.get_profile(request.profile_name)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    if not profile.get("enabled"):
        raise HTTPException(status_code=400, detail="Profile is disabled")

    build_id = str(uuid.uuid4())
    version = f"v{datetime.now(timezone.utc).strftime('%Y%m%d.%H%M')}"

    import json as _json
    await repo.create_build(
        id=build_id,
        profile_name=request.profile_name,
        version=version,
        status="pending",
        search_group=profile.get("search_group", ""),
        base_model=profile.get("base_model", ""),
        config_snapshot=_json.dumps(profile, ensure_ascii=False, default=str),
    )

    # 학습 파이프라인 백그라운드 실행
    distill_service = _get_state().get("distill_service")
    if distill_service:
        task = asyncio.create_task(
            distill_service.run_pipeline(build_id, request.profile_name, request.steps)
        )
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)
    else:
        logger.warning("Distill service not initialized, build %s will stay pending", build_id)

    return {"build_id": build_id, "version": version, "status": "pending"}


@router.get("/builds")
async def list_builds(profile_name: str | None = None, limit: int = 50):
    """빌드 이력 조회."""
    repo = _get_distill_repo()
    builds = await repo.list_builds(profile_name=profile_name, limit=limit)
    return {"items": builds}


@router.get("/builds/{build_id}")
async def get_build(build_id: str):
    """빌드 상세 조회."""
    repo = _get_distill_repo()
    build = await repo.get_build(build_id)
    if not build:
        raise HTTPException(status_code=404, detail="Build not found")
    return build


@router.post("/builds/{build_id}/deploy")
async def deploy_build(build_id: str):
    """특정 빌드를 배포 (S3 manifest 갱신)."""
    repo = _get_distill_repo()
    build = await repo.get_build(build_id)
    if not build:
        raise HTTPException(status_code=404, detail="Build not found")
    if build.get("status") != "completed":
        raise HTTPException(status_code=400, detail="Only completed builds can be deployed")

    await repo.update_build(build_id, status="deploying")

    # S3 manifest 갱신
    try:
        profile = await repo.get_profile(build["profile_name"])
        if not profile:
            await repo.update_build(build_id, status="completed")
            raise HTTPException(status_code=404, detail=f"Profile '{build['profile_name']}' not found")

        s3_uri = build.get("s3_uri")
        if not s3_uri:
            await repo.update_build(build_id, status="completed")
            raise HTTPException(status_code=400, detail="Build has no S3 URI")

        from src.distill.config import dict_to_profile
        from src.distill.deployer import DistillDeployer
        dp = dict_to_profile(profile)
        deployer = DistillDeployer(dp)
        await deployer.create_and_upload_manifest(s3_uri, build["version"], build)

        await repo.update_build(
            build_id, status="completed",
            deployed_at=datetime.now(timezone.utc),
        )
    except HTTPException:
        raise
    except Exception as e:
        await repo.update_build(build_id, status="completed",
                                error_message=f"Deploy failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    return {"success": True, "build_id": build_id}


@router.post("/builds/{build_id}/rollback")
async def rollback_build(build_id: str):
    """이전 빌드로 롤백 (해당 빌드의 manifest를 current로 복원)."""
    repo = _get_distill_repo()
    build = await repo.get_build(build_id)
    if not build:
        raise HTTPException(status_code=404, detail="Build not found")
    if not build.get("s3_uri"):
        raise HTTPException(status_code=400, detail="Build has no S3 URI")

    profile = await repo.get_profile(build["profile_name"])
    if not profile:
        raise HTTPException(status_code=404, detail=f"Profile '{build['profile_name']}' not found")

    try:
        from src.distill.config import dict_to_profile
        from src.distill.deployer import DistillDeployer
        dp = dict_to_profile(profile)
        deployer = DistillDeployer(dp)
        await deployer.create_and_upload_manifest(
            build["s3_uri"], build["version"], build,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Rollback failed: {e}")

    return {"success": True, "rolled_back_to": build["version"]}


# ---------------------------------------------------------------------------
# Training Data
# ---------------------------------------------------------------------------

@router.get("/training-data")
async def list_training_data(
    profile_name: str,
    status: str | None = None,
    source_type: str | None = None,
    limit: int = 50,
    offset: int = 0,
):
    """학습 데이터 목록."""
    repo = _get_distill_repo()
    return await repo.list_training_data(
        profile_name=profile_name, status=status,
        source_type=source_type, limit=limit, offset=offset,
    )


@router.post("/training-data", status_code=201)
async def add_training_data(request: TrainingDataAddRequest):
    """수동 QA 추가."""
    repo = _get_distill_repo()
    count = await repo.save_training_data([request.model_dump()])
    return {"added": count}


@router.put("/training-data/review")
async def review_training_data(request: TrainingDataReviewRequest):
    """학습 데이터 승인/거부."""
    if request.status not in ("approved", "rejected"):
        raise HTTPException(status_code=400, detail="Status must be 'approved' or 'rejected'")
    repo = _get_distill_repo()
    updated = await repo.update_training_data_status(request.ids, request.status)
    return {"updated": updated}


@router.get("/training-data/stats")
async def training_data_stats(profile_name: str):
    """프로필별 학습 데이터 통계."""
    repo = _get_distill_repo()
    return await repo.get_training_data_stats(profile_name)


# ---------------------------------------------------------------------------
# Edge Logs
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
        except Exception as e:
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


# ---------------------------------------------------------------------------
# Retrain
# ---------------------------------------------------------------------------

@router.post("/retrain")
async def trigger_retrain(request: RetrainRequest):
    """실패 질문 → 학습 데이터 추가 + 재학습 트리거."""
    repo = _get_distill_repo()
    profile = await repo.get_profile(request.profile_name)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    # 실패 로그를 한 번에 조회하여 id → log 매핑
    logs_result = await repo.list_edge_logs(
        profile_name=request.profile_name,
        success=False,
        limit=max(len(request.edge_log_ids), 100),
    )
    logs_by_id = {lg["id"]: lg for lg in logs_result.get("items", [])}

    entries_to_save: list[dict] = []
    import os as _os
    rag_url = _os.getenv("RAG_API_URL", "http://localhost:8000")

    for log_id in request.edge_log_ids:
        edge_log = logs_by_id.get(log_id)
        question = edge_log.get("query", "") if edge_log else ""
        if not question:
            logger.warning("Edge log %s not found or has no query", log_id)
            continue

        # 답변 결정: 수동 입력 > RAG 자동 생성
        corrected = (request.corrected_answers or {}).get(log_id)
        if corrected:
            answer = corrected
        elif request.generate_answers:
            try:
                import httpx
                resp = httpx.post(
                    f"{rag_url}/api/v1/search/hub",
                    json={"query": question, "top_k": 5, "include_answer": True},
                    timeout=60,
                )
                resp.raise_for_status()
                answer = resp.json().get("answer", "")
            except Exception as e:
                logger.warning("Teacher answer generation failed for '%s': %s", question[:30], e)
                answer = ""
        else:
            continue

        if not answer:
            continue

        entries_to_save.append({
            "profile_name": request.profile_name,
            "question": question,
            "answer": answer,
            "source_type": "retrain",
            "source_id": log_id,
        })

    added = 0
    if entries_to_save:
        added = await repo.save_training_data(entries_to_save)

    return {"added": added, "message": f"{added} entries added to training data"}
