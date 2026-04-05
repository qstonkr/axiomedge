"""Unit tests for src/database/models.py — Database ORM model definitions."""

from __future__ import annotations

import pytest

from src.database.models import (
    ApprovedKnowledgeEventModel,
    AuditLogModel,
    ContributorReputationModel,
    DataSourceModel,
    DocumentErrorReportModel,
    DocumentLifecycleModel,
    DocumentLineageModel,
    DocumentOwnerModel,
    GlossaryTermModel,
    IngestionRunModel,
    KBConfigModel,
    KBSearchGroupModel,
    KnowledgeAccessWhitelistModel,
    KnowledgeBase,
    KnowledgeCategoryModel,
    KnowledgeFeedbackModel,
    KnowledgeVersionModel,
    LifecycleTransitionModel,
    LineageEventModel,
    LineageRelationModel,
    ProvenanceModel,
    RegistryBase,
    TopicOwnerModel,
    TrustScoreModel,
    UsageLogModel,
)


class TestDeclarativeBases:
    """Test that declarative bases exist and are distinct."""

    def test_knowledge_base_exists(self) -> None:
        assert KnowledgeBase is not None
        assert hasattr(KnowledgeBase, "metadata")

    def test_registry_base_exists(self) -> None:
        assert RegistryBase is not None
        assert hasattr(RegistryBase, "metadata")

    def test_bases_are_distinct(self) -> None:
        assert KnowledgeBase is not RegistryBase


class TestDocumentOwnerModel:
    """Test DocumentOwnerModel."""

    def test_tablename(self) -> None:
        assert DocumentOwnerModel.__tablename__ == "document_owners"

    def test_unique_constraint(self) -> None:
        constraints = [
            c.name for c in DocumentOwnerModel.__table__.constraints
            if hasattr(c, "name") and c.name
        ]
        assert "uq_doc_owner_document_kb" in constraints

    def test_default_ownership_type(self) -> None:
        col = DocumentOwnerModel.__table__.columns["ownership_type"]
        assert col.default.arg == "assigned"


class TestGlossaryTermModel:
    """Test GlossaryTermModel."""

    def test_tablename(self) -> None:
        assert GlossaryTermModel.__tablename__ == "glossary_terms"

    def test_unique_constraint_kb_term(self) -> None:
        constraints = [
            c.name for c in GlossaryTermModel.__table__.constraints
            if hasattr(c, "name") and c.name
        ]
        assert "uq_glossary_kb_term" in constraints

    def test_default_source(self) -> None:
        col = GlossaryTermModel.__table__.columns["source"]
        assert col.default.arg == "manual"

    def test_default_status_pending(self) -> None:
        col = GlossaryTermModel.__table__.columns["status"]
        assert col.default.arg == "pending"

    def test_default_term_type(self) -> None:
        col = GlossaryTermModel.__table__.columns["term_type"]
        assert col.default.arg == "term"

    def test_indexes_defined(self) -> None:
        index_names = {idx.name for idx in GlossaryTermModel.__table__.indexes}
        assert "idx_glossary_kb" in index_names
        assert "idx_glossary_kb_status" in index_names


class TestTrustScoreModel:
    """Test TrustScoreModel."""

    def test_tablename(self) -> None:
        assert TrustScoreModel.__tablename__ == "knowledge_trust_scores"

    def test_default_kts_score(self) -> None:
        col = TrustScoreModel.__table__.columns["kts_score"]
        assert col.default.arg == 0.0

    def test_default_confidence_tier(self) -> None:
        col = TrustScoreModel.__table__.columns["confidence_tier"]
        assert col.default.arg == "uncertain"

    def test_default_freshness_domain(self) -> None:
        col = TrustScoreModel.__table__.columns["freshness_domain"]
        assert col.default.arg == "general"

    def test_vote_defaults_zero(self) -> None:
        assert TrustScoreModel.__table__.columns["upvotes"].default.arg == 0
        assert TrustScoreModel.__table__.columns["downvotes"].default.arg == 0


