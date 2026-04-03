"""Graph Schema Definition.

Defines node labels, relationship types, cardinality rules, and
fulltext indexes for the Knowledge Graph.

Adapted from oreo-ecosystem graph_schema.py for knowledge-local.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from .node_registry import (
    RELATION_TYPE_BY_KEY,
    NODE_TYPE_BY_KEY,
    build_graph_constraints,
    build_graph_indexes,
)

if TYPE_CHECKING:
    from .client import Neo4jClient

logger = logging.getLogger(__name__)

_ALREADY_EXISTS = "already exists"


# === Node/Relation Types (SSOT: node_registry.py) ===
NODE_TYPES = dict(NODE_TYPE_BY_KEY)
RELATION_TYPES = dict(RELATION_TYPE_BY_KEY)


# === Constraints / Indexes (Generated from SSOT) ===
GRAPH_CONSTRAINTS = build_graph_constraints()
GRAPH_INDEXES = build_graph_indexes()


# === Full-text Indexes (Search) ===
GRAPH_FULLTEXT_INDEXES = [
    # Unified entity name/title search (used by multi-hop searcher and repository)
    """
    CREATE FULLTEXT INDEX entity_name_title IF NOT EXISTS
    FOR (n:Document|Person|Team|Topic|System|Term)
    ON EACH [n.name, n.title]
    """,
    # Document full-text search
    """
    CREATE FULLTEXT INDEX document_search IF NOT EXISTS
    FOR (d:Document) ON EACH [d.title, d.content]
    """,
    # Topic full-text search
    """
    CREATE FULLTEXT INDEX topic_search IF NOT EXISTS
    FOR (tp:Topic) ON EACH [tp.name, tp.description]
    """,
    # Term full-text search
    """
    CREATE FULLTEXT INDEX term_search IF NOT EXISTS
    FOR (t:Term) ON EACH [t.name, t.definition, t.synonyms]
    """,
    # Person full-text search
    """
    CREATE FULLTEXT INDEX person_search IF NOT EXISTS
    FOR (p:Person) ON EACH [p.name, p.email]
    """,
    # System full-text search
    """
    CREATE FULLTEXT INDEX system_search IF NOT EXISTS
    FOR (s:System) ON EACH [s.name, s.description]
    """,
]


# === Cardinality Rules ===
# These are advisory: the schema doesn't enforce them in Neo4j,
# but integrity checks can validate against them.
CARDINALITY_RULES: dict[str, dict[str, str]] = {
    "OWNED_BY": {"from": "Document", "to": "Person", "cardinality": "many-to-one"},
    "AUTHORED": {"from": "Person", "to": "Document", "cardinality": "one-to-many"},
    "BELONGS_TO": {"from": "Document", "to": "KnowledgeBase", "cardinality": "many-to-one"},
    "CHILD_OF": {"from": "Document", "to": "Document", "cardinality": "many-to-one"},
    "MEMBER_OF": {"from": "Person", "to": "Team", "cardinality": "many-to-one"},
    "COVERS": {"from": "Document", "to": "Topic", "cardinality": "many-to-many"},
    "MENTIONS": {"from": "Document", "to": "Entity", "cardinality": "many-to-many"},
    "REFERENCES": {"from": "Document", "to": "Document", "cardinality": "many-to-many"},
    "RELATED_TO": {"from": "Entity", "to": "Entity", "cardinality": "many-to-many"},
}


async def _apply_ddl_group(
    client, statements: list[str], results: dict[str, Any], count_key: str, label: str,
) -> None:
    """Execute a group of DDL statements, counting successes and logging errors."""
    for stmt in statements:
        try:
            await client.execute_write(stmt.strip())
            results[count_key] += 1
        except Exception as e:
            if _ALREADY_EXISTS not in str(e).lower():
                results["errors"].append(f"{label} error: {e}")
                logger.warning("%s creation failed: %s", label, e)


async def apply_schema(client: "Neo4jClient") -> dict[str, Any]:
    """Apply graph schema (constraints, indexes, fulltext indexes).

    Idempotent: uses IF NOT EXISTS for all DDL statements.

    Args:
        client: Neo4j client

    Returns:
        Summary of applied schema elements
    """
    results: dict[str, Any] = {
        "constraints_created": 0,
        "indexes_created": 0,
        "fulltext_indexes_created": 0,
        "errors": [],
    }

    ddl_groups = [
        (GRAPH_CONSTRAINTS, "constraints_created", "Constraint"),
        (GRAPH_INDEXES, "indexes_created", "Index"),
        (GRAPH_FULLTEXT_INDEXES, "fulltext_indexes_created", "Fulltext index"),
    ]
    for statements, count_key, label in ddl_groups:
        await _apply_ddl_group(client, statements, results, count_key, label)

    logger.info(
        "Schema applied: %d constraints, %d indexes, %d fulltext indexes",
        results["constraints_created"],
        results["indexes_created"],
        results["fulltext_indexes_created"],
    )

    return results
