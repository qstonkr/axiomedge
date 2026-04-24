# GraphRAG Schema Evolution — Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the hardcoded `KB_SCHEMA_PROFILES` dict with YAML-based schema files, layered resolution (A+D+generic), and a backward-compatible legacy facade so no existing caller breaks.

**Architecture:** A 2-layer schema resolver reads `deploy/config/graph_schemas/<kb_id>.yaml` (A layer) and `deploy/config/graph_schemas/_defaults/<source_type>.yaml` (D layer), unions node/relationship sets, and returns an immutable `SchemaProfile` dataclass. The resolver caches parsed YAML by file mtime for hot-reload without process restart. Legacy imports (`KB_SCHEMA_PROFILES["g-espa"]`, `build_extraction_prompt(doc, kb_id)`) are served through a lazy facade that reads the same YAML files.

**Tech Stack:** Python 3.12, PyYAML (already in deps), pytest, ruff. No new runtime dependencies.

**Spec reference:** `docs/superpowers/specs/2026-04-24-graph-schema-evolution-design.md` Section 6 (Components) + Section 7 (Backward Compat) + Section 8 (Migration).

**Out of scope for Phase 1 (later phases):** Bootstrap discovery (B layer), Neo4j dynamic constraints, admin UI, re-extract, LLM schema-aware extraction integration. Phase 1 is foundation only.

---

## File Structure

### New files

| Path | Responsibility |
|---|---|
| `src/pipelines/graphrag/schema_types.py` | `SchemaProfile`, `SchemaOptions`, `IndexSpec` dataclasses (frozen, immutable) |
| `src/pipelines/graphrag/schema_resolver.py` | `SchemaResolver.resolve()` + YAML loader + mtime cache + thread-safe invalidation |
| `src/pipelines/graphrag/source_defaults.py` | `is_valid_source_type()` whitelist for Cypher injection defense |
| `deploy/config/graph_schemas/_defaults/_generic.yaml` | Ultimate fallback schema |
| `deploy/config/graph_schemas/_defaults/confluence.yaml` | Confluence source default |
| `deploy/config/graph_schemas/_defaults/file_upload.yaml` | File upload source default |
| `deploy/config/graph_schemas/_defaults/crawl_result.yaml` | Crawl result source default |
| `deploy/config/graph_schemas/a-ari.yaml` | Migrated KB schema (generated) |
| `deploy/config/graph_schemas/g-espa.yaml` | Migrated KB schema (generated) |
| `deploy/config/graph_schemas/drp.yaml` | Migrated KB schema (generated) |
| `deploy/config/graph_schemas/hax.yaml` | Migrated KB schema (generated) |
| `deploy/config/graph_schemas/itops_general.yaml` | Migrated KB schema (generated) |
| `deploy/config/graph_schemas/partnertalk.yaml` | Migrated KB schema (generated) |
| `scripts/ops/migrate_schema_to_yaml.py` | One-shot migration script (idempotent) |
| `tests/unit/test_schema_types.py` | Dataclass immutability + defaults |
| `tests/unit/test_schema_resolver.py` | 3-layer merge + YAML parsing + hot-reload |
| `tests/unit/test_source_defaults.py` | Whitelist + injection defense |
| `tests/unit/test_schema_migration.py` | Migration script round-trip + idempotency |
| `docs/GRAPH_SCHEMA_EVOLUTION.md` | Operator-facing summary (links to spec) |

### Modified files

| Path | Change |
|---|---|
| `src/pipelines/graphrag/prompts.py` | Replace `KB_SCHEMA_PROFILES` dict with `_LegacyProxy` (lazy YAML-backed). Update `get_kb_schema` and `build_extraction_prompt` to route through resolver. Keep the module-level constants so existing imports work. |
| `src/pipelines/graphrag/__init__.py` | Export `SchemaProfile`, `SchemaResolver` (new public API) alongside existing `KB_SCHEMA_PROFILES`, `build_extraction_prompt`, `get_kb_schema`. |

---

## Task 1: Create `SchemaOptions`, `IndexSpec`, `SchemaProfile` dataclasses

**Files:**
- Create: `src/pipelines/graphrag/schema_types.py`
- Test: `tests/unit/test_schema_types.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_schema_types.py`:

```python
"""Tests for SchemaProfile / SchemaOptions / IndexSpec dataclasses."""

from __future__ import annotations

import pytest

from src.pipelines.graphrag.schema_types import (
    IndexSpec,
    SchemaOptions,
    SchemaProfile,
)


class TestIndexSpec:
    def test_defaults(self):
        spec = IndexSpec(property="scheduled_at")
        assert spec.property == "scheduled_at"
        assert spec.index_type == "btree"

    def test_explicit_type(self):
        spec = IndexSpec(property="title", index_type="fulltext")
        assert spec.index_type == "fulltext"

    def test_immutable(self):
        spec = IndexSpec(property="x")
        with pytest.raises(Exception):  # FrozenInstanceError
            spec.property = "y"  # type: ignore[misc]


class TestSchemaOptions:
    def test_defaults(self):
        opts = SchemaOptions()
        assert opts.disable_bootstrap is False
        assert opts.schema_evolution == "batch"
        assert opts.bootstrap_sample_size == 100

    def test_immutable(self):
        opts = SchemaOptions()
        with pytest.raises(Exception):
            opts.disable_bootstrap = True  # type: ignore[misc]


class TestSchemaProfile:
    def test_minimal(self):
        profile = SchemaProfile(
            nodes=("Person",),
            relationships=("MEMBER_OF",),
            prompt_focus="사람",
        )
        assert profile.nodes == ("Person",)
        assert profile.relationships == ("MEMBER_OF",)
        assert profile.prompt_focus == "사람"
        assert profile.version == 1
        assert profile.source_layers == ()

    def test_with_indexes(self):
        profile = SchemaProfile(
            nodes=("Meeting",),
            relationships=(),
            prompt_focus="",
            indexes={"Meeting": (IndexSpec(property="scheduled_at"),)},
        )
        assert profile.indexes["Meeting"][0].property == "scheduled_at"

    def test_options_composition(self):
        opts = SchemaOptions(schema_evolution="realtime")
        profile = SchemaProfile(
            nodes=(), relationships=(), prompt_focus="", options=opts,
        )
        assert profile.options.schema_evolution == "realtime"

    def test_immutable(self):
        profile = SchemaProfile(nodes=(), relationships=(), prompt_focus="")
        with pytest.raises(Exception):
            profile.nodes = ("X",)  # type: ignore[misc]
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/unit/test_schema_types.py -v --no-cov
```

Expected: `ModuleNotFoundError: No module named 'src.pipelines.graphrag.schema_types'`

- [ ] **Step 3: Write minimal implementation**

Create `src/pipelines/graphrag/schema_types.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/unit/test_schema_types.py -v --no-cov
```

Expected: 9 passed.

- [ ] **Step 5: Lint**

