"""Phase 0: 파일럿 벤치마크 — 엣지 모델 후보 4종 비교.

토큰 효율 + zero-shot 한국어 답변 품질 비교.

Usage:
    # 토큰 효율만 (GPU 불필요, 1분)
    uv run python scripts/distill_pilot_benchmark.py --token-efficiency-only

    # 전체 벤치마크 (모델 다운로드 + 추론)
    uv run python scripts/distill_pilot_benchmark.py

    # 샘플 수 조정
    uv run python scripts/distill_pilot_benchmark.py --sample 10
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import ssl
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# SSL 우회 (회사 프록시 self-signed cert 대응)
ssl._create_default_https_context = ssl._create_unverified_context
os.environ.setdefault("CURL_CA_BUNDLE", "")
os.environ.setdefault("REQUESTS_CA_BUNDLE", "")

import requests  # noqa: E402
import urllib3  # noqa: E402

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_orig_get = requests.Session.get
_orig_post = requests.Session.post


def _get_no_verify(self, *a, **kw):
    kw.setdefault("verify", False)
    return _orig_get(self, *a, **kw)


def _post_no_verify(self, *a, **kw):
    kw.setdefault("verify", False)
    return _orig_post(self, *a, **kw)


requests.Session.get = _get_no_verify  # type: ignore
requests.Session.post = _post_no_verify  # type: ignore

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 벤치마크 대상 모델 4종
# ---------------------------------------------------------------------------

CANDIDATE_MODELS = [
    {
        "name": "Qwen2.5-0.5B",
        "model_id": "Qwen/Qwen2.5-0.5B-Instruct",
        "params": "0.5B",
    },
    {
        "name": "Qwen2.5-1.5B",
        "model_id": "Qwen/Qwen2.5-1.5B-Instruct",
        "params": "1.5B",
    },
    {
        "name": "Gemma3-1B",
        "model_id": "google/gemma-3-1b-it",
        "params": "1B",
    },
    {
        "name": "EXAONE-2.4B",
        "model_id": "LGAI-EXAONE/EXAONE-3.5-2.4B-Instruct",
        "params": "2.4B",
    },
]

# PBU 테스트 질문
PBU_QUESTIONS = [
    "유통기한 지난 상품 폐기 절차 알려줘",
    "카드 단말기 오류 코드 E03 해결 방법",
    "1+1 행사 상품 POS 등록 방법",
    "담배 판매 시 연령 확인 절차",
    "냉장고 온도 이상 시 대처 방법",
    "교대 근무 시 인수인계 사항",
    "반품 처리 절차",
    "현금 시재 정산 방법",
    "고객 컴플레인 대응 매뉴얼",
    "배달 주문 접수 및 처리 방법",
    "편의점 위생 관리 기준",
    "POS 시스템 재부팅 방법",
    "야간 근무 시 안전 수칙",
    "신상품 입고 처리 절차",
    "고객 포인트 적립 오류 해결",
    "폐점 시 마감 절차",
    "개점 시 오픈 절차",
    "고객 환불 규정",
    "택배 접수 방법",
    "무인 결제기 오류 대처법",
]

# 토큰 효율 측정용 텍스트
TOKEN_TEST_TEXTS = [
    "유통기한이 지난 상품은 매대에서 분리하고 POS에서 폐기 등록 후 폐기 박스에 보관합니다.",
    "GS25 편의점에서 카드 결제 오류가 발생하면 먼저 단말기를 재부팅하고, 그래도 안 되면 본사 콜센터에 연락합니다.",
    "야간 근무 시에는 매장 출입문 잠금을 확인하고, CCTV 작동 상태를 점검하며, 비상 연락망을 숙지해야 합니다.",
]


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class TokenResult:
    model_name: str
    text: str
    char_count: int
    token_count: int
    chars_per_token: float


@dataclass
class InferenceResult:
    model_name: str
    question: str
    answer: str
    latency_ms: int
    success: bool
    error: str = ""


@dataclass
class ModelSummary:
    model_name: str
    params: str
    avg_chars_per_token: float
    avg_latency_ms: float = 0
    avg_answer_chars: float = 0
    success_rate: float = 0
    est_chars_at_256_tokens: int = 0
    results: list[InferenceResult] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 1. 토큰 효율 측정
# ---------------------------------------------------------------------------

def measure_token_efficiency() -> list[TokenResult]:
    """각 모델 tokenizer의 한국어 토큰 효율 측정."""
    from transformers import AutoTokenizer

    results: list[TokenResult] = []

    for model_info in CANDIDATE_MODELS:
        model_id = model_info["model_id"]
        logger.info("Loading tokenizer: %s", model_id)

        try:
            tokenizer = AutoTokenizer.from_pretrained(model_id, trust_remote_code=True)
        except Exception as e:
            logger.error("Failed to load tokenizer %s: %s", model_id, e)
            continue

        for text in TOKEN_TEST_TEXTS:
            tokens = tokenizer.encode(text)
            token_count = len(tokens)
            chars_per_token = len(text) / token_count if token_count > 0 else 0

            results.append(TokenResult(
                model_name=model_info["name"],
                text=text[:40] + "...",
                char_count=len(text),
                token_count=token_count,
                chars_per_token=round(chars_per_token, 2),
            ))

    return results


# ---------------------------------------------------------------------------
# 2. Zero-shot 추론 벤치마크
# ---------------------------------------------------------------------------

def run_inference_benchmark(
    model_info: dict, questions: list[str],
) -> ModelSummary:
    """단일 모델 zero-shot 추론 테스트."""
    import gc

    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    model_id = model_info["model_id"]
    logger.info("Loading model: %s (%s)", model_info["name"], model_id)

    try:
        tokenizer = AutoTokenizer.from_pretrained(model_id, trust_remote_code=True)
        model = AutoModelForCausalLM.from_pretrained(
            model_id, torch_dtype="auto", device_map="auto", trust_remote_code=True,
        )
    except Exception as e:
        logger.error("Failed to load %s: %s", model_id, e)
        return ModelSummary(
            model_name=model_info["name"], params=model_info["params"],
            avg_chars_per_token=0,
        )

    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    results: list[InferenceResult] = []

    for i, question in enumerate(questions):
        logger.info("[%s] %d/%d: %s", model_info["name"], i + 1, len(questions), question[:30])
        t0 = time.monotonic()

        try:
            messages = [{"role": "user", "content": question}]
            text = tokenizer.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True,
            )
            inputs = tokenizer([text], return_tensors="pt").to(model.device)
            output_ids = model.generate(**inputs, max_new_tokens=256, do_sample=False)
            generated_ids = output_ids[0][inputs["input_ids"].shape[1]:]
            answer = tokenizer.decode(generated_ids, skip_special_tokens=True).strip()
            latency_ms = int((time.monotonic() - t0) * 1000)

            results.append(InferenceResult(
                model_name=model_info["name"], question=question,
                answer=answer, latency_ms=latency_ms, success=True,
            ))
        except Exception as e:
            latency_ms = int((time.monotonic() - t0) * 1000)
            results.append(InferenceResult(
                model_name=model_info["name"], question=question,
                answer="", latency_ms=latency_ms, success=False, error=str(e),
            ))

    # 메모리 해제
    del model, tokenizer
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()

    successful = [r for r in results if r.success]
    avg_latency = sum(r.latency_ms for r in successful) / len(successful) if successful else 0
    avg_chars = sum(len(r.answer) for r in successful) / len(successful) if successful else 0

    return ModelSummary(
        model_name=model_info["name"],
        params=model_info["params"],
        avg_chars_per_token=0,
        avg_latency_ms=round(avg_latency),
        avg_answer_chars=round(avg_chars),
        success_rate=len(successful) / len(results) if results else 0,
        results=results,
    )


# ---------------------------------------------------------------------------
# 출력
# ---------------------------------------------------------------------------

def print_token_results(results: list[TokenResult]) -> None:
    print("\n" + "=" * 75)
    print("한국어 토큰 효율 비교")
    print("=" * 75)
    print(f"{'모델':<18} {'텍스트':<30} {'문자':>5} {'토큰':>5} {'문자/토큰':>9}")
    print("-" * 75)
    for r in results:
        print(f"{r.model_name:<18} {r.text:<30} {r.char_count:>5} {r.token_count:>5} {r.chars_per_token:>9.2f}")

    # 모델별 평균
    print("\n--- 모델별 평균 ---")
    model_names = sorted(set(r.model_name for r in results))
    for name in model_names:
        mr = [r for r in results if r.model_name == name]
        avg_cpt = sum(r.chars_per_token for r in mr) / len(mr)
        avg_tok = sum(r.token_count for r in mr) / len(mr)
        est_256 = int(256 * avg_cpt)
        print(f"  {name:<18} 평균 {avg_cpt:.2f} 문자/토큰 (평균 {avg_tok:.0f}토큰)")
        print(f"  {'':18} → 256토큰 ≈ 한국어 {est_256}자 (약 {est_256 // 40}문장)")


def print_inference_results(summaries: list[ModelSummary]) -> None:
    print("\n" + "=" * 85)
    print("Zero-shot 추론 벤치마크")
    print("=" * 85)
    print(f"{'모델':<18} {'파라미터':>8} {'지연(ms)':>10} {'답변(자)':>10} {'성공률':>8}")
    print("-" * 60)
    for s in summaries:
        print(f"{s.model_name:<18} {s.params:>8} {s.avg_latency_ms:>10.0f} "
              f"{s.avg_answer_chars:>10.0f} {s.success_rate:>7.0%}")

    # 샘플 답변
    if len(summaries) >= 2:
        print("\n--- 샘플 답변 비교 (첫 3개 질문) ---")
        for i in range(min(3, len(summaries[0].results))):
            q = summaries[0].results[i].question
            print(f"\nQ: {q}")
            for s in summaries:
                if i < len(s.results):
                    a = s.results[i].answer[:150]
                    print(f"  [{s.model_name}] {a}")


def print_recommendation(
    token_results: list[TokenResult], summaries: list[ModelSummary],
) -> None:
    print("\n" + "=" * 50)
    print("추천")
    print("=" * 50)

    # 토큰 효율 기준
    model_names = sorted(set(r.model_name for r in token_results))
    efficiency: dict[str, float] = {}
    for name in model_names:
        mr = [r for r in token_results if r.model_name == name]
        efficiency[name] = sum(r.chars_per_token for r in mr) / len(mr)

    best_eff = max(efficiency, key=efficiency.get)
    print(f"\n  토큰 효율 1위: {best_eff} ({efficiency[best_eff]:.2f} 문자/토큰)")

    if summaries:
        # 지연 시간 기준
        fastest = min(summaries, key=lambda s: s.avg_latency_ms if s.avg_latency_ms > 0 else 99999)
        print(f"  응답 속도 1위: {fastest.model_name} ({fastest.avg_latency_ms:.0f}ms)")

        # 종합 추천
        print("\n  ── 엣지 배포 추천 ──")
        for s in sorted(summaries, key=lambda x: x.avg_latency_ms if x.avg_latency_ms > 0 else 99999):
            eff = efficiency.get(s.model_name, 0)
            est = int(256 * eff)
            verdict = ""
            if s.params == "0.5B":
                verdict = " ← POS 엣지 추천"
            elif s.params in ("1B", "1.5B"):
                verdict = " ← 엣지 서버 (여유 스펙)"
            elif s.params == "2.4B":
                verdict = " ← 사내 서버 / 참고용"
            print(f"  {s.model_name:<18} {s.params:>5} | {s.avg_latency_ms:>6.0f}ms | "
                  f"256토큰≈{est}자{verdict}")


def save_results(
    token_results: list[TokenResult],
    summaries: list[ModelSummary],
    output: str,
) -> None:
    data: dict[str, Any] = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "token_efficiency": [
            {
                "model": r.model_name, "text": r.text,
                "chars": r.char_count, "tokens": r.token_count,
                "chars_per_token": r.chars_per_token,
            }
            for r in token_results
        ],
    }
    if summaries:
        data["inference"] = [
            {
                "model": s.model_name, "params": s.params,
                "avg_latency_ms": s.avg_latency_ms,
                "avg_answer_chars": s.avg_answer_chars,
                "success_rate": s.success_rate,
                "results": [
                    {"question": r.question, "answer": r.answer,
                     "latency_ms": r.latency_ms, "success": r.success}
                    for r in s.results
                ],
            }
            for s in summaries
        ]
    Path(output).write_text(json.dumps(data, ensure_ascii=False, indent=2))
    logger.info("Results saved to %s", output)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Distill pilot benchmark")
    parser.add_argument("--token-efficiency-only", action="store_true",
                        help="토큰 효율만 측정 (GPU 불필요)")
    parser.add_argument("--sample", type=int, default=20, help="테스트 질문 수")
    parser.add_argument("--output", default="pilot_benchmark_results.json")
    args = parser.parse_args()

    # 1. 토큰 효율 측정
    logger.info("=== Step 1: 토큰 효율 측정 ===")
    token_results = measure_token_efficiency()
    print_token_results(token_results)

    summaries: list[ModelSummary] = []

    if not args.token_efficiency_only:
        # 2. Zero-shot 추론
        questions = PBU_QUESTIONS[:args.sample]
        for model_info in CANDIDATE_MODELS:
            logger.info("=== Step 2: %s 추론 벤치마크 ===", model_info["name"])
            summary = run_inference_benchmark(model_info, questions)
            summaries.append(summary)

        print_inference_results(summaries)

    print_recommendation(token_results, summaries)
    save_results(token_results, summaries, args.output)


if __name__ == "__main__":
    main()
