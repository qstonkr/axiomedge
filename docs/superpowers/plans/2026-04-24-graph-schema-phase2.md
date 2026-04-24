# GraphRAG Schema Evolution — Phase 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the Phase 1 `SchemaProfile` into the live extraction pipeline (LLM prompt + Neo4j storage). Dynamically register Neo4j uniqueness constraints for newly seen Tier-2 labels at ingestion time; drop LLM outputs that propose out-of-schema labels.

**Architecture:** A new `src/stores/neo4j/dynamic_schema.py` module exposes `ensure_dynamic_constraints(client, schema)` that idempotently creates `(label, id) UNIQUE` constraints for every node label in the resolved schema, skipping labels already in Tier 1 (`node_registry.NODE_LABELS`). Thread-safe module-level session cache avoids re-issuing DDL. `GraphRAGExtractor.extract()` gains optional `source_type` and `schema` parameters; the LLM response is post-filtered to drop out-of-schema labels. Ingestion pipeline (`ingestion.py`) propagates `RawDocument.source_type` so the resolver picks the right D-layer default.

**Tech Stack:** Python 3.12, existing Neo4j driver wrapper, pytest (mocked clients), no new runtime deps.

**Spec reference:** `docs/superpowers/specs/2026-04-24-graph-schema-evolution-design.md` §5.2 + §6.4.

**Out of scope (later phases):** Bootstrap (Phase 3), admin UI (Phase 4), re-extract (Phase 5), realtime (Phase 6). Phase 2 only wires the existing Phase 1 resolver into runtime — no new schema discovery.

---

## File Structure

### New files

| Path | Responsibility |
|---|---|
| `src/stores/neo4j/dynamic_schema.py` | `ensure_dynamic_constraints()` + session cache + `_SAFE_LABEL` regex |
| `tests/unit/test_dynamic_schema.py` | Tier 1 skip, injection defense, idempotency, fail-open |
| `tests/unit/test_extractor_schema_integration.py` | Extract with schema/source_type + hallucination drop |

### Modified files

| Path | Change |
|---|---|
| `src/pipelines/graphrag/extractor.py` | `extract()` signature gains `source_type: str \| None = None`, `schema: SchemaProfile \| None = None`. Internal: if `schema` not supplied, call `SchemaResolver.resolve(kb_id=, source_type=)`. After LLM parse, filter out-of-schema node/relationship labels. |
| `src/pipelines/graphrag/_neo4j_persistence.py` | `save_to_neo4j()` gains `schema: SchemaProfile \| None = None`. When provided, call `ensure_dynamic_constraints()` before the existing `_upsert_node_batches` block. |
| `src/pipelines/ingestion.py` (entry point) | When invoking `extractor.extract(...)` propagate `source_type=raw.source_type`, and when calling `save_to_neo4j` pass the resolved schema. |
| `src/stores/neo4j/__init__.py` | Re-export `ensure_dynamic_constraints`. |

---

## Task 1: `ensure_dynamic_constraints` — skeleton + Tier 1 skip + injection defense

**Files:**
- Create: `src/stores/neo4j/dynamic_schema.py`
- Test: `tests/unit/test_dynamic_schema.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_dynamic_schema.py`:

```python
"""Unit tests for ensure_dynamic_constraints."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.pipelines.graphrag.schema_types import IndexSpec, SchemaProfile
from src.stores.neo4j.dynamic_schema import (
    ensure_dynamic_constraints,
    reset_session_cache,
)


@pytest.fixture(autouse=True)
def _clean_cache():
    reset_session_cache()
    yield
    reset_session_cache()


@pytest.fixture
def mock_client():
    client = AsyncMock()
    client.execute_write = AsyncMock(return_value={"nodes_created": 0})
    return client


class TestSafeLabelRejection:
    @pytest.mark.asyncio
    async def test_injection_label_rejected(self, mock_client):
        schema = SchemaProfile(
            nodes=("Meeting; DROP DATABASE",),
            relationships=(),
            prompt_focus="",
        )
        stats = await ensure_dynamic_constraints(mock_client, schema)
        assert stats["failed"] == 1
        assert stats["created"] == 0
        # No Cypher executed for unsafe label
        mock_client.execute_write.assert_not_called()

    @pytest.mark.asyncio
    async def test_non_cypher_identifier_rejected(self, mock_client):
        schema = SchemaProfile(
            nodes=("123InvalidStart",),
            relationships=(),
            prompt_focus="",
        )
        stats = await ensure_dynamic_constraints(mock_client, schema)
        assert stats["failed"] == 1
        mock_client.execute_write.assert_not_called()


class TestTier1Skip:
    @pytest.mark.asyncio
    async def test_existing_tier1_label_skipped(self, mock_client):
        # Document is in node_registry.NODE_LABELS (Tier 1)
        schema = SchemaProfile(
            nodes=("Document",),
            relationships=(),
            prompt_focus="",
        )
        stats = await ensure_dynamic_constraints(mock_client, schema)
        assert stats["skipped"] == 1
        assert stats["created"] == 0
        mock_client.execute_write.assert_not_called()


class TestCreate:
    @pytest.mark.asyncio
    async def test_new_label_creates_unique_constraint(self, mock_client):
        schema = SchemaProfile(
            nodes=("Meeting",),
            relationships=(),
            prompt_focus="",
        )
        stats = await ensure_dynamic_constraints(mock_client, schema)
        assert stats["created"] == 1
        # Expect exactly one execute_write call for the id-unique constraint
        mock_client.execute_write.assert_awaited_once()
        call_cypher = mock_client.execute_write.await_args.args[0]
        assert "CREATE CONSTRAINT meeting_id_unique" in call_cypher
        assert "REQUIRE n.id IS UNIQUE" in call_cypher

    @pytest.mark.asyncio
    async def test_idempotent_within_session(self, mock_client):
        schema = SchemaProfile(
            nodes=("Meeting",),
            relationships=(),
            prompt_focus="",
        )
        await ensure_dynamic_constraints(mock_client, schema)
        mock_client.execute_write.reset_mock()
        # Second call should short-circuit via session cache
        stats2 = await ensure_dynamic_constraints(mock_client, schema)
        assert stats2["created"] == 0
        assert stats2["skipped"] == 1
        mock_client.execute_write.assert_not_called()

    @pytest.mark.asyncio
    async def test_custom_btree_index_emitted(self, mock_client):
        schema = SchemaProfile(
            nodes=("Meeting",),
            relationships=(),
            prompt_focus="",
            indexes={"Meeting": (IndexSpec(property="scheduled_at"),)},
        )
        await ensure_dynamic_constraints(mock_client, schema)
        # constraint + index = 2 calls
        assert mock_client.execute_write.await_count == 2
        idx_cypher = mock_client.execute_write.await_args_list[1].args[0]
        assert "CREATE INDEX meeting_scheduled_at_idx" in idx_cypher
        assert "ON (n.scheduled_at)" in idx_cypher

    @pytest.mark.asyncio
    async def test_fulltext_index_emitted(self, mock_client):
        schema = SchemaProfile(
            nodes=("Meeting",),
            relationships=(),
            prompt_focus="",
            indexes={
                "Meeting": (IndexSpec(property="title", index_type="fulltext"),),
            },
        )
        await ensure_dynamic_constraints(mock_client, schema)
        idx_cypher = mock_client.execute_write.await_args_list[1].args[0]
        assert "CREATE FULLTEXT INDEX meeting_title_ft" in idx_cypher
        assert "ON EACH [n.title]" in idx_cypher


class TestFailOpen:
    @pytest.mark.asyncio
    async def test_per_label_failure_does_not_abort_others(self, mock_client):
        from neo4j.exceptions import Neo4jError

        call_count = {"n": 0}

        async def flaky_write(cypher, *args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise Neo4jError("fake constraint collision")
            return {"nodes_created": 0}

        mock_client.execute_write = flaky_write
        schema = SchemaProfile(
            nodes=("Meeting", "Room"),
            relationships=(),
            prompt_focus="",
        )
        stats = await ensure_dynamic_constraints(mock_client, schema)
        # First label failed, second label succeeded
        assert stats["failed"] == 1
        assert stats["created"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/unit/test_dynamic_schema.py -v --no-cov
```