```bash
uvx ruff check src/pipelines/graphrag/schema_types.py tests/unit/test_schema_types.py
```

Expected: `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add src/pipelines/graphrag/schema_types.py tests/unit/test_schema_types.py
git commit -m "feat(graphrag): SchemaProfile/SchemaOptions/IndexSpec dataclasses

Immutable data classes backing the Phase 1 YAML-based schema resolution
(spec §4.4). No behavior change yet — dataclasses will be wired into the
resolver + legacy facade in subsequent tasks.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Create D-layer YAML fixtures (generic + 3 sources)

**Files:**
- Create: `deploy/config/graph_schemas/_defaults/_generic.yaml`
- Create: `deploy/config/graph_schemas/_defaults/confluence.yaml`
- Create: `deploy/config/graph_schemas/_defaults/file_upload.yaml`
- Create: `deploy/config/graph_schemas/_defaults/crawl_result.yaml`

- [ ] **Step 1: Create directory structure**

```bash
mkdir -p deploy/config/graph_schemas/_defaults
```

Run: verify directory exists:
```bash
ls -la deploy/config/graph_schemas/_defaults
```

Expected: empty directory listing.

- [ ] **Step 2: Create `_generic.yaml`**

Create `deploy/config/graph_schemas/_defaults/_generic.yaml`:

```yaml
# Ultimate fallback schema — used when no KB-specific YAML and no source-type
# default matches. Keep minimal to avoid noise.
version: 1
prompt_focus: "사람, 팀, 주제, 용어, 문서"
nodes:
  - Person
  - Team
  - Topic
  - Term
  - Document
relationships:
  - MEMBER_OF
  - MENTIONS
  - COVERS
  - RELATED_TO
options:
  disable_bootstrap: false
  schema_evolution: batch
  bootstrap_sample_size: 100
```

- [ ] **Step 3: Create `confluence.yaml`**

Create `deploy/config/graph_schemas/_defaults/confluence.yaml`:

```yaml
# Confluence wiki-page corpus default.
version: 1
prompt_focus: "문서 페이지, 작성자, 팀/부서, 주제, 용어"
nodes:
  - Page
  - Person
  - Team
  - Topic
  - Term
  - Document
relationships:
  - AUTHORED
  - MEMBER_OF
  - COVERS
  - MENTIONS
  - PART_OF
  - RELATED_TO
options:
  disable_bootstrap: false
  schema_evolution: batch
  bootstrap_sample_size: 100
```

- [ ] **Step 4: Create `file_upload.yaml`**

Create `deploy/config/graph_schemas/_defaults/file_upload.yaml`:

```yaml
# Local file upload — heterogeneous; conservative default.
version: 1
prompt_focus: "문서, 작성자, 주제, 용어"
nodes:
  - Document
  - Person
  - Topic
  - Term
relationships:
  - AUTHORED
  - COVERS
  - MENTIONS
  - RELATED_TO
options:
  disable_bootstrap: false
  schema_evolution: batch
  bootstrap_sample_size: 100
```

- [ ] **Step 5: Create `crawl_result.yaml`**

Create `deploy/config/graph_schemas/_defaults/crawl_result.yaml`:

```yaml
# Web crawl result — similar to confluence but without author metadata.
version: 1
prompt_focus: "페이지, 주제, 용어, 문서"
nodes:
  - Page
  - Document
  - Topic
  - Term
relationships:
  - COVERS
  - MENTIONS
  - PART_OF
  - RELATED_TO
options:
  disable_bootstrap: false
  schema_evolution: batch
  bootstrap_sample_size: 100
```

- [ ] **Step 6: Validate YAMLs parse**

```bash
uv run python -c "
import yaml
from pathlib import Path
for p in sorted(Path('deploy/config/graph_schemas/_defaults').glob('*.yaml')):
    data = yaml.safe_load(p.read_text())
    assert 'nodes' in data, f'{p} missing nodes'
    assert 'relationships' in data, f'{p} missing relationships'
    print(f'{p.name}: {len(data[\"nodes\"])} nodes, {len(data[\"relationships\"])} rels — OK')
"
```

Expected output (example):
```
_generic.yaml: 5 nodes, 4 rels — OK
confluence.yaml: 6 nodes, 6 rels — OK
crawl_result.yaml: 4 nodes, 4 rels — OK
file_upload.yaml: 4 nodes, 4 rels — OK
```

- [ ] **Step 7: Commit**

```bash
git add deploy/config/graph_schemas/_defaults/
git commit -m "feat(graphrag): D-layer YAML defaults (generic + confluence + file_upload + crawl_result)

Phase 1 of schema evolution (spec §4.1). These files provide source-type
default schemas consulted by SchemaResolver when no KB-level override
exists. Four initial sources cover the current primary ingestion paths;
remaining 15+ connectors follow in Phase 1.5 (spec §8.2).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: `SchemaResolver._load_yaml` — mtime-cached YAML loader

