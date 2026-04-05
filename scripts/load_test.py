"""Simple load test for Knowledge Local API.

Usage:
    uv run python scripts/load_test.py                    # Default: 10 concurrent, 50 requests
    uv run python scripts/load_test.py --concurrent 20 --total 100
    uv run python scripts/load_test.py --endpoint health   # Health check only
    uv run python scripts/load_test.py --base-url http://10.0.1.5:8000
"""

from __future__ import annotations

import argparse
import asyncio
import random
import statistics
import sys
import time

import httpx

# ---------------------------------------------------------------------------
# Sample Korean queries for search endpoint
# ---------------------------------------------------------------------------
SAMPLE_QUERIES = [
    "GS25 점포 운영 절차",
    "분쟁 조정 신청 방법",
    "영업활성화 장려금",
    "주간 보고 내용",
    "상품 등록 방법",
]

# ---------------------------------------------------------------------------
# Result collector
# ---------------------------------------------------------------------------


class RequestResult:
    __slots__ = ("status", "latency", "error")

    def __init__(self, status: int, latency: float, error: str | None = None):
        self.status = status
        self.latency = latency
        self.error = error


# ---------------------------------------------------------------------------
# Request functions
# ---------------------------------------------------------------------------


async def request_health(client: httpx.AsyncClient, base_url: str) -> RequestResult:
    start = time.perf_counter()
    try:
        resp = await client.get(f"{base_url}/health")
        elapsed = time.perf_counter() - start
        return RequestResult(resp.status_code, elapsed)
    except Exception as exc:
        elapsed = time.perf_counter() - start
        return RequestResult(0, elapsed, str(exc))


async def request_search(client: httpx.AsyncClient, base_url: str) -> RequestResult:
    query = random.choice(SAMPLE_QUERIES)
    payload = {
        "query": query,
        "top_k": 3,
        "include_answer": False,
    }
    start = time.perf_counter()
    try:
        resp = await client.post(
            f"{base_url}/api/v1/search/hub",
            json=payload,
        )
        elapsed = time.perf_counter() - start
        return RequestResult(resp.status_code, elapsed)
    except Exception as exc:
        elapsed = time.perf_counter() - start
        return RequestResult(0, elapsed, str(exc))


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------

ENDPOINT_FNS = {
    "health": request_health,
    "search": request_search,
}


async def worker(
    sem: asyncio.Semaphore,
    client: httpx.AsyncClient,
    base_url: str,
    endpoint: str,
    results: list[RequestResult],
) -> None:
    fn = ENDPOINT_FNS[endpoint]
    async with sem:
        result = await fn(client, base_url)
        results.append(result)


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def percentile(data: list[float], pct: float) -> float:
    """Return the pct-th percentile of data (0-100 scale)."""
    if not data:
        return 0.0
    sorted_data = sorted(data)
    k = (len(sorted_data) - 1) * pct / 100.0
    f = int(k)
    c = f + 1
    if c >= len(sorted_data):
        return sorted_data[f]
    return sorted_data[f] + (k - f) * (sorted_data[c] - sorted_data[f])


def print_report(
    endpoint: str,
    results: list[RequestResult],
    wall_time: float,
    concurrent: int,
) -> None:
    total = len(results)
    latencies = [r.latency for r in results]
    successes = [r for r in results if 200 <= r.status < 300]
    errors = [r for r in results if r.status == 0 or r.status >= 400]

    print()
    print("=" * 64)
    print(f"  Load Test Report: {endpoint.upper()}")
    print("=" * 64)
    print()
    print(f"  {'Concurrency':<24} {concurrent}")
    print(f"  {'Total requests':<24} {total}")
    print(f"  {'Wall time':<24} {wall_time:.2f}s")
    print(f"  {'Throughput':<24} {total / wall_time:.1f} req/s")
    print()

    # Status breakdown
    status_counts: dict[int, int] = {}
    for r in results:
        status_counts[r.status] = status_counts.get(r.status, 0) + 1

    print("  Status Breakdown:")
    for status in sorted(status_counts):
        label = "conn_error" if status == 0 else str(status)
        print(f"    {label:<12} {status_counts[status]:>6}  ({status_counts[status]/total*100:.1f}%)")
    print()

    # Latency stats
    if latencies:
        print("  Latency (seconds):")
        print(f"    {'Avg':<12} {statistics.mean(latencies):>10.4f}")
        print(f"    {'Min':<12} {min(latencies):>10.4f}")
        print(f"    {'P50':<12} {percentile(latencies, 50):>10.4f}")
        print(f"    {'P95':<12} {percentile(latencies, 95):>10.4f}")
        print(f"    {'P99':<12} {percentile(latencies, 99):>10.4f}")
        print(f"    {'Max':<12} {max(latencies):>10.4f}")
    print()

    # Error rate
    error_rate = len(errors) / total * 100 if total else 0
    success_rate = len(successes) / total * 100 if total else 0
    print(f"  {'Success rate':<24} {success_rate:.1f}%")
    print(f"  {'Error rate':<24} {error_rate:.1f}%")

    # Show first few errors if any
    if errors:
        print()
        print("  Sample errors:")
        for r in errors[:3]:
            msg = r.error or f"HTTP {r.status}"
            print(f"    - {msg[:80]}")

    print()
    print("=" * 64)
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def run(
    base_url: str,
    endpoint: str,
    concurrent: int,
    total: int,
) -> None:
    if endpoint not in ENDPOINT_FNS:
        print(f"Unknown endpoint: {endpoint}. Choose from: {', '.join(ENDPOINT_FNS)}")
        sys.exit(1)

    print(f"Running load test: endpoint={endpoint}, concurrent={concurrent}, total={total}")
    print(f"Target: {base_url}")
    print()

    results: list[RequestResult] = []
    sem = asyncio.Semaphore(concurrent)

    timeout = httpx.Timeout(60.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        wall_start = time.perf_counter()
        tasks = [
            worker(sem, client, base_url, endpoint, results)
            for _ in range(total)
        ]
        await asyncio.gather(*tasks)
        wall_time = time.perf_counter() - wall_start

    print_report(endpoint, results, wall_time, concurrent)


def main() -> None:
    parser = argparse.ArgumentParser(description="Load test for Knowledge Local API")
    parser.add_argument(
        "--base-url",
        default="http://localhost:8000",
        help="API base URL (default: http://localhost:8000)",
    )
    parser.add_argument(
        "--endpoint",
        default="all",
        choices=["health", "search", "all"],
        help="Endpoint to test (default: all)",
    )
    parser.add_argument(
        "--concurrent",
        type=int,
        default=10,
        help="Max concurrent requests (default: 10)",
    )
    parser.add_argument(
        "--total",
        type=int,
        default=50,
        help="Total number of requests (default: 50)",
    )
    args = parser.parse_args()

    endpoints = list(ENDPOINT_FNS.keys()) if args.endpoint == "all" else [args.endpoint]
    for ep in endpoints:
        asyncio.run(run(args.base_url, ep, args.concurrent, args.total))


if __name__ == "__main__":
    main()