Expected: `ModuleNotFoundError: No module named 'src.stores.neo4j.dynamic_schema'`

- [ ] **Step 3: Write the implementation**

Create `src/stores/neo4j/dynamic_schema.py`:

```python
"""Dynamic Neo4j constraint management for Tier-2 (domain) labels.

Tier 1 labels (Document/Section/Chunk/…) are statically managed by
``node_registry``. Tier 2 labels discovered through YAML schema need their
``(label, id) UNIQUE`` constraint created on demand so the ingestion MERGE
remains race-safe. Optional indexes declared in the KB YAML are also
emitted.

Spec: docs/superpowers/specs/2026-04-24-graph-schema-evolution-design.md §6.4.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import TYPE_CHECKING

from src.stores.neo4j.errors import NEO4J_FAILURE
from src.stores.neo4j.node_registry import NODE_LABELS

if TYPE_CHECKING:
    from src.pipelines.graphrag.schema_types import SchemaProfile
    from src.stores.neo4j.client import Neo4jClient

logger = logging.getLogger(__name__)

# ``CREATE CONSTRAINT ... FOR (n:Label)`` — label must be a safe identifier
# before we interpolate it into Cypher.
_SAFE_LABEL = re.compile(r"^[A-Z][a-zA-Z0-9_]{0,63}$")

# Per-process cache of labels for which we've already issued DDL this
# session. Cheap protection against firing the same CREATE CONSTRAINT on
# every single ingested document.
_applied_labels: set[str] = set()
_lock = asyncio.Lock()


async def ensure_dynamic_constraints(
    client: "Neo4jClient",
    schema: "SchemaProfile",
) -> dict[str, int]:
    """Idempotently create Tier-2 uniqueness constraints + optional indexes.

    Returns: ``{"created": N, "skipped": N, "failed": N}``.

    - Tier 1 labels (``NODE_LABELS``) are skipped — node_registry manages them.
    - Unsafe labels (fail ``_SAFE_LABEL``) are rejected with an ERROR log; no
      Cypher is issued.
    - Per-label failures are logged at ERROR and do NOT abort remaining labels.
    """
    stats = {"created": 0, "skipped": 0, "failed": 0}

    async with _lock:
        for label in schema.nodes:
            if label in NODE_LABELS:
                stats["skipped"] += 1
                continue
            if label in _applied_labels:
                stats["skipped"] += 1
                continue
            if not _SAFE_LABEL.match(label):
                logger.error(
                    "Dynamic constraint rejected — unsafe label: %r", label,
                )
                stats["failed"] += 1
                continue

            try:
                constraint_name = f"{label.lower()}_id_unique"
                await client.execute_write(
                    f"CREATE CONSTRAINT {constraint_name} IF NOT EXISTS "
                    f"FOR (n:{label}) REQUIRE n.id IS UNIQUE",
                )

                for spec in schema.indexes.get(label, ()):
                    if spec.index_type == "btree":
                        idx_name = f"{label.lower()}_{spec.property}_idx"
                        await client.execute_write(
                            f"CREATE INDEX {idx_name} IF NOT EXISTS "
                            f"FOR (n:{label}) ON (n.{spec.property})",
                        )
                    elif spec.index_type == "fulltext":
                        ft_name = f"{label.lower()}_{spec.property}_ft"
                        await client.execute_write(
                            f"CREATE FULLTEXT INDEX {ft_name} IF NOT EXISTS "
                            f"FOR (n:{label}) ON EACH [n.{spec.property}]",
                        )

                _applied_labels.add(label)
                stats["created"] += 1
                logger.info(
                    "Dynamic Neo4j constraint created for label=%s", label,
                )
            except NEO4J_FAILURE as exc:
                logger.error(
                    "Dynamic constraint creation failed for %s: %s",
                    label, exc,
                )
                stats["failed"] += 1

    return stats


def reset_session_cache() -> None:
    """Clear the in-memory applied-labels cache (tests / admin tool)."""
    global _applied_labels
    _applied_labels = set()


__all__ = ["ensure_dynamic_constraints", "reset_session_cache"]
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/unit/test_dynamic_schema.py -v --no-cov
```

Expected: all 7 pass.