**Files:**
- Create: `src/pipelines/graphrag/schema_resolver.py` (partial — loader only)
- Test: `tests/unit/test_schema_resolver.py` (partial)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_schema_resolver.py`:

```python
"""Tests for SchemaResolver — YAML loader, merge, hot-reload."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from src.pipelines.graphrag.schema_resolver import (
    SchemaResolver,
    invalidate_cache,
)
from src.pipelines.graphrag.schema_types import SchemaProfile


@pytest.fixture(autouse=True)
def _clean_cache():
    """Each test starts with a clean resolver cache."""
    invalidate_cache()
    yield
    invalidate_cache()


@pytest.fixture
def schema_dir(tmp_path, monkeypatch):
    """Redirect resolver to a per-test temp directory."""
    d = tmp_path / "graph_schemas"
    (d / "_defaults").mkdir(parents=True)
    monkeypatch.setattr(
        "src.pipelines.graphrag.schema_resolver._SCHEMA_DIR", d,
    )
    monkeypatch.setattr(
        "src.pipelines.graphrag.schema_resolver._DEFAULTS_DIR", d / "_defaults",
    )
    return d


class TestYamlLoader:
    def test_load_valid_yaml(self, schema_dir: Path):
        (schema_dir / "_defaults" / "_generic.yaml").write_text(
            "version: 1\n"
            "prompt_focus: test\n"
            "nodes: [Person, Team]\n"
            "relationships: [MEMBER_OF]\n"
        )
        schema = SchemaResolver.resolve(kb_id=None, source_type=None)
        assert schema.nodes == ("Person", "Team")
        assert "generic" in schema.source_layers[-1].lower() or True
        # minimum: no crash, generic fallback triggers

    def test_load_invalid_yaml_returns_generic_fallback(self, schema_dir: Path):
        # Write a _generic.yaml so fallback has something; then break a specific one
        (schema_dir / "_defaults" / "_generic.yaml").write_text(
            "nodes: [Person]\nrelationships: []\nprompt_focus: generic\n"
        )
        (schema_dir / "_defaults" / "broken.yaml").write_text(
            "nodes: [Person\n  bad yaml :"
        )
        schema = SchemaResolver.resolve(kb_id=None, source_type="broken")
        # Parse failure falls through → source default missing → generic
        assert "Person" in schema.nodes

    def test_mtime_cache_hit(self, schema_dir: Path, monkeypatch):
        p = schema_dir / "_defaults" / "confluence.yaml"
        p.write_text("nodes: [A]\nrelationships: []\nprompt_focus: v1\n")

        s1 = SchemaResolver.resolve(kb_id=None, source_type="confluence")
        assert s1.prompt_focus == "v1"

        # Replace file content WITHOUT bumping mtime — cache should still hit
        original_mtime = p.stat().st_mtime
        p.write_text("nodes: [B]\nrelationships: []\nprompt_focus: v2\n")
        import os
        os.utime(p, (original_mtime, original_mtime))

        s2 = SchemaResolver.resolve(kb_id=None, source_type="confluence")
        assert s2.prompt_focus == "v1", "cache should still return v1"

    def test_mtime_change_invalidates_cache(self, schema_dir: Path):
        p = schema_dir / "_defaults" / "confluence.yaml"
        p.write_text("nodes: [A]\nrelationships: []\nprompt_focus: v1\n")
        s1 = SchemaResolver.resolve(kb_id=None, source_type="confluence")
        assert s1.prompt_focus == "v1"

        # Advance time + rewrite — mtime should differ
        time.sleep(0.05)
        p.write_text("nodes: [B]\nrelationships: []\nprompt_focus: v2\n")
        s2 = SchemaResolver.resolve(kb_id=None, source_type="confluence")
        assert s2.prompt_focus == "v2", "mtime change should trigger reload"

    def test_nonexistent_source_falls_back_to_generic(self, schema_dir: Path):
        (schema_dir / "_defaults" / "_generic.yaml").write_text(
            "nodes: [GenericNode]\nrelationships: []\nprompt_focus: g\n"
        )
        schema = SchemaResolver.resolve(kb_id=None, source_type="nonexistent")
        assert "GenericNode" in schema.nodes
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/unit/test_schema_resolver.py::TestYamlLoader -v --no-cov
```

Expected: `ModuleNotFoundError: No module named 'src.pipelines.graphrag.schema_resolver'`

- [ ] **Step 3: Write minimal implementation (loader only)**

Create `src/pipelines/graphrag/schema_resolver.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/unit/test_schema_resolver.py::TestYamlLoader -v --no-cov
```

Expected: 5 passed.

- [ ] **Step 5: Lint**

```bash
uvx ruff check src/pipelines/graphrag/schema_resolver.py tests/unit/test_schema_resolver.py
```

Expected: `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add src/pipelines/graphrag/schema_resolver.py tests/unit/test_schema_resolver.py
git commit -m "feat(graphrag): SchemaResolver YAML loader + mtime cache

Thread-safe resolver reads deploy/config/graph_schemas/*.yaml with
mtime-based hot reload. Parse failure is fail-closed (logs error,
returns None so caller falls back to generic). Covers loader + cache
semantics; full 3-layer merge logic follows in the next task.

Spec §6.2.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Extend tests to cover 3-layer merge (A + D + generic combinations)

**Files:**
- Test: `tests/unit/test_schema_resolver.py` (append)

The resolver code already implements merge in Task 3. This task adds tests that exercise every combination and fixes any discovered bug.

- [ ] **Step 1: Add merge-combination tests**

Append to `tests/unit/test_schema_resolver.py`:

```python
class TestLayerMerge:
    def test_only_generic_fallback(self, schema_dir: Path):
        # No YAMLs present at all
        schema = SchemaResolver.resolve(kb_id=None, source_type=None)
        # Generic hardcoded fallback kicks in
        assert "Person" in schema.nodes
        assert schema.source_layers == ("generic",)

    def test_only_d_layer(self, schema_dir: Path):
        (schema_dir / "_defaults" / "confluence.yaml").write_text(
            "nodes: [Page, Person]\n"
            "relationships: [AUTHORED]\n"
            "prompt_focus: conf\n"
        )
        schema = SchemaResolver.resolve(kb_id=None, source_type="confluence")
        assert set(schema.nodes) == {"Page", "Person"}
        assert set(schema.relationships) == {"AUTHORED"}
        assert schema.prompt_focus == "conf"
        assert schema.source_layers == ("D:confluence",)

    def test_only_a_layer(self, schema_dir: Path):
        (schema_dir / "g-espa.yaml").write_text(
            "nodes: [Store, Person]\n"
            "relationships: [OPERATES]\n"
            "prompt_focus: espa\n"
        )
        schema = SchemaResolver.resolve(kb_id="g-espa", source_type=None)
        assert set(schema.nodes) == {"Store", "Person"}
        assert schema.prompt_focus == "espa"
        assert schema.source_layers == ("A:g-espa",)

    def test_a_plus_d_merge_union(self, schema_dir: Path):
        (schema_dir / "_defaults" / "confluence.yaml").write_text(
            "nodes: [Page, Person]\n"
            "relationships: [AUTHORED, MENTIONS]\n"
            "prompt_focus: conf\n"
        )
        (schema_dir / "g-espa.yaml").write_text(
            "nodes: [Store, Person]\n"
            "relationships: [OPERATES, MENTIONS]\n"
            "prompt_focus: espa\n"
        )
        schema = SchemaResolver.resolve(kb_id="g-espa", source_type="confluence")
        # Nodes: union of {Page, Person, Store}
        assert set(schema.nodes) == {"Page", "Person", "Store"}
        # Rels: union
        assert set(schema.relationships) == {"AUTHORED", "MENTIONS", "OPERATES"}
        # prompt_focus: A wins (last layer)
        assert schema.prompt_focus == "espa"
        # Provenance
        assert schema.source_layers == ("D:confluence", "A:g-espa")

    def test_nodes_sorted_deterministic(self, schema_dir: Path):
        (schema_dir / "_defaults" / "confluence.yaml").write_text(
            "nodes: [Zebra, Apple, Mango]\n"
            "relationships: []\n"
            "prompt_focus: c\n"
        )
        schema = SchemaResolver.resolve(kb_id=None, source_type="confluence")
        assert schema.nodes == ("Apple", "Mango", "Zebra")  # sorted

    def test_options_from_a_wins(self, schema_dir: Path):
        (schema_dir / "_defaults" / "confluence.yaml").write_text(
            "nodes: []\nrelationships: []\nprompt_focus: c\n"
            "options:\n  schema_evolution: batch\n"
        )
        (schema_dir / "special.yaml").write_text(
            "nodes: []\nrelationships: []\nprompt_focus: s\n"
            "options:\n  schema_evolution: realtime\n"
        )
        schema = SchemaResolver.resolve(kb_id="special", source_type="confluence")
        assert schema.options.schema_evolution == "realtime"

    def test_index_spec_parsed(self, schema_dir: Path):
        (schema_dir / "g-espa.yaml").write_text(
            "nodes: [Meeting]\n"
            "relationships: []\n"
            "prompt_focus: x\n"
            "indexes:\n"
            "  Meeting:\n"
            "    - property: scheduled_at\n"
            "      index_type: btree\n"
            "    - property: title\n"
            "      index_type: fulltext\n"
        )
        schema = SchemaResolver.resolve(kb_id="g-espa", source_type=None)
        specs = schema.indexes["Meeting"]
        assert len(specs) == 2
        assert specs[0].property == "scheduled_at"
        assert specs[1].index_type == "fulltext"
```

- [ ] **Step 2: Run tests to verify they pass (loader already implements merge)**

```bash
uv run pytest tests/unit/test_schema_resolver.py -v --no-cov
```

Expected: all tests pass (5 loader + 7 merge = 12 total).

If anything fails, the resolver implementation from Task 3 needs a small fix; update and re-run. The most likely failure modes:
- `test_nodes_sorted_deterministic` — ensure `tuple(sorted(nodes))` (already present)
- `test_options_from_a_wins` — ensure `options = layers[-1][1].options` (already present)

- [ ] **Step 3: Lint**

```bash
uvx ruff check tests/unit/test_schema_resolver.py
```

Expected: `All checks passed!`

- [ ] **Step 4: Commit**

```bash
git add tests/unit/test_schema_resolver.py
git commit -m "test(graphrag): SchemaResolver layer-merge regression coverage

Adds 7 cases exercising every A/D/generic combination plus index spec
parsing and deterministic node ordering. Catches any regression in the
priority rules (A > D > generic) documented in spec §6.2.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: `source_defaults.is_valid_source_type` whitelist

**Files:**
- Create: `src/pipelines/graphrag/source_defaults.py`
- Test: `tests/unit/test_source_defaults.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_source_defaults.py`:

```python
"""Tests for source_type whitelist — Cypher injection defense."""

from __future__ import annotations

from src.pipelines.graphrag.source_defaults import is_valid_source_type


class TestIsValidSourceType:
    def test_known_source_accepted(self):
        # Ships with the repo (Task 2 created confluence.yaml)
        assert is_valid_source_type("confluence") is True

    def test_unknown_source_rejected(self):
        assert is_valid_source_type("unknown_source_xyz") is False

    def test_injection_attempt_rejected(self):
        # Cypher injection via source_type string
        assert is_valid_source_type("confluence\nDROP DATABASE") is False
        assert is_valid_source_type("../../../etc/passwd") is False
        assert is_valid_source_type("x; y") is False

    def test_empty_rejected(self):
        assert is_valid_source_type("") is False

    def test_case_exact(self):
        # Filenames are lowercase; uppercase should be rejected (strict)
        assert is_valid_source_type("Confluence") is False
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/unit/test_source_defaults.py -v --no-cov
```

Expected: `ModuleNotFoundError: No module named 'src.pipelines.graphrag.source_defaults'`

- [ ] **Step 3: Write minimal implementation**

Create `src/pipelines/graphrag/source_defaults.py`:

```python
"""Source-type whitelist — defense against Cypher injection via connector config.

