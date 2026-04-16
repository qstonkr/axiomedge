"""Distill — training data endpoints.

PR9 에서 `src/api/routes/distill.py` (1373 줄) 의 training data 관련
15 개 endpoint 를 이 파일로 분리. URL prefix `/api/v1/distill` 은 동일
하게 유지해 외부 consumer 에 영향 없음.

분리 기준: "학습 데이터 (QA 쌍) 수명주기 전반" — 조회/추가/승인/거부/
생성/증강/cleanup/삭제/batch 통계. Edge server, build, profile 등 다른
도메인 endpoint 는 계속 `distill.py` 에 남아 있음 (후속 PR 에서 분리 예정).

구조:
    - 모든 Pydantic request model 은 이 파일에 정의 (distill.py 의 중복
      제거를 위해 이동). 기존 이름은 `_schemas.py` 공용 모듈 대신 이 파일
      에 유지 — training_data 전용 스키마이므로 OK.
    - router 는 별도 APIRouter 인스턴스. 같은 prefix 로 `include_router`
      되면 FastAPI 가 경로 합쳐서 처리.
    - `_get_distill_repo` 같은 private helper 는 `distill.py` 에서 import.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

# NOTE: `from src.api.app import _get_state` 는 deferred (함수 내부) import.
# distill.py 와 동일한 이유 — test 에서 직접 import 시 circular 방지.

logger = logging.getLogger(__name__)

# 같은 prefix + 다른 tag 로 FastAPI OpenAPI 분리.
router = APIRouter(prefix="/api/v1/distill", tags=["Distill - Training Data"])


def _get_state():
    """Deferred import wrapper — circular import 회피용."""
    from src.api.app import _get_state as _inner
    return _inner()


def _get_distill_repo():
    """distill repo 를 state 에서 조회 (distill.py 의 동일 helper 와 duplicate).

    이 helper 를 `distill.py` 에서 import 하면 circular import 가 생긴다
    (`distill.py` → `src.api.app` → 여러 route 파일 → `distill.py`). 복잡도가
    낮은 helper 이므로 각 route 모듈에 복제. 향후 공용 `_distill_common.py`
    로 추출 예정 (follow-up PR).
    """
    repo = _get_state().get("distill_repo")
    if not repo:
        raise HTTPException(status_code=503, detail="Distill plugin not initialized")
    return repo

# Background task 참조 유지 (asyncio.create_task 결과가 GC 되지 않도록).
# distill.py 의 것과 별개 — 파일별로 독립적으로 관리.
_background_tasks: set[asyncio.Task] = set()


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


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


class TrainingDataAddRequest(BaseModel):
    profile_name: str
    question: str
    answer: str
    source_type: str = "manual"
    kb_id: str | None = None


class TrainingDataReviewRequest(BaseModel):
    ids: list[str]
    status: str  # approved | rejected


# ---------------------------------------------------------------------------
# List / add / review
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


@router.put("/training-data/review-edit")
async def review_edit_training_data(request: TrainingDataEditReviewRequest):
    """승인/거부 + 텍스트 편집."""
    repo = _get_distill_repo()
    updated = await repo.bulk_update_training_data(
        [u.model_dump(exclude_none=True) for u in request.updates],
    )
    return {"updated": updated}


@router.get("/training-data/stats")
async def training_data_stats(profile_name: str):
    """프로필별 학습 데이터 통계."""
    repo = _get_distill_repo()
    return await repo.get_training_data_stats(profile_name)


@router.get("/training-data/batches/{batch_id}")
async def get_batch_stats(batch_id: str):
    """배치 생성 현황/통계."""
    repo = _get_distill_repo()
    return await repo.get_batch_stats(batch_id)


# ---------------------------------------------------------------------------
# Smart approve (품질 기반 일괄 승인)
# ---------------------------------------------------------------------------


# Smart-approve 에서 "답변 불가" 패턴으로 거부하는 문구 (prefix 매칭).
# 2개 이상 일치하면 reject. 2026-04 사용자 피드백 기반 튜닝.
_BAD_ANSWER_PATTERNS = (
    "제공된 문서들에", "제공된 문서에서", "주어진 문서들에서",
    "명시되어 있지 않", "포함되어 있지 않",
    "직접적인 정보가", "직접적인 정보는",
    "명확한 정보가", "구체적인 정보가 부족",
)
_MIN_ANSWER_CHARS = 20
_BAD_PATTERN_MATCH_THRESHOLD = 2


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

    from src.distill.data_gen.quality_filter import cleanup_answer_text

    approve_ids: list[str] = []
    reject_ids: list[str] = []
    cleanup_updates: list[dict] = []

    for it in items:
        answer = it.get("answer", "")
        item_id = it["id"]

        # 답변 불가 → 거부
        prefix = answer[:200]
        if sum(1 for p in _BAD_ANSWER_PATTERNS if p in prefix) >= _BAD_PATTERN_MATCH_THRESHOLD:
            reject_ids.append(item_id)
            continue

        # 너무 짧음 → 거부
        if len(answer.strip()) < _MIN_ANSWER_CHARS:
            reject_ids.append(item_id)
            continue

        # 마크다운 cleanup
        cleaned = cleanup_answer_text(answer)
        if cleaned != answer:
            cleanup_updates.append({"id": item_id, "answer": cleaned})

        approve_ids.append(item_id)

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


# ---------------------------------------------------------------------------
# Generation (background tasks)
# ---------------------------------------------------------------------------


def _require_distill_service():
    svc = _get_state().get("distill_service")
    if not svc:
        raise HTTPException(status_code=503, detail="Distill service not initialized")
    return svc


def _spawn_background(coro_factory, *, label: str) -> None:
    """백그라운드 coroutine 실행 + task 참조 유지 + 실패 로깅."""
    async def _run():
        try:
            return await coro_factory()
        except Exception as e:
            logger.error("%s failed: %s", label, e, exc_info=True)

    task = asyncio.create_task(_run())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


@router.post("/training-data/generate")
async def generate_training_data(request: GenerateDataRequest):
    """큐레이션용 QA 데이터 생성 (백그라운드)."""
    svc = _require_distill_service()
    _spawn_background(
        lambda: svc.generate_data_for_review(request.profile_name),
        label="Data generation",
    )
    return {"status": "generating", "profile_name": request.profile_name}


@router.post("/training-data/generate-test")
async def generate_test_data(request: GenerateTestDataRequest):
    """테스트 시드 데이터 생성 (백그라운드)."""
    svc = _require_distill_service()
    _spawn_background(
        lambda: svc.generate_test_data(request.profile_name, count=request.count),
        label="Test data generation",
    )
    return {"status": "generating", "profile_name": request.profile_name, "count": request.count}


@router.post("/training-data/augment")
async def augment_training_data(request: AugmentRequest):
    """승인된 QA를 질문 변형으로 증강 (백그라운드)."""
    svc = _require_distill_service()
    _spawn_background(
        lambda: svc.augment_approved_data(
            request.profile_name, max_variants=request.max_variants,
        ),
        label="Augmentation",
    )
    return {"status": "augmenting", "profile_name": request.profile_name}


@router.post("/training-data/generate-term-qa")
async def generate_term_qa(request: GenerateTermQARequest):
    """PBU 핵심 용어 → QA 학습 데이터 생성 (백그라운드)."""
    svc = _require_distill_service()
    _spawn_background(
        lambda: svc.generate_term_qa(request.profile_name, top_n=request.top_n),
        label="Term QA generation",
    )
    return {"status": "generating_terms", "profile_name": request.profile_name, "top_n": request.top_n}


# ---------------------------------------------------------------------------
# Cleanup / delete
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


_ALLOWED_DELETE_SOURCE_TYPES = frozenset({
    "test_seed", "term_qa", "chunk_qa", "usage_log_aug",
    "chunk_qa_aug", "test_seed_aug", "manual",
})


@router.delete("/training-data/by-source")
async def delete_by_source_type(profile_name: str, source_type: str):
    """특정 source_type 데이터 일괄 삭제."""
    if source_type not in _ALLOWED_DELETE_SOURCE_TYPES:
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