- [ ] **Step 5: Lint**

```bash
uvx ruff check src/stores/neo4j/dynamic_schema.py tests/unit/test_dynamic_schema.py
```

Expected: `All checks passed!`

- [ ] **Step 6: Export from package**

Edit `src/stores/neo4j/__init__.py` (if an `__init__.py` exports other symbols, append; otherwise create).

```bash
grep -n "^from \.\|^__all__" src/stores/neo4j/__init__.py
```

Add `from .dynamic_schema import ensure_dynamic_constraints, reset_session_cache` and extend `__all__`.

- [ ] **Step 7: Commit**

```bash
git add src/stores/neo4j/dynamic_schema.py tests/unit/test_dynamic_schema.py src/stores/neo4j/__init__.py
git commit -m "feat(neo4j): ensure_dynamic_constraints — Tier-2 label DDL

Idempotent (label, id) UNIQUE + optional btree/fulltext index creation
for Tier-2 (domain) labels discovered via YAML schema. Tier 1
(node_registry.NODE_LABELS) is skipped. Label is regex-validated before
interpolation into Cypher (injection defense). Per-label failures are
fail-open — other labels still processed.

Spec §6.4.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Extractor signature extension + schema resolution

**Files:**
- Modify: `src/pipelines/graphrag/extractor.py`
- Test: `tests/unit/test_extractor_schema_integration.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_extractor_schema_integration.py`:

```python
"""Extractor-side schema integration — signature + hallucination drop."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.pipelines.graphrag.extractor import GraphRAGExtractor
from src.pipelines.graphrag.schema_resolver import invalidate_cache
from src.pipelines.graphrag.schema_types import SchemaProfile


@pytest.fixture(autouse=True)
def _clean_cache():
    invalidate_cache()
    yield
    invalidate_cache()


class TestExtractorSignature:
    def test_extract_accepts_source_type_kwarg(self):
        extractor = GraphRAGExtractor()
        # Should not raise — signature accepts source_type
        import inspect
        sig = inspect.signature(extractor.extract)
        assert "source_type" in sig.parameters
        assert "schema" in sig.parameters

    def test_source_type_defaults_to_none(self):
        import inspect
        sig = inspect.signature(GraphRAGExtractor.extract)
        assert sig.parameters["source_type"].default is None
        assert sig.parameters["schema"].default is None


class TestHallucinationDrop:
    """LLM 이 schema outside 의 label 을 뽑으면 silent drop 되어야 한다.

    Phase 2 의 injection/drift 방어: schema 에 없는 type 은 extraction
    결과에서 제거. LLM 호출 자체는 mock.
    """

    def test_out_of_schema_nodes_dropped(self, monkeypatch):
        extractor = GraphRAGExtractor()
        schema = SchemaProfile(
            nodes=("Person", "Team"),
            relationships=("MEMBER_OF",),
            prompt_focus="x",
        )
        # Force LLM to return a hallucinated "FakeType"
        fake_json = (
            '{"nodes":['
            '{"id":"Alice","type":"Person"},'
            '{"id":"Intruder","type":"FakeType"}'
            '],"relationships":[]}'
        )

        # Stub the LLM wrapper — we don't care about the call, just the response
        mock_llm = MagicMock()
        mock_llm.invoke = MagicMock(return_value=fake_json)
        monkeypatch.setattr(extractor, "_get_llm", lambda: mock_llm)

        result = extractor.extract(
            document="doc",
            source_title="t",
            source_page_id="p",
            source_updated_at=None,
            kb_id=None,
            schema=schema,
        )

        types = {n.type for n in result.nodes}
        assert "Person" in types
        assert "FakeType" not in types, "schema-outside label must be dropped"

    def test_out_of_schema_relationships_dropped(self, monkeypatch):
        extractor = GraphRAGExtractor()
        schema = SchemaProfile(
            nodes=("Person", "Team"),
            relationships=("MEMBER_OF",),
            prompt_focus="x",
        )
        fake_json = (
            '{"nodes":['
            '{"id":"Alice","type":"Person"},'
            '{"id":"Red","type":"Team"}'
            '],"relationships":['
            '{"source":"Alice","type":"MEMBER_OF","target":"Red"},'
            '{"source":"Alice","type":"BELONGS_TO","target":"Red"}'
            ']}'
        )
        mock_llm = MagicMock()
        mock_llm.invoke = MagicMock(return_value=fake_json)
        monkeypatch.setattr(extractor, "_get_llm", lambda: mock_llm)

        result = extractor.extract(
            document="doc",
            source_title="t",
            source_page_id="p",
            source_updated_at=None,
            kb_id=None,
            schema=schema,
        )
        rel_types = {r.type for r in result.relationships}
        assert "MEMBER_OF" in rel_types
        assert "BELONGS_TO" not in rel_types
```

- [ ] **Step 2: Read the existing `extract` method**

```bash
grep -n "def extract\b" src/pipelines/graphrag/extractor.py
```

Note the line range. Read the full method so you don't lose any existing behavior.

```bash
awk '/^    def extract\(/,/^    def [^e]/' src/pipelines/graphrag/extractor.py | head -80
```

- [ ] **Step 3: Update the method**

In `src/pipelines/graphrag/extractor.py`:

1. At the top of the file, add imports if missing:
   ```python
   from .schema_resolver import SchemaResolver
   from .schema_types import SchemaProfile
   ```

2. Extend the `extract` method signature. The current signature (confirmed in Phase 1) is:
   ```python
   def extract(
       self,
       document: str,
       source_title: str | None = None,
       source_page_id: str | None = None,
       source_updated_at: str | None = None,
       kb_id: str | None = None,
       max_length: int = 8000,
   ) -> ExtractionResult:
   ```

   Change to:
   ```python
   def extract(
       self,
       document: str,
       source_title: str | None = None,
       source_page_id: str | None = None,
       source_updated_at: str | None = None,
       kb_id: str | None = None,
       max_length: int = 8000,
       source_type: str | None = None,
       schema: SchemaProfile | None = None,
   ) -> ExtractionResult:
   ```

3. Inside the method, resolve schema if not provided:
   ```python
   if schema is None:
       schema = SchemaResolver.resolve(kb_id=kb_id, source_type=source_type)
   ```

4. The existing code already calls `build_extraction_prompt(doc_text, kb_id)`. Replace that with:
   ```python
   prompt = build_extraction_prompt(doc_text, schema)
   ```
   (Phase 1 made `build_extraction_prompt` accept `SchemaProfile` as second arg.)

5. After the parse block that populates `result.nodes` / `result.relationships`, add the hallucination drop:
   ```python
   # Phase 2: drop out-of-schema labels (LLM hallucination defense)
   allowed_nodes = set(schema.nodes)
   allowed_rels = set(schema.relationships)
   if allowed_nodes:
       result.nodes = [n for n in result.nodes if n.type in allowed_nodes]
   if allowed_rels:
       result.relationships = [
           r for r in result.relationships if r.type in allowed_rels
       ]
   ```

The `if allowed_nodes:` guard is important — if schema is empty (e.g., generic fallback with no overrides), don't drop anything (backward compat with the existing Phase 1 test matrix).

- [ ] **Step 4: Run the new integration tests**

```bash
uv run pytest tests/unit/test_extractor_schema_integration.py -v --no-cov
```

Expected: 4 passed.

- [ ] **Step 5: Run existing extractor tests (no regression)**

```bash
uv run pytest tests/unit/test_graphrag_full.py tests/unit/test_graphrag_coverage.py tests/unit/test_graphrag_extractor_backfill.py -q --no-cov 2>&1 | tail -5
```

Expected: all pass.

- [ ] **Step 6: Lint**

```bash
uvx ruff check src/pipelines/graphrag/extractor.py tests/unit/test_extractor_schema_integration.py
```

- [ ] **Step 7: Commit**

```bash
git add src/pipelines/graphrag/extractor.py tests/unit/test_extractor_schema_integration.py
git commit -m "feat(graphrag): schema-aware extraction + hallucination drop

extract() gains source_type and schema kwargs (both optional, defaults
preserve existing behavior). When schema is resolved, LLM output is
post-filtered — any node.type or relationship.type outside the schema
is dropped silently. This is defense-in-depth: even if the prompt
failed to constrain the LLM, the result matches the declared schema.

Spec §5.2.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: `save_to_neo4j` dynamic constraint integration

**Files:**
- Modify: `src/pipelines/graphrag/_neo4j_persistence.py`
- Test: append to `tests/unit/test_dynamic_schema.py`

- [ ] **Step 1: Append integration test**

Append to `tests/unit/test_dynamic_schema.py`:

```python
class TestSaveToNeo4jIntegration:
    """save_to_neo4j(schema=...) must call ensure_dynamic_constraints."""

    @pytest.mark.asyncio
    async def test_schema_none_is_backward_compat(self, monkeypatch):
        """Legacy caller (no schema arg) must still work — constraint
        prep is skipped, existing behavior preserved."""
        from src.pipelines.graphrag._neo4j_persistence import Neo4jPersistenceMixin
        from src.pipelines.graphrag.models import ExtractionResult

        # Use bare mixin via an empty host class
        class _Host(Neo4jPersistenceMixin):
            _neo4j_driver = None
            def _get_neo4j_driver(self):
                raise RuntimeError("driver not configured")

        host = _Host()
        result = ExtractionResult()
        # schema=None → should early-return (no driver access)
        # With schema=None, save_to_neo4j triggers _get_neo4j_driver and
        # our mock raises — acceptable since no schema-prep path runs.
        with pytest.raises(RuntimeError):
            host.save_to_neo4j(result)  # driver RuntimeError expected

    @pytest.mark.asyncio
    async def test_schema_triggers_constraint_prep(self, monkeypatch):
        """When schema is passed, ensure_dynamic_constraints is invoked
        before the existing upsert block."""
        from src.pipelines.graphrag.models import ExtractionResult
        from src.pipelines.graphrag.schema_types import SchemaProfile

        prep_calls = []

        async def fake_prep(client, schema):
            prep_calls.append(schema)
            return {"created": 0, "skipped": 0, "failed": 0}

        monkeypatch.setattr(
            "src.pipelines.graphrag._neo4j_persistence.ensure_dynamic_constraints",
            fake_prep,
            raising=False,
        )

        # Build a minimal host that provides _get_neo4j_driver returning a
        # mock driver whose .session() context exits cleanly with 0 writes.
        from unittest.mock import MagicMock

        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)
        mock_session.run = MagicMock(return_value=iter([]))
        mock_driver = MagicMock()
        mock_driver.session.return_value = mock_session

        from src.pipelines.graphrag._neo4j_persistence import Neo4jPersistenceMixin

        class _Host(Neo4jPersistenceMixin):
            def _get_neo4j_driver(self):
                return mock_driver
            # Used by save_to_neo4j for batching; keep inert
            def _prepare_node_batches(self, *_a, **_k):
                return ({}, 0)
            def _upsert_node_batches(self, *_a, **_k):
                return (0, 0)
            _client = MagicMock()

        host = _Host()
        schema = SchemaProfile(
            nodes=("Meeting",),
            relationships=("SCHEDULED_IN",),
            prompt_focus="",
        )
        host.save_to_neo4j(ExtractionResult(), schema=schema)

        assert len(prep_calls) == 1
        assert prep_calls[0] is schema
```

- [ ] **Step 2: Read the existing `save_to_neo4j`**

```bash
grep -n "def save_to_neo4j\b" src/pipelines/graphrag/_neo4j_persistence.py
awk '/^    def save_to_neo4j\(/,/^    def [^s]/' src/pipelines/graphrag/_neo4j_persistence.py | head -40
```

Note that `save_to_neo4j` is **synchronous** (uses `with driver.session()`), not async — this matches the existing codebase pattern. The `ensure_dynamic_constraints` call needs to be adapted or we must add an async path. Decision: call `asyncio.run` only when schema is provided and the call site is sync; callers from async ingestion pipelines should await a separate async variant.

Simpler: keep `save_to_neo4j` signature synchronous; prep constraints by calling a sync adapter that wraps the async `ensure_dynamic_constraints` via `asyncio.run` when no loop is running, else schedules on the existing loop.

For Phase 2 minimum viable, since the whole of `save_to_neo4j` is sync, we'll add a **sync helper** next to `ensure_dynamic_constraints` that blocks the caller. Simpler and avoids event-loop gymnastics.

Add to `src/stores/neo4j/dynamic_schema.py` AFTER `reset_session_cache`:

```python
def ensure_dynamic_constraints_sync(
    client: "Neo4jClient",
    schema: "SchemaProfile",
) -> dict[str, int]:
    """Synchronous wrapper around ``ensure_dynamic_constraints`` for legacy
    callers that run inside ``with driver.session()`` blocks.

    If called from within a running event loop, submits the coroutine to
    that loop; otherwise spins up a fresh one via ``asyncio.run``.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(ensure_dynamic_constraints(client, schema))
    # Inside an existing loop — schedule + wait.
    future = asyncio.run_coroutine_threadsafe(
        ensure_dynamic_constraints(client, schema), loop,
    )
    return future.result()
