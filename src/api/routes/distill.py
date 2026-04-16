"""Distill Plugin API — 엣지 모델 프로필/빌드/로그/재학습 관리.

Created: 2026-04-06
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

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


class RetrainRequest(BaseModel):
    profile_name: str
    edge_log_ids: list[str]
    generate_answers: bool = True
    corrected_answers: dict[str, str] | None = None


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
    except Exception as e:  # noqa: BLE001
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
    except Exception as e:  # noqa: BLE001
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

# NOTE: training-data/* endpoints 는 PR9 에서 `distill_training_data.py` 로 분리.

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
        except Exception as e:  # noqa: BLE001
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
            except Exception as e:  # noqa: BLE001
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

# NOTE: training-data generate/generate-test/batches/review-edit endpoints 는
# PR9 에서 `distill_training_data.py` 로 분리.

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


# NOTE: training-data augment/generate-term-qa/cleanup-answers/by-source/batch
# endpoints 는 PR9 에서 `distill_training_data.py` 로 분리.


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
        except Exception as e:  # noqa: BLE001
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
    except Exception as e:  # noqa: BLE001
        return {"app_version": "", "app_downloads": {}, "error": str(e)}


# ---------------------------------------------------------------------------
# Edge Servers (엣지 서버 관리)
