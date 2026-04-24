"""Smoke tests for Phase 3 DB models — column presence + constraints."""

from __future__ import annotations

from src.stores.postgres.models import (
    BootstrapRunModel,
    ReextractJobModel,
    SchemaCandidateModel,
)


class TestCandidateModel:
    def test_has_required_columns(self):
        cols = {c.name for c in SchemaCandidateModel.__table__.columns}
        assert {
            "id", "kb_id", "candidate_type", "label", "frequency",
            "confidence_avg", "confidence_min", "confidence_max",
            "source_label", "target_label", "examples", "status",
            "merged_into", "rejected_reason", "similar_labels",
            "first_seen_at", "last_seen_at", "decided_at", "decided_by",
        } <= cols

    def test_unique_constraint_on_kb_type_label(self):
        # Either a UniqueConstraint or a unique Index on (kb_id, candidate_type, label)
        targets = [
            sorted([c.name for c in cnst.columns])
            for cnst in SchemaCandidateModel.__table__.constraints
            if hasattr(cnst, "columns")
        ]
        targets += [
            sorted([c.name for c in idx.columns])
            for idx in SchemaCandidateModel.__table__.indexes
            if getattr(idx, "unique", False)
        ]
        assert sorted(["kb_id", "candidate_type", "label"]) in targets


class TestBootstrapRunModel:
    def test_has_required_columns(self):
        cols = {c.name for c in BootstrapRunModel.__table__.columns}
        assert {
            "id", "kb_id", "status", "triggered_by", "sample_size",
            "docs_scanned", "candidates_found", "started_at",
            "completed_at", "error_message",
        } <= cols


class TestReextractJobModel:
    def test_has_required_columns(self):
        cols = {c.name for c in ReextractJobModel.__table__.columns}
        assert {
            "id", "kb_id", "schema_version_from", "schema_version_to",
            "status", "docs_total", "docs_processed", "docs_failed",
            "queued_at",
        } <= cols
