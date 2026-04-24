"""Knowledge ORM Models - Shared Declarative Base

Consolidates all knowledge-related ORM models under one Base so that
Alembic can detect all tables from a single metadata.

Tables:
- document_owners: Document ownership assignments
- topic_owners: Topic SME assignments
- document_error_reports: Error reports with escalation
- glossary_terms: Domain glossary with synonyms/abbreviations
- knowledge_trust_scores: KTS composite scores
- knowledge_feedback: User feedback/votes
- contributor_reputations: Gamification reputation
- knowledge_document_lineage: Per-document ingestion lineage
- knowledge_ingestion_runs: Ingestion run tracking
- knowledge_provenances: Document provenance
- knowledge_lineage_events: Lineage event history
- knowledge_lineage_relations: Lineage relations
- knowledge_versions: Content versioning
- knowledge_usage_logs: Usage tracking
- document_lifecycles: Document lifecycle state
- document_lifecycle_transitions: Lifecycle transitions
- knowledge_audit_logs: Audit trail
- knowledge_data_sources: Data source registry
- knowledge_access_whitelist: Dashboard access whitelist
- approved_knowledge_events: Flywheel approved events
- kb_configs: KB registry configurations
- knowledge_categories: L1/L2 categories

"""

from __future__ import annotations

import uuid as uuidlib
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import declarative_base

# Shared declarative base for all knowledge models
KnowledgeBase = declarative_base()

_FK_INGESTION_RUN_ID = "knowledge_ingestion_runs.id"

# Separate base for KB registry + categories (uses JSONB, PG-specific)
RegistryBase = declarative_base()


# =============================================================================
# Document Ownership
# =============================================================================


class DocumentOwnerModel(KnowledgeBase):
    """Document ownership assignments."""

    __tablename__ = "document_owners"

    id = Column(String(36), primary_key=True)
    document_id = Column(String(255), nullable=False)
    kb_id = Column(String(255), nullable=False)
    owner_user_id = Column(String(255), nullable=False)
    backup_owner_user_id = Column(String(255), nullable=True)
    ownership_type = Column(String(20), nullable=False, default="assigned")
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        UniqueConstraint("document_id", "kb_id", name="uq_doc_owner_document_kb"),
        Index("idx_doc_owner_kb", "kb_id"),
        Index("idx_doc_owner_user", "owner_user_id"),
    )


class TopicOwnerModel(KnowledgeBase):
    """Topic SME assignments."""

    __tablename__ = "topic_owners"

    id = Column(String(36), primary_key=True)
    kb_id = Column(String(255), nullable=False)
    topic_name = Column(String(255), nullable=False)
    topic_keywords = Column(Text, nullable=False, default="[]")  # JSON string
    sme_user_id = Column(String(255), nullable=False)
    escalation_chain = Column(Text, nullable=False, default="[]")  # JSON string
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        UniqueConstraint("kb_id", "topic_name", name="uq_topic_owner_kb_topic"),
        Index("idx_topic_owner_kb", "kb_id"),
        Index("idx_topic_owner_sme", "sme_user_id"),
    )


class DocumentErrorReportModel(KnowledgeBase):
    """Document error reports with escalation."""

    __tablename__ = "document_error_reports"

    id = Column(String(36), primary_key=True)
    document_id = Column(String(255), nullable=False)
    kb_id = Column(String(255), nullable=False)
    error_type = Column(String(20), nullable=False)
    description = Column(Text, nullable=False)
    reporter_user_id = Column(String(255), nullable=False)
    assigned_to = Column(String(255), nullable=True)
    status = Column(String(20), nullable=False, default="pending")
    priority = Column(String(20), nullable=False, default="medium")
    resolution_note = Column(Text, nullable=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        Index("idx_error_report_doc_kb", "document_id", "kb_id"),
        Index("idx_error_report_assignee_status", "assigned_to", "status"),
        Index("idx_error_report_status", "status"),
    )


# =============================================================================
# Glossary
# =============================================================================