```

Don't forget to add `ensure_dynamic_constraints_sync` to the module `__all__`.

- [ ] **Step 3: Update `_neo4j_persistence.py`**

At the top of `_neo4j_persistence.py` add:

```python
from src.stores.neo4j.dynamic_schema import ensure_dynamic_constraints_sync
```

Find the `def save_to_neo4j(self, result: ExtractionResult) -> dict[str, int]:` line and change to:

```python
def save_to_neo4j(
    self,
    result: ExtractionResult,
    schema: "SchemaProfile | None" = None,
) -> dict[str, int]:
```

Also add to the top of the file (for the forward-ref type hint):

```python
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from src.pipelines.graphrag.schema_types import SchemaProfile
```

Inside `save_to_neo4j`, after the driver acquisition (`driver = self._get_neo4j_driver()`) and **before** the main `with driver.session()` block, add:

```python
if schema is not None:
    try:
        ensure_dynamic_constraints_sync(self._client, schema)
    except NEO4J_FAILURE as exc:
        logger.warning(
            "Dynamic constraint prep failed (ingestion continues): %s", exc,
        )
```

Here `self._client` is the `Neo4jClient` instance; verify the mixin's host class provides it (if not, pass the driver directly to the constraint function — it just needs `execute_write`).

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/unit/test_dynamic_schema.py -v --no-cov
```

