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


def ensure_dynamic_constraints_sync(
    client: "Neo4jClient",
    schema: "SchemaProfile",
) -> dict[str, int]:
    """Synchronous wrapper around ``ensure_dynamic_constraints``.

    Legacy sync callers (``save_to_neo4j``) run inside
    ``with driver.session()`` blocks and cannot ``await``. This adapter
    runs the coroutine to completion, handling both "inside an existing
    event loop" and "no loop yet" call contexts.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(ensure_dynamic_constraints(client, schema))
    future = asyncio.run_coroutine_threadsafe(
        ensure_dynamic_constraints(client, schema), loop,
    )
    return future.result()


__all__ = [
    "ensure_dynamic_constraints",
    "ensure_dynamic_constraints_sync",
    "reset_session_cache",
]