class GlossaryTermModel(KnowledgeBase):
    """Domain glossary terms with synonyms and abbreviations."""

    __tablename__ = "glossary_terms"

    id = Column(String(36), primary_key=True)
    kb_id = Column(String(255), nullable=False)
    term = Column(String(500), nullable=False)
    term_ko = Column(String(500), nullable=True)
    definition = Column(Text, nullable=False)
    synonyms = Column(Text, nullable=False, default="[]")  # JSON string
    abbreviations = Column(Text, nullable=False, default="[]")  # JSON string
    related_terms = Column(Text, nullable=False, default="[]")  # JSON string
    source = Column(String(20), nullable=False, default="manual")
    confidence_score = Column(Float, nullable=True)
    status = Column(String(20), nullable=False, default="pending")
    occurrence_count = Column(Integer, nullable=False, default=0)
    category = Column(String(255), nullable=True)
    created_by = Column(String(255), nullable=True)
    approved_by = Column(String(255), nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    scope = Column(String(20), nullable=False, default="kb")
    source_kb_ids = Column(Text, nullable=False, default="[]")  # JSON array
    physical_meaning = Column(String(1000), nullable=True)
    composition_info = Column(String(1000), nullable=True)
    domain_name = Column(String(255), nullable=True)
    term_type = Column(String(10), nullable=False, default="term")  # word | term
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        UniqueConstraint("kb_id", "term", name="uq_glossary_kb_term"),
        Index("idx_glossary_kb", "kb_id"),
        Index("idx_glossary_kb_status", "kb_id", "status"),
        Index("idx_glossary_scope", "scope"),
        Index("idx_glossary_scope_status", "scope", "status"),
        Index("ix_glossary_term_type", "term_type"),
    )


# =============================================================================
# Trust Scores
# =============================================================================


class TrustScoreModel(KnowledgeBase):
    """Knowledge Trust Score (KTS) for each knowledge entry."""

    __tablename__ = "knowledge_trust_scores"

    id = Column(String(36), primary_key=True)
    entry_id = Column(String(255), nullable=False)
    kb_id = Column(String(255), nullable=False)

    # Composite score
    kts_score = Column(Float, nullable=False, default=0.0)
    confidence_tier = Column(String(20), nullable=False, default="uncertain")

    # 6 sub-scores (0.0-1.0)
    source_credibility = Column(Float, nullable=False, default=0.0)
    freshness_score = Column(Float, nullable=False, default=1.0)
    user_validation_score = Column(Float, nullable=False, default=0.5)
    usage_score = Column(Float, nullable=False, default=0.0)
    hallucination_score = Column(Float, nullable=False, default=1.0)
    consistency_score = Column(Float, nullable=False, default=1.0)

    # Source metadata
    source_type = Column(String(50), nullable=False, default="auto_extracted")
    freshness_domain = Column(String(20), nullable=False, default="general")

    # Raw signals for user_validation_score
    upvotes = Column(Integer, nullable=False, default=0)
    downvotes = Column(Integer, nullable=False, default=0)
    expert_reviews = Column(Integer, nullable=False, default=0)
    open_error_reports = Column(Integer, nullable=False, default=0)

    # Raw signals for usage_score
    view_count = Column(Integer, nullable=False, default=0)
    citation_count = Column(Integer, nullable=False, default=0)
    bookmark_count = Column(Integer, nullable=False, default=0)

    # Timestamps
    last_evaluated_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        UniqueConstraint("entry_id", "kb_id", name="uq_trust_score_entry_kb"),
        Index("idx_trust_score_kb", "kb_id"),
        Index("idx_trust_score_kb_kts", "kb_id", "kts_score"),
        Index("idx_trust_score_kb_freshness", "kb_id", "freshness_score"),
    )


# =============================================================================
# Knowledge Feedback
# =============================================================================


class KnowledgeFeedbackModel(KnowledgeBase):
    """Knowledge feedback (votes, corrections, error reports)."""

    __tablename__ = "knowledge_feedback"

    id = Column(String(36), primary_key=True)
    entry_id = Column(String(255), nullable=False)
    kb_id = Column(String(255), nullable=False)
    user_id = Column(String(255), nullable=False)
    feedback_type = Column(String(30), nullable=False)
    status = Column(String(20), nullable=False, default="pending")
    error_category = Column(String(30), nullable=True)
    description = Column(Text, nullable=True)
    suggested_content = Column(Text, nullable=True)
    reviewer_id = Column(String(255), nullable=True)
    review_note = Column(Text, nullable=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    kts_impact = Column(Float, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        Index("idx_feedback_entry_kb", "entry_id", "kb_id"),
        Index("idx_feedback_user", "user_id"),
        Index("idx_feedback_status", "status"),
        Index("idx_feedback_type_status", "feedback_type", "status"),
    )


# =============================================================================
# Contributor Reputation
# =============================================================================


class ContributorReputationModel(KnowledgeBase):
    """Contributor reputation and gamification."""

    __tablename__ = "contributor_reputations"

    id = Column(String(36), primary_key=True)
    user_id = Column(String(255), nullable=False)
    total_points = Column(Integer, nullable=False, default=0)
    rank = Column(String(20), nullable=False, default="novice")
    badges = Column(Text, nullable=False, default="[]")  # JSON string

    # Activity counters
    corrections_submitted = Column(Integer, nullable=False, default=0)
    corrections_accepted = Column(Integer, nullable=False, default=0)
    reviews_done = Column(Integer, nullable=False, default=0)
    error_reports_confirmed = Column(Integer, nullable=False, default=0)
    contributions_count = Column(Integer, nullable=False, default=0)

    # Streak
    current_streak_days = Column(Integer, nullable=False, default=0)
    longest_streak_days = Column(Integer, nullable=False, default=0)
    last_activity_date = Column(DateTime(timezone=True), nullable=True)

    # Timestamps
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        UniqueConstraint("user_id", name="uq_contributor_reputation_user"),
        Index("idx_reputation_user", "user_id"),
        Index("idx_reputation_total_points", "total_points"),
    )


