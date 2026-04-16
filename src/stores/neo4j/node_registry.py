"""Graph Node Registry (SSOT)

Purpose:
    Provide a single source of truth for Knowledge Graph node/relationship
    definitions used by schema creation, graph building, and Neo4j loading.

Features:
    - Canonical node registry with uniqueness/index metadata
    - Canonical relationship registry for Cypher whitelist validation
    - Neo4j DDL generators for constraints and indexes
    - Shared label sets for runtime validation

Usage:
    from src.stores.neo4j.node_registry import (
        NODE_TYPE_BY_KEY,
        RELATION_TYPE_BY_KEY,
        build_graph_constraints,
        build_graph_indexes,
    )

Examples:
    constraints = build_graph_constraints()
    indexes = build_graph_indexes()
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NodeConfig:
    """Node metadata for schema generation and runtime validation."""

    label: str
    unique_property: str | None = "id"
    constraint_name: str | None = None
    indexes: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class RelationConfig:
    """Relationship metadata for runtime validation."""

    relation_type: str


ALL_NODE_TYPES: dict[str, NodeConfig] = {
    # Content
    "document": NodeConfig(
        label="Document",
        unique_property="id",
        constraint_name="document_id_unique",
        indexes=(
            ("document_title_idx", "title"),
            ("document_kb_id_idx", "kb_id"),
            ("document_status_idx", "status"),
            ("document_source_type_idx", "source_type"),
        ),
    ),
    "section": NodeConfig(
        label="Section",
        unique_property="id",
        constraint_name="section_id_unique",
        indexes=(("section_doc_id_idx", "document_id"),),
    ),
    "table": NodeConfig(
        label="Table",
        unique_property="id",
        constraint_name="table_id_unique",
        indexes=(("table_doc_id_idx", "document_id"),),
    ),
    "attachment": NodeConfig(
        label="Attachment",
        unique_property="id",
        constraint_name="attachment_id_unique",
        indexes=(
            ("attachment_doc_id_idx", "document_id"),
            ("attachment_type_idx", "file_type"),
        ),
    ),
    "chunk": NodeConfig(
        label="Chunk",
        unique_property="id",
        constraint_name="chunk_id_unique",
        indexes=(
            ("chunk_document_id_idx", "document_id"),
            ("chunk_source_type_idx", "source_type"),
        ),
    ),
    "code_block": NodeConfig(
        label="CodeBlock",
        unique_property="id",
        constraint_name="code_block_id_unique",
        indexes=(
            ("code_block_doc_id_idx", "document_id"),
            ("code_block_language_idx", "language"),
        ),
    ),
    # Organization
    "person": NodeConfig(
        label="Person",
        unique_property="email",
        constraint_name="person_email_unique",
        indexes=(
            ("person_name_idx", "name"),
            ("person_department_idx", "department"),
        ),
    ),
    "team": NodeConfig(
        label="Team",
        unique_property="name",
        constraint_name="team_name_unique",
        indexes=(("team_division_idx", "division"),),
    ),
    "division": NodeConfig(
        label="Division",
        unique_property="name",
        constraint_name="division_name_unique",
    ),
    "department": NodeConfig(
        label="Department",
        unique_property="name",
        constraint_name="department_name_unique",
    ),
    "contact": NodeConfig(
        label="Contact",
        unique_property="id",
        constraint_name="contact_id_unique",
        indexes=(
            ("contact_type_idx", "type"),
            ("contact_person_name_idx", "person_name"),
        ),
    ),
    # Classification
    "topic": NodeConfig(
        label="Topic",
        unique_property="name",
        constraint_name="topic_name_unique",
        indexes=(("topic_category_idx", "category"),),
    ),
    "term": NodeConfig(
        label="Term",
        unique_property="id",
        constraint_name="term_id_unique",
        indexes=(
            ("term_name_idx", "name"),
            ("term_name_lower_idx", "name_lower"),
            ("term_ko_lower_idx", "term_ko_lower"),
            ("term_kb_id_idx", "kb_id"),
            ("term_category_idx", "category"),
            ("term_confidence_idx", "confidence"),
        ),
    ),
    "tag": NodeConfig(
        label="Tag",
        unique_property="name",
        constraint_name="tag_name_unique",
    ),
    # System
    "system": NodeConfig(
        label="System",
        unique_property="name",
        constraint_name="system_name_unique",
        indexes=(("system_type_idx", "type"),),
    ),
    "kb": NodeConfig(
        label="KnowledgeBase",
        unique_property="id",
        constraint_name="kb_id_unique",
    ),
    "module": NodeConfig(
        label="Module",
        unique_property="id",
        constraint_name="module_id_unique",
        indexes=(("module_system_name_idx", "system_name"),),
    ),
    # Process/Event
    "schedule": NodeConfig(
        label="Schedule",
        unique_property="id",
        constraint_name="schedule_id_unique",
        indexes=(
            ("schedule_date_idx", "schedule_date"),
            ("schedule_status_idx", "status"),
        ),
    ),
    "issue": NodeConfig(
        label="Issue",
        unique_property="id",
        constraint_name="issue_id_unique",
        indexes=(
            ("issue_key_idx", "issue_key"),
            ("issue_status_idx", "status"),
        ),
    ),
    "meeting": NodeConfig(
        label="Meeting",
        unique_property="id",
        constraint_name="meeting_id_unique",
        indexes=(("meeting_date_idx", "meeting_date"),),
    ),
    "release": NodeConfig(
        label="Release",
        unique_property="id",
        constraint_name="release_id_unique",
        indexes=(("release_date_idx", "release_date"),),
    ),
    # Process Steps (visual extraction)
    "process_step": NodeConfig(
        label="ProcessStep",
        unique_property="id",
        constraint_name="process_step_id_unique",
        indexes=(("process_step_doc_id_idx", "document_id"),),
    ),
    # Cross-KB Graph Traversal (P0-P5)
    "concept_cluster": NodeConfig(
        label="ConceptCluster",
        unique_property="id",
        constraint_name="concept_cluster_id_unique",
        indexes=(
            ("concept_cluster_name_idx", "name"),
            ("concept_cluster_category_idx", "category"),
        ),
    ),
    "temporal_event": NodeConfig(
        label="TemporalEvent",
        unique_property="id",
        constraint_name="temporal_event_id_unique",
        indexes=(
            ("temporal_event_time_idx", "event_time"),
            ("temporal_event_type_idx", "event_type"),
        ),
    ),
    # Location
    "store": NodeConfig(
        label="Store",
        unique_property="name",
        constraint_name="store_name_unique",
        indexes=(
            ("store_kb_id_idx", "kb_id"),
        ),
    ),
    # Compatibility / fallback
    "entity": NodeConfig(
        label="Entity",
        unique_property="id",
        constraint_name="entity_id_unique",
    ),
    "project": NodeConfig(
        label="Project",
        unique_property="id",
        constraint_name="project_id_unique",
    ),
    "technology": NodeConfig(
        label="Technology",
        unique_property="id",
        constraint_name="technology_id_unique",
    ),
    # Tree Index (heading_path 기반 문서 구조 트리)
    "tree_root": NodeConfig(
        label="TreeRoot",
        unique_property="id",
        constraint_name="tree_root_id_unique",
        indexes=(
            ("tree_root_doc_id_idx", "doc_id"),
        ),
    ),
    "tree_section": NodeConfig(
        label="TreeSection",
        unique_property="id",
        constraint_name="tree_section_id_unique",
        indexes=(
            ("tree_section_doc_id_idx", "doc_id"),
            ("tree_section_title_idx", "title"),
        ),
    ),
    "tree_page": NodeConfig(
        label="TreePage",
        unique_property="id",
        constraint_name="tree_page_id_unique",
        indexes=(
            ("tree_page_chunk_id_idx", "chunk_id"),
            ("tree_page_doc_id_idx", "doc_id"),
        ),
    ),
}

ALL_RELATION_TYPES: dict[str, RelationConfig] = {
    # Ownership
    "owned_by": RelationConfig("OWNED_BY"),
    "responsible_for": RelationConfig("RESPONSIBLE_FOR"),
    "owns": RelationConfig("OWNS"),
    # Structure
    "has_section": RelationConfig("HAS_SECTION"),
    "has_table": RelationConfig("HAS_TABLE"),
    "has_attachment": RelationConfig("HAS_ATTACHMENT"),
    "has_chunk": RelationConfig("HAS_CHUNK"),
    "has_code_block": RelationConfig("HAS_CODE_BLOCK"),
    "has_module": RelationConfig("HAS_MODULE"),
    # Hierarchy
    "belongs_to": RelationConfig("BELONGS_TO"),
    "member_of": RelationConfig("MEMBER_OF"),
    "parent_of": RelationConfig("PARENT_OF"),
    "child_of": RelationConfig("CHILD_OF"),
    # Reference
    "references": RelationConfig("REFERENCES"),
    "supersedes": RelationConfig("SUPERSEDES"),
    "based_on": RelationConfig("BASED_ON"),
    "cites": RelationConfig("CITES"),
    "references_issue": RelationConfig("REFERENCES_ISSUE"),
    # Knowledge
    "covers": RelationConfig("COVERS"),
    "defines": RelationConfig("DEFINES"),
    "tagged_with": RelationConfig("TAGGED_WITH"),
    "categorized_as": RelationConfig("CATEGORIZED_AS"),
    # Entity
    "about": RelationConfig("ABOUT"),
    "mentions": RelationConfig("MENTIONS"),
    "created_by": RelationConfig("CREATED_BY"),
    "modified_by": RelationConfig("MODIFIED_BY"),
    "authored_by": RelationConfig("AUTHORED_BY"),
    "contacted_via": RelationConfig("CONTACTED_VIA"),
    # Similarity
    "semantically_similar": RelationConfig("SEMANTICALLY_SIMILAR"),
    "related_to": RelationConfig("RELATED_TO"),
    "synonym_of": RelationConfig("SYNONYM_OF"),
    # Conflict
    "conflicts_with": RelationConfig("CONFLICTS_WITH"),
    "duplicates": RelationConfig("DUPLICATES"),
    "contradicts": RelationConfig("CONTRADICTS"),
    # System
    "uses": RelationConfig("USES"),
    "depends_on": RelationConfig("DEPENDS_ON"),
    "integrates_with": RelationConfig("INTEGRATES_WITH"),
    "deployed_on": RelationConfig("DEPLOYED_ON"),
    # Lifecycle
    "archived_from": RelationConfig("ARCHIVED_FROM"),
    "migrated_from": RelationConfig("MIGRATED_FROM"),
    "extracted_from": RelationConfig("EXTRACTED_FROM"),
    # Lineage
    "derived_from": RelationConfig("DERIVED_FROM"),
    "replaces": RelationConfig("REPLACES"),
    "extends": RelationConfig("EXTENDS"),
    "revises": RelationConfig("REVISES"),
    "confirms": RelationConfig("CONFIRMS"),
    # Process/Event
    "has_schedule": RelationConfig("HAS_SCHEDULE"),
    "has_release": RelationConfig("HAS_RELEASE"),
    "from_meeting": RelationConfig("FROM_MEETING"),
    "assigned_to": RelationConfig("ASSIGNED_TO"),
    # Process Steps (visual extraction)
    "next_step": RelationConfig("NEXT_STEP"),
    "has_process_step": RelationConfig("HAS_PROCESS_STEP"),
    # Cross-KB Graph Traversal (P0-P5)
    "caused_by": RelationConfig("CAUSED_BY"),
    "resolved_by": RelationConfig("RESOLVED_BY"),
    "has_symptom": RelationConfig("HAS_SYMPTOM"),
    "same_concept": RelationConfig("SAME_CONCEPT"),
    "documented_in": RelationConfig("DOCUMENTED_IN"),
    "occurred_with": RelationConfig("OCCURRED_WITH"),
    "preceded_by": RelationConfig("PRECEDED_BY"),
    "expert_in": RelationConfig("EXPERT_IN"),
    "member_of_concept": RelationConfig("MEMBER_OF_CONCEPT"),
    "cross_kb_related": RelationConfig("CROSS_KB_RELATED"),
    # Tree Index (문서 구조 트리)
    "has_tree_root": RelationConfig("HAS_TREE_ROOT"),
    "has_tree_section": RelationConfig("HAS_TREE_SECTION"),
    "has_tree_page": RelationConfig("HAS_TREE_PAGE"),
    "tree_next_sibling": RelationConfig("TREE_NEXT_SIBLING"),
    "tree_has_summary": RelationConfig("TREE_HAS_SUMMARY"),
}


NODE_TYPE_BY_KEY: dict[str, str] = {
    key: config.label for key, config in ALL_NODE_TYPES.items()
}
RELATION_TYPE_BY_KEY: dict[str, str] = {
    key: config.relation_type for key, config in ALL_RELATION_TYPES.items()
}

NODE_LABELS: frozenset[str] = frozenset(NODE_TYPE_BY_KEY.values())
RELATION_LABELS: frozenset[str] = frozenset(RELATION_TYPE_BY_KEY.values())


def build_graph_constraints() -> list[str]:
    """Build Neo4j uniqueness constraints from the node registry."""
    constraints: list[str] = []
    for config in ALL_NODE_TYPES.values():
        if not config.unique_property:
            continue

        constraint_name = (
            config.constraint_name
            if config.constraint_name
            else f"{config.label.lower()}_{config.unique_property}_unique"
        )

        constraints.append(
            f"""
            CREATE CONSTRAINT {constraint_name} IF NOT EXISTS
            FOR (n:{config.label}) REQUIRE n.{config.unique_property} IS UNIQUE
            """.strip()
        )
    return constraints


def build_graph_indexes() -> list[str]:
    """Build Neo4j index DDL from the node registry."""
    indexes: list[str] = []
    for config in ALL_NODE_TYPES.values():
        for index_name, property_name in config.indexes:
            indexes.append(
                f"""
                CREATE INDEX {index_name} IF NOT EXISTS
                FOR (n:{config.label}) ON (n.{property_name})
                """.strip()
            )
    return indexes


def is_supported_node_label(label: str) -> bool:
    """Return whether a node label is registered."""
    return label in NODE_LABELS


def is_supported_relation_label(label: str) -> bool:
    """Return whether a relation label is registered."""
    return label in RELATION_LABELS


__all__ = [
    "ALL_NODE_TYPES",
    "ALL_RELATION_TYPES",
    "NODE_TYPE_BY_KEY",
    "RELATION_TYPE_BY_KEY",
    "NODE_LABELS",
    "RELATION_LABELS",
    "NodeConfig",
    "RelationConfig",
    "build_graph_constraints",
    "build_graph_indexes",
    "is_supported_node_label",
    "is_supported_relation_label",
]
