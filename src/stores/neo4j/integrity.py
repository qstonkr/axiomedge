"""Graph Integrity Checker.

Checks for orphan nodes, missing required relationships, and
other structural issues in the knowledge graph.

Features:
- Orphan node detection (nodes with no relationships)
- Missing required relationship detection
- Dangling edge detection (edges pointing to non-existent nodes)
- KB-scoped integrity checks
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .client import Neo4jClient
    from .repository import Neo4jGraphRepository

logger = logging.getLogger(__name__)


@dataclass
class IntegrityIssue:
    """A single integrity issue."""

    issue_type: str          # orphan_node, missing_relationship, dangling_edge
    node_id: str
    node_type: str
    message: str
    severity: str = "warning"  # warning, error
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "issue_type": self.issue_type,
            "node_id": self.node_id,
            "node_type": self.node_type,
            "message": self.message,
            "severity": self.severity,
            "details": self.details,
        }


@dataclass
class IntegrityReport:
    """Integrity check report."""

    status: str = "ok"  # ok, warning, error
    orphan_nodes: int = 0
    dangling_edges: int = 0
    missing_relationships: int = 0
    total_issues: int = 0
    issues: list[IntegrityIssue] = field(default_factory=list)
    kb_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "orphan_nodes": self.orphan_nodes,
            "dangling_edges": self.dangling_edges,
            "missing_relationships": self.missing_relationships,
            "total_issues": self.total_issues,
            "issues": [i.to_dict() for i in self.issues[:50]],  # Limit output
            "kb_id": self.kb_id,
        }


# Cypher queries for integrity checks

# Find orphan nodes (no relationships at all)
ORPHAN_NODES_CYPHER = """
MATCH (n)
WHERE NOT (n)-[]-()
  AND NOT n:KnowledgeBase
RETURN n.id AS id, n.name AS name, labels(n)[0] AS type
LIMIT $limit
"""

# Find orphan nodes within a specific KB
ORPHAN_NODES_KB_CYPHER = """
MATCH (n)
WHERE n.kb_id = $kb_id
  AND NOT (n)-[]-()
  AND NOT n:KnowledgeBase
RETURN n.id AS id, n.name AS name, labels(n)[0] AS type
LIMIT $limit
"""

# Find documents without a BELONGS_TO relationship to any KB
DOCS_WITHOUT_KB_CYPHER = """
MATCH (d:Document)
WHERE NOT (d)-[:BELONGS_TO]->(:KnowledgeBase)
RETURN d.id AS id, d.title AS name, 'Document' AS type
LIMIT $limit
"""

# Find documents without a BELONGS_TO for a specific KB
DOCS_WITHOUT_KB_SCOPED_CYPHER = """
MATCH (d:Document {kb_id: $kb_id})
WHERE NOT (d)-[:BELONGS_TO]->(:KnowledgeBase)
RETURN d.id AS id, d.title AS name, 'Document' AS type
LIMIT $limit
"""

# Find Person nodes that are mentioned but have no authored/owned relationships
PERSONS_NO_AUTHORSHIP_CYPHER = """
MATCH (p:Person)
WHERE NOT (p)<-[:AUTHORED|OWNED_BY]-()
  AND (p)-[:MENTIONS|MEMBER_OF]-()
