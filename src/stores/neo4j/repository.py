"""Neo4j Graph Repository (SSOT data access layer) — facade.

Unified repository for all Knowledge Graph read/write operations.
All Neo4j access should go through this class to ensure:
- Single MERGE key for Documents: ``{id: $doc_id}``
- SSOT label validation via ``node_registry``
- Single driver via injected ``Neo4jClient``

Created: 2026-03-09 (Knowledge Graph Refactoring Phase 2)

Implementation split:
- _write_ops.py  — write methods (upsert, batch, lineage)
- _search_ops.py — entity search (find_related_chunks, search_entities, find_experts)
- _read_ops.py   — graph traversal, process, tree index, stats/health
- _noop.py       — NoOpNeo4jGraphRepository (test stub)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .client import Neo4jClient

from .node_registry import (
    NODE_LABELS,
    RELATION_LABELS,
    is_supported_node_label,
    is_supported_relation_label,
)

# Mixin imports
from ._write_ops import WriteOpsMixin
from ._search_ops import SearchOpsMixin
from ._read_ops import ReadOpsMixin

# Re-export NoOp for backward compatibility
from ._noop import NoOpNeo4jGraphRepository  # noqa: F401

logger = logging.getLogger(__name__)


class Neo4jGraphRepository(WriteOpsMixin, SearchOpsMixin, ReadOpsMixin):
    """Unified data access layer for the Knowledge Graph.

    All write paths use ``MERGE (d:Document {id: $doc_id})`` as the
    single MERGE key to prevent duplicate Document nodes.
    """

    def __init__(self, neo4j_client: Neo4jClient) -> None:
        self._client = neo4j_client

    # -- Private Helpers --------------------------------------------------

    def _resolve_node_type(self, raw_type: str) -> str:
        """Validate and return a SSOT-registered node label."""
        if is_supported_node_label(raw_type):
            return raw_type
        # Case-insensitive lookup
        for label in NODE_LABELS:
            if label.lower() == raw_type.lower():
                return label
        logger.warning("Unsupported node type %r, falling back to Entity", raw_type)
        return "Entity"

    def _resolve_relation_type(self, raw_type: str) -> str:
        """Validate and return a SSOT-registered relation type."""
        if is_supported_relation_label(raw_type):
            return raw_type
        for label in RELATION_LABELS:
            if label.lower() == raw_type.lower():
                return label
        logger.warning(
            "Unsupported relation type %r, falling back to RELATED_TO", raw_type
        )
        return "RELATED_TO"