# =============================================================================
# Ingestion Runs
# =============================================================================


class IngestionRunModel(KnowledgeBase):
    """Ingestion run tracking."""

    __tablename__ = "knowledge_ingestion_runs"

    id = Column(String(36), primary_key=True)
    kb_id = Column(String(255), nullable=False)
    source_type = Column(String(50), nullable=False)
    source_name = Column(String(255), nullable=False)
    status = Column(String(20), nullable=False, default="running")
    version_fingerprint = Column(String(128), nullable=True)
    documents_fetched = Column(Integer, default=0)
    documents_ingested = Column(Integer, default=0)
    documents_held = Column(Integer, default=0)
    documents_rejected = Column(Integer, default=0)
    chunks_stored = Column(Integer, default=0)
    chunks_deduped = Column(Integer, default=0)
    errors = Column(Text, nullable=True)  # JSON array
    run_metadata = Column("metadata", Text, nullable=True)  # JSON dict
    started_at = Column(DateTime(timezone=True), nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("idx_ingestion_run_kb", "kb_id"),
        Index("idx_ingestion_run_status", "status"),
        Index("idx_ingestion_run_started", started_at.desc()),
    )


# =============================================================================
# Provenance
# =============================================================================


class ProvenanceModel(KnowledgeBase):
    """Document provenance tracking."""

    __tablename__ = "knowledge_provenances"

    id = Column(String(36), primary_key=True)
    knowledge_id = Column(String(255), nullable=False)
    kb_id = Column(String(255), nullable=False)
    ingestion_run_id = Column(
        String(36), ForeignKey(_FK_INGESTION_RUN_ID), nullable=True
    )
    source_type = Column(String(20), nullable=False)
    source_url = Column(Text, nullable=True)
    source_id = Column(String(255), nullable=True)
    source_system = Column(String(100), nullable=False, default="axiomedge")
    crawled_at = Column(DateTime(timezone=True), nullable=False)
    crawled_by = Column(String(100), nullable=False, default="knowledge-ingestion")
    extraction_metadata = Column(Text, nullable=True)  # JSON
    original_author = Column(String(255), nullable=True)
    original_created_at = Column(DateTime(timezone=True), nullable=True)
    original_modified_at = Column(DateTime(timezone=True), nullable=True)
    contributors = Column(Text, default="[]")  # JSON array
    verification_status = Column(String(30), nullable=False, default="unverified")
    quality_score = Column(Float, default=0.0)
    content_hash = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        UniqueConstraint("knowledge_id", "kb_id", name="uq_prov_knowledge_kb"),
        Index("idx_prov_kb", "kb_id"),
        Index("idx_prov_source", "source_type", "source_id"),
        Index("idx_prov_run", "ingestion_run_id"),
    )


# =============================================================================
# Document Lineage
# =============================================================================


class DocumentLineageModel(KnowledgeBase):
    """Per-document ingestion lineage tracking."""

    __tablename__ = "knowledge_document_lineage"

    id = Column(String(36), primary_key=True)
    ingestion_run_id = Column(
        String(36), ForeignKey(_FK_INGESTION_RUN_ID), nullable=True
    )
    kb_id = Column(String(128), nullable=False)
    source_type = Column(String(64), nullable=False)
    source_uri = Column(Text, nullable=False)
    source_hash = Column(String(128), nullable=True)
    chunk_count = Column(Integer, nullable=False, default=0)
    embedding_model = Column(String(128), nullable=True)
    status = Column(String(32), nullable=False, default="indexed")
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("idx_doc_lineage_run", "ingestion_run_id"),
        Index("idx_doc_lineage_kb", "kb_id"),
        Index("idx_doc_lineage_status", "status"),
        Index("idx_doc_lineage_source_uri", "source_uri"),
        Index("idx_doc_lineage_created_at", created_at.desc()),
    )