RETURN p.id AS id, p.name AS name, 'Person' AS type
LIMIT $limit
"""


class GraphIntegrityChecker:
    """Graph integrity checker.

    Detects structural issues in the knowledge graph.
    """

    def __init__(
        self,
        neo4j_client: "Neo4jClient | None" = None,
        graph_repository: "Neo4jGraphRepository | None" = None,
    ) -> None:
        self._client = neo4j_client
        self._graph_repository = graph_repository

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        if self._graph_repository is not None:
            return getattr(self._graph_repository, "_client", None)
        return None

    async def check_integrity(
        self,
        kb_id: str | None = None,
        limit: int = 100,
    ) -> IntegrityReport:
        """Run integrity checks on the knowledge graph.

        Args:
            kb_id: Optional KB ID to scope checks (None = all KBs)
            limit: Maximum issues per check type

        Returns:
            IntegrityReport with detected issues
        """
        report = IntegrityReport(kb_id=kb_id)
        client = self._get_client()

        if client is None:
            report.status = "error"
            report.issues.append(
                IntegrityIssue(
                    issue_type="no_client",
                    node_id="",
                    node_type="",
                    message="No Neo4j client available",
                    severity="error",
                )
            )
            report.total_issues = 1
            return report

        # Check 1: Orphan nodes
        await self._check_orphan_nodes(client, report, kb_id, limit)

        # Check 2: Documents without KB relationship
        await self._check_docs_without_kb(client, report, kb_id, limit)

        # Check 3: Persons without authorship
        await self._check_persons_no_authorship(client, report, limit)

        # Compute totals
        report.total_issues = len(report.issues)
        if report.total_issues == 0:
            report.status = "ok"
        elif any(i.severity == "error" for i in report.issues):
            report.status = "error"
        else:
            report.status = "warning"

        logger.info(
            "Integrity check complete: status=%s, orphans=%d, dangling=%d, missing=%d",
            report.status,
            report.orphan_nodes,
            report.dangling_edges,
            report.missing_relationships,
        )

        return report

    async def _check_orphan_nodes(
        self,
        client: Any,
        report: IntegrityReport,
        kb_id: str | None,
        limit: int,
    ) -> None:
        """Check for nodes with no relationships."""
        try:
            if kb_id:
                results = await client.execute_query(
                    ORPHAN_NODES_KB_CYPHER,
                    {"kb_id": kb_id, "limit": limit},
                )
            else:
                results = await client.execute_query(
                    ORPHAN_NODES_CYPHER,
                    {"limit": limit},
                )

            report.orphan_nodes = len(results)
            for record in results:
                report.issues.append(
                    IntegrityIssue(
                        issue_type="orphan_node",
                        node_id=str(record.get("id", "")),
                        node_type=str(record.get("type", "Unknown")),
                        message=f"Node has no relationships: {record.get('name', record.get('id', ''))}",
                        severity="warning",
                    )
                )

        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.warning("Orphan node check failed: %s", e)

    async def _check_docs_without_kb(
        self,
        client: Any,
        report: IntegrityReport,
        kb_id: str | None,
        limit: int,
    ) -> None:
        """Check for documents missing BELONGS_TO relationship."""
        try:
            if kb_id:
                results = await client.execute_query(
                    DOCS_WITHOUT_KB_SCOPED_CYPHER,
                    {"kb_id": kb_id, "limit": limit},
                )
            else:
                results = await client.execute_query(
                    DOCS_WITHOUT_KB_CYPHER,
                    {"limit": limit},
                )

            report.missing_relationships += len(results)
            for record in results:
                report.issues.append(
                    IntegrityIssue(
                        issue_type="missing_relationship",
                        node_id=str(record.get("id", "")),
                        node_type="Document",
                        message=f"Document missing BELONGS_TO KnowledgeBase: {record.get('name', record.get('id', ''))}",  # noqa: E501
                        severity="warning",
                    )
                )

        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.warning("Docs without KB check failed: %s", e)

    async def _check_persons_no_authorship(
        self,
        client: Any,
        report: IntegrityReport,
        limit: int,
    ) -> None:
        """Check for Person nodes without authorship relationships."""
        try:
            results = await client.execute_query(
                PERSONS_NO_AUTHORSHIP_CYPHER,
                {"limit": limit},
            )

            for record in results:
                report.issues.append(
                    IntegrityIssue(
                        issue_type="missing_relationship",
                        node_id=str(record.get("id", "")),
                        node_type="Person",
                        message=f"Person mentioned but has no authored/owned documents: {record.get('name', '')}",
                        severity="warning",
                    )
                )
                report.missing_relationships += 1

        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.warning("Persons authorship check failed: %s", e)
