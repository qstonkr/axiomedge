"""Run GraphRAG sequentially for all KBs."""
import subprocess
import sys
import time

KB_ORDER = ["a-ari", "drp", "g-espa", "partnertalk", "hax", "itops_general"]

# Allow skipping already-completed KBs via CLI arg
skip_until = sys.argv[1] if len(sys.argv) > 1 else None
started = skip_until is None

for kb in KB_ORDER:
    if not started:
        if kb == skip_until:
            started = True
        else:
            print(f"[SKIP] {kb}")
            continue

    print(f"\n{'='*60}")
    print(f"[START] {kb} — {time.strftime('%H:%M:%S')}")
    print(f"{'='*60}")

    result = subprocess.run(
        ["uv", "run", "python", "scripts/run_graphrag.py", kb],
        env={
            **__import__("os").environ,
            "GRAPHRAG_USE_SAGEMAKER": "true",
            "AWS_PROFILE": "jeongbeomkim",
        },
    )

    if result.returncode != 0:
        print(f"[FAIL] {kb} — exit code {result.returncode}")
    else:
        print(f"[DONE] {kb} — {time.strftime('%H:%M:%S')}")

print(f"\n{'='*60}")
print(f"ALL DONE — {time.strftime('%H:%M:%S')}")
