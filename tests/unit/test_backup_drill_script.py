"""Tests for backup drill script — safety guards + parsing logic.

Actual restore round-trip is exercised by .github/workflows/backup-drill.yml
(nightly CI job with real Postgres + Qdrant containers).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).parent.parent.parent / "scripts" / "ops" / "backup_drill.sh"


def test_script_exists_and_executable() -> None:
    assert SCRIPT.exists(), f"missing: {SCRIPT}"
    assert os.access(SCRIPT, os.X_OK), "backup_drill.sh must be executable"


def test_refuses_without_confirmation() -> None:
    """Without DRILL_CONFIRM=yes, exits with code 2."""
    env = {**os.environ}
    env.pop("DRILL_CONFIRM", None)
    result = subprocess.run(
        [str(SCRIPT)], env=env, capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 2
    assert "DRILL_CONFIRM=yes" in result.stderr


def test_refuses_production_database_url() -> None:
    """Heuristic check rejects DATABASE_URLs containing 'prod' / '.com' / '.io'."""
    for url in [
        "postgresql://user:pass@db-prod.internal:5432/x",
        "postgresql://user:pass@my-host.com:5432/x",
        "postgresql://user:pass@my-host.io:5432/x",
        "postgresql://user:pass@production-db:5432/x",
    ]:
        env = {**os.environ, "DRILL_CONFIRM": "yes", "DATABASE_URL": url}
        result = subprocess.run(
            [str(SCRIPT)], env=env, capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 3, f"should reject {url}, got returncode={result.returncode}"
        assert "production" in result.stderr.lower()


def test_accepts_localhost_database_url() -> None:
    """Drill against localhost should not be rejected by safety guard.

    (We can't run the actual drill in unit tests — no Postgres available —
    but we verify the guard logic permits localhost.)
    """
    # Simulate: DRILL_CONFIRM=yes + DATABASE_URL=localhost → guard passes,
    # then fails downstream when psql can't connect. That's expected.
    env = {**os.environ, "DRILL_CONFIRM": "yes", "DATABASE_URL": "postgresql://u:p@localhost:5499/x"}
    result = subprocess.run(
        [str(SCRIPT)], env=env, capture_output=True, text=True, timeout=15,
    )
    # Either psql connection fails (expected w/o running PG) or other failure —
    # but NOT exit code 2 (missing confirm) or 3 (prod URL detected).
    assert result.returncode not in (2, 3), (
        f"safety guard incorrectly blocked localhost URL: rc={result.returncode}, stderr={result.stderr[:200]}"
    )
