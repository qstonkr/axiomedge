"""Distill build management endpoints.

Build trigger, deploy, rollback, retrain, reset-to-base, delete.
Extracted from distill.py for SRP.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/distill", tags=["Distill Builds"])

_background_tasks: set[asyncio.Task] = set()


def _get_state():
    from src.api.app import _get_state as _inner
    return _inner()


def _get_distill_repo():
    repo = _get_state().get("distill_repo")
    if not repo:
        raise HTTPException(status_code=503, detail="Distill plugin not initialized")
    return repo



class BuildTriggerRequest(BaseModel):
    profile_name: str
    steps: list[str] | None = None
    use_curated_data: bool = False

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
