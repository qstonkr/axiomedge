"""Dataclasses for GraphRAG schema resolution.

Spec: docs/superpowers/specs/2026-04-24-graph-schema-evolution-design.md §4.4
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class IndexSpec:
    """Single Neo4j index on a node property.

    Used in SchemaProfile.indexes to express per-label custom indexes
    (the default constraint on ``(label, id) UNIQUE`` is always created
    regardless of this field).
    """

    property: str
    index_type: Literal["btree", "fulltext", "range"] = "btree"


@dataclass(frozen=True)
class SchemaOptions:
    """Per-KB schema behavior flags.

    - ``disable_bootstrap``: skip this KB during Phase-3 bootstrap runs.
    - ``schema_evolution``: ``batch`` (default, cron-based) or ``realtime``
      (Phase-6 opt-in; per-doc inline discovery).
    - ``bootstrap_sample_size``: override default sample size (Phase 3).
    """

    disable_bootstrap: bool = False
    schema_evolution: Literal["batch", "realtime"] = "batch"
    bootstrap_sample_size: int = 100


@dataclass(frozen=True)
class SchemaProfile:
    """Resolved schema for an (kb_id, source_type) pair.

    Produced by ``SchemaResolver.resolve()`` after merging D (source default)
    and A (KB override) YAML layers. Immutable; changes require a new
    instance.
    """

    nodes: tuple[str, ...]
    relationships: tuple[str, ...]
    prompt_focus: str
    indexes: dict[str, tuple[IndexSpec, ...]] = field(default_factory=dict)
    options: SchemaOptions = field(default_factory=SchemaOptions)
    version: int = 1
    # ("D:confluence", "A:g-espa") — provenance for debugging / observability
    source_layers: tuple[str, ...] = field(default_factory=tuple)
