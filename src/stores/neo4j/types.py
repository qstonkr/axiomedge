"""Graph repository Protocol — structural interface for Neo4j + NoOp.

Both Neo4jGraphRepository and NoOpNeo4jGraphRepository satisfy this
Protocol without explicit inheritance (structural/duck typing).
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

# Neo4j property values — scalars that Neo4j driver accepts.
ScalarValue = str | int | float | bool | None
NodeProperties = dict[str, ScalarValue]


@runtime_checkable
class GraphRepository(Protocol):
    """Protocol for graph data access (Neo4j real + NoOp stub)."""

    async def upsert_document(
        self, doc_id: str, **kw: ScalarValue,
    ) -> dict[str, int]: ...
    async def upsert_entity(
        self, entity_type: str, entity_id: str, **kw: ScalarValue,
    ) -> dict[str, int]: ...
    async def create_relationship(
        self, source_id: str, target_id: str, rel_type: str,
        **kw: ScalarValue,
    ) -> dict[str, int]: ...
    async def batch_upsert_nodes(
        self, node_type: str, nodes: list[NodeProperties], **kw: Any,
    ) -> list[dict[str, int]]: ...
    async def batch_upsert_edges(
        self, rel_type: str, edges: list[NodeProperties], **kw: Any,
    ) -> list[dict[str, int]]: ...
    async def find_related_chunks(
        self, entity_names: list[str], **kw: Any,
    ) -> set[str]: ...
    async def search_entities(
        self, keywords: list[str], **kw: Any,
    ) -> list[NodeProperties]: ...
    async def find_experts(
        self, topic: str, **kw: Any,
    ) -> list[NodeProperties]: ...
    async def get_entity_neighbors(
        self, entity_name: str, entity_type: str, **kw: Any,
    ) -> list[NodeProperties]: ...
    async def find_similar_documents(
        self, doc_id: str, **kw: Any,
    ) -> list[NodeProperties]: ...
