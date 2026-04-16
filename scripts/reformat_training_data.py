"""학습 데이터 답변 재작성 배치 — Phase 1.

기존 approved training data 의 답변을 AnswerReformatter 로 2문단 풍부 포맷으로
재작성. 원본은 건드리지 않고 source_type="reformatted" 신규 행으로 저장.

Usage:
    AWS_PROFILE=jeongbeomkim \\
      uv run python scripts/reformat_training_data.py --profile pbu-store

    # Dry run (LLM 호출만, DB 저장 안함) — 샘플 눈으로 확인용
    uv run python scripts/reformat_training_data.py --profile pbu-store --limit 5 --dry-run

    # 동시성 조정 (기본 4, 너무 높이면 SageMaker throttle)
    uv run python scripts/reformat_training_data.py --profile pbu-store --concurrency 6

결과 확인:
    # 배치 통계
    curl http://localhost:8000/api/v1/distill/training-data/stats?profile_name=pbu-store
    # 대시보드에서 배치 ID 로 필터 → 사람이 승인 → 학습 트리거
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import uuid

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.config import get_settings
from src.stores.postgres.session import to_async_database_url
from src.distill.config import dict_to_profile
from src.distill.data_gen.llm_helper import LLMHelper
from src.distill.data_gen.reformatter import AnswerReformatter, build_reformatted_row
from src.distill.repository import DistillRepository

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("reformat_batch")


async def _run(profile_name: str, limit: int, dry_run: bool, concurrency: int) -> int:
    settings = get_settings()
    db_url = to_async_database_url(settings.database.database_url)
    engine = create_async_engine(db_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    # Teacher LLM — service 경로와 동일하게 SageMaker EXAONE
    from src.llm.sagemaker_client import SageMakerConfig, SageMakerLLMClient
    llm_client = SageMakerLLMClient(config=SageMakerConfig())
    llm_helper = LLMHelper(
        llm_client,
        qdrant_url=settings.qdrant.url,
        concurrency=concurrency,
        timeout_sec=90,  # 재작성은 길어질 수 있어서 조금 여유
    )

    repo = DistillRepository(session_factory)

    profile_dict = await repo.get_profile(profile_name)
    if not profile_dict:
        logger.error("Profile not found: %s", profile_name)
        return 1
    profile = dict_to_profile(profile_dict.get("config", profile_dict))
    logger.info("Profile: %s (base_model=%s)", profile_name, profile.base_model)

    # 원본 approved 행 조회 — reformatted 는 제외 (이미 재작성된 것)
    # + 이미 재작성본이 존재하는 원본도 스킵 (재실행 시 실패분만 처리)
    result = await repo.list_training_data(
        profile_name=profile_name, status="approved", limit=100000,
    )
    all_rows = result.get("items", [])

    # pending + approved 양쪽 reformatted 행 전부 조회 (계보 추적용)
    reformatted_any = []
    for st in ("pending", "approved"):
        sub = await repo.list_training_data(
            profile_name=profile_name, status=st,
            source_type="reformatted", limit=100000,
        )
        reformatted_any.extend(sub.get("items", []))
    already_reformatted_ids = {
        r.get("augmented_from") for r in reformatted_any if r.get("augmented_from")
    }

    candidates = [
        r for r in all_rows
        if r.get("source_type") != "reformatted"
        and r.get("id") not in already_reformatted_ids
    ]
    if limit > 0:
        candidates = candidates[:limit]
    logger.info(
        "Candidates: %d (total approved: %d, already reformatted: %d)",
        len(candidates), len(all_rows), len(already_reformatted_ids),
    )

    if not candidates:
        logger.warning("No candidates to reformat")
        return 0

    # Reformatter 실행
    reformatter = AnswerReformatter(
        llm_helper=llm_helper, max_retries=1, concurrency=concurrency,
    )
    logger.info("Starting reformat (concurrency=%d, dry_run=%s)", concurrency, dry_run)

    summary, results = await reformatter.reformat_batch(candidates)

    # 샘플 출력 (최대 3개) — 눈으로 품질 확인
    print("\n" + "=" * 80)
    print(f"Reformat batch summary ({profile_name})")
    print("=" * 80)
    print(f"  Total:   {summary.total}")
    print(f"  Success: {summary.success}")
    print(f"  Failed:  {summary.failed}")
    print(f"  Avg len: {summary.avg_answer_len:.1f} chars")
    if summary.failure_reasons:
        print("  Failure reasons:")
        for reason, count in sorted(
            summary.failure_reasons.items(), key=lambda x: -x[1],
        ):
            print(f"    {reason}: {count}")

    success_samples = [r for r in results if r.success][:3]
    for i, r in enumerate(success_samples):
        orig = next(c for c in candidates if c.get("id") == r.source_id)
        print(f"\n--- Sample {i + 1} ---")
        print(f"Q: {orig['question'][:100]}")
        print(f"Before ({len(orig['answer'])} chars): {orig['answer'][:150]}...")
        print(f"After  ({len(r.reformatted_answer)} chars):")
        print(r.reformatted_answer)

    if dry_run:
        print("\n[DRY RUN] DB 저장 생략")
        return 0

    # 성공 결과를 DB 에 저장 (원본은 건드리지 않음)
    batch_id = str(uuid.uuid4())
    new_rows = []
    for r in results:
        if not r.success or not r.reformatted_answer:
            continue
        orig = next(c for c in candidates if c.get("id") == r.source_id)
        new_rows.append(build_reformatted_row(
            orig, r.reformatted_answer,
            profile_name=profile_name, batch_id=batch_id,
        ))

    if new_rows:
        saved = await repo.save_training_data_batch(new_rows)
        logger.info(
            "Saved %d reformatted rows (batch_id=%s)",
            saved, batch_id,
        )
        print(f"\n✓ Saved {saved} reformatted rows as batch {batch_id}")
        print("  상태: pending (대시보드에서 리뷰 후 approve)")
        print(f"  한번에 승인: PATCH /api/v1/distill/training-data/batch "
              f"with batch_id={batch_id}")
    else:
        logger.warning("No successful reformats to save")

    return 0 if summary.success > 0 else 2


async def _reject_fallback_originals(profile_name: str) -> int:
    """Reformatter 가 재작성 실패한 원본을 rejected 처리.

    학습에 fallback 으로 들어가는 원본들은 대체로 문제가 있는 샘플:
    - LLM 이 완고히 한 문단으로만 답한 케이스 → 원본이 "모른다" 류 답변
    - 기존의 긴 번호 목록 구조 → 1B 학습에 해로움

    이 함수는 reformatted 가 대체한 원본은 그대로 두고, 재작성 산출물이 없는
    approved 원본만 rejected 로 옮긴다. 원본 데이터 자체는 DB 에 보존되고
    status 만 바뀌므로 필요 시 재활성화 가능.
    """
    settings = get_settings()
    db_url = to_async_database_url(settings.database.database_url)
    engine = create_async_engine(db_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    repo = DistillRepository(session_factory)

    result = await repo.list_training_data(
        profile_name=profile_name, status="approved", limit=100000,
    )
    items = result.get("items", [])

    reformatted_origin_ids = {
        it.get("augmented_from") for it in items
        if it.get("source_type") == "reformatted" and it.get("augmented_from")
    }
    fallback_ids = [
        it["id"] for it in items
        if it.get("source_type") != "reformatted"
        and it.get("id") not in reformatted_origin_ids
    ]

    if not fallback_ids:
        print("No fallback originals to reject")
        return 0

    print(f"Rejecting {len(fallback_ids)} fallback originals without reformat...")
    updated = await repo.update_training_data_status(fallback_ids, "rejected")
    print(f"✓ Rejected {updated} rows")
    return updated


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True, help="Distill profile name")
    parser.add_argument(
        "--limit", type=int, default=0,
        help="Max samples to reformat (0=all)",
    )
    parser.add_argument(
        "--concurrency", type=int, default=4,
        help="Parallel LLM calls (default 4)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Call LLM + show samples but don't save to DB",
    )
    parser.add_argument(
        "--keep-fallbacks", action="store_true",
        help=(
            "Default behavior after reformat batch is to auto-reject approved "
            "originals without a reformatted version (they're often 'I don't know' "
            "answers or heavily structured — harmful for 1B training). "
            "Pass this flag to keep fallbacks in training export."
        ),
    )
    parser.add_argument(
        "--reject-fallbacks-only", action="store_true",
        help=(
            "Skip reformat; only run the fallback cleanup step. Useful for "
            "re-running cleanup after a previous reformat batch + approval."
        ),
    )
    args = parser.parse_args()

    if args.reject_fallbacks_only:
        asyncio.run(_reject_fallback_originals(args.profile))
        sys.exit(0)

    exit_code = asyncio.run(_run(
        profile_name=args.profile,
        limit=args.limit,
        dry_run=args.dry_run,
        concurrency=args.concurrency,
    ))

    # 기본 동작: 재작성 배치가 성공했고 dry_run 이 아니면 fallback 자동 reject
    # 이유: reformat 실패한 원본은 대체로 "모른다" 류 답변이거나 heavily structured
    # 라서 1B 학습에 해로움. 사용자가 opt-out 하려면 --keep-fallbacks 사용.
    if exit_code == 0 and not args.dry_run and not args.keep_fallbacks:
        print("\nAuto-rejecting fallback originals (use --keep-fallbacks to skip)...")
        asyncio.run(_reject_fallback_originals(args.profile))

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
