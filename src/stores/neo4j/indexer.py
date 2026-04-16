"""Graph Indexer.

Creates required Neo4j indexes on startup and indexes documents
with their entities into the graph.

Adapted from oreo-ecosystem graph_indexer.py for knowledge-local.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from .schema import apply_schema

if TYPE_CHECKING:
    from .client import Neo4jClient

logger = logging.getLogger(__name__)


async def ensure_indexes(client: "Neo4jClient") -> dict[str, Any]:
    """Idempotent index creation on startup.

    Creates all constraints, btree indexes, and fulltext indexes
    defined in graph.schema. Safe to call multiple times.

    Args:
        client: Neo4j client

    Returns:
        Schema application results
    """
    try:
        results = await apply_schema(client)
        errors = results.get("errors", [])
        if errors:
            logger.warning("Schema application had %d errors: %s", len(errors), errors[:3])
        else:
            logger.info(
                "Graph indexes ensured: %d constraints, %d indexes, %d fulltext",
                results["constraints_created"],
                results["indexes_created"],
                results["fulltext_indexes_created"],
            )
        return results
    except Exception as e:  # noqa: BLE001
        logger.error("Failed to ensure graph indexes: %s", e)
        return {"constraints_created": 0, "indexes_created": 0, "fulltext_indexes_created": 0, "errors": [str(e)]}