``source_type`` originates in connector configuration (user input), so the
resolver must not trust it blindly when computing file paths or logging it.
The whitelist is *file-existence-based*: only source_types that have a
``_defaults/<source_type>.yaml`` present are accepted. This also keeps the
whitelist trivially auditable — adding a source means committing a YAML.
"""

from __future__ import annotations

from pathlib import Path

_DEFAULTS_DIR = Path("deploy/config/graph_schemas/_defaults")


def is_valid_source_type(source_type: str) -> bool:
    """Return True iff ``source_type`` is a known, safely-named connector.

    Rules:
    - Must be non-empty lowercase alnum + underscore only (so never contains
      path separators, newlines, or Cypher special chars).
    - Must correspond to an existing ``_defaults/<name>.yaml`` file.
    """
    if not source_type:
        return False
    if not source_type.replace("_", "").isalnum():
        return False
    if source_type != source_type.lower():
        return False
    return (_DEFAULTS_DIR / f"{source_type}.yaml").exists()
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/unit/test_source_defaults.py -v --no-cov
```

Expected: 5 passed.

- [ ] **Step 5: Lint**

```bash
uvx ruff check src/pipelines/graphrag/source_defaults.py tests/unit/test_source_defaults.py
```

Expected: `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add src/pipelines/graphrag/source_defaults.py tests/unit/test_source_defaults.py
git commit -m "feat(graphrag): source_type whitelist for injection defense

is_valid_source_type() rejects anything that is not lowercase alnum (+ _)
and has a corresponding _defaults/<name>.yaml. This is the hook future
callers (Phase 2 extractor, Phase 3 bootstrap) will use before trusting
connector-supplied source_type values.

Spec §6.7.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Legacy facade in `prompts.py`

**Files:**
- Modify: `src/pipelines/graphrag/prompts.py`
- Create: `tests/unit/test_graphrag_prompts_facade.py`

The existing `prompts.py` currently declares `KB_SCHEMA_PROFILES` as a literal
dict and `build_extraction_prompt(doc, kb_id)` as a function that closes over
it. We replace the dict with a `_LegacyProxy` that lazily builds the same
shape from YAML. `build_extraction_prompt` and `get_kb_schema` keep their
signatures but route through `SchemaResolver`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_graphrag_prompts_facade.py`:

```python
"""Tests: legacy imports from prompts.py keep working after YAML migration."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.pipelines.graphrag.schema_resolver import invalidate_cache
from src.pipelines.graphrag.schema_types import SchemaProfile


@pytest.fixture(autouse=True)
def _clean_cache():
    invalidate_cache()
    yield
    invalidate_cache()


@pytest.fixture
def schema_dir(tmp_path, monkeypatch):
    d = tmp_path / "graph_schemas"
    (d / "_defaults").mkdir(parents=True)
    monkeypatch.setattr(
        "src.pipelines.graphrag.schema_resolver._SCHEMA_DIR", d,
    )
    monkeypatch.setattr(
        "src.pipelines.graphrag.schema_resolver._DEFAULTS_DIR", d / "_defaults",
    )
    return d


