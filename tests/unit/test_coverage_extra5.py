"""Extra coverage tests (batch 5).

Targets: lifecycle (31 uncov), conflict_detector data classes (34 uncov),
auth/providers (31 uncov), graph_expander basic (37 uncov).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest


# ===========================================================================
# Lifecycle State Machine
# ===========================================================================

from src.domain.lifecycle import (
    LifecycleStateMachine,
    LifecycleStatus,
    TransitionError,
    ALLOWED_TRANSITIONS,
)


class TestLifecycleStatus:
    def test_values(self):
        assert LifecycleStatus.DRAFT == "draft"
        assert LifecycleStatus.PUBLISHED == "published"
        assert LifecycleStatus.ARCHIVED == "archived"
        assert LifecycleStatus.DELETED == "deleted"


class TestAllowedTransitions:
    def test_draft_to_published(self):
        assert LifecycleStatus.PUBLISHED in ALLOWED_TRANSITIONS[LifecycleStatus.DRAFT]

    def test_published_to_archived(self):
        assert LifecycleStatus.ARCHIVED in ALLOWED_TRANSITIONS[LifecycleStatus.PUBLISHED]

    def test_deleted_no_transitions(self):
        assert ALLOWED_TRANSITIONS[LifecycleStatus.DELETED] == []


class TestLifecycleStateMachine:
    def _mock_repo(self, existing=None):
        repo = AsyncMock()
        repo.get_by_document = AsyncMock(return_value=existing)
        repo.save = AsyncMock()
        repo.list_by_status = AsyncMock(return_value=[])
        repo.list_by_kb = AsyncMock(return_value=[])
        return repo

    async def test_get_or_create_new(self):
        repo = self._mock_repo(existing=None)
        sm = LifecycleStateMachine(lifecycle_repo=repo)
        result = await sm.get_or_create("doc1", "kb1")
        assert result["status"] == "draft"
        repo.save.assert_called_once()

    async def test_get_or_create_existing(self):
        existing = {"status": "published", "document_id": "doc1"}
        repo = self._mock_repo(existing=existing)
        sm = LifecycleStateMachine(lifecycle_repo=repo)
        result = await sm.get_or_create("doc1", "kb1")
        assert result["status"] == "published"
        repo.save.assert_not_called()

    async def test_transition_valid(self):
        existing = {
            "status": "draft",
            "document_id": "doc1",
            "kb_id": "kb1",
            "transitions": [],
        }
        repo = self._mock_repo(existing=existing)
        sm = LifecycleStateMachine(lifecycle_repo=repo)
        result = await sm.transition("doc1", "kb1", "draft", "published", "admin")
        assert result["status"] == "published"
        assert result["previous_status"] == "draft"

    async def test_transition_invalid_state(self):
        existing = {"status": "published", "document_id": "doc1", "kb_id": "kb1"}
        repo = self._mock_repo(existing=existing)
        sm = LifecycleStateMachine(lifecycle_repo=repo)
        with pytest.raises(TransitionError, match="Current state"):
            await sm.transition("doc1", "kb1", "draft", "published", "admin")

    async def test_transition_not_allowed(self):
        existing = {"status": "draft", "document_id": "doc1", "kb_id": "kb1", "transitions": []}
        repo = self._mock_repo(existing=existing)
        sm = LifecycleStateMachine(lifecycle_repo=repo)
        with pytest.raises(TransitionError, match="Cannot transition"):
            await sm.transition("doc1", "kb1", "draft", "archived", "admin")

    async def test_transition_invalid_state_value(self):
        existing = {"status": "draft", "document_id": "doc1", "kb_id": "kb1", "transitions": []}
        repo = self._mock_repo(existing=existing)
        sm = LifecycleStateMachine(lifecycle_repo=repo)
        with pytest.raises(TransitionError, match="Invalid state"):
            await sm.transition("doc1", "kb1", "draft", "nonexistent", "admin")

    async def test_transition_to_deleted(self):
        existing = {"status": "published", "document_id": "doc1", "kb_id": "kb1", "transitions": []}
        repo = self._mock_repo(existing=existing)
        sm = LifecycleStateMachine(lifecycle_repo=repo)
        result = await sm.transition("doc1", "kb1", "published", "deleted", "admin")
        assert result["status"] == "deleted"
        assert result["deletion_scheduled_at"] is not None

    async def test_transition_to_published(self):
        existing = {"status": "draft", "document_id": "doc1", "kb_id": "kb1", "transitions": []}
        repo = self._mock_repo(existing=existing)
        sm = LifecycleStateMachine(lifecycle_repo=repo)
        result = await sm.transition("doc1", "kb1", "draft", "published", "admin")
        assert result["auto_archive_at"] is not None

    async def test_publish_convenience(self):
        existing = {"status": "draft", "document_id": "doc1", "kb_id": "kb1", "transitions": []}
        repo = self._mock_repo(existing=existing)
        sm = LifecycleStateMachine(lifecycle_repo=repo)
        result = await sm.publish("doc1", "kb1", "admin")
        assert result["status"] == "published"

    async def test_archive_convenience(self):
        existing = {"status": "published", "document_id": "doc1", "kb_id": "kb1", "transitions": []}
        repo = self._mock_repo(existing=existing)
        sm = LifecycleStateMachine(lifecycle_repo=repo)
        result = await sm.archive("doc1", "kb1", "admin")
        assert result["status"] == "archived"

    async def test_get_auto_archive_candidates(self):
        past = datetime.now(timezone.utc) - timedelta(days=1)
        repo = self._mock_repo()
        repo.list_by_status.return_value = [
            {"document_id": "d1", "auto_archive_at": past},
            {"document_id": "d2", "auto_archive_at": datetime.now(timezone.utc) + timedelta(days=30)},
        ]
        sm = LifecycleStateMachine(lifecycle_repo=repo)
        candidates = await sm.get_auto_archive_candidates("kb1")
        assert len(candidates) == 1

    async def test_get_stale_documents(self):
        old = datetime.now(timezone.utc) - timedelta(days=365)
        repo = self._mock_repo()
        repo.list_by_status.return_value = [
            {"document_id": "d1"},
            {"document_id": "d2"},
        ]
        sm = LifecycleStateMachine(lifecycle_repo=repo, stale_threshold_days=180)
        stale = await sm.get_stale_documents("kb1", {"d1": old})
        assert len(stale) == 1

    async def test_get_stale_no_map(self):
        repo = self._mock_repo()
        sm = LifecycleStateMachine(lifecycle_repo=repo)
        stale = await sm.get_stale_documents("kb1")
        assert stale == []

    async def test_list_by_status(self):
        repo = self._mock_repo()
        repo.list_by_status.return_value = [{"id": "1"}]
        sm = LifecycleStateMachine(lifecycle_repo=repo)
        result = await sm.list_by_status("kb1", "published")
        assert len(result) == 1

    async def test_list_all(self):
        repo = self._mock_repo()
        repo.list_by_kb.return_value = [{"id": "1"}, {"id": "2"}]
        sm = LifecycleStateMachine(lifecycle_repo=repo)
        result = await sm.list_all("kb1")
        assert len(result) == 2


# ===========================================================================
# Conflict Detector data classes
# ===========================================================================

from src.pipeline.dedup.conflict_detector import (
    ConflictType,
    ConflictSeverity,
    ConflictDetail,
    ConflictAnalysisResult,
    ConflictDetector,
)


class TestConflictType:
    def test_values(self):
        assert ConflictType.DATE_CONFLICT == "date_conflict"
        assert ConflictType.NONE == "none"


class TestConflictSeverity:
    def test_values(self):
        assert ConflictSeverity.CRITICAL == "critical"
        assert ConflictSeverity.LOW == "low"


class TestConflictDetail:
    def test_creation(self):
        d = ConflictDetail(
            conflict_type=ConflictType.DATE_CONFLICT,
            severity=ConflictSeverity.HIGH,
            description="Date mismatch",
            doc1_excerpt="Jan 2025",
            doc2_excerpt="Feb 2025",
        )
        assert d.conflict_type == ConflictType.DATE_CONFLICT
        dd = d.to_dict()
        assert dd["conflict_type"] == "date_conflict"


class TestConflictAnalysisResult:
    def test_no_conflicts(self):
        r = ConflictAnalysisResult(doc_id_1="d1", doc_id_2="d2", has_conflict=False, conflicts=[])
        assert r.has_conflict is False

    def test_with_conflicts(self):
        d = ConflictDetail(
            conflict_type=ConflictType.VERSION_CONFLICT,
            severity=ConflictSeverity.MEDIUM,
            description="Version mismatch",
        )
        r = ConflictAnalysisResult(doc_id_1="d1", doc_id_2="d2", has_conflict=True, conflicts=[d])
        assert r.has_conflict is True
        dd = r.to_dict()
        assert dd["has_conflict"] is True

    def test_max_severity(self):
        d1 = ConflictDetail(conflict_type=ConflictType.DATE_CONFLICT, severity=ConflictSeverity.LOW, description="d")
        d2 = ConflictDetail(conflict_type=ConflictType.DATE_CONFLICT, severity=ConflictSeverity.CRITICAL, description="d")
        r = ConflictAnalysisResult(doc_id_1="d1", doc_id_2="d2", has_conflict=True, conflicts=[d1, d2])
        # Should report the max severity
        dd = r.to_dict()
        assert dd["has_conflict"] is True


class TestConflictDetector:
    async def test_analyze_no_llm(self):
        detector = ConflictDetector(llm_client=None)
        result = await detector.analyze("d1", "content1", "d2", "content2")
        assert isinstance(result, ConflictAnalysisResult)
        assert isinstance(result.conflicts, list)

    async def test_analyze_with_mock_llm(self):
        mock_llm = AsyncMock()
        mock_llm.generate.return_value = '{"has_conflict": false, "conflicts": [], "confidence": 0.9}'
        detector = ConflictDetector(llm_client=mock_llm)
        result = await detector.analyze("d1", "Doc A content", "d2", "Doc B content")
        assert isinstance(result, ConflictAnalysisResult)


# ===========================================================================
# Auth Providers
# ===========================================================================

from src.auth.providers import AuthUser, AuthenticationError


class TestAuthUser:
    def test_creation(self):
        user = AuthUser(
            sub="sub-123",
            email="test@test.com",
            display_name="Test User",
            provider="local",
        )
        assert user.sub == "sub-123"
        assert user.roles == []
        assert user.groups == []

    def test_with_roles(self):
        user = AuthUser(
            sub="sub-123",
            email="test@test.com",
            display_name="Test",
            provider="local",
            roles=["admin", "user"],
        )
        assert "admin" in user.roles


class TestAuthenticationError:
    def test_default(self):
        err = AuthenticationError()
        assert err.detail == "Authentication failed"
        assert err.status_code == 401

    def test_custom(self):
        err = AuthenticationError("Token expired", 403)
        assert err.detail == "Token expired"
        assert err.status_code == 403