class LineageEventModel(KnowledgeBase):
    """Lineage event history."""

    __tablename__ = "knowledge_lineage_events"

    id = Column(String(36), primary_key=True)
    knowledge_id = Column(String(255), nullable=False)
    kb_id = Column(String(255), nullable=False)
    event_type = Column(String(30), nullable=False)
    event_description = Column(Text, nullable=False)
    actor = Column(String(100), nullable=False)
    version_before = Column(String(20), nullable=True)
    version_after = Column(String(20), nullable=True)
    event_metadata = Column(Text, default="{}")  # JSON
    ingestion_run_id = Column(
        String(36), ForeignKey(_FK_INGESTION_RUN_ID), nullable=True
    )
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("idx_lineage_evt_knowledge", "knowledge_id", "kb_id"),
        Index("idx_lineage_evt_type", "event_type"),
        Index("idx_lineage_evt_created", created_at.desc()),
    )


class LineageRelationModel(KnowledgeBase):
    """Lineage relations between knowledge documents."""

    __tablename__ = "knowledge_lineage_relations"

    id = Column(String(36), primary_key=True)
    source_knowledge_id = Column(String(255), nullable=False)
    target_knowledge_id = Column(String(255), nullable=False)
    relation_type = Column(String(30), nullable=False)
    relation_metadata = Column(Text, default="{}")  # JSON
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    created_by = Column(String(100), nullable=False)
    notes = Column(Text, nullable=True)

    __table_args__ = (
        Index("idx_lineage_rel_source", "source_knowledge_id"),
        Index("idx_lineage_rel_target", "target_knowledge_id"),
        Index("idx_lineage_rel_type", "relation_type"),
    )


# =============================================================================
# Knowledge Versioning
# =============================================================================


class KnowledgeVersionModel(KnowledgeBase):
    """Content versioning for knowledge documents."""

    __tablename__ = "knowledge_versions"

    id = Column(String(36), primary_key=True)
    knowledge_id = Column(String(255), nullable=False)
    kb_id = Column(String(255), nullable=False)
    version = Column(String(20), nullable=False)
    major = Column(Integer, nullable=False)
    minor = Column(Integer, nullable=False)
    patch = Column(Integer, nullable=False)
    content_snapshot = Column(Text, nullable=False)
    metadata_snapshot = Column(Text, default="{}")  # JSON
    change_type = Column(String(10), nullable=False)
    change_reason = Column(Text, default="")
    change_summary = Column(Text, default="")
    previous_version_id = Column(String(36), ForeignKey("knowledge_versions.id"), nullable=True)
    diff_additions = Column(Integer, default=0)
    diff_deletions = Column(Integer, default=0)
    diff_text = Column(Text, nullable=True)
    content_hash = Column(String(64), nullable=False)
    is_current = Column(Boolean, nullable=False, default=True)
    approval_status = Column(String(20), nullable=False, default="not_required")
    approved_by = Column(String(100), nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    created_by = Column(String(100), nullable=False)

    __table_args__ = (
        Index("idx_ver_knowledge", "knowledge_id", "kb_id"),
        Index("idx_ver_current", "knowledge_id", "is_current"),
        Index("idx_ver_approval", "approval_status"),
    )


# =============================================================================
# Usage Logs
# =============================================================================


class UsageLogModel(KnowledgeBase):
    """Usage tracking logs."""

    __tablename__ = "knowledge_usage_logs"

    id = Column(String(36), primary_key=True)
    knowledge_id = Column(String(255), nullable=False)
    kb_id = Column(String(255), nullable=False)
    usage_type = Column(String(20), nullable=False)
    user_id = Column(String(255), nullable=True)
    session_id = Column(String(255), nullable=True)
    context = Column(Text, default="{}")  # JSON
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("idx_usage_knowledge", "knowledge_id", "kb_id"),
        Index("idx_usage_type", "usage_type"),
        Index("idx_usage_created", created_at.desc()),
    )


# =============================================================================
# Document Lifecycle
# =============================================================================


class DocumentLifecycleModel(KnowledgeBase):
    """Document lifecycle state tracking."""

    __tablename__ = "document_lifecycles"

    id = Column(String(36), primary_key=True)
    document_id = Column(String(255), nullable=False)
    kb_id = Column(String(255), nullable=False)
    status = Column(String(30), nullable=False, default="draft")
    previous_status = Column(String(30), nullable=True)
    status_changed_at = Column(DateTime(timezone=True), nullable=True)
    status_changed_by = Column(String(255), nullable=True)
    auto_archive_at = Column(DateTime(timezone=True), nullable=True)
    deletion_scheduled_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        UniqueConstraint("document_id", "kb_id", name="uq_lifecycle_document_kb"),
        Index("idx_lifecycle_kb", "kb_id"),
        Index("idx_lifecycle_kb_status", "kb_id", "status"),
    )