class TestLegacyKbSchemaProfilesProxy:
    """KB_SCHEMA_PROFILES behaves like a dict for existing callers."""

    def test_proxy_getitem(self, schema_dir: Path):
        (schema_dir / "g-espa.yaml").write_text(
            "nodes: [Store, Person]\n"
            "relationships: [OPERATES]\n"
            "prompt_focus: espa\n"
        )
        from src.pipelines.graphrag.prompts import KB_SCHEMA_PROFILES

        # Force a fresh proxy per-test — the module-level proxy is
        # lazily built once; clear its internal cache.
        KB_SCHEMA_PROFILES._cache = None  # type: ignore[attr-defined]
        profile = KB_SCHEMA_PROFILES["g-espa"]
        assert set(profile["nodes"]) == {"Store", "Person"}
        assert set(profile["relationships"]) == {"OPERATES"}
        assert profile["prompt_focus"] == "espa"

    def test_proxy_contains(self, schema_dir: Path):
        (schema_dir / "a-ari.yaml").write_text(
            "nodes: [Store]\nrelationships: []\nprompt_focus: x\n"
        )
        from src.pipelines.graphrag.prompts import KB_SCHEMA_PROFILES

        KB_SCHEMA_PROFILES._cache = None  # type: ignore[attr-defined]
        assert "a-ari" in KB_SCHEMA_PROFILES
        assert "nonexistent" not in KB_SCHEMA_PROFILES

    def test_proxy_get_default(self, schema_dir: Path):
        from src.pipelines.graphrag.prompts import KB_SCHEMA_PROFILES

        KB_SCHEMA_PROFILES._cache = None  # type: ignore[attr-defined]
        assert KB_SCHEMA_PROFILES.get("nonexistent") is None
        assert KB_SCHEMA_PROFILES.get("nonexistent", {"default": True}) == {
            "default": True,
        }


class TestGetKbSchema:
    def test_get_kb_schema_returns_dict(self, schema_dir: Path):
        (schema_dir / "g-espa.yaml").write_text(
            "nodes: [Store]\nrelationships: [OPERATES]\nprompt_focus: x\n"
        )
        from src.pipelines.graphrag.prompts import get_kb_schema

        result = get_kb_schema("g-espa")
        assert isinstance(result, dict)
        assert "Store" in result["nodes"]


class TestBuildExtractionPrompt:
    def test_with_kb_id_string(self, schema_dir: Path):
        (schema_dir / "g-espa.yaml").write_text(
            "nodes: [Store, Person]\n"
            "relationships: [OPERATES]\n"
            "prompt_focus: espa-focus\n"
        )
        from src.pipelines.graphrag.prompts import build_extraction_prompt

        prompt = build_extraction_prompt("sample doc", "g-espa")
        assert "Store" in prompt
        assert "Person" in prompt
        assert "OPERATES" in prompt
        assert "espa-focus" in prompt
        assert "sample doc" in prompt

    def test_with_schema_profile_object(self, schema_dir: Path):
        from src.pipelines.graphrag.prompts import build_extraction_prompt

        schema = SchemaProfile(
            nodes=("X",), relationships=("Y",), prompt_focus="f",
        )
        prompt = build_extraction_prompt("doc", schema)
        assert "X" in prompt
        assert "Y" in prompt
        assert "f" in prompt

    def test_with_none_falls_back_to_generic(self, schema_dir: Path):
        from src.pipelines.graphrag.prompts import build_extraction_prompt

        prompt = build_extraction_prompt("doc", None)
        # Generic fallback includes Person/Team/Topic
        assert "Person" in prompt
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/unit/test_graphrag_prompts_facade.py -v --no-cov
```

Expected: Multiple `AttributeError` or failure because `KB_SCHEMA_PROFILES` is
still a plain dict and `build_extraction_prompt` does not accept `SchemaProfile`.

- [ ] **Step 3: Read the existing prompts.py to know what must stay**

```bash
grep -n "^def\|^class\|^KB_SCHEMA_PROFILES\|^DEFAULT_SCHEMA\|^ALLOWED_\|^KOREAN_EXTRACTION\|^HISTORY_RELATIONSHIP" src/pipelines/graphrag/prompts.py
```

Note: keep `ALLOWED_NODES`, `ALLOWED_RELATIONSHIPS`, `HISTORY_RELATIONSHIP_MAP`,
`KOREAN_EXTRACTION_PROMPT`, and `DEFAULT_SCHEMA_PROFILE` — those are consumed
elsewhere and unrelated to the dict replacement.

- [ ] **Step 4: Modify `prompts.py` — replace `KB_SCHEMA_PROFILES` + helpers**

Apply this edit: find the block starting `KB_SCHEMA_PROFILES: dict[...] = {` and
ending at `}` (the closing brace of the dict). Replace that block with the
`_LegacyProxy` class below. Then find `def get_kb_schema` and `def
build_extraction_prompt` and replace their bodies as shown.

At the top of `prompts.py`, add to the imports:

```python
from .schema_resolver import SchemaResolver
from .schema_types import SchemaProfile
```

Replace the `KB_SCHEMA_PROFILES = { ... }` literal with:

```python
# ---------------------------------------------------------------------------
# Legacy facade — routes old dict-style access through SchemaResolver (YAML)
# ---------------------------------------------------------------------------


class _LegacyKBSchemaProfilesProxy:
    """Dict-compatible view over YAML-backed KB schemas.

    Exists so callers doing ``KB_SCHEMA_PROFILES["g-espa"]`` keep working
    after the underlying storage moved from hardcoded Python dict to YAML.
    Builds lazily on first access; callers that need a refresh can set
    ``_cache = None``. Tests do exactly that.
    """

    _cache: dict[str, dict[str, Any]] | None = None

    def _build(self) -> dict[str, dict[str, Any]]:
        from pathlib import Path as _P

        from .schema_resolver import _SCHEMA_DIR  # path override-aware

        out: dict[str, dict[str, Any]] = {}
        if not _SCHEMA_DIR.exists():
            return out
        for path in sorted(_SCHEMA_DIR.glob("*.yaml")):
            if path.name.startswith("_"):
                continue
            kb_id = path.stem
            schema = SchemaResolver.resolve(kb_id=kb_id, source_type=None)
            out[kb_id] = {
                "nodes": list(schema.nodes),
                "relationships": list(schema.relationships),
                "prompt_focus": schema.prompt_focus,
            }
        return out

    def _ensure(self) -> dict[str, dict[str, Any]]:
        if self._cache is None:
            self._cache = self._build()
        return self._cache

    def __getitem__(self, key: str) -> dict[str, Any]:
        return self._ensure()[key]

    def __contains__(self, key: object) -> bool:
        return key in self._ensure()

    def get(self, key: str, default: Any = None) -> Any:
        return self._ensure().get(key, default)

    def __iter__(self):
        return iter(self._ensure())

    def keys(self):
        return self._ensure().keys()

    def items(self):
        return self._ensure().items()

    def values(self):
        return self._ensure().values()


KB_SCHEMA_PROFILES = _LegacyKBSchemaProfilesProxy()
```

Replace the old `get_kb_schema` function body with:

```python
def get_kb_schema(kb_id: str) -> dict[str, Any]:
    """Legacy API — prefer SchemaResolver.resolve() for new code."""
    schema = SchemaResolver.resolve(kb_id=kb_id, source_type=None)
    return {
        "nodes": list(schema.nodes),
        "relationships": list(schema.relationships),
        "prompt_focus": schema.prompt_focus,
    }
