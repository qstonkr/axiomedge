"""질문 증강 배치 — Phase 1.5.

Phase 1 reformatter 로 만든 reformatted approved 샘플에 대해 질문을 N가지로 증강.
학습 시 exposures 수를 (1 + N)배로 늘려 train_loss 수렴 가속.

Usage:
    # Dry run (LLM 호출만, DB 저장 없음)
    AWS_PROFILE=$AWS_PROFILE uv run python scripts/augment_questions.py \\
      --profile pbu-store --limit 5 --dry-run

    # 전체 배치 (기본 4 variations per fact)
    AWS_PROFILE=$AWS_PROFILE uv run python scripts/augment_questions.py \\
      --profile pbu-store --n-variations 4 --concurrency 4

    # 이미 증강된 건 스킵됨 (idempotent) — 실패 배치 재실행 가능
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
from src.distill.data_gen.question_augmenter import (
    QuestionAugmenter,
    build_augmented_row,
)
from src.distill.repository import DistillRepository

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("augment_batch")


async def _run(
    profile_name: str,
    limit: int,
    n_variations: int,
    dry_run: bool,
    concurrency: int,
    verify: bool,
) -> int:
    settings = get_settings()
    db_url = to_async_database_url(settings.database.database_url)
    engine = create_async_engine(db_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    from src.nlp.llm.sagemaker_client import SageMakerConfig, SageMakerLLMClient
    llm_client = SageMakerLLMClient(config=SageMakerConfig())
    llm_helper = LLMHelper(
        llm_client,
        qdrant_url=settings.qdrant.url,
        concurrency=concurrency,
        timeout_sec=60,
    )

    repo = DistillRepository(session_factory)

    profile_dict = await repo.get_profile(profile_name)
    if not profile_dict:
        logger.error("Profile not found: %s", profile_name)
        return 1
    profile = dict_to_profile(profile_dict.get("config", profile_dict))
    logger.info("Profile: %s (base_model=%s)", profile_name, profile.base_model)

    # reformatted approved 행만 대상
    result = await repo.list_training_data(
        profile_name=profile_name,
        status="approved",
        source_type="reformatted",
        limit=100000,
    )
    reformatted_rows = result.get("items", [])

    # 이미 증강된 parent id 들 (idempotency) — pending/approved 모두 체크
    already_augmented_parent_ids: set[str] = set()
    for st in ("pending", "approved"):
        sub = await repo.list_training_data(
            profile_name=profile_name,
            status=st,
            source_type="reformatted_aug",
            limit=100000,
        )
        for it in sub.get("items", []):
            parent_id = it.get("augmented_from")
            if parent_id:
                already_augmented_parent_ids.add(parent_id)

    candidates = [
        r for r in reformatted_rows
        if r.get("id") not in already_augmented_parent_ids
    ]
    if limit > 0:
        candidates = candidates[:limit]

    logger.info(
        "Candidates: %d (approved reformatted: %d, already augmented: %d)",
        len(candidates), len(reformatted_rows), len(already_augmented_parent_ids),
    )

    if not candidates:
        logger.warning("No candidates to augment")
        return 0

    augmenter = QuestionAugmenter(
        llm_helper=llm_helper,
        n_variations=n_variations,
        max_retries=1,
        concurrency=concurrency,
        verify=verify,
    )

    logger.info(
        "Starting augment (n_variations=%d, concurrency=%d, dry_run=%s, verify=%s)",
        n_variations, concurrency, dry_run, verify,
    )
    summary, results = await augmenter.augment_batch(candidates)

    # Print summary
    print("\n" + "=" * 80)
    print(f"Question augmentation batch summary ({profile_name})")
    print("=" * 80)
    print(f"  Target n_variations per fact: {n_variations}")
    print(f"  Total facts processed:   {summary.total}")
    print(f"  Success:                 {summary.success}")
    print(f"  Failed:                  {summary.failed}")
    print(f"  Total variations built:  {summary.total_variations_generated}")
    if verify:
        print(f"  Variations verified:     {summary.total_variations_verified}")
        print(f"  Variations rejected:     {summary.total_variations_rejected}")
        if summary.total_variations_generated > 0:
            pass_rate = (
                100 * summary.total_variations_verified
                / summary.total_variations_generated
            )
            print(f"  Verification pass rate:  {pass_rate:.1f}%")
    if summary.failure_reasons:
        print("  Failure reasons:")
        for reason, count in sorted(
            summary.failure_reasons.items(), key=lambda x: -x[1],
        ):
            print(f"    {reason}: {count}")

    # Sample output — dry_run 이면 usable 전부, 아니면 첫 3개만
    usable_results = [r for r in results if r.usable]
    sample_cap = len(usable_results) if dry_run else 3
    success_samples = usable_results[:sample_cap]
    for i, r in enumerate(success_samples):
        parent = next(c for c in candidates if c.get("id") == r.source_id)
        print(f"\n--- Sample {i + 1} ---")
        print(f"ORIGINAL Q: {parent['question']}")
        for j, v in enumerate(r.variations, 1):
            print(f"  VAR {j}:    {v}")
        if verify and r.rejected_variations:
            print("  REJECTED (verification failed):")
            for rv in r.rejected_variations:
                print(f"    ❌ {rv}")

    if dry_run:
        print("\n[DRY RUN] DB 저장 생략")
        return 0

    # Save all usable augmentations (full success + partial with >=1 valid variation)
    batch_id = str(uuid.uuid4())
    new_rows = []
    for r in results:
        if not r.usable:
            continue
        parent = next(c for c in candidates if c.get("id") == r.source_id)
        for new_q in r.variations:
            new_rows.append(build_augmented_row(
                parent, new_q,
                profile_name=profile_name, batch_id=batch_id,
            ))

    if new_rows:
        saved = await repo.save_training_data_batch(new_rows)
        logger.info(
            "Saved %d augmented rows (batch_id=%s, parents=%d)",
            saved, batch_id, summary.success,
        )
        print(f"\n✓ Saved {saved} augmented rows as batch {batch_id}")
        print("  상태: pending (대시보드에서 리뷰 후 approve)")
    else:
        logger.warning("No successful augmentations to save")

    return 0 if summary.success > 0 else 2


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True, help="Distill profile name")
    parser.add_argument(
        "--limit", type=int, default=0,
        help="Max facts to augment (0=all)",
    )
    parser.add_argument(
        "--n-variations", type=int, default=4,
        help="Number of question variations per fact (default 4)",
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
        "--verify", action="store_true",
        help="Teacher LLM judge 로 각 변형이 원본 답변에 여전히 맞는지 검증",
    )
    args = parser.parse_args()

    exit_code = asyncio.run(_run(
        profile_name=args.profile,
        limit=args.limit,
        n_variations=args.n_variations,
        dry_run=args.dry_run,
        concurrency=args.concurrency,
        verify=args.verify,
    ))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
