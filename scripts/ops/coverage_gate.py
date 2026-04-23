"""Touched-file coverage gate.

PR 이 수정한 파일만 뽑아 coverage 기준을 체크한다. 전체 coverage 임계값
(``pyproject.toml::fail_under``) 와는 별개로, **각 PR 이 건드린 파일은
line 커버리지 80% 이상** 을 유지하도록 강제한다. 이렇게 해야:

1. 새 코드는 항상 잘 테스트되고 (`uv run pytest` 돌릴 때 새 test 없이
   file touch 하면 바로 fail)
2. 기존 저커버리지 파일을 "살짝 수정" 하다가 더 나빠지는 일 방지
3. 전체 커버리지 베이스라인이 monotonic 하게 상승

Usage:
    # Local (main 과 비교)
    uv run python scripts/ops/coverage_gate.py

    # 다른 base 비교
    uv run python scripts/ops/coverage_gate.py --base origin/main --threshold 80

    # JSON 리포트 경로 지정
    uv run python scripts/ops/coverage_gate.py --report coverage.json

    # 간편 사용: ``make test-coverage-gate``

전제:
    pytest-cov 를 ``--cov=src --cov-report=json:coverage.json`` 으로 돌려
    coverage.json 이 있어야 한다. Makefile ``test-unit`` target 이 대신 처리.

면제 파일 (gate 제외):
    - ``src/llm/prompts.py``  (템플릿 상수)
    - 테스트 자체는 커버리지 측정 대상 아님
    - ``dashboard/`` / ``scripts/`` / ``cli/`` 는 source scope 에 없음 (pyproject.toml 참고)
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

DEFAULT_THRESHOLD = 80.0
DEFAULT_REPORT = "coverage.json"
DEFAULT_BASE = "main"

# Gate 에서 제외할 파일. 이유 주석 필수.
EXEMPT_FILES: frozenset[str] = frozenset({
    # Prompt template 상수 모음 — 실행 경로가 아님.
    "src/llm/prompts.py",
})


def git_changed_files(base: str) -> list[str]:
    """``git diff --name-only base...HEAD`` 로 touched file 추출."""
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", f"{base}...HEAD"],
            capture_output=True, text=True, check=True,
        )
    except subprocess.CalledProcessError as e:
        # ``base`` 가 존재하지 않는 CI 환경 (새 repo) → fallback 으로 unstaged
        print(f"[coverage-gate] git diff vs {base} failed: {e}", file=sys.stderr)
        result = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            capture_output=True, text=True, check=True,
        )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def filter_src_python(files: list[str]) -> list[str]:
    """``src/**/*.py`` 이고 exempt 가 아닌 파일만."""
    out: list[str] = []
    for path in files:
        if not path.startswith("src/"):
            continue
        if not path.endswith(".py"):
            continue
        if path.endswith("__init__.py"):
            # 대부분 re-export facade — 커버리지 의미 없음
            continue
        if path in EXEMPT_FILES:
            continue
        out.append(path)
    return out


def load_report(report_path: str) -> dict:
    """Coverage JSON 파서. 없거나 깨져 있으면 즉시 exit."""
    p = Path(report_path)
    if not p.exists():
        print(
            f"[coverage-gate] coverage report not found: {report_path}\n"
            "  먼저 `make test-unit` 또는 "
            "`uv run pytest --cov=src --cov-report=json:coverage.json` 실행.",
            file=sys.stderr,
        )
        sys.exit(2)
    try:
        with p.open() as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        print(f"[coverage-gate] invalid JSON in {report_path}: {e}", file=sys.stderr)
        sys.exit(2)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default=DEFAULT_BASE, help="git base ref (default: main)")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD,
                        help="per-file line coverage floor (default: 80)")
    parser.add_argument("--report", default=DEFAULT_REPORT,
                        help="coverage JSON path (default: coverage.json)")
    args = parser.parse_args()

    changed = git_changed_files(args.base)
    targets = filter_src_python(changed)
    if not targets:
        print(f"[coverage-gate] no src/*.py files changed vs {args.base} — skip")
        return 0

    report = load_report(args.report)
    files_report: dict = report.get("files", {})

    print(f"[coverage-gate] threshold = {args.threshold:.0f}% (per file)")
    print(f"[coverage-gate] touched {len(targets)} src file(s):")

    failures: list[tuple[str, float]] = []
    missing_from_report: list[str] = []

    for path in sorted(targets):
        entry = files_report.get(path)
        if entry is None:
            # coverage 리포트에 해당 파일 정보가 없음 — 신규 파일인데
            # 아무도 import 하지 않았거나 (dead code), 테스트가 import 안 했거나.
            missing_from_report.append(path)
            continue
        pct = entry.get("summary", {}).get("percent_covered", 0.0)
        num_stmt = entry.get("summary", {}).get("num_statements", 0)
        marker = "✅" if pct >= args.threshold else "❌"
        print(f"  {marker} {pct:5.1f}%  ({num_stmt:4d} stmt)  {path}")
        if pct < args.threshold:
            failures.append((path, pct))

    if missing_from_report:
        print("\n[coverage-gate] WARNING — touched files not in coverage report:")
        for path in missing_from_report:
            print(f"    ⚠  {path}")
        print(
            "  → 해당 파일이 어떤 test 에서도 import 되지 않았을 가능성.\n"
            "    pragma: no cover 가 아닌데 리포트 누락이면 gate 실패로 처리.",
        )
        if len(missing_from_report) > 0:
            failures.extend((p, 0.0) for p in missing_from_report)

    if failures:
        print(f"\n[coverage-gate] ❌ {len(failures)} file(s) below {args.threshold:.0f}% floor:")
        for path, pct in failures:
            print(f"    {pct:5.1f}%  {path}")
        print(
            "\n  Fix options:\n"
            "    1. Add unit tests (권장)\n"
            "    2. Split PR 해서 복잡한 분기만 먼저 수정\n"
            "    3. # pragma: no cover (명확한 근거 + 리뷰어 승인 필수)\n"
            "\n  docs/TESTING.md 참고.",
        )
        return 1

    print(f"\n[coverage-gate] ✅ all {len(targets)} touched files >= {args.threshold:.0f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