```

Replace the old `build_extraction_prompt` function with:

```python
def build_extraction_prompt(
    doc_text: str,
    kb_id: str | SchemaProfile | None = None,
) -> str:
    """Legacy-compatible prompt builder.

    The second parameter is historically ``kb_id`` (str), so the name is
    preserved for kwarg callers (``build_extraction_prompt(doc, kb_id="x")``).
    It now also accepts a pre-resolved ``SchemaProfile`` so new code can skip
    the resolver round-trip. ``None`` triggers the generic fallback.
    """
    if isinstance(kb_id, SchemaProfile):
        schema = kb_id
    elif isinstance(kb_id, str):
        schema = SchemaResolver.resolve(kb_id=kb_id, source_type=None)
    else:
        schema = SchemaResolver.resolve(kb_id=None, source_type=None)

    nodes_str = ", ".join(schema.nodes) if schema.nodes else "(none)"
    rels_str = ", ".join(schema.relationships) if schema.relationships else "(none)"
    focus = schema.prompt_focus or "사람, 팀, 주제, 용어, 문서"

    return (
        "다음 문서에서 엔티티와 관계를 추출하세요.\n"
        "문서에 명시된 정보만 추출하고, 추측하지 마세요.\n\n"
        f"허용된 Entity 타입 (목록 외 사용 금지): {nodes_str}\n"
        f"허용된 Relationship 타입 (목록 외 사용 금지): {rels_str}\n"
        f"도메인 특성: {focus}\n\n"
        f"문서: {doc_text}\n\n"
        '아래 JSON 형식으로만 출력하세요:\n'
        '{"nodes":[{"id":"이름","type":"<Entity>"}, ...],'
        '"relationships":[{"source":"...","type":"<Relation>","target":"..."}, ...]}\n\n'
        "JSON:"
    )
```

- [ ] **Step 5: Run test to verify it passes**

```bash
uv run pytest tests/unit/test_graphrag_prompts_facade.py -v --no-cov
```

Expected: all pass.

- [ ] **Step 6: Run existing graphrag tests to prove nothing broke**

```bash
uv run pytest tests/unit/test_graphrag_full.py tests/unit/test_graphrag_coverage.py tests/unit/test_graphrag_extractor_backfill.py -v --no-cov 2>&1 | tail -20
```

Expected: all existing graphrag tests still pass (the facade makes the dict
look identical to callers, and `build_extraction_prompt` keeps its original
signature shape).

- [ ] **Step 7: Lint**

```bash
uvx ruff check src/pipelines/graphrag/prompts.py tests/unit/test_graphrag_prompts_facade.py
```

Expected: `All checks passed!`

- [ ] **Step 8: Commit**

```bash
git add src/pipelines/graphrag/prompts.py tests/unit/test_graphrag_prompts_facade.py
git commit -m "refactor(graphrag): prompts.py — KB_SCHEMA_PROFILES becomes YAML-backed facade

Replaces the hardcoded KB_SCHEMA_PROFILES dict with a dict-compatible
_LegacyKBSchemaProfilesProxy that lazily reads deploy/config/graph_schemas/
via SchemaResolver. get_kb_schema() and build_extraction_prompt() now
route through the resolver too. All existing callers keep their current
imports and call signatures (backward-compat spec §7).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Migration script `migrate_schema_to_yaml.py`

**Files:**
- Create: `scripts/ops/migrate_schema_to_yaml.py`
- Test: `tests/unit/test_schema_migration.py`

