"""Phase 1 vs Phase 1.5 A/B 비교용 baseline 측정 스크립트.

흐름:
    1. DB 에서 20 reformatted approved 샘플 랜덤 추출 (seed=42 로 재현성)
    2. 각 샘플에 대해 QuestionAugmenter 로 4 변형 생성 (총 80 변형)
    3. 원본 20 + 변형 80 = 총 100 테스트 질문
    4. 로컬 model.gguf 로 각 질문 inference
    5. 결과를 /tmp/distill_model_test/baseline_results.json 에 저장
    6. 간단한 자동 점수 (token Jaccard similarity) + 샘플 출력

Phase 1.5 완료 후:
    - 같은 baseline_questions.json 으로 다시 실행 → results 비교 → 개선폭 정량화

Usage:
    AWS_PROFILE=$AWS_PROFILE uv run python scripts/baseline_test.py \\
      --model /tmp/distill_model_test/model.gguf \\
      --label phase1
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import random
import re
import sys
import time
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.config import get_settings
from src.stores.postgres.session import to_async_database_url
from src.distill.data_gen.llm_helper import LLMHelper
from src.distill.data_gen.question_augmenter import QuestionAugmenter
from src.distill.repository import DistillRepository

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("baseline_test")

PROFILE = "pbu-store"
N_SAMPLES = 20
N_VARIATIONS = 4
RANDOM_SEED = 42

# Gemma 3 turn tokens stop (edge server 와 동일)
STOP_TOKENS = ["<end_of_turn>", "<start_of_turn>"]

TEST_SET_PATH = "/tmp/distill_model_test/baseline_questions.json"


def _tokenize(text: str) -> set[str]:
    """Korean-friendly token set (공백 + 주요 어미 분리)."""
    if not text:
        return set()
    # Strip punctuation, lowercase, split on whitespace
    cleaned = re.sub(r"[^\w\s가-힣]", " ", text.lower())
    return {t for t in cleaned.split() if len(t) >= 2}


def _jaccard(a: str, b: str) -> float:
    """Token-level Jaccard similarity [0, 1]."""
    ta, tb = _tokenize(a), _tokenize(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


async def _build_test_set() -> list[dict]:
    """DB 에서 테스트 셋 구성 (idempotent — cache 파일 있으면 재사용)."""
    cache = Path(TEST_SET_PATH)
    if cache.exists():
        logger.info("Loading cached test set: %s", cache)
        return json.loads(cache.read_text())

    settings = get_settings()
    db_url = to_async_database_url(settings.database.database_url)
    engine = create_async_engine(db_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    from src.nlp.llm.sagemaker_client import SageMakerConfig, SageMakerLLMClient
    llm_client = SageMakerLLMClient(config=SageMakerConfig())
    llm_helper = LLMHelper(
        llm_client,
        qdrant_url=settings.qdrant.url,
        concurrency=4,
        timeout_sec=60,
    )

    repo = DistillRepository(session_factory)
    result = await repo.list_training_data(
        profile_name=PROFILE,
        status="approved",
        source_type="reformatted",
        limit=100000,
    )
    rows = result.get("items", [])
    if len(rows) < N_SAMPLES:
        raise ValueError(f"Only {len(rows)} reformatted rows (need {N_SAMPLES})")

    random.seed(RANDOM_SEED)
    base_samples = random.sample(rows, N_SAMPLES)
    logger.info("Selected %d base samples (seed=%d)", N_SAMPLES, RANDOM_SEED)

    # 각 샘플에 대해 변형 생성
    augmenter = QuestionAugmenter(
        llm_helper=llm_helper,
        n_variations=N_VARIATIONS,
        max_retries=1,
        concurrency=4,
    )
    logger.info("Generating %d variations per sample...", N_VARIATIONS)
    summary, aug_results = await augmenter.augment_batch(base_samples)
    logger.info(
        "Augment: %d/%d success, %d total variations",
        summary.success, summary.total, summary.total_variations_generated,
    )

    # 테스트 셋 구성: 원본 20 + 변형 (성공한 만큼)
    test_set = []
    for sample, aug_result in zip(base_samples, aug_results):
        sample_id = sample["id"]
        # 원본 질문
        test_set.append({
            "type": "original",
            "parent_id": sample_id,
            "question": sample["question"],
            "expected_answer": sample["answer"],
        })
        # 변형 질문들 (답변은 원본 것 재사용)
        for j, var_q in enumerate(aug_result.variations):
            test_set.append({
                "type": "augmented",
                "variation_idx": j + 1,
                "parent_id": sample_id,
                "question": var_q,
                "expected_answer": sample["answer"],
            })

    # 캐시
    Path(TEST_SET_PATH).parent.mkdir(parents=True, exist_ok=True)
    Path(TEST_SET_PATH).write_text(json.dumps(test_set, ensure_ascii=False, indent=2))
    logger.info("Saved test set: %s (%d questions)", TEST_SET_PATH, len(test_set))
    return test_set


def _run_inference(model_path: str, test_set: list[dict]) -> list[dict]:
    """모델 로드 후 각 질문에 inference. 결과 반환."""
    logger.info("Loading GGUF: %s", model_path)
    from llama_cpp import Llama
    t0 = time.monotonic()
    llm = Llama(model_path=model_path, n_ctx=1024, n_threads=4, verbose=False)
    logger.info("Model loaded in %.1fs", time.monotonic() - t0)

    results = []
    for i, item in enumerate(test_set, 1):
        t_start = time.monotonic()
        try:
            output = llm.create_chat_completion(
                messages=[{"role": "user", "content": item["question"]}],
                max_tokens=350,
                temperature=0.3,
                stop=STOP_TOKENS,
            )
            answer = output["choices"][0]["message"]["content"].strip()
        except Exception as e:
            logger.warning("Inference %d failed: %s", i, e)
            answer = ""
        latency = time.monotonic() - t_start
        jaccard = _jaccard(answer, item["expected_answer"])
        results.append({
            **item,
            "generated_answer": answer,
            "jaccard": round(jaccard, 3),
            "latency_sec": round(latency, 2),
        })
        if i % 10 == 0:
            logger.info("Progress: %d/%d", i, len(test_set))
    del llm
    return results


def _print_summary(results: list[dict], label: str) -> None:
    """점수 분포 및 원본 vs 변형 비교."""
    originals = [r for r in results if r["type"] == "original"]
    augmented = [r for r in results if r["type"] == "augmented"]

    def _stats(group: list[dict], name: str) -> None:
        if not group:
            return
        scores = [r["jaccard"] for r in group]
        buckets = {"high (≥0.5)": 0, "mid (0.2~0.5)": 0, "low (<0.2)": 0}
        for s in scores:
            if s >= 0.5:
                buckets["high (≥0.5)"] += 1
            elif s >= 0.2:
                buckets["mid (0.2~0.5)"] += 1
            else:
                buckets["low (<0.2)"] += 1
        avg = sum(scores) / len(scores)
        print(f"\n--- {name} (n={len(group)}) ---")
        print(f"  평균 Jaccard: {avg:.3f}")
        for k, v in buckets.items():
            print(f"    {k}: {v} ({100*v/len(group):.1f}%)")

    print(f"\n{'='*80}")
    print(f"BASELINE RESULTS — label={label}")
    print(f"{'='*80}")
    _stats(originals, "원본 질문 (trained)")
    _stats(augmented, "변형 질문 (unseen paraphrase)")

    # 샘플 출력 (first 3 original + first 3 augmented)
    print("\n--- 샘플 미리보기 ---")
    for r in (originals[:3] + augmented[:3]):
        print(f"\n[{r['type']}] jaccard={r['jaccard']}")
        print(f"  Q: {r['question'][:100]}")
        print(f"  EXPECTED: {r['expected_answer'][:150]}...")
        print(f"  GENERATED: {r['generated_answer'][:150]}...")


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True, help="Path to model.gguf")
    parser.add_argument(
        "--label", default="baseline",
        help="Label for result file (e.g. 'phase1', 'phase1.5')",
    )
    args = parser.parse_args()

    if not Path(args.model).exists():
        logger.error("Model not found: %s", args.model)
        return 1

    # 1. 테스트 셋 구성 (캐시 사용)
    test_set = await _build_test_set()
    print(f"Test set: {len(test_set)} questions "
          f"({sum(1 for t in test_set if t['type']=='original')} original + "
          f"{sum(1 for t in test_set if t['type']=='augmented')} augmented)")

    # 2. Inference
    results = _run_inference(args.model, test_set)

    # 3. 결과 저장
    out_path = Path(f"/tmp/distill_model_test/baseline_results_{args.label}.json")
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2))
    logger.info("Saved: %s", out_path)

    # 4. 요약
    _print_summary(results, args.label)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