Expected: 9 passed (7 from Task 1 + 2 from Task 3).

- [ ] **Step 5: Run regression suite**

```bash
uv run pytest tests/unit/test_graphrag_full.py tests/unit/test_graphrag_coverage.py tests/unit/test_graphrag_extractor_backfill.py -q --no-cov 2>&1 | tail -5
```

Expected: all pass.

- [ ] **Step 6: Lint**

```bash
uvx ruff check src/stores/neo4j/dynamic_schema.py src/pipelines/graphrag/_neo4j_persistence.py tests/unit/test_dynamic_schema.py
```

- [ ] **Step 7: Commit**

```bash
git add src/stores/neo4j/dynamic_schema.py src/pipelines/graphrag/_neo4j_persistence.py tests/unit/test_dynamic_schema.py
git commit -m "feat(graphrag): save_to_neo4j triggers dynamic constraint prep

save_to_neo4j() gains an optional schema kwarg. When provided, Tier-2
label DDL is issued before the upsert block via
ensure_dynamic_constraints_sync() (sync adapter around the async core).
Schema prep failure is fail-open — ingestion proceeds; the constraint
will be attempted again on the next document that touches the same
label.

Spec §5.2.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Ingestion pipeline wiring

**Files:**
- Modify: `src/pipelines/ingestion.py` (just the call sites)

- [ ] **Step 1: Locate the call sites**

```bash
grep -n "extractor.extract\b\|save_to_neo4j\b" src/pipelines/ingestion.py | head -10
```

There may be one or two call sites depending on the ingestion flow.

- [ ] **Step 2: Update each call site**

For each `extractor.extract(...)` call, add:

```python
source_type=raw.source_type,
```

(The `RawDocument` model already carries `source_type` as a field — no new plumbing required.)

For each `save_to_neo4j(result)` call that is followed by an extractor-initiated persistence, resolve schema and pass through:

```python
from src.pipelines.graphrag.schema_resolver import SchemaResolver
schema = SchemaResolver.resolve(kb_id=kb_id, source_type=raw.source_type)
extractor.save_to_neo4j(result, schema=schema)
```

If `ingestion.py` doesn't directly import SchemaResolver, add the import at the top. Keep the resolver call cheap — it's mtime-cached.

- [ ] **Step 3: Run integration regression**

```bash
uv run pytest tests/unit/test_graphrag_full.py tests/unit/test_graphrag_coverage.py tests/unit/test_graphrag_extractor_backfill.py tests/unit/test_dynamic_schema.py tests/unit/test_extractor_schema_integration.py -q --no-cov 2>&1 | tail -5
```

Expected: all pass.

- [ ] **Step 4: Lint**

```bash
uvx ruff check src/pipelines/ingestion.py
```

- [ ] **Step 5: Commit**

```bash
git add src/pipelines/ingestion.py
git commit -m "feat(ingestion): propagate source_type + schema to graph extraction