class LifecycleTransitionModel(KnowledgeBase):
    """Append-only lifecycle transition history."""

    __tablename__ = "document_lifecycle_transitions"

    id = Column(String(36), primary_key=True)
    lifecycle_id = Column(String(36), ForeignKey("document_lifecycles.id", ondelete="CASCADE"), nullable=False)
    from_status = Column(String(30), nullable=False)
    to_status = Column(String(30), nullable=False)
    transitioned_by = Column(String(255), nullable=False)
    transitioned_at = Column(DateTime(timezone=True), nullable=False)
    reason = Column(Text, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("idx_transition_lifecycle", "lifecycle_id"),
        Index("idx_transition_lifecycle_at", "lifecycle_id", transitioned_at.desc()),
    )


# =============================================================================
# Audit Logs
# =============================================================================


class AuditLogModel(KnowledgeBase):
    """Immutable audit trail for knowledge operations."""

    __tablename__ = "knowledge_audit_logs"

    id = Column(String(36), primary_key=True)
    knowledge_id = Column(String(255), nullable=False)
    event_type = Column(String(50), nullable=False)
    actor = Column(String(100), nullable=False)
    details = Column(Text, default="{}")  # JSON
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("idx_audit_knowledge", "knowledge_id"),
        Index("idx_audit_event", "event_type"),
    )


# =============================================================================
# Data Source Registry
# =============================================================================


class DataSourceModel(KnowledgeBase):
    """Persistent data source registry."""

    __tablename__ = "knowledge_data_sources"

    id = Column(String(100), primary_key=True)
    name = Column(String(255), nullable=False, unique=True)
    source_type = Column(String(50), nullable=False)
    kb_id = Column(String(100), nullable=False)
    # 0005_data_source_org_required.py 에서 NOT NULL + FK(organizations.id)
    # 강제. 모든 repo 메서드는 org_id 필터를 요구해 cross-tenant 누설 차단
    # (KBConfigModel.organization_id 와 동일 패턴, 0004 참조).
    organization_id = Column(String(100), nullable=False)
    crawl_config = Column(Text, default="{}")  # JSON
    pipeline_config = Column(Text, default="{}")  # JSON
    schedule = Column(String(50), default="daily")
    status = Column(String(50), default="pending")
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )
    last_sync_at = Column(DateTime(timezone=True), nullable=True)
    last_sync_result = Column(Text, default="{}")  # JSON
    error_message = Column(Text, nullable=True)
    metadata_ = Column("metadata", Text, default="{}")  # JSON
    # 0006_data_source_secret.py — connector token (Confluence PAT, Git PAT
    # 등) 의 at-rest 암호화 저장. plain 은 SecretBox(path=secret_path) 가
    # 보관. crawl_config 의 평문 token 필드는 deprecated — 절대 저장 X.
    secret_path = Column(String(255), nullable=True)
    has_secret = Column(Boolean, nullable=False, default=False)
    # 0007_data_source_owner_user.py — 누가 등록한 source 인지 추적.
    # NULL = admin 등록 (organization-wide). value = 사용자 self-service —
    # 본인의 personal KB 에만 attach (라우트 권한 체크: kb.owner_id == owner_user_id).
    owner_user_id = Column(String(100), nullable=True)

    __table_args__ = (
        Index("idx_kds_kb_id", "kb_id"),
        Index("idx_kds_status", "status"),
        Index("idx_kds_source_type", "source_type"),
        Index("idx_kds_org_id", "organization_id"),
        Index("idx_kds_owner_user", "owner_user_id"),
    )


# =============================================================================
# Bulk Upload Sessions — presigned URL flow 의 진행 상태 추적 (0009)
# =============================================================================


