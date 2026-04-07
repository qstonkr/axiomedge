"""엣지 서빙 벤치마크 — llama-cpp-python 기반 (edge/server.py 동일 엔진).

edge/server.py와 동일한 llama_cpp.Llama + create_chat_completion으로
GGUF 모델 4종의 서빙 성능을 측정.

Usage:
    # 전체 벤치마크 (20문항, 4모델)
    uv run python scripts/edge_serving_benchmark.py

    # 샘플 수 조정
    uv run python scripts/edge_serving_benchmark.py --sample 5

    # 특정 모델만
    uv run python scripts/edge_serving_benchmark.py --models Qwen2.5-0.5B Gemma3-1B
"""

from __future__ import annotations

import argparse
import gc
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

OLLAMA_BLOBS = Path.home() / ".ollama" / "models" / "blobs"

# Ollama manifest에서 GGUF blob digest를 읽어 경로 반환
def _resolve_gguf_path(ollama_tag: str) -> str | None:
    """ollama_tag (e.g. 'qwen2.5/0.5b') → GGUF blob 경로."""
    manifest = (
        Path.home() / ".ollama" / "models" / "manifests"
        / "registry.ollama.ai" / "library" / ollama_tag
    )
    if not manifest.exists():
        return None
    try:
        data = json.loads(manifest.read_text())
        for layer in data.get("layers", []):
            if "model" in layer.get("mediaType", ""):
                digest = layer["digest"].replace(":", "-")
                blob = OLLAMA_BLOBS / digest
                return str(blob) if blob.exists() else None
    except (json.JSONDecodeError, OSError):
        pass
    return None


