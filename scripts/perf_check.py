#!/usr/bin/env python3
"""Compare k6 summary JSON output against perf baseline; fail on regression.

Usage:
    k6 run --summary-export=loadtest/results/search.json loadtest/search.js
    python scripts/perf_check.py loadtest/results/search.json --scenario search.js \
        --baseline loadtest/baseline.json --tolerance 0.20

Tolerance: how much worse than baseline is acceptable (0.20 = 20% slower OK).
Exit code 1 on any regression beyond tolerance.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def extract_metrics(k6_summary: dict) -> dict[str, float]:
    """Pull the metrics we care about out of k6 --summary-export JSON."""
    metrics = k6_summary.get("metrics", {})
    duration = metrics.get("http_req_duration", {})
    failed = metrics.get("http_req_failed", {})
    requests = metrics.get("http_reqs", {})

    duration_values = duration.get("values", {})
    return {
        "http_req_duration_p50": duration_values.get("med") or duration_values.get("p(50)") or 0.0,
        "http_req_duration_p95": duration_values.get("p(95)", 0.0),
        "http_req_duration_p99": duration_values.get("p(99)", 0.0),
        "http_req_failed_rate": failed.get("values", {}).get("rate", 0.0),
        "throughput_min": requests.get("values", {}).get("rate", 0.0),
    }


def compare(actual: dict[str, float], baseline: dict[str, float], tolerance: float) -> list[str]:
    """Return list of regression descriptions (empty if all OK)."""
    regressions: list[str] = []
    for metric, base in baseline.items():
        if metric.startswith("_") or metric not in actual:
            continue
        cur = actual[metric]

        # For latency / failure_rate: higher = worse. Allow up to base * (1 + tol).
        if metric.endswith("_rate") or "duration" in metric:
            limit = base * (1 + tolerance)
            if cur > limit:
                regressions.append(
                    f"{metric}: {cur:.2f} > baseline {base:.2f} (+{tolerance:.0%} tol = {limit:.2f})"
                )
        # For throughput: higher = better. Allow drop to base * (1 - tol).
        elif "throughput" in metric:
            floor = base * (1 - tolerance)
            if cur < floor:
                regressions.append(
                    f"{metric}: {cur:.2f} < baseline {base:.2f} (-{tolerance:.0%} tol = {floor:.2f})"
                )
    return regressions


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("summary", type=Path, help="k6 --summary-export output JSON")
    parser.add_argument("--scenario", required=True, help="key in baseline.json (e.g., search.js)")
    parser.add_argument("--baseline", type=Path, default=Path("loadtest/baseline.json"))
    parser.add_argument("--tolerance", type=float, default=0.20, help="default 20%")
    args = parser.parse_args()

    if not args.summary.exists():
        logger.error("k6 summary not found: %s", args.summary)
        return 2
    if not args.baseline.exists():
        logger.error("baseline not found: %s", args.baseline)
        return 2

    summary = json.loads(args.summary.read_text())
    baseline_all = json.loads(args.baseline.read_text())
    scenario_baseline = baseline_all.get(args.scenario)
    if not scenario_baseline:
        logger.error("scenario %s not in baseline.json", args.scenario)
        return 2

    actual = extract_metrics(summary)
    logger.info("Scenario: %s", args.scenario)
    logger.info("Actual:   %s", json.dumps(actual, indent=2))
    logger.info("Baseline: %s", json.dumps({k: v for k, v in scenario_baseline.items() if not k.startswith("_")}, indent=2))

    regressions = compare(actual, scenario_baseline, args.tolerance)
    if not regressions:
        logger.info("✓ Performance gate passed (tolerance=%.0f%%)", args.tolerance * 100)
        return 0

    logger.error("✗ Performance regression detected:")
    for r in regressions:
        logger.error("  %s", r)
    return 1


if __name__ == "__main__":
    sys.exit(main())
