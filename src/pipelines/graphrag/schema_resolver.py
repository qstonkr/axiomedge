"""Schema resolution — merge A (KB YAML) + D (source-type default YAML).

B (bootstrap) is merged into A at approve-time, so at runtime the resolver
only needs two layers. Spec: docs/superpowers/specs/2026-04-24-*.md §6.2.
"""

from __future__ import annotations

import logging
from pathlib import Path
from threading import Lock
from typing import Any

import yaml

from .schema_types import IndexSpec, SchemaOptions, SchemaProfile

logger = logging.getLogger(__name__)

_SCHEMA_DIR = Path("deploy/config/graph_schemas")
_DEFAULTS_DIR = _SCHEMA_DIR / "_defaults"

# Cache key: ("kb", <kb_id>) or ("source", <source_type>)
_cache: dict[tuple[str, str], SchemaProfile] = {}
_cache_mtime: dict[tuple[str, str], float] = {}
_cache_lock = Lock()


class SchemaResolver:
    """Stateless resolver (module-level cache). Thread-safe hot-reload."""

    @staticmethod
    def resolve(
        *,
        kb_id: str | None,
        source_type: str | None,
    ) -> SchemaProfile:
        """Resolve with priority A > D > generic fallback.

        Merge semantics: UNION for nodes/relationships; prompt_focus and
        options taken from the topmost layer that provided them.
        """
        layers: list[tuple[str, SchemaProfile]] = []

        if source_type:
            d = SchemaResolver._load_source_default(source_type)
            if d:
                layers.append((f"D:{source_type}", d))

        if kb_id:
            a = SchemaResolver._load_kb_schema(kb_id)
            if a:
                layers.append((f"A:{kb_id}", a))

        if not layers:
            # Try YAML-based generic fallback first (so ops can tune it
            # without a code change), then fall through to hardcoded default.
            yaml_generic = SchemaResolver._load_yaml(
                _DEFAULTS_DIR / "_generic.yaml",
                fallback_path=None,
                cache_key=("source", "_generic"),
            )
            if yaml_generic:
                return SchemaProfile(
                    nodes=yaml_generic.nodes,
                    relationships=yaml_generic.relationships,
                    prompt_focus=yaml_generic.prompt_focus,
                    indexes=yaml_generic.indexes,
                    options=yaml_generic.options,
                    version=yaml_generic.version,
                    source_layers=("D:_generic",),
                )
            return SchemaResolver._generic_fallback()

        nodes: set[str] = set()
        rels: set[str] = set()
        indexes: dict[str, list[IndexSpec]] = {}
        for _, p in layers:
            nodes.update(p.nodes)
            rels.update(p.relationships)
            for label, specs in p.indexes.items():
                indexes.setdefault(label, []).extend(specs)

        prompt_focus = layers[-1][1].prompt_focus or (
            layers[0][1].prompt_focus if layers else ""
        )
        options = layers[-1][1].options
        version = layers[-1][1].version

        return SchemaProfile(
            nodes=tuple(sorted(nodes)),
            relationships=tuple(sorted(rels)),
            prompt_focus=prompt_focus,
            indexes={k: tuple(v) for k, v in indexes.items()},
            options=options,
            version=version,
            source_layers=tuple(name for name, _ in layers),
        )

    @staticmethod
    def _load_source_default(source_type: str) -> SchemaProfile | None:
        primary = _DEFAULTS_DIR / f"{source_type}.yaml"
        fallback = _DEFAULTS_DIR / "_generic.yaml"
        return SchemaResolver._load_yaml(
            primary, fallback, cache_key=("source", source_type),
        )

    @staticmethod
    def _load_kb_schema(kb_id: str) -> SchemaProfile | None:
        path = _SCHEMA_DIR / f"{kb_id}.yaml"
        return SchemaResolver._load_yaml(
            path, fallback_path=None, cache_key=("kb", kb_id),
        )

    @staticmethod
    def _load_yaml(
        path: Path,
        fallback_path: Path | None,
        cache_key: tuple[str, str],
    ) -> SchemaProfile | None:
        target = path if path.exists() else fallback_path
        if not target or not target.exists():
            return None

        mtime = target.stat().st_mtime
        with _cache_lock:
            cached = _cache.get(cache_key)
            if cached is not None and _cache_mtime.get(cache_key) == mtime:
                return cached

        try:
            data = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            logger.error("Schema YAML parse failed: %s — %s", target, exc)
            return None  # fail-closed

        profile = SchemaResolver._parse(data)
        with _cache_lock:
            _cache[cache_key] = profile
            _cache_mtime[cache_key] = mtime
        return profile

    @staticmethod
    def _parse(data: dict[str, Any]) -> SchemaProfile:
        opt_raw = data.get("options") or {}
        options = SchemaOptions(
            disable_bootstrap=bool(opt_raw.get("disable_bootstrap", False)),
            schema_evolution=str(opt_raw.get("schema_evolution", "batch")),
            bootstrap_sample_size=int(opt_raw.get("bootstrap_sample_size", 100)),
        )

        idx_raw = data.get("indexes") or {}
        indexes = {
            label: tuple(
                IndexSpec(
                    property=spec["property"],
                    index_type=spec.get("index_type", "btree"),
                )
                for spec in specs
            )
            for label, specs in idx_raw.items()
        }

        return SchemaProfile(
            nodes=tuple(data.get("nodes") or ()),
            relationships=tuple(data.get("relationships") or ()),
            prompt_focus=str(data.get("prompt_focus") or ""),
            indexes=indexes,
            options=options,
            version=int(data.get("version", 1)),
        )

    @staticmethod
    def _generic_fallback() -> SchemaProfile:
        return SchemaProfile(
            nodes=("Document", "Person", "Team", "Term", "Topic"),
            relationships=("COVERS", "MEMBER_OF", "MENTIONS", "RELATED_TO"),
            prompt_focus="사람, 팀, 주제, 용어, 문서",
            source_layers=("generic",),
        )


def invalidate_cache(cache_key: tuple[str, str] | None = None) -> None:
    """Clear cached YAML parses. Used by tests and admin tool."""
    with _cache_lock:
        if cache_key is None:
            _cache.clear()
            _cache_mtime.clear()
        else:
            _cache.pop(cache_key, None)
            _cache_mtime.pop(cache_key, None)
