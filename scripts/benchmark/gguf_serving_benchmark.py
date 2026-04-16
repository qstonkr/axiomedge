"""GGUF 서빙 벤치마크 — Ollama 기반 엣지 모델 후보 비교.

Ollama에 로드된 GGUF 모델의 서빙 성능(지연시간, 처리량, VRAM, 답변 품질)을 측정.
기존 transformers 추론 결과(pilot_benchmark_results.json)와 비교 가능.

Usage:
    # 전체 벤치마크 (20문항)
    uv run python scripts/gguf_serving_benchmark.py

    # 샘플 수 조정
    uv run python scripts/gguf_serving_benchmark.py --sample 5

    # 특정 모델만
    uv run python scripts/gguf_serving_benchmark.py --models qwen2.5:0.5b gemma3:1b
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

OLLAMA_BASE = "http://localhost:11434"

# 벤치마크 대상 — Ollama 모델 태그
CANDIDATE_MODELS = [
    {"name": "Qwen2.5-0.5B", "ollama_tag": "qwen2.5:0.5b", "params": "0.5B"},
    {"name": "Qwen2.5-1.5B", "ollama_tag": "qwen2.5:1.5b", "params": "1.5B"},
    {"name": "Gemma3-1B", "ollama_tag": "gemma3:1b", "params": "1B"},
    {"name": "EXAONE-2.4B", "ollama_tag": "exaone3.5:2.4b", "params": "2.4B"},
]

# distill_pilot_benchmark.py 와 동일한 질문 세트
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


@dataclass
class GGUFInferenceResult:
    model_name: str
    question: str
    answer: str
    latency_ms: int
    tokens_generated: int
    tokens_per_sec: float
    success: bool
    error: str = ""


@dataclass
class GGUFModelSummary:
    model_name: str
    params: str
    ollama_tag: str
    model_size_mb: float = 0
    avg_latency_ms: float = 0
    avg_tokens_per_sec: float = 0
    avg_answer_chars: float = 0
    success_rate: float = 0
    korean_rate: float = 0  # 한국어로 답변한 비율
    results: list[GGUFInferenceResult] = field(default_factory=list)


def check_ollama() -> bool:
    """Ollama 서버 상태 확인."""
    try:
        resp = requests.get(f"{OLLAMA_BASE}/api/tags", timeout=5)
        return resp.status_code == 200
    except requests.ConnectionError:
        return False


def get_model_info(tag: str) -> dict:
    """Ollama 모델 메타정보 조회."""
    try:
        resp = requests.post(f"{OLLAMA_BASE}/api/show", json={"name": tag}, timeout=10)
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return {}


def is_korean(text: str) -> bool:
    """텍스트에 한국어가 50% 이상인지 판별."""
    if not text:
        return False
    korean_chars = sum(1 for c in text if '\uac00' <= c <= '\ud7a3')
    alpha_chars = sum(1 for c in text if c.isalpha())
    return (korean_chars / alpha_chars) >= 0.5 if alpha_chars > 0 else False


def run_single_inference(tag: str, question: str) -> dict:
    """Ollama /api/chat 단일 추론."""
    payload = {
        "model": tag,
        "messages": [{"role": "user", "content": question}],
        "stream": False,
        "options": {"num_predict": 256, "temperature": 0},
    }
    t0 = time.monotonic()
    resp = requests.post(f"{OLLAMA_BASE}/api/chat", json=payload, timeout=120)
    elapsed_ms = int((time.monotonic() - t0) * 1000)
    resp.raise_for_status()
    data = resp.json()

    answer = data.get("message", {}).get("content", "").strip()
    eval_count = data.get("eval_count", 0)
    eval_duration_ns = data.get("eval_duration", 0)
    tps = (eval_count / (eval_duration_ns / 1e9)) if eval_duration_ns > 0 else 0

    return {
        "answer": answer,
        "latency_ms": elapsed_ms,
        "tokens_generated": eval_count,
        "tokens_per_sec": round(tps, 1),
    }


def warm_up_model(tag: str) -> None:
    """첫 추론은 모델 로딩 포함이므로 워밍업."""
    logger.info("Warming up %s ...", tag)
    try:
        run_single_inference(tag, "안녕하세요")
    except Exception as e:
        logger.warning("Warmup failed for %s: %s", tag, e)


def run_gguf_benchmark(
    model_info: dict, questions: list[str],
) -> GGUFModelSummary:
    """단일 모델 GGUF 서빙 벤치마크."""
    tag = model_info["ollama_tag"]
    name = model_info["name"]
    logger.info("=== %s (%s) ===", name, tag)

    # 모델 사이즈
    meta = get_model_info(tag)
    size_bytes = meta.get("size", 0)
    if not size_bytes and "model_info" in meta:
        # fallback
        size_bytes = sum(
            v for k, v in meta.get("model_info", {}).items()
            if isinstance(v, (int, float)) and "size" in k.lower()
        )
    size_mb = round(size_bytes / 1024 / 1024, 1) if size_bytes else 0

    warm_up_model(tag)

    results: list[GGUFInferenceResult] = []
    for i, question in enumerate(questions):
        logger.info("[%s] %d/%d: %s", name, i + 1, len(questions), question[:30])
        try:
            out = run_single_inference(tag, question)
            results.append(GGUFInferenceResult(
                model_name=name, question=question,
                answer=out["answer"], latency_ms=out["latency_ms"],
                tokens_generated=out["tokens_generated"],
                tokens_per_sec=out["tokens_per_sec"], success=True,
            ))
        except Exception as e:
            logger.error("[%s] Failed: %s", name, e)
            results.append(GGUFInferenceResult(
                model_name=name, question=question,
                answer="", latency_ms=0, tokens_generated=0,
                tokens_per_sec=0, success=False, error=str(e),
            ))

    successful = [r for r in results if r.success]
    korean_count = sum(1 for r in successful if is_korean(r.answer))

    return GGUFModelSummary(
        model_name=name,
        params=model_info["params"],
        ollama_tag=tag,
        model_size_mb=size_mb,
        avg_latency_ms=round(sum(r.latency_ms for r in successful) / len(successful)) if successful else 0,
        avg_tokens_per_sec=round(sum(r.tokens_per_sec for r in successful) / len(successful), 1) if successful else 0,
        avg_answer_chars=round(sum(len(r.answer) for r in successful) / len(successful)) if successful else 0,
        success_rate=len(successful) / len(results) if results else 0,
        korean_rate=korean_count / len(successful) if successful else 0,
        results=results,
    )


# ---------------------------------------------------------------------------
# 출력
# ---------------------------------------------------------------------------

def print_gguf_results(summaries: list[GGUFModelSummary]) -> None:
    print("\n" + "=" * 90)
    print("GGUF 서빙 벤치마크 (Ollama)")
    print("=" * 90)
    print(f"{'모델':<18} {'파라미터':>6} {'크기(MB)':>9} {'지연(ms)':>9} "
          f"{'tok/s':>7} {'답변(자)':>8} {'한국어':>7} {'성공률':>7}")
    print("-" * 90)
    for s in summaries:
        print(f"{s.model_name:<18} {s.params:>6} {s.model_size_mb:>9.0f} "
              f"{s.avg_latency_ms:>9.0f} {s.avg_tokens_per_sec:>7.1f} "
              f"{s.avg_answer_chars:>8.0f} {s.korean_rate:>6.0%} {s.success_rate:>6.0%}")

    # 샘플 답변 비교
    print("\n--- 샘플 답변 비교 (첫 3개 질문) ---")
    for i in range(min(3, len(summaries[0].results) if summaries else 0)):
        q = summaries[0].results[i].question
        print(f"\nQ: {q}")
        for s in summaries:
            if i < len(s.results):
                a = s.results[i].answer[:200]
                lang = "KR" if is_korean(s.results[i].answer) else "??"
                print(f"  [{s.model_name}] ({lang}) {a}")


def print_comparison(summaries: list[GGUFModelSummary]) -> None:
    """transformers 결과와 GGUF 결과 비교."""
    pilot_path = Path(__file__).parent.parent / "pilot_benchmark_results.json"
    if not pilot_path.exists():
        return

    pilot = json.loads(pilot_path.read_text())
    print("\n" + "=" * 90)
    print("Transformers vs GGUF (Ollama) 비교")
    print("=" * 90)
    print(f"{'모델':<18} {'':^20} {'지연(ms)':^20} {'답변(자)':^20}")
    print(f"{'':18} {'Transformers':>10} {'GGUF':>10} {'Transformers':>10} {'GGUF':>10}")
    print("-" * 90)

    for s in summaries:
        p = pilot.get(s.model_name, {})
        tf_lat = p.get("avg_latency_ms", "-")
        tf_chars = p.get("avg_answer_chars", "-")
        tf_lat_str = f"{tf_lat:>10.0f}" if isinstance(tf_lat, (int, float)) else f"{tf_lat:>10}"
        tf_chars_str = f"{tf_chars:>10.0f}" if isinstance(tf_chars, (int, float)) else f"{tf_chars:>10}"
        print(f"{s.model_name:<18} {tf_lat_str} {s.avg_latency_ms:>10.0f} "
              f"{tf_chars_str} {s.avg_answer_chars:>10.0f}")


def print_recommendation(summaries: list[GGUFModelSummary]) -> None:
    print("\n" + "=" * 50)
    print("종합 추천")
    print("=" * 50)

    # 한국어 안정성 필터
    kr_stable = [s for s in summaries if s.korean_rate >= 0.8]
    if kr_stable:
        fastest = min(kr_stable, key=lambda x: x.avg_latency_ms)
        print(f"\n  한국어 안정 + 최고 속도: {fastest.model_name} "
              f"({fastest.avg_latency_ms:.0f}ms, 한국어 {fastest.korean_rate:.0%})")

    best_tps = max(summaries, key=lambda x: x.avg_tokens_per_sec)
    print(f"  최고 처리량: {best_tps.model_name} ({best_tps.avg_tokens_per_sec:.1f} tok/s)")

    print("\n  ── 엣지 배포 적합성 ──")
    for s in sorted(summaries, key=lambda x: x.avg_latency_ms if x.avg_latency_ms > 0 else 99999):
        kr_badge = "OK" if s.korean_rate >= 0.8 else "NG"
        verdict = ""
        if s.korean_rate < 0.8:
            verdict = " ← 한국어 불안정, 부적합"
        elif s.params == "0.5B":
            verdict = " ← POS 엣지 추천"
        elif s.params in ("1B", "1.5B"):
            verdict = " ← 엣지 서버 추천"
        print(f"  {s.model_name:<18} {s.params:>5} | {s.avg_latency_ms:>6.0f}ms | "
              f"{s.avg_tokens_per_sec:>5.1f} tok/s | KR:{kr_badge}{verdict}")


def save_results(summaries: list[GGUFModelSummary], output: str) -> None:
    data = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "backend": "ollama",
        "models": [
            {
                "model": s.model_name,
                "params": s.params,
                "ollama_tag": s.ollama_tag,
                "model_size_mb": s.model_size_mb,
                "avg_latency_ms": s.avg_latency_ms,
                "avg_tokens_per_sec": s.avg_tokens_per_sec,
                "avg_answer_chars": s.avg_answer_chars,
                "success_rate": s.success_rate,
                "korean_rate": s.korean_rate,
                "results": [
                    {
                        "question": r.question,
                        "answer": r.answer,
                        "latency_ms": r.latency_ms,
                        "tokens_generated": r.tokens_generated,
                        "tokens_per_sec": r.tokens_per_sec,
                        "success": r.success,
                    }
                    for r in s.results
                ],
            }
            for s in summaries
        ],
    }
    Path(output).write_text(json.dumps(data, ensure_ascii=False, indent=2))
    logger.info("Results saved to %s", output)


def main():
    parser = argparse.ArgumentParser(description="GGUF serving benchmark via Ollama")
    parser.add_argument("--sample", type=int, default=20, help="테스트 질문 수")
    parser.add_argument("--models", nargs="+", help="특정 모델만 테스트 (e.g. qwen2.5:0.5b)")
    parser.add_argument("--output", default="gguf_benchmark_results.json")
    args = parser.parse_args()

    if not check_ollama():
        logger.error("Ollama 서버에 연결할 수 없습니다. `ollama serve` 실행 후 재시도하세요.")
        return

    candidates = CANDIDATE_MODELS
    if args.models:
        candidates = [m for m in candidates if m["ollama_tag"] in args.models]

    questions = PBU_QUESTIONS[:args.sample]
    summaries: list[GGUFModelSummary] = []

    for model_info in candidates:
        summary = run_gguf_benchmark(model_info, questions)
        summaries.append(summary)

    print_gguf_results(summaries)
    print_comparison(summaries)
    print_recommendation(summaries)
    save_results(summaries, args.output)


if __name__ == "__main__":
    main()
