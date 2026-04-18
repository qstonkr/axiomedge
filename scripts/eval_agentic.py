#!/usr/bin/env python3
"""Agentic RAG vs Naive RAG A/B 평가 — golden set 활용.

기존 scripts/distill/run_rag_evaluation.py 의 golden set 을 재사용해서
naive (`/api/v1/search/hub`) vs agentic (`/api/v1/agentic/ask`) 를 같은 question
으로 호출 후 답변 품질 비교.

비교 메트릭:
- Multi-hop accuracy (golden set "복합 질문" 카테고리)
- Latency p50/p95
- Cost per query (agentic 만 — naive 는 토큰 추적 X)

Usage:
    AWS_PROFILE=$AWS_PROFILE uv run python scripts/eval_agentic.py
    uv run python scripts/eval_agentic.py --kb g-espa --limit 10 --output eval/agentic_ab.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import statistics
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)


@dataclass
class QueryResult:
    query: str
    naive_answer: str = ""
    naive_latency_ms: float = 0.0
    naive_chunk_count: int = 0
    naive_error: str | None = None
    agentic_answer: str = ""
    agentic_latency_ms: float = 0.0
    agentic_iterations: int = 0
    agentic_steps: int = 0
    agentic_cost_usd: float = 0.0
    agentic_confidence: float = 0.0
    agentic_provider: str = ""
    agentic_error: str | None = None


@dataclass
class EvalSummary:
    total: int
    naive_success: int
    agentic_success: int
    naive_latency_p50: float = 0.0
    naive_latency_p95: float = 0.0
    agentic_latency_p50: float = 0.0
    agentic_latency_p95: float = 0.0
    agentic_total_cost_usd: float = 0.0
    avg_iterations: float = 0.0
    avg_steps: float = 0.0
    results: list[QueryResult] = field(default_factory=list)


async def _post_naive(
    client: httpx.AsyncClient, base_url: str, query: str, kb_ids: list[str] | None,
) -> dict[str, Any]:
    body: dict[str, Any] = {"query": query, "top_k": 5, "include_answer": True}
    if kb_ids:
        body["kb_ids"] = kb_ids
    resp = await client.post(f"{base_url}/api/v1/search/hub", json=body, timeout=120)
    resp.raise_for_status()
    return resp.json()


async def _post_agentic(
    client: httpx.AsyncClient, base_url: str, query: str, kb_ids: list[str] | None,
) -> dict[str, Any]:
    body: dict[str, Any] = {"query": query}
    if kb_ids:
        body["kb_ids"] = kb_ids
    resp = await client.post(f"{base_url}/api/v1/agentic/ask", json=body, timeout=180)
    resp.raise_for_status()
    return resp.json()


async def _evaluate_one(
    client: httpx.AsyncClient, base_url: str, query: str, kb_ids: list[str] | None,
) -> QueryResult:
    result = QueryResult(query=query)
    # naive
    t0 = time.perf_counter()
    try:
        naive = await _post_naive(client, base_url, query, kb_ids)
        result.naive_latency_ms = (time.perf_counter() - t0) * 1000
        result.naive_answer = naive.get("answer") or ""
        result.naive_chunk_count = naive.get("total_chunks", 0)
    except Exception as e:  # noqa: BLE001
        result.naive_error = f"{type(e).__name__}: {e}"
        result.naive_latency_ms = (time.perf_counter() - t0) * 1000
    # agentic
    t0 = time.perf_counter()
    try:
        agentic = await _post_agentic(client, base_url, query, kb_ids)
        result.agentic_latency_ms = (time.perf_counter() - t0) * 1000
        result.agentic_answer = agentic.get("answer") or ""
        result.agentic_iterations = agentic.get("iteration_count", 0)
        result.agentic_steps = agentic.get("total_steps_executed", 0)
        result.agentic_cost_usd = agentic.get("estimated_cost_usd", 0.0)
        result.agentic_confidence = agentic.get("confidence", 0.0)
        result.agentic_provider = agentic.get("llm_provider", "")
    except Exception as e:  # noqa: BLE001
        result.agentic_error = f"{type(e).__name__}: {e}"
        result.agentic_latency_ms = (time.perf_counter() - t0) * 1000
    return result


async def run_eval(
    queries: list[str], base_url: str, kb_ids: list[str] | None,
) -> EvalSummary:
    summary = EvalSummary(total=len(queries), naive_success=0, agentic_success=0)
    async with httpx.AsyncClient() as client:
        for i, q in enumerate(queries):
            logger.info("[%d/%d] %s", i + 1, len(queries), q[:80])
            r = await _evaluate_one(client, base_url, q, kb_ids)
            summary.results.append(r)
            if not r.naive_error and r.naive_answer:
                summary.naive_success += 1
            if not r.agentic_error and r.agentic_answer:
                summary.agentic_success += 1

    naive_latencies = [r.naive_latency_ms for r in summary.results if not r.naive_error]
    agentic_latencies = [r.agentic_latency_ms for r in summary.results if not r.agentic_error]
    if naive_latencies:
        summary.naive_latency_p50 = statistics.median(naive_latencies)
        summary.naive_latency_p95 = _percentile(naive_latencies, 95)
    if agentic_latencies:
        summary.agentic_latency_p50 = statistics.median(agentic_latencies)
        summary.agentic_latency_p95 = _percentile(agentic_latencies, 95)
    summary.agentic_total_cost_usd = sum(r.agentic_cost_usd for r in summary.results)
    iters = [r.agentic_iterations for r in summary.results if not r.agentic_error]
    steps = [r.agentic_steps for r in summary.results if not r.agentic_error]
    summary.avg_iterations = statistics.mean(iters) if iters else 0.0
    summary.avg_steps = statistics.mean(steps) if steps else 0.0
    return summary


def _percentile(values: list[float], p: int) -> float:
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    k = (len(sorted_vals) - 1) * (p / 100)
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    return sorted_vals[f] + (k - f) * (sorted_vals[c] - sorted_vals[f])


def _print_summary(summary: EvalSummary) -> None:
    s = summary
    print()
    print("=" * 70)
    print(f"Agentic RAG vs Naive RAG — {s.total} queries")
    print("=" * 70)
    print(f"  Success rate:    naive {s.naive_success}/{s.total}  |  agentic {s.agentic_success}/{s.total}")
    print(f"  Latency p50:     naive {s.naive_latency_p50:.0f}ms  |  agentic {s.agentic_latency_p50:.0f}ms")
    print(f"  Latency p95:     naive {s.naive_latency_p95:.0f}ms  |  agentic {s.agentic_latency_p95:.0f}ms")
    print(f"  Avg iterations:  agentic {s.avg_iterations:.1f}")
    print(f"  Avg steps:       agentic {s.avg_steps:.1f}")
    print(f"  Total cost:      agentic ${s.agentic_total_cost_usd:.4f}")
    print()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--queries-file", type=Path,
        help="JSON 또는 텍스트 파일 (한 줄 한 question). 미지정 시 sample 5개 사용.",
    )
    parser.add_argument("--kb", default=None, help="kb_id (콤마 구분). 비우면 전체.")
    parser.add_argument("--limit", type=int, default=0, help="처리 query 수 (0 = 전체)")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--output", type=Path, default=Path("eval/agentic_ab.json"))
    args = parser.parse_args()

    if args.queries_file:
        text = args.queries_file.read_text()
        try:
            queries = json.loads(text)
            if isinstance(queries, dict) and "queries" in queries:
                queries = queries["queries"]
        except json.JSONDecodeError:
            queries = [line.strip() for line in text.splitlines() if line.strip()]
    else:
        # Default sample for smoke
        queries = [
            "Kubernetes pod 재시작 방법 알려줘",
            "신촌점 차주 매장 점검 일정",
            "API 502 에러 디버깅 방법",
            "PBU 가 뭐야?",
            "최근 3일 사이 가장 많이 본 KB 는?",
        ]
    if args.limit > 0:
        queries = queries[: args.limit]
    kb_ids = [k.strip() for k in args.kb.split(",")] if args.kb else None

    logger.info("Eval target: %s | KBs: %s | %d queries", args.base_url, kb_ids, len(queries))
    summary = asyncio.run(run_eval(queries, args.base_url, kb_ids))
    _print_summary(summary)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(asdict(summary), ensure_ascii=False, indent=2))
    logger.info("Detailed results → %s", args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
