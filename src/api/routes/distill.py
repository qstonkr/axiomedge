"""Distill Plugin API — 엣지 모델 프로필/빌드/로그/재학습 관리.

Created: 2026-04-06
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from fastapi import Header
from pydantic import BaseModel, Field, field_validator

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
    # 필수. 디폴트 문자열 금지 — distill_base_models 레지스트리에서 선택한
    # hf_id 를 클라이언트(대시보드)가 반드시 지정해야 한다.
    base_model: str = Field(..., min_length=1, max_length=200)
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
    use_curated_data: bool = False  # True: approved 데이터만 사용

    @field_validator("steps")
    @classmethod
    def validate_steps(cls, v):
        if v is not None:
            from src.distill.config import VALID_BUILD_STEPS
            unknown = set(v) - VALID_BUILD_STEPS
            if unknown:
                msg = f"Unknown steps: {unknown}"
                raise ValueError(msg)
        return v


class GenerateDataRequest(BaseModel):
    profile_name: str


class GenerateTestDataRequest(BaseModel):
    profile_name: str
    count: int = 50


class TrainingDataUpdateItem(BaseModel):
    id: str
    status: str | None = None
    question: str | None = None
    answer: str | None = None
    review_comment: str | None = None


class TrainingDataEditReviewRequest(BaseModel):
    updates: list[TrainingDataUpdateItem]


class AugmentRequest(BaseModel):
    profile_name: str
    max_variants: int = 3


class GenerateTermQARequest(BaseModel):
    profile_name: str
    top_n: int = 100  # 상위 빈도 용어 수


class ServerUpdateRequest(BaseModel):
    update_type: str = "both"  # model | app | both


class BulkServerUpdateRequest(BaseModel):
    profile_name: str
    update_type: str = "both"


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
            distill_service.run_pipeline(
                build_id, request.profile_name, request.steps,
                use_curated_data=request.use_curated_data,
            )
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


@router.get("/builds/versions")
async def list_version_history(profile_name: str):
    """모델 버전 히스토리."""
    repo = _get_distill_repo()
    versions = await repo.list_version_history(profile_name)
    return {"items": versions}


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
        from src.distill.deployer import DistillDeployer, _parse_s3_uri
        dp = dict_to_profile(profile)
        deployer = DistillDeployer(dp)

        # GPU 학습 빌드는 s3_uri가 훈련 출력 경로(train/<id>/output/...)에 있음.
        # 버전 경로 {prefix}{version}/model.gguf 로 옮기고 DB 갱신.
        # (bucket, key) 튜플로 정규화해서 비교 — 문자열 비교는 trailing slash 등으로 false positive.
        try:
            current_bucket, current_key = _parse_s3_uri(s3_uri)
        except ValueError:
            await repo.update_build(build_id, status="completed")
            raise HTTPException(status_code=400, detail=f"Invalid s3_uri: {s3_uri}")

        versioned_key = f"{dp.deploy.s3_prefix}{build['version']}/model.gguf"
        if (current_bucket, current_key) != (dp.deploy.s3_bucket, versioned_key):
            s3_uri = await deployer.copy_in_s3(s3_uri, build["version"])
            await repo.update_build(build_id, s3_uri=s3_uri)

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

    # 현재 배포 중인 빌드 확인
    current = await repo.get_latest_build(build["profile_name"], status="completed")
    current_id = current["id"] if current and current.get("deployed_at") else None

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

    # rollback_from 기록
    if current_id:
        await repo.rollback_to(build_id, current_id)
    else:
        await repo.update_build(
            build_id, deployed_at=datetime.now(timezone.utc),
        )

    return {"success": True, "rolled_back_to": build["version"]}


# ---------------------------------------------------------------------------
# Training Data
# ---------------------------------------------------------------------------

@router.get("/training-data")
async def list_training_data(
    profile_name: str,
    status: str | None = None,
    source_type: str | None = None,
    batch_id: str | None = None,
    sort_by: str = "created_at",
    sort_order: str = "desc",
    limit: int = 50,
    offset: int = 0,
):
    """학습 데이터 목록."""
    repo = _get_distill_repo()
    return await repo.list_training_data(
        profile_name=profile_name, status=status,
        source_type=source_type, batch_id=batch_id,
        sort_by=sort_by, sort_order=sort_order,
        limit=limit, offset=offset,
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


@router.post("/training-data/smart-approve")
async def smart_approve(profile_name: str, source_type: str | None = None):
    """품질 체크 후 일괄 승인 (불량은 자동 거부, 마크다운은 cleanup 후 승인).

    1. 답변 불가 패턴 → 자동 거부
    2. 너무 짧은 답변 (< 20자) → 자동 거부
    3. 마크다운 잔존 → cleanup 후 승인
    4. 나머지 → 승인
    """
    repo = _get_distill_repo()
    result = await repo.list_training_data(
        profile_name=profile_name, source_type=source_type,
        status="pending", limit=10000,
    )
    items = result.get("items", [])
    if not items:
        return {"approved": 0, "rejected": 0, "cleaned": 0, "total": 0}

    bad_patterns = [
        "제공된 문서들에", "제공된 문서에서", "주어진 문서들에서",
        "명시되어 있지 않", "포함되어 있지 않",
        "직접적인 정보가", "직접적인 정보는",
        "명확한 정보가", "구체적인 정보가 부족",
    ]

    from src.distill.data_gen.quality_filter import cleanup_answer_text

    approve_ids = []
    reject_ids = []
    cleanup_updates = []

    for it in items:
        answer = it.get("answer", "")
        item_id = it["id"]

        # 답변 불가 → 거부
        prefix = answer[:200]
        if sum(1 for p in bad_patterns if p in prefix) >= 2:
            reject_ids.append(item_id)
            continue

        # 너무 짧음 → 거부
        if len(answer.strip()) < 20:
            reject_ids.append(item_id)
            continue

        # 마크다운 cleanup (공통 함수)
        cleaned = cleanup_answer_text(answer)
        if cleaned != answer:
            cleanup_updates.append({"id": item_id, "answer": cleaned})

        approve_ids.append(item_id)

    # 실행
    if reject_ids:
        await repo.update_training_data_status(reject_ids, "rejected")
    if cleanup_updates:
        await repo.bulk_update_training_data(cleanup_updates)
    if approve_ids:
        await repo.update_training_data_status(approve_ids, "approved")

    return {
        "approved": len(approve_ids),
        "rejected": len(reject_ids),
        "cleaned": len(cleanup_updates),
        "total": len(items),
    }


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

    # 요청된 ID의 로그만 조회 (필요한 수만큼)
    logs_result = await repo.list_edge_logs(
        profile_name=request.profile_name,
        success=False,
        limit=len(request.edge_log_ids) + 10,  # 약간의 여유
    )
    logs_by_id = {lg["id"]: lg for lg in logs_result.get("items", [])
                  if lg["id"] in set(request.edge_log_ids)}

    entries_to_save: list[dict] = []
    from src.config import get_settings
    rag_url = get_settings().distill.rag_api_url

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
                async with httpx.AsyncClient() as client:
                    resp = await client.post(
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


# ---------------------------------------------------------------------------
# Data Curation (큐레이션)
# ---------------------------------------------------------------------------

@router.post("/training-data/generate")
async def generate_training_data(request: GenerateDataRequest):
    """큐레이션용 QA 데이터 생성 (백그라운드)."""
    distill_service = _get_state().get("distill_service")
    if not distill_service:
        raise HTTPException(status_code=503, detail="Distill service not initialized")

    async def _run():
        try:
            return await distill_service.generate_data_for_review(request.profile_name)
        except Exception as e:
            logger.error("Data generation failed: %s", e)

    task = asyncio.create_task(_run())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    return {"status": "generating", "profile_name": request.profile_name}


@router.post("/training-data/generate-test")
async def generate_test_data(request: GenerateTestDataRequest):
    """테스트 시드 데이터 생성 (백그라운드)."""
    distill_service = _get_state().get("distill_service")
    if not distill_service:
        raise HTTPException(status_code=503, detail="Distill service not initialized")

    async def _run():
        try:
            return await distill_service.generate_test_data(
                request.profile_name, count=request.count,
            )
        except Exception as e:
            logger.error("Test data generation failed: %s", e)

    task = asyncio.create_task(_run())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    return {"status": "generating", "profile_name": request.profile_name, "count": request.count}


@router.get("/training-data/batches/{batch_id}")
async def get_batch_stats(batch_id: str):
    """배치 생성 현황/통계."""
    repo = _get_distill_repo()
    return await repo.get_batch_stats(batch_id)


@router.put("/training-data/review-edit")
async def review_edit_training_data(request: TrainingDataEditReviewRequest):
    """승인/거부 + 텍스트 편집."""
    repo = _get_distill_repo()
    updated = await repo.bulk_update_training_data(
        [u.model_dump(exclude_none=True) for u in request.updates]
    )
    return {"updated": updated}


# ---------------------------------------------------------------------------
# Model Reset (베이스 모델 리셋)
# ---------------------------------------------------------------------------

@router.post("/builds/reset-to-base")
async def reset_to_base_model(profile_name: str):
    """파인튜닝을 초기화하고 베이스 모델(양자화 GGUF)을 S3에 배포.

    모든 파인튜닝 빌드를 무시하고 원본 베이스 모델로 엣지 서버를 리셋합니다.
    """
    repo = _get_distill_repo()
    profile = await repo.get_profile(profile_name)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    distill_service = _get_state().get("distill_service")
    if not distill_service:
        raise HTTPException(status_code=503, detail="Distill service not initialized")

    # 베이스 모델로 빌드 생성 (학습 없이 양자화 + 배포만)
    build_id = str(uuid.uuid4())
    version = f"v{datetime.now(timezone.utc).strftime('%Y%m%d.%H%M')}-base"

    import json as _json
    await repo.create_build(
        id=build_id,
        profile_name=profile_name,
        version=version,
        status="pending",
        search_group=profile.get("search_group", ""),
        base_model=profile.get("base_model", ""),
        config_snapshot=_json.dumps(profile, ensure_ascii=False, default=str),
    )

    # 양자화 + 배포만 실행 (학습 스킵)
    task = asyncio.create_task(
        distill_service.run_pipeline(
            build_id, profile_name,
            steps=["quantize", "deploy"],
        )
    )
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    return {
        "build_id": build_id,
        "version": version,
        "message": "Base model reset initiated (quantize + deploy, no training)",
    }


# ---------------------------------------------------------------------------
# Augmentation (질문 변형)
# ---------------------------------------------------------------------------

@router.post("/training-data/augment")
async def augment_training_data(request: AugmentRequest):
    """승인된 QA를 질문 변형으로 증강 (백그라운드)."""
    distill_service = _get_state().get("distill_service")
    if not distill_service:
        raise HTTPException(status_code=503, detail="Distill service not initialized")

    async def _run():
        try:
            return await distill_service.augment_approved_data(
                request.profile_name, max_variants=request.max_variants,
            )
        except Exception as e:
            logger.error("Augmentation failed: %s", e)

    task = asyncio.create_task(_run())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    return {"status": "augmenting", "profile_name": request.profile_name}


# ---------------------------------------------------------------------------
# Term QA (용어 학습 데이터)
# ---------------------------------------------------------------------------

@router.post("/training-data/generate-term-qa")
async def generate_term_qa(request: GenerateTermQARequest):
    """PBU 핵심 용어 → QA 학습 데이터 생성 (백그라운드)."""
    distill_service = _get_state().get("distill_service")
    if not distill_service:
        raise HTTPException(status_code=503, detail="Distill service not initialized")

    async def _run():
        try:
            return await distill_service.generate_term_qa(
                request.profile_name, top_n=request.top_n,
            )
        except Exception as e:
            logger.error("Term QA generation failed: %s", e)

    task = asyncio.create_task(_run())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    return {"status": "generating_terms", "profile_name": request.profile_name, "top_n": request.top_n}


# ---------------------------------------------------------------------------
# Answer Cleanup (답변 정리)
# ---------------------------------------------------------------------------

@router.post("/training-data/cleanup-answers")
async def cleanup_answers(profile_name: str, source_type: str | None = None):
    """기존 학습 데이터 답변에서 마크다운/추론/출처 참조 일괄 제거."""
    repo = _get_distill_repo()
    result = await repo.list_training_data(
        profile_name=profile_name, source_type=source_type, limit=10000,
    )
    items = result.get("items", [])
    if not items:
        return {"cleaned": 0}

    from src.distill.data_gen.quality_filter import cleanup_answer_text

    updates = []
    for it in items:
        answer = it.get("answer", "")
        cleaned = cleanup_answer_text(answer)

        if cleaned != answer:
            updates.append({"id": it["id"], "answer": cleaned})

    if updates:
        await repo.bulk_update_training_data(updates)

    return {"cleaned": len(updates), "total": len(items)}


# ---------------------------------------------------------------------------
# Data Reset (초기화)
# ---------------------------------------------------------------------------

@router.delete("/training-data/by-source")
async def delete_by_source_type(profile_name: str, source_type: str):
    """특정 source_type 데이터 일괄 삭제."""
    allowed = {"test_seed", "term_qa", "chunk_qa", "usage_log_aug",
               "chunk_qa_aug", "test_seed_aug", "manual"}
    if source_type not in allowed:
        raise HTTPException(status_code=400, detail=f"Invalid source_type: {source_type}")
    repo = _get_distill_repo()
    deleted = await repo.delete_training_data_by_source(profile_name, source_type)
    return {"deleted": deleted}


@router.delete("/training-data/batch/{batch_id}")
async def delete_batch_data(batch_id: str):
    """특정 배치의 데이터 일괄 삭제."""
    repo = _get_distill_repo()
    deleted = await repo.delete_training_data_by_batch(batch_id)
    return {"deleted": deleted}


@router.delete("/builds/{build_id}")
async def delete_build(build_id: str):
    """빌드 삭제 (배포 중이거나 진행 중인 빌드는 삭제 불가)."""
    repo = _get_distill_repo()
    build = await repo.get_build(build_id)
    if not build:
        raise HTTPException(status_code=404, detail="Build not found")
    if build.get("deployed_at"):
        raise HTTPException(status_code=400, detail="Cannot delete deployed build. Rollback first.")
    if build.get("status") in ("pending", "generating", "training", "evaluating", "quantizing", "deploying"):
        raise HTTPException(status_code=400, detail="Cannot delete in-progress build")

    # S3 GGUF 정리 (best-effort)
    s3_uri = build.get("s3_uri")
    if s3_uri:
        try:
            profile = await repo.get_profile(build["profile_name"])
            if profile:
                from src.distill.config import dict_to_profile
                from src.distill.deployer import DistillDeployer
                dp = dict_to_profile(profile)
                deployer = DistillDeployer(dp)
                await deployer.delete_s3_object(s3_uri)
        except Exception as e:
            logger.warning("S3 cleanup failed for build %s: %s", build_id, e)

    deleted = await repo.delete_build(build_id)
    if not deleted:
        raise HTTPException(status_code=500, detail="Delete failed")
    return {"success": True, "build_id": build_id}


# ---------------------------------------------------------------------------
# App Build (앱 빌드/배포)
# ---------------------------------------------------------------------------

@router.get("/app/info")
async def get_app_info(profile_name: str):
    """현재 앱 버전 + 다운로드 정보 조회 (manifest에서)."""
    repo = _get_distill_repo()
    profile = await repo.get_profile(profile_name)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    try:
        from src.distill.config import dict_to_profile
        dp = dict_to_profile(profile)

        def _fetch():
            from src.distill.deployer import _s3_client
            s3 = _s3_client()
            manifest_key = f"{dp.deploy.s3_prefix}manifest.json"
            resp = s3.get_object(Bucket=dp.deploy.s3_bucket, Key=manifest_key)
            import json as _json
            manifest = _json.loads(resp["Body"].read())
            return {
                "app_version": manifest.get("app_version", ""),
                "app_downloads": manifest.get("app_downloads", {}),
                "model_version": manifest.get("version", ""),
                "manifest_url": f"s3://{dp.deploy.s3_bucket}/{manifest_key}",
            }

        return await asyncio.to_thread(_fetch)
    except Exception as e:
        return {"app_version": "", "app_downloads": {}, "error": str(e)}


# ---------------------------------------------------------------------------
# Edge Servers (엣지 서버 관리)
# ---------------------------------------------------------------------------

class HeartbeatRequest(BaseModel):
    store_id: str
    status: str = "online"
    model_version: str | None = None
    model_sha256: str | None = None
    app_version: str | None = None
    os_type: str | None = None
    cpu_info: str | None = None
    ram_total_mb: int | None = None
    ram_used_mb: int | None = None
    disk_free_mb: int | None = None
    avg_latency_ms: int | None = None
    total_queries: int = 0
    success_rate: float | None = None
    server_ip: str | None = None
    profile_name: str | None = None
    display_name: str | None = None


class StoreRegisterRequest(BaseModel):
    """매장 사전 등록 요청."""
    store_id: str
    profile_name: str
    display_name: str = ""


@router.post("/edge-servers/register")
async def register_edge_server(request: StoreRegisterRequest):
    """매장 사전 등록 — 본사에서 장비 출고 전 등록.

    API key를 자동 발급하고, 장비에 세팅할 완전한 출고 명령어를 반환.
    """
    import hashlib
    import re
    import secrets

    if not re.match(r"^[a-z0-9][a-z0-9_-]{1,48}[a-z0-9]$", request.store_id):
        raise HTTPException(
            status_code=400,
            detail="store_id는 영소문자, 숫자, 하이픈만 가능 (3~50자)",
        )

    repo = _get_distill_repo()

    existing = await repo.get_edge_server(request.store_id)
    if existing:
        raise HTTPException(status_code=409, detail=f"Store '{request.store_id}' already registered")

    api_key = f"edge-{secrets.token_urlsafe(24)}"
    api_key_hash = hashlib.sha256(api_key.encode()).hexdigest()

    try:
        await repo.register_edge_server(
            store_id=request.store_id,
            profile_name=request.profile_name,
            display_name=request.display_name or request.store_id,
            api_key_hash=api_key_hash,
        )
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))

    # 출고 설정 생성 (API key 포함)
    provision = _build_provision_config(request.store_id, request.profile_name, api_key)

    return {
        "store_id": request.store_id,
        "api_key": api_key,
        "profile_name": request.profile_name,
        "status": "pending",
        "provision_command": provision["command"],
        "message": "매장 등록 완료. 아래 출고 명령어를 장비에서 실행하세요.",
    }


def _build_provision_config(
    store_id: str, profile_name: str, api_key: str | None = None,
) -> dict:
    """출고 설정 생성 (내부 헬퍼)."""
    import os
    import socket
    # 환경변수 우선, 없으면 로컬 IP 자동 감지
    api_url = os.getenv("EXTERNAL_API_URL", "")
    if not api_url:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
            s.close()
            api_url = f"http://{local_ip}:8000"
        except Exception:
            api_url = "http://localhost:8000"

    # S3 직접 접근이 아닌 API를 통해 manifest 제공 (S3 퍼블릭 차단 대응)
    manifest_url = f"{api_url}/api/v1/distill/manifest/{profile_name}"

    parts = [
        f"STORE_ID={store_id}",
        f"MANIFEST_URL={manifest_url}",
        f"CENTRAL_API_URL={api_url}",
    ]
    if api_key:
        parts.insert(1, f"EDGE_API_KEY={api_key}")

    command = (
        f"curl -sfL {api_url}/api/v1/distill/provision.sh -o /tmp/provision.sh && \\\n  "
        + " \\\n  ".join(parts)
        + " \\\n  bash /tmp/provision.sh"
    )

    return {
        "store_id": store_id,
        "profile_name": profile_name,
        "env": {
            "STORE_ID": store_id,
            "EDGE_API_KEY": api_key or "(등록 시 발급된 키 사용)",
            "MANIFEST_URL": manifest_url,
            "CENTRAL_API_URL": api_url,
        },
        "command": command,
    }


@router.get("/edge-servers/{store_id}/provision")
async def provision_edge_server(store_id: str):
    """출고 설정 — 장비에 세팅할 환경 설정 반환.

    ⚠ EDGE_API_KEY는 등록 시 1회만 발급. 분실 시 삭제 후 재등록 필요.
    """
    repo = _get_distill_repo()
    server = await repo.get_edge_server(store_id)
    if not server:
        raise HTTPException(status_code=404, detail="Store not found")

    config = _build_provision_config(
        store_id, server.get("profile_name", ""),
    )
    return config


@router.post("/edge-servers/heartbeat")
async def edge_server_heartbeat(
    request: HeartbeatRequest,
    authorization: str | None = Header(None),
):
    """heartbeat 수신 (등록 겸용, Bearer 인증 필수)."""
    repo = _get_distill_repo()

    api_key = ""
    if authorization and authorization.startswith("Bearer "):
        api_key = authorization[7:]
    if not api_key:
        raise HTTPException(status_code=401, detail="Missing Authorization Bearer token")

    try:
        result = await repo.upsert_heartbeat(request.model_dump(), api_key)
        return result
    except PermissionError:
        raise HTTPException(status_code=401, detail="Invalid API key")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/edge-servers")
async def list_edge_servers(
    profile_name: str | None = None,
    status: str | None = None,
):
    """등록된 엣지 서버 목록."""
    repo = _get_distill_repo()
    servers = await repo.list_edge_servers(profile_name=profile_name, status=status)
    return {"items": servers}


@router.get("/manifest/{profile_name}")
async def get_manifest(profile_name: str):
    """매니페스트 프록시 — S3에서 가져오고 download_url을 매 호출마다 재서명.

    저장된 download_url은 임시 STS 세션 만료 시 함께 무효화되므로,
    엣지가 조회할 때마다 fresh한 pre-signed URL을 발급해 응답에 주입한다.
    """
    import json as _json

    from src.distill.deployer import _parse_s3_uri, _s3_client

    repo = _get_distill_repo()
    profile = await repo.get_profile(profile_name)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    config = profile.get("config", "{}")
    if isinstance(config, str):
        config = _json.loads(config) if config else {}
    deploy = config.get("deploy", {})
    bucket = deploy.get("s3_bucket", "gs-knowledge-models")
    prefix = deploy.get("s3_prefix", f"{profile_name}/")
    manifest_key = f"{prefix}manifest.json"

    try:
        s3 = _s3_client()
        obj = s3.get_object(Bucket=bucket, Key=manifest_key)
        manifest = _json.loads(obj["Body"].read().decode())
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Manifest not found: {e}")

    s3_uri = manifest.get("s3_uri", "")
    if s3_uri.startswith("s3://"):
        try:
            model_bucket, model_key = _parse_s3_uri(s3_uri)
            manifest["download_url"] = s3.generate_presigned_url(
                "get_object",
                Params={"Bucket": model_bucket, "Key": model_key},
                ExpiresIn=86400,
            )
        except Exception as e:
            logger.warning("Failed to refresh presigned URL for %s: %s", s3_uri, e)

    return manifest


class AppVersionRequest(BaseModel):
    version: str


@router.post("/profiles/{profile_name}/app-version")
async def set_app_version(profile_name: str, request: AppVersionRequest):
    """엣지 앱 소스 버전 태그를 갱신.

    실제 바이너리/패키지 배포는 하지 않는다. S3 manifest.json 의
    `app_version` 필드만 업데이트. 엣지는 다음 heartbeat 시 이 값을
    자기 `.app_version` 과 비교해 차이가 있으면 중앙 API 의 edge-files
    엔드포인트에서 server.py / sync.py 를 재다운로드 (source-file update).
    """
    import json as _json

    from src.distill.deployer import _s3_client

    repo = _get_distill_repo()
    profile = await repo.get_profile(profile_name)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    config = profile.get("config", "{}")
    if isinstance(config, str):
        config = _json.loads(config) if config else {}
    deploy = config.get("deploy", {})
    bucket = deploy.get("s3_bucket", "gs-knowledge-models")
    prefix = deploy.get("s3_prefix", f"{profile_name}/")
    manifest_key = f"{prefix}manifest.json"

    def _update():
        s3 = _s3_client()
        try:
            obj = s3.get_object(Bucket=bucket, Key=manifest_key)
            manifest = _json.loads(obj["Body"].read().decode())
        except Exception:
            manifest = {}
        manifest["app_version"] = request.version
        s3.put_object(
            Bucket=bucket,
            Key=manifest_key,
            Body=_json.dumps(manifest, ensure_ascii=False, indent=2),
            ContentType="application/json",
        )
        return manifest

    try:
        updated = await asyncio.to_thread(_update)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update app version: {e}")

    return {
        "success": True,
        "profile_name": profile_name,
        "app_version": request.version,
        "manifest": updated,
    }


@router.get("/provision.sh")
async def download_provision_script():
    """출고 스크립트 다운로드."""
    from pathlib import Path
    from fastapi.responses import FileResponse
    script = Path(__file__).resolve().parents[3] / "edge" / "provision.sh"
    if not script.exists():
        raise HTTPException(status_code=404, detail="provision.sh not found")
    return FileResponse(script, media_type="text/plain", filename="provision.sh")


@router.get("/edge-files/{filename}")
async def download_edge_file(filename: str):
    """엣지 서버 코드 파일 다운로드 (server.py, sync.py)."""
    from pathlib import Path
    from fastapi.responses import FileResponse
    allowed = {"server.py", "sync.py"}
    if filename not in allowed:
        raise HTTPException(status_code=404, detail=f"File not found: {filename}")
    filepath = Path(__file__).resolve().parents[3] / "edge" / filename
    if not filepath.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {filename}")
    return FileResponse(filepath, media_type="text/plain", filename=filename)


@router.get("/edge-servers/fleet-stats")
async def fleet_stats(profile_name: str):
    """fleet 현황 통계."""
    repo = _get_distill_repo()
    return await repo.get_fleet_stats(profile_name)


@router.get("/edge-servers/{store_id}")
async def get_edge_server(store_id: str):
    """서버 상세."""
    repo = _get_distill_repo()
    server = await repo.get_edge_server(store_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    return server


@router.delete("/edge-servers/{store_id}")
async def delete_edge_server(store_id: str):
    """서버 등록 해제."""
    repo = _get_distill_repo()
    deleted = await repo.delete_edge_server(store_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Server not found")
    return {"success": True}


@router.post("/edge-servers/{store_id}/request-update")
async def request_server_update(store_id: str, request: ServerUpdateRequest):
    """엣지 서버 업데이트 요청 (다음 sync 주기에 반영)."""
    repo = _get_distill_repo()
    try:
        return await repo.request_server_update(store_id, request.update_type)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/edge-servers/bulk-request-update")
async def bulk_request_update(request: BulkServerUpdateRequest):
    """구버전 서버 전체 업데이트 요청."""
    repo = _get_distill_repo()
    count = await repo.bulk_request_server_update(
        request.profile_name, request.update_type,
    )
    return {"updated": count}
