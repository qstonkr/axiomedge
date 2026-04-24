"""CLI: graph-schema-* operator commands.

Invocations:
    uv run python -m src.cli.graph_schema_cli scaffold <source_type>
    uv run python -m src.cli.graph_schema_cli dry-run <kb_id>
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

_DEFAULTS_DIR = Path("deploy/config/graph_schemas/_defaults")
_SAFE_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

_TEMPLATE = {
    "version": 1,
    "prompt_focus": "TODO: describe the typical content of this source",
    "nodes": [
        "Person",
        "Document",
        "Topic",
    ],
    "relationships": [
        "AUTHORED",
        "MENTIONS",
        "RELATED_TO",
    ],
    "options": {
        "disable_bootstrap": False,
        "schema_evolution": "batch",
        "bootstrap_sample_size": 100,
    },
}


def scaffold_source_default(source_type: str) -> Path:
    """Create ``_defaults/<source_type>.yaml`` from a template.

    Rejects unsafe names (injection defense) and refuses to overwrite.
    """
    if not _SAFE_NAME.match(source_type):
        raise ValueError(
            f"unsafe source_type name: {source_type!r} — "
            "must match [a-z][a-z0-9_]*",
        )
    _DEFAULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = _DEFAULTS_DIR / f"{source_type}.yaml"
    if path.exists():
        raise FileExistsError(f"{path} already exists; delete first to regen")
    path.write_text(
        yaml.dump(_TEMPLATE, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return path


def dry_run(kb_id: str) -> dict:
    """Print what SchemaResolver would resolve for ``kb_id`` today.

    Useful when an operator wants to preview schema without running a
    real bootstrap.
    """
    from src.pipelines.graphrag import SchemaResolver

    schema = SchemaResolver.resolve(kb_id=kb_id, source_type=None)
    return {
        "kb_id": kb_id,
        "version": schema.version,
        "source_layers": list(schema.source_layers),
        "nodes": list(schema.nodes),
        "relationships": list(schema.relationships),
        "prompt_focus": schema.prompt_focus,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="graph-schema-cli")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_scaffold = sub.add_parser(
        "scaffold", help="Create _defaults/<source_type>.yaml from template",
    )
    p_scaffold.add_argument("source_type")

    p_dry = sub.add_parser(
        "dry-run", help="Preview SchemaResolver output for a kb_id",
    )
    p_dry.add_argument("kb_id")

    args = parser.parse_args(argv)

    if args.cmd == "scaffold":
        try:
            path = scaffold_source_default(args.source_type)
        except (ValueError, FileExistsError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(f"wrote {path}")
        return 0

    if args.cmd == "dry-run":
        import json

        info = dry_run(args.kb_id)
        print(json.dumps(info, indent=2, ensure_ascii=False))
        return 0

    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["dry_run", "main", "scaffold_source_default"]