Ingestion pipeline now passes RawDocument.source_type into
extractor.extract() so the resolver picks the right D-layer default
(e.g., jira → Issue/Project/..., confluence → Page/Person/...).
save_to_neo4j() receives the resolved SchemaProfile so dynamic
constraints for Tier-2 labels are created before MERGE.

Spec §5.2 + §6.4.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Full regression + coverage gate

- [ ] **Step 1: Run the full Phase 2 test surface**

```bash
uv run pytest \
  tests/unit/test_dynamic_schema.py \
  tests/unit/test_extractor_schema_integration.py \
  tests/unit/test_schema_types.py \
  tests/unit/test_schema_resolver.py \
  tests/unit/test_source_defaults.py \
  tests/unit/test_schema_migration.py \
  tests/unit/test_graphrag_prompts_facade.py \
  tests/unit/test_graphrag_full.py \
  tests/unit/test_graphrag_coverage.py \
  tests/unit/test_graphrag_extractor_backfill.py \
  tests/unit/test_neo4j_read_ops.py \
  tests/unit/test_graph_full.py \
  -q --no-cov 2>&1 | tail -5
```

Expected: all pass.

- [ ] **Step 2: Coverage for new modules ≥ 80%**

```bash
uv run pytest \
  tests/unit/test_dynamic_schema.py \
  tests/unit/test_extractor_schema_integration.py \
  --cov=src.stores.neo4j.dynamic_schema \
  --cov-report=term-missing --no-cov-on-fail 2>&1 | tail -10
```

