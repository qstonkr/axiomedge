"""Helpers for graph_schema admin API: atomic YAML writer + git wrapper.

Spec §5.3 (YAML auto-PR flow).
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import yaml

logger = logging.getLogger(__name__)

_SCHEMA_DIR = Path("deploy/config/graph_schemas")
_DATE_ISO_FMT = "%Y-%m-%dT%H:%M:%SZ"


def merge_label_into_yaml(
    *,
    kb_id: str,
    candidate_type: Literal["node", "relationship"],
    label: str,
    approved_by: str,
) -> Path:
    """Add ``label`` to the KB's YAML, bump version, record metadata.

    - Atomic: write to temp file, then rename.
    - Idempotent: re-approving an existing label is a no-op for the list.
    - If the KB has no YAML file yet, create one seeded with this label.

    Returns the path to the updated YAML.
    """
    _SCHEMA_DIR.mkdir(parents=True, exist_ok=True)
    path = _SCHEMA_DIR / f"{kb_id}.yaml"

    if path.exists():
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    else:
        data = {
            "version": 0,  # bumped to 1 below
            "kb_id": kb_id,
            "prompt_focus": "",
            "nodes": [],
            "relationships": [],
            "options": {
                "disable_bootstrap": False,
                "schema_evolution": "batch",
                "bootstrap_sample_size": 100,
            },
        }

    key = "nodes" if candidate_type == "node" else "relationships"
    labels: list[str] = list(data.get(key) or [])
    if label not in labels:
        labels.append(label)
        labels.sort()
        data[key] = labels

    data["version"] = int(data.get("version", 0)) + 1

    meta = data.setdefault("_metadata", {})
    meta["last_approved_at"] = datetime.now(UTC).strftime(_DATE_ISO_FMT)
    meta["last_approved_by"] = approved_by
    approved_list = meta.setdefault("approved_candidates", [])
    entry = {
        "label": label,
        "type": candidate_type,
        "version_added": data["version"],
    }
    if entry not in approved_list:
        approved_list.append(entry)

    _atomic_write_yaml(path, data)
    return path


def _atomic_write_yaml(path: Path, data: dict[str, Any]) -> None:
    """Write YAML via temp file + rename to avoid partial-write races."""
    with tempfile.NamedTemporaryFile(
        mode="w", dir=path.parent, delete=False, suffix=".tmp",
        encoding="utf-8",
    ) as tmp:
        yaml.dump(
            data, tmp, allow_unicode=True,
            sort_keys=False, default_flow_style=False,
        )
        tmp_path = Path(tmp.name)
    os.replace(tmp_path, path)


def git_commit_and_push(
    *,
    path: Path,
    branch: str,
    message: str,
    bot_name: str = "axiomedge-schema-bot",
    bot_email: str = "schema-bot@axiomedge.local",
    push: bool | None = None,
) -> dict[str, Any]:
    """Create a branch, commit ``path``, optionally push.

    ``push`` default: controlled by ``GRAPH_SCHEMA_AUTO_PUSH`` env (set to
    ``"1"``/``"true"`` to enable). Default off so CI/tests don't accidentally
    push. Returns ``{branch, commit_sha, pushed}``.
    """
    if push is None:
        push = os.getenv("GRAPH_SCHEMA_AUTO_PUSH", "").lower() in (
            "1", "true", "yes",
        )

    def _run(*args: str) -> str:
        r = subprocess.run(
            list(args), capture_output=True, text=True, check=False,
        )
        if r.returncode != 0:
            raise RuntimeError(f"git {' '.join(args[1:])}: {r.stderr.strip()}")
        return r.stdout.strip()

    _run("git", "checkout", "-B", branch)
    _run("git", "add", str(path))
    _run(
        "git", "-c", f"user.name={bot_name}", "-c", f"user.email={bot_email}",
        "commit", "-m", message,
    )
    sha = _run("git", "rev-parse", "HEAD")

    pushed = False
    if push:
        _run("git", "push", "-u", "origin", branch)
        pushed = True

    return {"branch": branch, "commit_sha": sha, "pushed": pushed}


__all__ = ["git_commit_and_push", "merge_label_into_yaml"]
