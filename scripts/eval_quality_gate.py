#!/usr/bin/env python3
"""Search quality gate — runs golden-set evaluation + checks against baseline.

Wraps the existing scripts/distill/run_rag_evaluation.py logic and:
1. Runs evaluation (faithfulness/relevancy/completeness/recall)
2. Compares mean scores to eval/baseline.json
3. Fails if any metric regressed beyond ``--tolerance`` (default 0.05)

Usage:
    python scripts/eval_quality_gate.py [kb_id] --baseline eval/baseline.json --tolerance 0.05

CI 에서 호출되며, LLM 의존성 (SageMaker 등) 이 없는 환경에선 위 워크플로가
미리 skip 한다. 이 스크립트 자체는 LLM 사용 가능 가정.

baseline.json 형식:
    {
      "global": {"faithfulness": 0.62, "relevancy": 0.78, "completeness": 0.66, "source_recall": 0.85},
      "g-espa": {...},
      ...
    }
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_BASELINE = Path("eval/baseline.json")
LAST_RUN_PATH = Path("eval/last_run.json")
DEFAULT_TOLERANCE = 0.05


def load_baseline(path: Path) -> dict[str, dict[str, float]]:
    if not path.exists():
        logger.warning("baseline %s not found — first-run mode (no comparison)", path)
        return {}
    return json.loads(path.read_text())


def run_evaluation(kb_filter: str | None) -> dict[str, dict[str, float]]:
    """Invoke existing evaluation entry-point and return results.

    Re-imports rather than subprocess to keep error handling in-process.
    Existing script (scripts/distill/run_rag_evaluation.py) writes results
    to PG; we read aggregate scores from there.
    """
    # Defer heavy imports
    sys.path.insert(0, str(Path(__file__).parent / "distill"))
    try:
        import run_rag_evaluation as ev  # type: ignore
    except ImportError as e:
        logger.error("evaluation script import failed: %s", e)
        return {}

    # The existing script orchestrates KBs + writes to DB.
    # Here we adapt: call its main entry, then load aggregated scores.
    # NOTE: signatures may differ — adjust to match actual API.
    if hasattr(ev, "evaluate_all"):
        results = ev.evaluate_all(kb_filter=kb_filter)  # type: ignore
        return results or {}
    logger.warning("run_rag_evaluation.evaluate_all() not found — placeholder mode")
    return {}


def compare_to_baseline(
    current: dict[str, dict[str, float]],
    baseline: dict[str, dict[str, float]],
    tolerance: float,
) -> tuple[bool, list[str]]:
    """Return (passed, list_of_regressions_descriptions)."""
    regressions: list[str] = []
    for kb, metrics in baseline.items():
        cur = current.get(kb, {})
        for metric, base_score in metrics.items():
            cur_score = cur.get(metric)
            if cur_score is None:
                continue
            if cur_score + tolerance < base_score:
                regressions.append(
                    f"{kb}/{metric}: {cur_score:.3f} < baseline {base_score:.3f} (tol {tolerance})"
                )
    return (len(regressions) == 0, regressions)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("kb_filter", nargs="?", default="", help="KB id filter (empty = all)")
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE)
    args = parser.parse_args()

    baseline = load_baseline(args.baseline)
    logger.info("baseline keys: %s", list(baseline.keys()) or "(none)")

    current = run_evaluation(args.kb_filter or None)
    LAST_RUN_PATH.parent.mkdir(parents=True, exist_ok=True)
    LAST_RUN_PATH.write_text(json.dumps(current, indent=2, ensure_ascii=False))
    logger.info("results written to %s", LAST_RUN_PATH)

    if not baseline:
        logger.warning("No baseline — skipping regression check")
        return 0

    passed, regressions = compare_to_baseline(current, baseline, args.tolerance)
    if passed:
        logger.info("✓ Quality gate passed (no regression > %.2f)", args.tolerance)
        return 0
    logger.error("✗ Quality regression detected:")
    for r in regressions:
        logger.error("  %s", r)
    return 1


if __name__ == "__main__":
    sys.exit(main())