Expected: `src/stores/neo4j/dynamic_schema.py` coverage ≥ 80%.

- [ ] **Step 3: Ruff clean**

```bash
uvx ruff check src/stores/neo4j/dynamic_schema.py src/pipelines/graphrag/ src/pipelines/ingestion.py tests/unit/test_dynamic_schema.py tests/unit/test_extractor_schema_integration.py
```

Expected: `All checks passed!`

- [ ] **Step 4: Smoke test end-to-end schema flow**

```bash
uv run python -c "
from src.pipelines.graphrag import SchemaResolver, SchemaProfile
schema = SchemaResolver.resolve(kb_id='g-espa', source_type='confluence')
assert 'Store' in schema.nodes
assert 'OPERATES' in schema.relationships
print(f'smoke ok — resolved schema: {len(schema.nodes)} nodes, {len(schema.relationships)} rels, layers={schema.source_layers}')
"
```

Expected: smoke ok with layers like `('D:confluence', 'A:g-espa')`.

---

## Spec Coverage Cross-Check

| Spec reference | Phase 2 task |
|---|---|
| §5.2 extractor integration | Task 2 |
| §5.2 save_to_neo4j schema | Task 3 |
| §5.2 ingestion propagation | Task 4 |
| §6.4 dynamic constraints | Tasks 1, 3 |
| §6.4 Tier 1/2 split | Task 1 |
| §6.4 label injection defense | Task 1 |
| §9.1 unit + integration tests | Tasks 1, 2, 3 |
| §9.3 coverage ≥ 80% | Task 5 |

Deferred to later phases: bootstrap discovery (Phase 3), admin UI (Phase 4),
re-extract (Phase 5), realtime (Phase 6).
