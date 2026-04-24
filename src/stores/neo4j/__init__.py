"""Graph module - Neo4j client, repository, and utilities."""

from .client import Neo4jClient, NoOpNeo4jClient
from .repository import Neo4jGraphRepository, NoOpNeo4jGraphRepository
from .node_registry import (
    NODE_LABELS,
    RELATION_LABELS,
    NODE_TYPE_BY_KEY,
    RELATION_TYPE_BY_KEY,
    build_graph_constraints,
    build_graph_indexes,
)
from .lucene_utils import sanitize_lucene, build_lucene_or_query
from .indexer import ensure_indexes
from .schema import apply_schema
from .entity_resolver import EntityResolver, ResolvedEntity, ResolutionStage, EntityType
from .multi_hop_searcher import MultiHopSearcher, RelatedNode, Expert, KnowledgePath
from .integrity import GraphIntegrityChecker, IntegrityReport, IntegrityIssue
from .dynamic_schema import (
    ensure_dynamic_constraints,
    ensure_dynamic_constraints_sync,
    reset_session_cache,
)

__all__ = [
    "Neo4jClient",
    "NoOpNeo4jClient",
    "Neo4jGraphRepository",
    "NoOpNeo4jGraphRepository",
    "NODE_LABELS",
    "RELATION_LABELS",
    "NODE_TYPE_BY_KEY",
    "RELATION_TYPE_BY_KEY",
    "build_graph_constraints",
    "build_graph_indexes",
    "sanitize_lucene",
    "build_lucene_or_query",
    "ensure_indexes",
    "apply_schema",
    "EntityResolver",
    "ResolvedEntity",
    "ResolutionStage",
    "EntityType",
    "MultiHopSearcher",
    "RelatedNode",
    "Expert",
    "KnowledgePath",
    "GraphIntegrityChecker",
    "IntegrityReport",
    "IntegrityIssue",
    "ensure_dynamic_constraints",
    "ensure_dynamic_constraints_sync",
    "reset_session_cache",
]