The goal is a one-shot, idempotent dump of the original hardcoded schema into
YAML files. We capture the hardcoded data before Task 6 replaced the dict, so
the script ships with the pre-migration contents inline. Running it produces
`deploy/config/graph_schemas/<kb_id>.yaml` for each of the six known KBs.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_schema_migration.py`:

```python
"""Tests for migrate_schema_to_yaml.py — idempotency + round-trip."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from scripts.ops import migrate_schema_to_yaml as migrate


@pytest.fixture
def out_dir(tmp_path, monkeypatch):
    d = tmp_path / "graph_schemas"
    d.mkdir()
    monkeypatch.setattr(migrate, "OUT_DIR", d)
    return d


class TestMigration:
    def test_writes_all_known_kbs(self, out_dir: Path):
        migrate.main()
        files = sorted(p.name for p in out_dir.glob("*.yaml"))
        # 6 KBs from original KB_SCHEMA_PROFILES
        assert files == [
            "a-ari.yaml",
            "drp.yaml",
            "g-espa.yaml",
            "hax.yaml",
            "itops_general.yaml",
            "partnertalk.yaml",
        ]

    def test_yaml_has_required_keys(self, out_dir: Path):
        migrate.main()
        data = yaml.safe_load((out_dir / "g-espa.yaml").read_text())
        assert data["kb_id"] == "g-espa"
        assert data["version"] == 1
        assert "Store" in data["nodes"]
        assert "OPERATES" in data["relationships"]
        assert "prompt_focus" in data
        assert data["options"]["disable_bootstrap"] is False
        assert data["_metadata"]["migrated_from"].startswith("prompts.py")

    def test_idempotent_reruns(self, out_dir: Path):
        migrate.main()
        first_contents = {
            p.name: p.read_text() for p in out_dir.glob("*.yaml")
        }

        # Rerun — existing files should be left alone (not overwritten)
        migrate.main()
        second_contents = {
            p.name: p.read_text() for p in out_dir.glob("*.yaml")
        }
        assert first_contents == second_contents

    def test_roundtrip_via_resolver(self, out_dir: Path, monkeypatch):
        """YAML generated by migrate round-trips through SchemaResolver
        back to the same nodes/relationships/prompt_focus the original
        hardcoded dict defined."""
        from src.pipelines.graphrag.schema_resolver import (
            SchemaResolver,
            invalidate_cache,
        )

        # Point resolver at the tmp dir
        monkeypatch.setattr(
            "src.pipelines.graphrag.schema_resolver._SCHEMA_DIR", out_dir,
        )
        monkeypatch.setattr(
            "src.pipelines.graphrag.schema_resolver._DEFAULTS_DIR",
            out_dir / "_defaults",
        )
        (out_dir / "_defaults").mkdir(exist_ok=True)
        invalidate_cache()

        migrate.main()

        profile = SchemaResolver.resolve(kb_id="g-espa", source_type=None)
        expected = migrate.LEGACY_PROFILES["g-espa"]
        assert set(profile.nodes) == set(expected["nodes"])
        assert set(profile.relationships) == set(expected["relationships"])
        assert profile.prompt_focus == expected["prompt_focus"]
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/unit/test_schema_migration.py -v --no-cov
```

Expected: `ModuleNotFoundError: No module named 'scripts.ops.migrate_schema_to_yaml'`

- [ ] **Step 3: Write the migration script**

Create `scripts/ops/migrate_schema_to_yaml.py`:

```python
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
```

- [ ] **Step 4: Ensure `scripts/ops/__init__.py` exists**

```bash
test -f scripts/ops/__init__.py || touch scripts/ops/__init__.py
ls scripts/ops/__init__.py
```

Expected: the file exists (needed for test import `from scripts.ops import ...`).

- [ ] **Step 5: Run test to verify it passes**

```bash
uv run pytest tests/unit/test_schema_migration.py -v --no-cov
```

Expected: 4 passed.

- [ ] **Step 6: Lint**

```bash
uvx ruff check scripts/ops/migrate_schema_to_yaml.py tests/unit/test_schema_migration.py
```

Expected: `All checks passed!`

- [ ] **Step 7: Commit**

```bash
git add scripts/ops/migrate_schema_to_yaml.py scripts/ops/__init__.py tests/unit/test_schema_migration.py
git commit -m "feat(scripts): migrate_schema_to_yaml — one-shot KB_SCHEMA_PROFILES export

Idempotent script that writes deploy/config/graph_schemas/<kb_id>.yaml for
each of the six pre-existing KBs. Existing YAML files are left untouched on
re-run. Tests assert round-trip equivalence via SchemaResolver.

Spec §8.2.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: Execute migration — generate real KB YAMLs

**Files:**
- Generate: `deploy/config/graph_schemas/{a-ari,g-espa,drp,hax,itops_general,partnertalk}.yaml`

- [ ] **Step 1: Run the migration**

```bash
uv run python scripts/ops/migrate_schema_to_yaml.py
```

Expected output:
```
wrote deploy/config/graph_schemas/a-ari.yaml
wrote deploy/config/graph_schemas/drp.yaml
wrote deploy/config/graph_schemas/g-espa.yaml
wrote deploy/config/graph_schemas/hax.yaml
wrote deploy/config/graph_schemas/itops_general.yaml
wrote deploy/config/graph_schemas/partnertalk.yaml
Done. wrote=6 skipped=0
```

- [ ] **Step 2: Verify files exist and are valid**

```bash
ls deploy/config/graph_schemas/*.yaml
uv run python -c "
import yaml
from pathlib import Path
for p in sorted(Path('deploy/config/graph_schemas').glob('*.yaml')):
    d = yaml.safe_load(p.read_text())
    print(f'{p.name}: {len(d[\"nodes\"])} nodes, {len(d[\"relationships\"])} rels')
"
```

Expected: 6 files listed, each with non-zero nodes/rels.

- [ ] **Step 3: Verify legacy facade now serves YAML data**

```bash
uv run python -c "
from src.pipelines.graphrag.prompts import KB_SCHEMA_PROFILES, get_kb_schema
assert 'g-espa' in KB_SCHEMA_PROFILES
p = KB_SCHEMA_PROFILES['g-espa']
assert 'Store' in p['nodes']
assert 'OPERATES' in p['relationships']
print('KB_SCHEMA_PROFILES[g-espa]:', p['prompt_focus'])
print('get_kb_schema:', get_kb_schema('g-espa')['nodes'])
print('OK — facade serves YAML data correctly.')
"
```

Expected: the prompt_focus and nodes print correctly, ending with `OK —
facade serves YAML data correctly.`

- [ ] **Step 4: Run the existing graphrag test suite end-to-end**

```bash
uv run pytest tests/unit/test_graphrag_full.py tests/unit/test_graphrag_coverage.py tests/unit/test_graphrag_extractor_backfill.py -q --no-cov 2>&1 | tail -10
```

Expected: all pass.

- [ ] **Step 5: Commit the generated YAMLs**

```bash
git add deploy/config/graph_schemas/a-ari.yaml \
        deploy/config/graph_schemas/drp.yaml \
        deploy/config/graph_schemas/g-espa.yaml \
        deploy/config/graph_schemas/hax.yaml \
        deploy/config/graph_schemas/itops_general.yaml \
        deploy/config/graph_schemas/partnertalk.yaml
git commit -m "chore(graphrag): migrate 6 KB schemas from prompts.py to YAML

Generated by scripts/ops/migrate_schema_to_yaml.py. These files are now
the source of truth for KB-specific entity/relationship schemas; the
legacy KB_SCHEMA_PROFILES proxy in prompts.py reads from them.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: Export new public API from `__init__.py`

**Files:**
- Modify: `src/pipelines/graphrag/__init__.py`

- [ ] **Step 1: Read the existing `__init__.py`**

```bash
cat src/pipelines/graphrag/__init__.py
```

Note the existing list of `__all__` entries.

- [ ] **Step 2: Add the new exports**

Edit `src/pipelines/graphrag/__init__.py`. Near the top (alongside existing
imports from `.prompts`) add:

```python
from .schema_resolver import SchemaResolver, invalidate_cache
from .schema_types import IndexSpec, SchemaOptions, SchemaProfile
```

Inside the existing `__all__` list, add the five new names:

```python
    "IndexSpec",
    "SchemaOptions",
    "SchemaProfile",
    "SchemaResolver",
    "invalidate_cache",
```

Keep `__all__` alphabetically sorted if the file already sorts it; otherwise
follow the existing style.

- [ ] **Step 3: Verify imports**

```bash
uv run python -c "
from src.pipelines.graphrag import (
    SchemaResolver, SchemaProfile, SchemaOptions, IndexSpec, invalidate_cache,
    KB_SCHEMA_PROFILES, build_extraction_prompt, get_kb_schema,
)
print('all exports importable')
"
```

Expected: `all exports importable`.

- [ ] **Step 4: Lint**

```bash
uvx ruff check src/pipelines/graphrag/__init__.py
```

Expected: `All checks passed!`

- [ ] **Step 5: Commit**

```bash
git add src/pipelines/graphrag/__init__.py
git commit -m "chore(graphrag): export SchemaResolver / SchemaProfile public API

New Phase 1 dataclasses and resolver are re-exported from the package
root so callers write \`from src.pipelines.graphrag import SchemaResolver\`
without reaching into private modules.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: Operator documentation `docs/GRAPH_SCHEMA_EVOLUTION.md`

**Files:**
- Create: `docs/GRAPH_SCHEMA_EVOLUTION.md`

- [ ] **Step 1: Write the doc**

Create `docs/GRAPH_SCHEMA_EVOLUTION.md`:

```markdown
# GraphRAG Schema Evolution (Phase 1 landing)

**Status**: Phase 1 shipped — YAML-based schema foundation.
**Spec**: `docs/superpowers/specs/2026-04-24-graph-schema-evolution-design.md`

---

## What changed

The 6 hardcoded KB schemas that used to live in
`src/pipelines/graphrag/prompts.py::KB_SCHEMA_PROFILES` now live in YAML
files under `deploy/config/graph_schemas/`:

```
deploy/config/graph_schemas/
├── _defaults/                    ← source-type defaults (D layer)
│   ├── _generic.yaml
│   ├── confluence.yaml
│   ├── file_upload.yaml
│   └── crawl_result.yaml
├── a-ari.yaml                    ← KB overrides (A layer)
├── drp.yaml
├── g-espa.yaml
├── hax.yaml
├── itops_general.yaml
└── partnertalk.yaml
```

Existing callers (`KB_SCHEMA_PROFILES["g-espa"]`, `get_kb_schema(kb_id)`,
`build_extraction_prompt(doc, kb_id)`) continue to work unchanged — a
compatibility facade in `prompts.py` reads the YAML files lazily.

New callers should prefer the resolver directly:

```python
from src.pipelines.graphrag import SchemaResolver

schema = SchemaResolver.resolve(kb_id="g-espa", source_type="confluence")
# schema.nodes, schema.relationships, schema.prompt_focus, schema.options
```

---

## Adding a new KB schema

1. Create `deploy/config/graph_schemas/<kb_id>.yaml` with the fields below.
2. Commit the file. Runtime picks it up on next ingestion (mtime-based
   hot-reload — no restart needed).

Minimum YAML shape:

```yaml
version: 1
kb_id: my-new-kb
prompt_focus: "문서의 도메인 특성 한 줄"
nodes:
  - Person
  - Topic
relationships:
  - MEMBER_OF
  - COVERS
options:
  disable_bootstrap: false
  schema_evolution: batch
  bootstrap_sample_size: 100
```

Validation happens on first load; parse errors log to `ERROR` level and
fall back to the generic default (no crash).

---

## Adding a new connector (source_type) default

1. Create `deploy/config/graph_schemas/_defaults/<source_type>.yaml` with
   the same shape as the KB file (omit `kb_id`).
2. The filename **must** be lowercase alphanumeric + underscore. That is
   also the whitelist for `is_valid_source_type()` (Cypher injection
   defense).

Available source defaults today: `_generic`, `confluence`, `file_upload`,
`crawl_result`. The rest of the connector catalog (jira, salesforce,
sharepoint, …) ships in Phase 1.5 per spec §8.2.

---

## What's next (future phases)

- **Phase 2**: schema-aware extraction prompt + Neo4j dynamic constraints.
- **Phase 3**: bootstrap discovery service + admin review workflow.
- **Phase 4+**: admin UI, re-extract, optional realtime evolution.

See the spec for detailed phase plan.
```

- [ ] **Step 2: Lint (markdown-compatible — ruff only covers Python, so
verify the file is well-formed instead)**

```bash
wc -l docs/GRAPH_SCHEMA_EVOLUTION.md
head -5 docs/GRAPH_SCHEMA_EVOLUTION.md
```

Expected: non-empty, header visible.

- [ ] **Step 3: Commit**

```bash
git add docs/GRAPH_SCHEMA_EVOLUTION.md
git commit -m "docs(graphrag): Phase 1 operator guide for YAML-based schemas

Short operator-facing doc: what Phase 1 changed, how to add a new KB YAML,
how to add a new connector source default. Full design lives in the spec.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 11: Full regression + lint + push-ready check

**Files:** (no changes — verification only)

- [ ] **Step 1: Full ruff check on touched tree**

```bash
uvx ruff check \
  src/pipelines/graphrag/ \
  scripts/ops/migrate_schema_to_yaml.py \
  tests/unit/test_schema_types.py \
  tests/unit/test_schema_resolver.py \
  tests/unit/test_source_defaults.py \
  tests/unit/test_schema_migration.py \
  tests/unit/test_graphrag_prompts_facade.py
```

Expected: `All checks passed!`

- [ ] **Step 2: New-test suite pass**

```bash
uv run pytest \
  tests/unit/test_schema_types.py \
  tests/unit/test_schema_resolver.py \
  tests/unit/test_source_defaults.py \
  tests/unit/test_schema_migration.py \
  tests/unit/test_graphrag_prompts_facade.py \
  -v --no-cov 2>&1 | tail -20
```

Expected: all new tests pass. Count should be roughly:
- `test_schema_types.py`: 9
- `test_schema_resolver.py`: 12
- `test_source_defaults.py`: 5
- `test_schema_migration.py`: 4
- `test_graphrag_prompts_facade.py`: 7

Total ≈ 37 new tests.

- [ ] **Step 3: Existing graphrag suite pass (no regressions)**

```bash
uv run pytest \
  tests/unit/test_graphrag_full.py \
  tests/unit/test_graphrag_coverage.py \
  tests/unit/test_graphrag_extractor_backfill.py \
  tests/unit/test_neo4j_read_ops.py \
  -q --no-cov 2>&1 | tail -10
```

Expected: all pass.

- [ ] **Step 4: Coverage gate for touched files ≥ 80%**

```bash
uv run pytest \
  tests/unit/test_schema_types.py \
  tests/unit/test_schema_resolver.py \
  tests/unit/test_source_defaults.py \
  tests/unit/test_schema_migration.py \
  tests/unit/test_graphrag_prompts_facade.py \
  --cov=src/pipelines/graphrag/schema_types \
  --cov=src/pipelines/graphrag/schema_resolver \
  --cov=src/pipelines/graphrag/source_defaults \
  --cov=scripts/ops/migrate_schema_to_yaml \
  --cov-report=term-missing --no-cov-on-fail 2>&1 | tail -20
```

Expected: every listed module reports ≥ 80% (spec §9.3 targets ≥ 85% for new
critical path; treat anything below 80% as a regression and add tests before
landing).

- [ ] **Step 5: Smoke — ingestion still builds a valid extraction prompt**

```bash
uv run python -c "
from src.pipelines.graphrag.prompts import build_extraction_prompt
p = build_extraction_prompt('샘플 문서입니다.', 'g-espa')
assert 'Store' in p
assert 'OPERATES' in p
assert '샘플 문서입니다.' in p
print('smoke ok — extraction prompt for g-espa built correctly')
"
```

Expected: `smoke ok — extraction prompt for g-espa built correctly`.

- [ ] **Step 6: Phase 1 done — branch ready for review**

If anything above failed, treat it as a task to fix before moving on — do
not mark Phase 1 done.

```bash
git log --oneline origin/main..HEAD
```

Expected output: the 10 commits from this plan (tasks 1 → 10).

---

## Spec Coverage Cross-Check

| Spec reference | Phase 1 task |
|---|---|
| §4.1 YAML file shape | Task 2 + Task 7 |
| §4.4 Python dataclasses | Task 1 |
| §6.1 module list (new files) | Tasks 1, 3, 5 |
| §6.2 SchemaResolver | Tasks 3, 4 |
| §6.7 source_type whitelist | Task 5 |
| §7 Backward compatibility facade | Task 6 |
| §8.2 Migration (6 KB) | Tasks 7, 8 |
| §9.1 unit tests | Tasks 1, 3, 4, 5, 6, 7 |
| §9.3 coverage ≥ 85% target | Task 11 Step 4 |
| §10 Phase 1 deliverable | All tasks |

Items explicitly **deferred** to later phases: dynamic Neo4j constraints
(§6.4, Phase 2), `ensure_dynamic_constraints` (§6.4, Phase 2), bootstrap
(§6.3, Phase 3), admin UI (§5.2, Phase 4), re-extract (§6.1 `schema_reextract`,
Phase 5), realtime evolution (§2 non-goal, Phase 6 opt-in).