class BulkUploadSessionModel(KnowledgeBase):
    """대량 파일 업로드 세션. 1 session = 사용자가 한 번 드래그한 N개 파일.

    presigned URL flow:
    1. POST /uploads/init → row 생성 (status='pending', total_files=N)
    2. 브라우저 → S3 직접 PUT (백엔드 우회)
    3. POST /uploads/{sid}/finalize → arq enqueue (status='processing')
    4. arq job → 각 파일 download + ingest → increment processed_files
    5. 완료 시 status='completed' 또는 'failed'
    """

    __tablename__ = "knowledge_bulk_upload_sessions"

    id = Column(String(36), primary_key=True)
    kb_id = Column(String(100), nullable=False)
    organization_id = Column(String(100), nullable=False)
    owner_user_id = Column(String(100), nullable=False)
    s3_prefix = Column(String(255), nullable=False)
    total_files = Column(Integer, nullable=False)
    processed_files = Column(Integer, nullable=False, default=0)
    failed_files = Column(Integer, nullable=False, default=0)
    # pending → processing → completed / failed
    status = Column(String(20), nullable=False, default="pending")
    # JSON list: [{"filename": "...", "error_message": "..."}, ...]
    errors = Column(Text, default="[]")
    # JSON list: [{"file_idx": 0, "filename": "report.pdf", "s3_key": "...", "size": 12345}]
    files = Column(Text, default="[]")
    created_at = Column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at = Column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("idx_bus_owner_status", "owner_user_id", "status"),
        Index("idx_bus_kb", "kb_id"),
    )


# =============================================================================
# Knowledge Access Whitelist
# =============================================================================


class KnowledgeAccessWhitelistModel(KnowledgeBase):
    """Knowledge Dashboard access whitelist."""

    __tablename__ = "knowledge_access_whitelist"

    id = Column(String(36), primary_key=True)
    email = Column(String(255), nullable=False, unique=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    granted_by = Column(String(100), nullable=False)
    reason = Column(String(500), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        Index("idx_whitelist_active", "is_active", "email"),
    )


# =============================================================================
# Approved Knowledge Events (Flywheel)
# =============================================================================


class ApprovedKnowledgeEventModel(KnowledgeBase):
    """Staging table for HITL-approved knowledge events."""

    __tablename__ = "approved_knowledge_events"

    event_id = Column(String(36), primary_key=True)
    source_system = Column(String(50), nullable=False)
    approval_type = Column(String(50), nullable=False)
    approved_by = Column(String(255), nullable=False, default="")
    approved_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    title = Column(String(500), nullable=False, default="")
    body = Column(Text, nullable=False, default="")
    category = Column(String(50), nullable=False, default="troubleshooting")
    tags = Column(Text, nullable=False, default="[]")  # JSON array
    confidence = Column(Float, nullable=False, default=1.0)
    verified = Column(Boolean, nullable=False, default=True)
    original_source_uri = Column(String(1000), nullable=False, default="")
    extra_metadata = Column("metadata", Text, nullable=False, default="{}")  # JSON object
    ingested_at = Column(DateTime(timezone=True), nullable=True)  # Nullable: pending until ingested
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("idx_approved_events_pending", "ingested_at"),
        Index("idx_approved_events_source", "source_system", "approval_type"),
        Index("idx_approved_events_created", "created_at"),
    )


# =============================================================================
# KB Registry (uses RegistryBase for JSONB columns)
# =============================================================================