class TestIngestionRunModel:
    """Test IngestionRunModel."""

    def test_tablename(self) -> None:
        assert IngestionRunModel.__tablename__ == "knowledge_ingestion_runs"

    def test_default_status_running(self) -> None:
        col = IngestionRunModel.__table__.columns["status"]
        assert col.default.arg == "running"

    def test_columns_exist(self) -> None:
        cols = set(IngestionRunModel.__table__.columns.keys())
        expected = {"id", "kb_id", "source_type", "source_name", "status",
                    "documents_fetched", "documents_ingested", "chunks_stored"}
        assert expected.issubset(cols)


class TestDocumentLifecycleModel:
    """Test DocumentLifecycleModel."""

    def test_tablename(self) -> None:
        assert DocumentLifecycleModel.__tablename__ == "document_lifecycles"

    def test_default_status_draft(self) -> None:
        col = DocumentLifecycleModel.__table__.columns["status"]
        assert col.default.arg == "draft"

    def test_unique_constraint(self) -> None:
        constraints = [
            c.name for c in DocumentLifecycleModel.__table__.constraints
            if hasattr(c, "name") and c.name
        ]
        assert "uq_lifecycle_document_kb" in constraints


class TestKnowledgeVersionModel:
    """Test KnowledgeVersionModel."""

    def test_tablename(self) -> None:
        assert KnowledgeVersionModel.__tablename__ == "knowledge_versions"

    def test_default_is_current_true(self) -> None:
        col = KnowledgeVersionModel.__table__.columns["is_current"]
        assert col.default.arg is True

    def test_default_approval_status(self) -> None:
        col = KnowledgeVersionModel.__table__.columns["approval_status"]
        assert col.default.arg == "not_required"

    def test_self_referencing_foreign_key(self) -> None:
        col = KnowledgeVersionModel.__table__.columns["previous_version_id"]
        fks = list(col.foreign_keys)
        assert len(fks) == 1
        assert "knowledge_versions.id" in str(fks[0].target_fullname)


class TestKBConfigModel:
    """Test KBConfigModel (uses RegistryBase)."""

    def test_tablename(self) -> None:
        assert KBConfigModel.__tablename__ == "kb_configs"

    def test_uses_registry_base(self) -> None:
        assert KBConfigModel.__table__.metadata is RegistryBase.metadata

    def test_default_tier(self) -> None:
        col = KBConfigModel.__table__.columns["tier"]
        assert col.default.arg == "team"

    def test_default_storage_backend(self) -> None:
        col = KBConfigModel.__table__.columns["storage_backend"]
        assert col.default.arg == "qdrant"

    def test_default_status_pending(self) -> None:
        col = KBConfigModel.__table__.columns["status"]
        assert col.default.arg == "pending"

    def test_default_data_classification(self) -> None:
        col = KBConfigModel.__table__.columns["data_classification"]
        assert col.default.arg == "internal"


class TestContributorReputationModel:
    """Test ContributorReputationModel."""

    def test_tablename(self) -> None:
        assert ContributorReputationModel.__tablename__ == "contributor_reputations"

    def test_default_rank_novice(self) -> None:
        col = ContributorReputationModel.__table__.columns["rank"]
        assert col.default.arg == "novice"

    def test_default_total_points_zero(self) -> None:
        col = ContributorReputationModel.__table__.columns["total_points"]
        assert col.default.arg == 0


class TestApprovedKnowledgeEventModel:
    """Test ApprovedKnowledgeEventModel."""

    def test_tablename(self) -> None:
        assert ApprovedKnowledgeEventModel.__tablename__ == "approved_knowledge_events"

    def test_primary_key_is_event_id(self) -> None:
        col = ApprovedKnowledgeEventModel.__table__.columns["event_id"]
        assert col.primary_key is True

    def test_default_category(self) -> None:
        col = ApprovedKnowledgeEventModel.__table__.columns["category"]
        assert col.default.arg == "troubleshooting"

    def test_ingested_at_nullable(self) -> None:
        col = ApprovedKnowledgeEventModel.__table__.columns["ingested_at"]
        assert col.nullable is True
