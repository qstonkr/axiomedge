#!/bin/bash
# Run dashboard tests individually to avoid sys.modules pollution
set -e
FAILED=0
PASSED=0
for f in tests/unit/test_dashboard_*.py; do
  result=$(PYTHONPATH=dashboard .venv/bin/pytest "$f" -q --tb=no --no-cov 2>&1 | tail -1)
  if echo "$result" | grep -q "failed"; then
    echo "FAIL: $f - $result"
    FAILED=$((FAILED + 1))
  else
    count=$(echo "$result" | grep -oP '\d+ passed' | grep -oP '\d+')
    PASSED=$((PASSED + count))
  fi
done
echo "=== Dashboard Tests: $PASSED passed, $FAILED files with failures ==="