def _utc_now() -> datetime:
    """UTC now as naive datetime for DB timestamps."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class KBConfigModel(RegistryBase):
    """PostgreSQL model for kb_configs table."""

    __tablename__ = "kb_configs"

    id = Column(String(100), primary_key=True)
    name = Column(String(255), nullable=False, unique=True)
    description = Column(Text, nullable=True)

    # 3-Tier hierarchy
    tier = Column(String(20), nullable=False, default="team", index=True)
    parent_kb_id = Column(String(100), nullable=True, index=True)
    # B-0 Day 5: NOT NULL + FK enforced by alembic 0004_kb_org_required.
    # Stays declared nullable=False here so SQLAlchemy create_all (used by
    # tests) matches the migrated production schema.
    organization_id = Column(
        String(100),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    department_id = Column(String(100), nullable=True, index=True)
    owner_id = Column(String(100), nullable=True, index=True)
    data_classification = Column(String(20), nullable=False, default="internal", index=True)

    # Dataset info
    dataset_id = Column(String(255), nullable=True)
    dataset_ids_by_env = Column(JSONB, nullable=False, default=dict)

    # Vector store backend
    storage_backend = Column(String(20), nullable=False, default="qdrant")

    # Sync config
    sync_sources = Column(JSONB, nullable=False, default=list)
    sync_schedule = Column(String(100), nullable=True)
    last_synced_at = Column(DateTime, nullable=True)

    # Document/chunk counts (updated after ingestion)
    document_count = Column(Integer, nullable=False, default=0)
    chunk_count = Column(Integer, nullable=False, default=0)
    last_ingested_at = Column(DateTime, nullable=True)

    # Status and settings
    status = Column(String(20), nullable=False, default="pending", index=True)
    settings = Column(JSONB, nullable=False, default=dict)

    # Metadata
    created_at = Column(DateTime, nullable=False, default=_utc_now)
    updated_at = Column(DateTime, nullable=False, default=_utc_now, onupdate=_utc_now)

    __table_args__ = (
        Index("idx_kb_tier", "tier"),
        Index("idx_kb_organization", "organization_id"),
        Index("idx_kb_department", "department_id"),
        Index("idx_kb_classification", "data_classification"),
        Index("idx_kb_status", "status"),
        Index("idx_kb_tier_org", "tier", "organization_id"),
        Index("idx_kb_storage_backend", "storage_backend"),
        Index("idx_kb_parent", "parent_kb_id"),
        Index("idx_kb_owner", "owner_id"),
    )


# =============================================================================
# Knowledge Categories
# =============================================================================


class KnowledgeCategoryModel(RegistryBase):
    """L1/L2 knowledge categories."""

    __tablename__ = "knowledge_categories"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuidlib.uuid4, server_default=sa.text("gen_random_uuid()"))
    level = Column(SmallInteger, nullable=False, default=1)
    parent_id = Column(PG_UUID(as_uuid=True), ForeignKey("knowledge_categories.id"), nullable=True)
    name = Column(String(50), nullable=False)
    name_ko = Column(String(50), nullable=True)
    description = Column(Text, nullable=True)
    keywords = Column(JSONB, nullable=False, default=list)
    sort_order = Column(SmallInteger, nullable=False, default=0)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True))
    updated_at = Column(DateTime(timezone=True))


# =============================================================================
# KB Search Groups (BU/Team scope grouping)
# =============================================================================


class OrganizationModel(RegistryBase):
    """Organization (tenant) — top-level grouping for users + KBs.

    SAML / SSO 통합 시 IdP 의 organization 또는 tenant 와 매핑.
    현재는 데이터 모델만 prep 상태 — 활성 사용은 Tenant + SAML 통합 후
    (별도 PR). UserModel.organization_id / KBConfigModel.organization_id 가
    여기 id 를 참조하도록 향후 FK 추가 가능.

    PII / 격리 원칙: 같은 organization 안의 사용자들만 같은 KB 접근 가능.
    Cross-tenant access 는 명시적 invite 또는 share 통해서만.
    """

    __tablename__ = "organizations"

    id = Column(String(100), primary_key=True)
    # URL-safe identifier — e.g., "acme-corp"
    slug = Column(String(100), nullable=False, unique=True, index=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)

    # SSO / SAML metadata (옵셔널 — 미통합 organization 도 허용)
    sso_provider = Column(String(50), nullable=True)  # "saml" | "oidc" | None
    sso_metadata = Column(JSONB, nullable=False, default=dict)  # IdP entityID, x509, etc.

    # Quotas / billing (점진 도입)
    max_users = Column(Integer, nullable=True)
    max_kbs = Column(Integer, nullable=True)
    max_storage_gb = Column(Integer, nullable=True)

    # Status — active / suspended / trial 등
    status = Column(String(20), nullable=False, default="active", index=True)
    settings = Column(JSONB, nullable=False, default=dict)

    created_at = Column(DateTime, nullable=False, default=_utc_now)
    updated_at = Column(DateTime, nullable=False, default=_utc_now, onupdate=_utc_now)

    __table_args__ = (
        Index("idx_organizations_status", "status"),
    )


class OrgMembershipModel(RegistryBase):
    """User ↔ Organization membership with role.

    한 사용자가 여러 organization 에 속할 수 있음 (consultant, multi-tenant scenarios).
    UserModel.organization_id 는 "기본 organization" 을 가리키고, 이 테이블은
    실제 권한 격리의 SSOT.
    """

    __tablename__ = "org_memberships"

    id = Column(String(36), primary_key=True)  # uuid
    user_id = Column(String(100), nullable=False, index=True)
    organization_id = Column(String(100), nullable=False, index=True)
    # admin | member | viewer
    role = Column(String(20), nullable=False, default="member")
    invited_by = Column(String(100), nullable=True)
    invited_at = Column(DateTime, nullable=False, default=_utc_now)
    joined_at = Column(DateTime, nullable=True)
    status = Column(String(20), nullable=False, default="active")  # active|invited|removed

    __table_args__ = (
        UniqueConstraint("user_id", "organization_id", name="uq_org_member_user_org"),
        Index("idx_org_membership_user", "user_id"),
        Index("idx_org_membership_org", "organization_id"),
    )


class KBSearchGroupModel(RegistryBase):
    """KB search groups for scoped cross-KB search.

    Users can create groups of KBs (e.g., by BU or team) and search
    only within those KBs. Enables scoped federated search.

    Example:
        - "CVS팀" group → [cvs-kb, infra-kb, miso-faq]
        - "홈쇼핑AX" group → [hs-kb, hax-kb, infra-kb]
        - "전체" group → [all KBs]
    """

    __tablename__ = "kb_search_groups"

    id = Column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuidlib.uuid4,
        server_default=sa.text("gen_random_uuid()"),
    )
    name = Column(String(100), nullable=False, unique=True)
    description = Column(Text, nullable=True)
    kb_ids = Column(JSONB, nullable=False, default=list)  # ["cvs-kb", "infra-kb"]
    is_default = Column(Boolean, nullable=False, default=False)
    created_by = Column(String(100), nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


# =============================================================================
# Graph Schema Evolution (Phase 3)
# =============================================================================


class SchemaCandidateModel(KnowledgeBase):
    """LLM-discovered entity/relationship type candidates (pre-approval).

    Spec §4.3. Unique on (kb_id, candidate_type, label).
    """

    __tablename__ = "graph_schema_candidates"
    __table_args__ = (
        UniqueConstraint(
            "kb_id", "candidate_type", "label",
            name="uq_schema_candidates_kb_type_label",
        ),
        Index("ix_schema_candidates_kb_status", "kb_id", "status", "frequency"),
    )

    id = Column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuidlib.uuid4,
        server_default=sa.text("gen_random_uuid()"),
    )
    kb_id = Column(String(64), nullable=False)
    candidate_type = Column(String(16), nullable=False)  # 'node'|'relationship'
    label = Column(String(64), nullable=False)
    frequency = Column(Integer, nullable=False, default=1)
    confidence_avg = Column(Float, nullable=False)
    confidence_min = Column(Float, nullable=False)
    confidence_max = Column(Float, nullable=False)
    source_label = Column(String(64), nullable=True)
    target_label = Column(String(64), nullable=True)
    examples = Column(JSONB, nullable=False, default=list)
    status = Column(String(16), nullable=False, default="pending")
    merged_into = Column(String(64), nullable=True)
    rejected_reason = Column(Text, nullable=True)
    similar_labels = Column(JSONB, nullable=False, default=list)
    first_seen_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=sa.func.now(),
    )
    last_seen_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=sa.func.now(),
    )
    decided_at = Column(DateTime(timezone=True), nullable=True)
    decided_by = Column(String(128), nullable=True)


class BootstrapRunModel(KnowledgeBase):
    """Audit log + concurrent-safety anchor for schema bootstrap runs.

    Spec §4.3 + §6.5. ``status='running'`` + ``started_at > now() - 1h``
    rows block concurrent runs for the same kb_id.
    """

    __tablename__ = "graph_schema_bootstrap_runs"
    __table_args__ = (
        Index("ix_bootstrap_runs_kb_time", "kb_id", "started_at"),
    )

    id = Column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuidlib.uuid4,
        server_default=sa.text("gen_random_uuid()"),
    )
    kb_id = Column(String(64), nullable=False)
    # running|completed|failed|cancelled
    status = Column(String(16), nullable=False)
    # cron|kb_create|manual|volume_threshold
    triggered_by = Column(String(32), nullable=False)
    triggered_by_user = Column(String(128), nullable=True)
    sample_size = Column(Integer, nullable=False)
    sample_strategy = Column(String(16), nullable=False)  # stratified|random
    docs_scanned = Column(Integer, default=0)
    candidates_found = Column(Integer, default=0)
    llm_calls = Column(Integer, default=0)
    error_message = Column(Text, nullable=True)
    started_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=sa.func.now(),
    )
    completed_at = Column(DateTime(timezone=True), nullable=True)
    duration_ms = Column(Integer, nullable=True)


class ReextractJobModel(KnowledgeBase):
    """On-demand re-extract job queue (Phase 5 consumer).

    Phase 3 only creates the table so Phase 5 can push rows without a
    second DB migration.
    """

    __tablename__ = "graph_schema_reextract_jobs"
    __table_args__ = (
        Index("ix_reextract_jobs_kb", "kb_id", "queued_at"),
    )

    id = Column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuidlib.uuid4,
        server_default=sa.text("gen_random_uuid()"),
    )
    kb_id = Column(String(64), nullable=False)
    triggered_by_user = Column(String(128), nullable=False)
    schema_version_from = Column(Integer, nullable=False)
    schema_version_to = Column(Integer, nullable=False)
    status = Column(String(16), nullable=False, default="queued")
    docs_total = Column(Integer, nullable=True)
    docs_processed = Column(Integer, default=0)
    docs_failed = Column(Integer, default=0)
    error_message = Column(Text, nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    queued_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=sa.func.now(),
    )
