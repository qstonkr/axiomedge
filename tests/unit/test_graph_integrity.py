"""Unit tests for GraphIntegrityChecker."""

from __future__ import annotations

import asyncio

import pytest


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()

from src.stores.neo4j.integrity import (
    GraphIntegrityChecker,
    IntegrityIssue,
    IntegrityReport,
)


class TestIntegrityReportStructure:

    def test_integrity_report_structure(self):
        """IntegrityReport should have correct default values and serialization."""
        report = IntegrityReport()
        assert report.status == "ok"
        assert report.orphan_nodes == 0
        assert report.dangling_edges == 0
        assert report.missing_relationships == 0
        assert report.total_issues == 0
        assert report.issues == []
        assert report.kb_id is None

        d = report.to_dict()
        assert d["status"] == "ok"
        assert d["orphan_nodes"] == 0
        assert d["issues"] == []

    def test_integrity_report_with_issues(self):
        """Report with issues should reflect correct status."""
        report = IntegrityReport(
            status="warning",
            orphan_nodes=3,
            total_issues=3,
            issues=[
                IntegrityIssue(
                    issue_type="orphan_node",
                    node_id="node-1",
                    node_type="Document",
                    message="Node has no relationships",
                    severity="warning",
                ),
                IntegrityIssue(
                    issue_type="orphan_node",
                    node_id="node-2",
                    node_type="Person",
                    message="Node has no relationships",
                    severity="warning",
                ),
                IntegrityIssue(
                    issue_type="missing_relationship",
                    node_id="node-3",
                    node_type="Document",
                    message="Missing BELONGS_TO",
                    severity="error",
                ),
            ],
        )

        d = report.to_dict()
        assert d["orphan_nodes"] == 3
        assert d["total_issues"] == 3
        assert len(d["issues"]) == 3
        assert d["issues"][2]["severity"] == "error"

    def test_integrity_issue_to_dict(self):
        """IntegrityIssue should serialize correctly."""
        issue = IntegrityIssue(
            issue_type="dangling_edge",
            node_id="n-42",
            node_type="Topic",
            message="Edge points to non-existent node",
            severity="error",
            details={"target_id": "n-999"},
        )
        d = issue.to_dict()
        assert d["issue_type"] == "dangling_edge"
        assert d["node_id"] == "n-42"
        assert d["details"]["target_id"] == "n-999"

    def test_no_client_returns_error(self):
        """Checker without a client should return error report."""
        checker = GraphIntegrityChecker(neo4j_client=None, graph_repository=None)
        report = _run(checker.check_integrity())
        assert report.status == "error"
        assert report.total_issues == 1
        assert report.issues[0].issue_type == "no_client"
