"""One-shot migration: KB_SCHEMA_PROFILES → deploy/config/graph_schemas/*.yaml.

Usage:
    uv run python scripts/ops/migrate_schema_to_yaml.py

Idempotent — re-running does NOT overwrite existing YAML files. Delete the
file first if you truly want it regenerated.

Spec: §8.2.
"""

from __future__ import annotations

from pathlib import Path

import yaml

OUT_DIR = Path("deploy/config/graph_schemas")

# Hardcoded snapshot of the pre-migration KB_SCHEMA_PROFILES (prompts.py).
# Kept inline here so the migration script is self-contained and the tests
# can assert round-trip equivalence even after prompts.py moves to the
# YAML-backed proxy.
LEGACY_PROFILES: dict[str, dict[str, list[str] | str]] = {
    "a-ari": {
        "nodes": [
            "Store", "Process", "Product", "Person", "Policy", "Term", "Location",
        ],
        "relationships": [
            "OPERATES", "FOLLOWS", "SELLS", "MANAGES", "APPLIES_TO",
            "LOCATED_IN", "PART_OF",
        ],
        "prompt_focus": "점포, 절차/프로세스, 상품, 정책/규정, 용어",
    },
    "g-espa": {
        "nodes": [
            "Store", "Person", "Process", "Event", "Product", "Location",
            "Team", "Term",
        ],
        "relationships": [
            "MANAGES", "OPERATES", "PARTICIPATES_IN", "LOCATED_IN",
            "RESPONSIBLE_FOR", "RELATED_TO", "SELLS", "PART_OF",
        ],
        "prompt_focus": (
            "점포(GS25/CU), 경영주/OFC(사람), ESPA활동/개선활동, "
            "상품카테고리, 지역/상권, 매출성과, 경쟁점"
        ),
    },
    "drp": {
        "nodes": [
            "Store", "Person", "Policy", "Event", "Location", "Team",
        ],
        "relationships": [
            "MANAGES", "APPLIES_TO", "PARTICIPATES_IN", "LOCATED_IN",
            "RESPONSIBLE_FOR", "RELATED_TO",
        ],
        "prompt_focus": "점포, 당사자(사람), 정책/규정, 분쟁사건, 지역",
    },
    "hax": {
        "nodes": [
            "System", "Team", "Person", "Process", "Project", "Term", "Document",
        ],
        "relationships": [
            "MANAGES", "MEMBER_OF", "IMPLEMENTS", "OWNS", "RESPONSIBLE_FOR",
            "DEFINES", "PART_OF",
        ],
        "prompt_focus": "시스템, 팀/부서, 담당자, 프로세스, 프로젝트, 용어",
    },
    "itops_general": {
        "nodes": [
            "System", "Team", "Person", "Process", "Project", "Term",
            "Document", "Policy", "Logic",
        ],
        "relationships": [
            "MANAGES", "MEMBER_OF", "IMPLEMENTS", "OWNS", "RESPONSIBLE_FOR",
            "DEFINES", "PART_OF", "FOLLOWS", "APPLIES_TO",
        ],
        "prompt_focus": (
            "시스템, 팀/부서, 담당자, 프로세스, 프로젝트, 용어, "
            "정책/규정, 비즈니스로직, 업무절차"
        ),
    },
    "partnertalk": {
        "nodes": ["Person", "Product", "Store", "Process", "Term", "Event"],
        "relationships": [
            "SELLS", "MANAGES", "APPLIES_TO", "RELATED_TO", "FOLLOWS",
        ],
        "prompt_focus": "협력사(사람/회사), 상품, 점포, 문의절차, 용어",
    },
}


def _emit_yaml(kb_id: str, profile: dict[str, list[str] | str]) -> str:
    data = {
        "version": 1,
        "kb_id": kb_id,
        "prompt_focus": profile["prompt_focus"],
        "nodes": sorted(profile["nodes"]),  # type: ignore[arg-type]
        "relationships": sorted(profile["relationships"]),  # type: ignore[arg-type]
        "options": {
            "disable_bootstrap": False,
            "schema_evolution": "batch",
            "bootstrap_sample_size": 100,
        },
        "_metadata": {
            "migrated_from": "prompts.py::KB_SCHEMA_PROFILES",
            "migrated_at": "2026-04-24",
            "approved_candidates": [],
        },
    }
    return yaml.dump(data, allow_unicode=True, sort_keys=False)


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    wrote = 0
    skipped = 0
    for kb_id, profile in LEGACY_PROFILES.items():
        path = OUT_DIR / f"{kb_id}.yaml"
        if path.exists():
            print(f"skip {kb_id} (already exists)")
            skipped += 1
            continue
        path.write_text(_emit_yaml(kb_id, profile), encoding="utf-8")
        print(f"wrote {path}")
        wrote += 1
    print(f"Done. wrote={wrote} skipped={skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