# 벤치마크 대상 모델
CANDIDATE_MODELS = [
    {
        "name": "Qwen2.5-0.5B",
        "ollama_tag": "qwen2.5/0.5b",
        "params": "0.5B",
    },
    {
        "name": "Qwen2.5-1.5B",
        "ollama_tag": "qwen2.5/1.5b",
        "params": "1.5B",
    },
    {
        "name": "Gemma3-1B",
        "ollama_tag": "gemma3/1b",
        "params": "1B",
    },
    {
        "name": "EXAONE-2.4B",
        "ollama_tag": "exaone3.5/2.4b",
        "params": "2.4B",
    },
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

# edge/server.py 기본값과 동일
N_CTX = int(os.getenv("EDGE_N_CTX", "512"))
N_THREADS = int(os.getenv("EDGE_N_THREADS", "4"))
MAX_TOKENS = int(os.getenv("EDGE_MAX_TOKENS", "256"))


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
    gguf_path: str
    gguf_size_mb: float = 0
    load_time_sec: float = 0
    avg_latency_ms: float = 0
    avg_answer_chars: float = 0
    success_rate: float = 0
    korean_rate: float = 0
    results: list[InferenceResult] = field(default_factory=list)


def is_korean(text: str) -> bool:
    """텍스트에 한국어가 50% 이상인지 판별."""
    if not text:
        return False
    korean_chars = sum(1 for c in text if '\uac00' <= c <= '\ud7a3')
    alpha_chars = sum(1 for c in text if c.isalpha())
    return (korean_chars / alpha_chars) >= 0.5 if alpha_chars > 0 else False


def run_model_benchmark(
    model_info: dict, questions: list[str],
) -> ModelSummary:
    """단일 모델 llama-cpp 벤치마크 (edge/server.py 동일 방식)."""
    from llama_cpp import Llama

    name = model_info["name"]
    gguf_path = _resolve_gguf_path(model_info["ollama_tag"])
    if not gguf_path:
        logger.error("[%s] GGUF 파일을 찾을 수 없습니다: %s", name, model_info["ollama_tag"])
        return ModelSummary(model_name=name, params=model_info["params"], gguf_path="")

    gguf_size_mb = round(os.path.getsize(gguf_path) / 1024 / 1024, 1)
    logger.info("=== %s (%s, %.0fMB) ===", name, model_info["params"], gguf_size_mb)

    # 모델 로드 (edge/server.py load_model과 동일)
    t0 = time.monotonic()
    try:
        llm = Llama(model_path=gguf_path, n_ctx=N_CTX, n_threads=N_THREADS, verbose=False)
    except Exception as e:
        logger.error("[%s] 모델 로드 실패: %s", name, e)
        return ModelSummary(
            model_name=name, params=model_info["params"],
            gguf_path=gguf_path, gguf_size_mb=gguf_size_mb,
        )
    load_time = round(time.monotonic() - t0, 1)
    logger.info("[%s] 로드 완료: %.1fs", name, load_time)

    # 워밍업
    try:
        llm.create_chat_completion(
            messages=[{"role": "user", "content": "안녕하세요"}],
            max_tokens=16,
        )
    except Exception as e:
        logger.warning("[%s] 워밍업 실패: %s", name, e)

    # 추론 (edge/server.py /ask 엔드포인트와 동일)
    results: list[InferenceResult] = []
    for i, question in enumerate(questions):
        logger.info("[%s] %d/%d: %s", name, i + 1, len(questions), question[:30])
        t0 = time.monotonic()
        try:
            output = llm.create_chat_completion(
                messages=[{"role": "user", "content": question}],
                max_tokens=MAX_TOKENS,
            )
            answer = output["choices"][0]["message"]["content"].strip()
            latency_ms = int((time.monotonic() - t0) * 1000)
            results.append(InferenceResult(
                model_name=name, question=question,
                answer=answer, latency_ms=latency_ms, success=True,
            ))
        except Exception as e:
            latency_ms = int((time.monotonic() - t0) * 1000)
            logger.error("[%s] 추론 실패: %s", name, e)
            results.append(InferenceResult(
                model_name=name, question=question,
                answer="", latency_ms=latency_ms, success=False, error=str(e),
            ))

    # 메모리 해제
    del llm
    gc.collect()

    successful = [r for r in results if r.success]
    korean_count = sum(1 for r in successful if is_korean(r.answer))

    return ModelSummary(
        model_name=name,
        params=model_info["params"],
        gguf_path=gguf_path,
        gguf_size_mb=gguf_size_mb,
        load_time_sec=load_time,
        avg_latency_ms=round(sum(r.latency_ms for r in successful) / len(successful)) if successful else 0,
        avg_answer_chars=round(sum(len(r.answer) for r in successful) / len(successful)) if successful else 0,
        success_rate=len(successful) / len(results) if results else 0,
        korean_rate=korean_count / len(successful) if successful else 0,
        results=results,
    )


# ---------------------------------------------------------------------------
# 출력
# ---------------------------------------------------------------------------

def print_results(summaries: list[ModelSummary]) -> None:
    print("\n" + "=" * 100)
    print("엣지 서빙 벤치마크 (llama-cpp-python, edge/server.py 동일 엔진)")
    print(f"설정: n_ctx={N_CTX}, n_threads={N_THREADS}, max_tokens={MAX_TOKENS}")
    print("=" * 100)
    print(f"{'모델':<18} {'파라미터':>6} {'GGUF(MB)':>9} {'로드(s)':>8} "
          f"{'지연(ms)':>9} {'답변(자)':>8} {'한국어':>7} {'성공률':>7}")
    print("-" * 100)
    for s in summaries:
        print(f"{s.model_name:<18} {s.params:>6} {s.gguf_size_mb:>9.0f} "
              f"{s.load_time_sec:>8.1f} {s.avg_latency_ms:>9.0f} "
              f"{s.avg_answer_chars:>8.0f} {s.korean_rate:>6.0%} {s.success_rate:>6.0%}")

    # 샘플 답변 비교
    valid = [s for s in summaries if s.results]
    if len(valid) >= 2:
        print("\n--- 샘플 답변 비교 (첫 3개 질문) ---")
        for i in range(min(3, len(valid[0].results))):
            q = valid[0].results[i].question
            print(f"\nQ: {q}")
            for s in valid:
                if i < len(s.results):
                    a = s.results[i].answer[:200]
                    lang = "KR" if is_korean(s.results[i].answer) else "??"
                    print(f"  [{s.model_name}] ({lang}) {a}")


def print_comparison(summaries: list[ModelSummary]) -> None:
    """transformers 결과와 비교."""
    pilot_path = Path(__file__).parent.parent / "pilot_benchmark_results.json"
    if not pilot_path.exists():
        return

    pilot = json.loads(pilot_path.read_text())
    print("\n" + "=" * 100)
    print("Transformers (FP32) vs GGUF (Q4_K_M, llama-cpp) 비교")
    print("=" * 100)
    print(f"{'모델':<18} {'':^24} {'지연(ms)':^24} {'답변(자)':^24}")
    print(f"{'':18} {'Transformers':>12} {'GGUF':>12} {'Transformers':>12} {'GGUF':>12}")
    print("-" * 100)

    for s in summaries:
        p = pilot.get(s.model_name, {})
        tf_lat = p.get("avg_latency_ms", "-")
        tf_chars = p.get("avg_answer_chars", "-")
        tf_lat_str = f"{tf_lat:>12.0f}" if isinstance(tf_lat, (int, float)) else f"{tf_lat:>12}"
        tf_chars_str = f"{tf_chars:>12.0f}" if isinstance(tf_chars, (int, float)) else f"{tf_chars:>12}"
        gguf_lat = f"{s.avg_latency_ms:>12.0f}" if s.avg_latency_ms > 0 else f"{'FAIL':>12}"
        gguf_chars = f"{s.avg_answer_chars:>12.0f}" if s.avg_answer_chars > 0 else f"{'FAIL':>12}"
        speedup = ""
        if isinstance(tf_lat, (int, float)) and s.avg_latency_ms > 0:
            ratio = tf_lat / s.avg_latency_ms
            speedup = f" ({ratio:.1f}x)"
        print(f"{s.model_name:<18} {tf_lat_str} {gguf_lat}{speedup} {tf_chars_str} {gguf_chars}")


def print_recommendation(summaries: list[ModelSummary]) -> None:
    valid = [s for s in summaries if s.success_rate > 0]
    if not valid:
        print("\n모든 모델이 실패했습니다.")
        return

    print("\n" + "=" * 60)
    print("종합 추천")
    print("=" * 60)

    kr_stable = [s for s in valid if s.korean_rate >= 0.8]
    if kr_stable:
        fastest = min(kr_stable, key=lambda x: x.avg_latency_ms)
        print(f"\n  한국어 안정 + 최고 속도: {fastest.model_name} "
              f"({fastest.avg_latency_ms:.0f}ms, 한국어 {fastest.korean_rate:.0%})")

    print("\n  ── 엣지 배포 적합성 ──")
    for s in sorted(valid, key=lambda x: x.avg_latency_ms):
        kr_badge = "OK" if s.korean_rate >= 0.8 else "NG"
        verdict = ""
        if s.korean_rate < 0.8:
            verdict = " <- 한국어 불안정"
        elif s.params == "0.5B":
            verdict = " <- POS 엣지 추천"
        elif s.params in ("1B", "1.5B"):
            verdict = " <- 엣지 서버 추천"
        elif s.params == "2.4B":
            verdict = " <- 사내 서버 / 참고"
        print(f"  {s.model_name:<18} {s.params:>5} | {s.gguf_size_mb:>6.0f}MB | "
              f"{s.avg_latency_ms:>6.0f}ms | KR:{kr_badge}{verdict}")

    failed = [s for s in summaries if s.success_rate == 0]
    if failed:
        print("\n  ── 실패 모델 ──")
        for s in failed:
            print(f"  {s.model_name:<18} {s.params:>5} | 로드/추론 실패")


def save_results(summaries: list[ModelSummary], output: str) -> None:
    data = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "backend": "llama-cpp-python",
        "config": {"n_ctx": N_CTX, "n_threads": N_THREADS, "max_tokens": MAX_TOKENS},
        "models": [
            {
                "model": s.model_name,
                "params": s.params,
                "gguf_size_mb": s.gguf_size_mb,
                "load_time_sec": s.load_time_sec,
                "avg_latency_ms": s.avg_latency_ms,
                "avg_answer_chars": s.avg_answer_chars,
                "success_rate": s.success_rate,
                "korean_rate": s.korean_rate,
                "results": [
                    {
                        "question": r.question,
                        "answer": r.answer,
                        "latency_ms": r.latency_ms,
                        "success": r.success,
                        "error": r.error,
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
    parser = argparse.ArgumentParser(description="Edge serving benchmark (llama-cpp-python)")
    parser.add_argument("--sample", type=int, default=20, help="테스트 질문 수")
    parser.add_argument("--models", nargs="+", help="특정 모델만 (e.g. Qwen2.5-0.5B EXAONE-2.4B)")
    parser.add_argument("--output", default="edge_benchmark_results.json")
    args = parser.parse_args()

    candidates = CANDIDATE_MODELS
    if args.models:
        candidates = [m for m in candidates if m["name"] in args.models]

    questions = PBU_QUESTIONS[:args.sample]
    summaries: list[ModelSummary] = []

    for model_info in candidates:
        summary = run_model_benchmark(model_info, questions)
        summaries.append(summary)

    print_results(summaries)
    print_comparison(summaries)
    print_recommendation(summaries)
    save_results(summaries, args.output)


if __name__ == "__main__":
    main()
