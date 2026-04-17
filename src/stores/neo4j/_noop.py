"""No-op Neo4j graph repository — extracted from repository.py.

Test / development stub that satisfies the same interface as
Neo4jGraphRepository without requiring a live Neo4j connection.
"""

from __future__ import annotations

import asyncio
from typing import Any

from .types import NodeProperties, ScalarValue


class NoOpNeo4jGraphRepository:
    """Test / development no-op implementation."""

    # -- shared no-op returns (avoids S4144 duplicate implementation) --
    _EMPTY_UPSERT: dict[str, int] = {"nodes_created": 0, "properties_set": 0}
    _EMPTY_REL: dict[str, int] = {"nodes_created": 0, "relationships_created": 0}

    async def _noop_upsert(self) -> dict[str, int]:
        await asyncio.sleep(0)
        return dict(self._EMPTY_UPSERT)

    async def _noop_rel(self) -> dict[str, int]:
        await asyncio.sleep(0)
        return dict(self._EMPTY_REL)

    async def _noop_list(self) -> list[NodeProperties]:
        await asyncio.sleep(0)
        return []

    async def upsert_document(self, _doc_id: str, **_kw: ScalarValue) -> dict[str, int]:
        return await self._noop_upsert()

    async def upsert_entity(
        self, _entity_type: str, _entity_id: str, **_kw: ScalarValue,
    ) -> dict[str, int]:
        return await self._noop_upsert()

    async def create_relationship(
        self, _source_id: str, _target_id: str, _rel_type: str,
        **_kw: ScalarValue,
    ) -> dict[str, int]:
        return await self._noop_rel()

    async def batch_upsert_nodes(
        self, _node_type: str, _nodes: list[NodeProperties], **_kw: Any,
    ) -> list[dict[str, int]]:
        return await self._noop_list()  # type: ignore[return-value]

    async def batch_upsert_edges(
        self, _rel_type: str, _edges: list[NodeProperties], **_kw: Any,
    ) -> list[dict[str, int]]:
        return await self._noop_list()  # type: ignore[return-value]

    async def upsert_document_lineage(self, _doc_id: str, **_kw: ScalarValue) -> dict[str, int]:
        return await self._noop_upsert()

    async def find_related_chunks(self, _entity_names: list[str], **_kw: Any) -> set[str]:
        await asyncio.sleep(0)
        return set()

    async def search_entities(self, _keywords: list[str], **_kw: Any) -> list[NodeProperties]:
        return await self._noop_list()

    async def find_experts(self, _topic: str, **_kw: Any) -> list[NodeProperties]:
        return await self._noop_list()

    async def search_related_nodes(self, _doc_id: str, **_kw: Any) -> list[NodeProperties]:
        return await self._noop_list()

    async def get_entity_neighbors(
        self, _entity_name: str, _entity_type: str, **_kw: Any,
    ) -> list[NodeProperties]:
        return await self._noop_list()

    async def get_knowledge_path(self, _source_id: str, _target_id: str) -> list[NodeProperties]:
        return await self._noop_list()

    async def find_common_entities(self, _doc_ids: list[str]) -> list[NodeProperties]:
        return await self._noop_list()

    async def find_similar_documents(self, _doc_id: str, **_kw: Any) -> list[NodeProperties]:
        return await self._noop_list()

    async def query_process_chain(self, _process_keyword: str, **_kw: Any) -> list[NodeProperties]:
        return await self._noop_list()

    async def find_step_context(self, _step_keyword: str, **_kw: Any) -> NodeProperties:
        await asyncio.sleep(0)
        return {}

    async def _noop_zero(self) -> int:
        await asyncio.sleep(0)
        return 0

    async def get_entity_count(self) -> int:
        return await self._noop_zero()

    async def get_document_count(self) -> int:
        return await self._noop_zero()

    async def get_stats(self) -> dict[str, Any]:
        await asyncio.sleep(0)
        return {"node_types": {}, "edge_types": {}}

    async def health_check(self) -> bool:
        await asyncio.sleep(0)
        return True
