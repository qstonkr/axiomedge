"""GraphRAG Data Models - Entity, Relationship, ExtractionResult."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class GraphNode:
    """그래프 노드"""
    id: str
    type: str
    properties: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"id": self.id, "type": self.type, **self.properties}


@dataclass
class GraphRelationship:
    """그래프 관계"""
    source: str
    target: str
    type: str
    properties: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "target": self.target,
            "type": self.type,
            **self.properties
        }


@dataclass
class ExtractionResult:
    """추출 결과"""
    nodes: list[GraphNode] = field(default_factory=list)
    relationships: list[GraphRelationship] = field(default_factory=list)
    source_document: str | None = None
    source_page_id: str | None = None
    source_updated_at: str | None = None  # ISO 형식 (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS)
    kb_id: str | None = None
    raw_response: str | None = None

    @property
    def node_count(self) -> int:
        return len(self.nodes)

    @property
    def relationship_count(self) -> int:
        return len(self.relationships)

    def to_dict(self) -> dict:
        return {
            "nodes": [n.to_dict() for n in self.nodes],
            "relationships": [r.to_dict() for r in self.relationships],
            "source_document": self.source_document,
            "source_page_id": self.source_page_id,
            "source_updated_at": self.source_updated_at,
        }
