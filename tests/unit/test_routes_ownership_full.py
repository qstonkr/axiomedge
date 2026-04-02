"""Unit tests for src/api/routes/ownership.py — comprehensive coverage."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Pre-import to avoid circular import issues
import src.api.app  # noqa: F401
from src.api.routes import ownership as own_mod


# ============================================================================
# Helpers
# ============================================================================

def _run(coro):
    return asyncio.run(coro)


def _mock_state(**overrides) -> MagicMock:
    state = MagicMock()
    _map: dict[str, Any] = {}
    _map.update(overrides)
    state.get = lambda k, default=None: _map.get(k, default)
    return state


# ============================================================================
# List Document Owners
# ============================================================================

class TestListDocumentOwners:
    def test_list_with_repo(self):
        repo = AsyncMock()
        repo.get_by_kb = AsyncMock(return_value=[{"document_id": "d1", "owner_user_id": "u1"}])
        with patch.object(own_mod, "_get_state", return_value=_mock_state(doc_owner_repo=repo)):
            result = _run(own_mod.list_document_owners(kb_id="kb1"))
        assert result["total"] == 1
        assert result["kb_id"] == "kb1"

    def test_list_no_repo(self):
        with patch.object(own_mod, "_get_state", return_value=_mock_state()):
            result = _run(own_mod.list_document_owners(kb_id="kb1"))
        assert result["total"] == 0

    def test_list_exception(self):
        repo = AsyncMock()
        repo.get_by_kb = AsyncMock(side_effect=RuntimeError("db"))
        with patch.object(own_mod, "_get_state", return_value=_mock_state(doc_owner_repo=repo)):
            result = _run(own_mod.list_document_owners(kb_id="kb1"))
        assert result["total"] == 0


# ============================================================================
# Get Document Owner
# ============================================================================

class TestGetDocumentOwner:
    def test_get_found(self):
        repo = AsyncMock()
        repo.get_by_document = AsyncMock(return_value={"document_id": "d1", "owner_user_id": "u1"})
        with patch.object(own_mod, "_get_state", return_value=_mock_state(doc_owner_repo=repo)):
            result = _run(own_mod.get_document_owner("d1", kb_id="kb1"))
        assert result["owner_user_id"] == "u1"

    def test_get_not_found(self):
        repo = AsyncMock()
        repo.get_by_document = AsyncMock(return_value=None)
        with patch.object(own_mod, "_get_state", return_value=_mock_state(doc_owner_repo=repo)):
            result = _run(own_mod.get_document_owner("d1", kb_id="kb1"))
        assert result["status"] == "unassigned"

    def test_get_no_repo(self):
        with patch.object(own_mod, "_get_state", return_value=_mock_state()):
            result = _run(own_mod.get_document_owner("d1", kb_id="kb1"))
        assert result["status"] == "unassigned"


# ============================================================================
# Assign Document Owner
# ============================================================================

class TestAssignDocumentOwner:
    def test_assign_with_repo(self):
        repo = AsyncMock()
        repo.save = AsyncMock()
        with patch.object(own_mod, "_get_state", return_value=_mock_state(doc_owner_repo=repo)):
            result = _run(own_mod.assign_document_owner({"document_id": "d1", "owner_user_id": "u1"}))
        assert result["success"] is True

    def test_assign_no_repo(self):
        with patch.object(own_mod, "_get_state", return_value=_mock_state()):
            result = _run(own_mod.assign_document_owner({"document_id": "d1"}))
        assert result["success"] is True
        assert "stub" in result["message"]

    def test_assign_repo_exception(self):
        repo = AsyncMock()
        repo.save = AsyncMock(side_effect=RuntimeError("db"))
        with patch.object(own_mod, "_get_state", return_value=_mock_state(doc_owner_repo=repo)):
            with pytest.raises(Exception) as exc_info:
                _run(own_mod.assign_document_owner({"document_id": "d1"}))
            assert exc_info.value.status_code == 500


# ============================================================================
# Transfer Ownership
# ============================================================================

class TestTransferOwnership:
    def test_transfer_success(self):
        repo = AsyncMock()
        repo.get_by_document = AsyncMock(return_value={
            "document_id": "d1", "owner_user_id": "u1",
            "backup_owner_user_id": "u2", "ownership_type": "assigned",
        })
        repo.save = AsyncMock()
        with patch.object(own_mod, "_get_state", return_value=_mock_state(doc_owner_repo=repo)):
            result = _run(own_mod.transfer_ownership("d1", {"kb_id": "kb1", "new_owner_user_id": "u3"}))
        assert result["success"] is True

    def test_transfer_not_found(self):
        repo = AsyncMock()
        repo.get_by_document = AsyncMock(return_value=None)
        with patch.object(own_mod, "_get_state", return_value=_mock_state(doc_owner_repo=repo)):
            with pytest.raises(Exception) as exc_info:
                _run(own_mod.transfer_ownership("d1", {"kb_id": "kb1"}))
            assert exc_info.value.status_code == 404

    def test_transfer_no_repo(self):
        with patch.object(own_mod, "_get_state", return_value=_mock_state()):
            result = _run(own_mod.transfer_ownership("d1", {}))
        assert result["success"] is True
        assert "stub" in result["message"]

    def test_transfer_save_exception(self):
        repo = AsyncMock()
        repo.get_by_document = AsyncMock(return_value={
            "document_id": "d1", "owner_user_id": "u1", "ownership_type": "assigned",
        })
        repo.save = AsyncMock(side_effect=RuntimeError("db"))
        with patch.object(own_mod, "_get_state", return_value=_mock_state(doc_owner_repo=repo)):
            with pytest.raises(Exception) as exc_info:
                _run(own_mod.transfer_ownership("d1", {"kb_id": "kb1"}))
            assert exc_info.value.status_code == 500


# ============================================================================
# Verify Document Owner
# ============================================================================

class TestVerifyDocumentOwner:
    def test_verify_success(self):
        repo = AsyncMock()
        repo.get_by_document = AsyncMock(return_value={"document_id": "d1", "owner_user_id": "u1"})
        repo.save = AsyncMock()
        with patch.object(own_mod, "_get_state", return_value=_mock_state(doc_owner_repo=repo)):
            result = _run(own_mod.verify_document_owner("d1", {"kb_id": "kb1"}))
        assert result["success"] is True

    def test_verify_not_found(self):
        repo = AsyncMock()
        repo.get_by_document = AsyncMock(return_value=None)
        with patch.object(own_mod, "_get_state", return_value=_mock_state(doc_owner_repo=repo)):
            with pytest.raises(Exception) as exc_info:
                _run(own_mod.verify_document_owner("d1", {"kb_id": "kb1"}))
            assert exc_info.value.status_code == 404

    def test_verify_no_repo(self):
        with patch.object(own_mod, "_get_state", return_value=_mock_state()):
            result = _run(own_mod.verify_document_owner("d1", {}))
        assert result["success"] is True
        assert "stub" in result["message"]


# ============================================================================
# Stale Owners
# ============================================================================

class TestStaleOwners:
    def test_stale_with_repo(self):
        repo = AsyncMock()
        repo.get_by_kb = AsyncMock(return_value=[
            {"document_id": "d1", "updated_at": "2020-01-01T00:00:00"},
            {"document_id": "d2", "updated_at": "2099-01-01T00:00:00"},
        ])
        with patch.object(own_mod, "_get_state", return_value=_mock_state(doc_owner_repo=repo)):
            result = _run(own_mod.get_stale_owners(kb_id="kb1", days_threshold=90))
        assert result["total"] == 1  # only d1 is stale
        assert result["stale_owners"][0]["document_id"] == "d1"

    def test_stale_no_updated_at(self):
        repo = AsyncMock()
        repo.get_by_kb = AsyncMock(return_value=[
            {"document_id": "d1", "updated_at": None},
        ])
        with patch.object(own_mod, "_get_state", return_value=_mock_state(doc_owner_repo=repo)):
            result = _run(own_mod.get_stale_owners(kb_id="kb1"))
        assert result["total"] == 0

    def test_stale_no_repo(self):
        with patch.object(own_mod, "_get_state", return_value=_mock_state()):
            result = _run(own_mod.get_stale_owners(kb_id="kb1"))
        assert result["total"] == 0

    def test_stale_exception(self):
        repo = AsyncMock()
        repo.get_by_kb = AsyncMock(side_effect=RuntimeError("db"))
        with patch.object(own_mod, "_get_state", return_value=_mock_state(doc_owner_repo=repo)):
            result = _run(own_mod.get_stale_owners(kb_id="kb1"))
        assert result["total"] == 0


# ============================================================================
# Owner Availability
# ============================================================================

class TestOwnerAvailability:
    def test_get_availability_with_repo(self):
        repo = AsyncMock()
        repo.get_by_owner = AsyncMock(return_value=[{"doc_id": "d1"}, {"doc_id": "d2"}])
        with patch.object(own_mod, "_get_state", return_value=_mock_state(doc_owner_repo=repo)):
            result = _run(own_mod.get_owner_availability("u1"))
        assert result["owned_documents"] == 2
        assert result["available"] is True

    def test_get_availability_no_repo(self):
        with patch.object(own_mod, "_get_state", return_value=_mock_state()):
            result = _run(own_mod.get_owner_availability("u1"))
        assert result["owned_documents"] == 0

    def test_get_availability_exception(self):
        repo = AsyncMock()
        repo.get_by_owner = AsyncMock(side_effect=RuntimeError("db"))
        with patch.object(own_mod, "_get_state", return_value=_mock_state(doc_owner_repo=repo)):
            result = _run(own_mod.get_owner_availability("u1"))
        assert result["owned_documents"] == 0


class TestUpdateOwnerAvailability:
    def test_update_with_repo(self):
        repo = MagicMock()
        with patch.object(own_mod, "_get_state", return_value=_mock_state(doc_owner_repo=repo)):
            result = _run(own_mod.update_owner_availability("u1", {"available": True}))
        assert result["success"] is True

    def test_update_no_repo(self):
        with patch.object(own_mod, "_get_state", return_value=_mock_state()):
            result = _run(own_mod.update_owner_availability("u1", {}))
        assert result["success"] is True


# ============================================================================
# Topic Owners
# ============================================================================

class TestTopicOwners:
    def test_list_with_repo(self):
        repo = AsyncMock()
        repo.get_by_kb = AsyncMock(return_value=[{"topic_name": "K8s", "sme_user_id": "u1"}])
        with patch.object(own_mod, "_get_state", return_value=_mock_state(topic_owner_repo=repo)):
            result = _run(own_mod.list_topic_owners(kb_id="kb1"))
        assert result["total"] == 1

    def test_list_no_repo(self):
        with patch.object(own_mod, "_get_state", return_value=_mock_state()):
            result = _run(own_mod.list_topic_owners(kb_id="kb1"))
        assert result["total"] == 0

    def test_list_exception(self):
        repo = AsyncMock()
        repo.get_by_kb = AsyncMock(side_effect=RuntimeError("db"))
        with patch.object(own_mod, "_get_state", return_value=_mock_state(topic_owner_repo=repo)):
            result = _run(own_mod.list_topic_owners(kb_id="kb1"))
        assert result["total"] == 0


class TestAssignTopicOwner:
    def test_assign_with_repo(self):
        repo = AsyncMock()
        repo.save = AsyncMock()
        with patch.object(own_mod, "_get_state", return_value=_mock_state(topic_owner_repo=repo)):
            result = _run(own_mod.assign_topic_owner({"topic_name": "K8s", "sme_user_id": "u1"}))
        assert result["success"] is True

    def test_assign_no_repo(self):
        with patch.object(own_mod, "_get_state", return_value=_mock_state()):
            result = _run(own_mod.assign_topic_owner({}))
        assert result["success"] is True
        assert "stub" in result["message"]

    def test_assign_exception(self):
        repo = AsyncMock()
        repo.save = AsyncMock(side_effect=RuntimeError("db"))
        with patch.object(own_mod, "_get_state", return_value=_mock_state(topic_owner_repo=repo)):
            with pytest.raises(Exception) as exc_info:
                _run(own_mod.assign_topic_owner({}))
            assert exc_info.value.status_code == 500


# ============================================================================
# Search Experts
# ============================================================================

class TestSearchExperts:
    def test_search_with_match(self):
        repo = AsyncMock()
        repo.get_by_kb = AsyncMock(return_value=[
            {"topic_name": "Kubernetes", "topic_keywords": ["k8s", "container"], "sme_user_id": "u1", "kb_id": "kb1"},
            {"topic_name": "Database", "topic_keywords": ["sql", "postgres"], "sme_user_id": "u2", "kb_id": "kb1"},
        ])
        with patch.object(own_mod, "_get_state", return_value=_mock_state(topic_owner_repo=repo)):
            result = _run(own_mod.search_experts(query="k8s", kb_id="kb1"))
        assert result["total"] == 1
        assert result["experts"][0]["user_id"] == "u1"

    def test_search_topic_name_match(self):
        repo = AsyncMock()
        repo.get_by_kb = AsyncMock(return_value=[
            {"topic_name": "Kubernetes", "topic_keywords": [], "sme_user_id": "u1", "kb_id": "kb1"},
        ])
        with patch.object(own_mod, "_get_state", return_value=_mock_state(topic_owner_repo=repo)):
            result = _run(own_mod.search_experts(query="kube", kb_id="kb1"))
        assert result["total"] == 1

    def test_search_no_match(self):
        repo = AsyncMock()
        repo.get_by_kb = AsyncMock(return_value=[
            {"topic_name": "Database", "topic_keywords": ["sql"], "sme_user_id": "u1", "kb_id": "kb1"},
        ])
        with patch.object(own_mod, "_get_state", return_value=_mock_state(topic_owner_repo=repo)):
            result = _run(own_mod.search_experts(query="networking", kb_id="kb1"))
        assert result["total"] == 0

    def test_search_no_kb_id(self):
        with patch.object(own_mod, "_get_state", return_value=_mock_state()):
            result = _run(own_mod.search_experts(query="test"))
        assert result["total"] == 0

    def test_search_no_repo(self):
        with patch.object(own_mod, "_get_state", return_value=_mock_state()):
            result = _run(own_mod.search_experts(query="test", kb_id="kb1"))
        assert result["total"] == 0

    def test_search_dedup_users(self):
        repo = AsyncMock()
        repo.get_by_kb = AsyncMock(return_value=[
            {"topic_name": "K8s", "topic_keywords": ["k8s"], "sme_user_id": "u1", "kb_id": "kb1"},
            {"topic_name": "Container", "topic_keywords": ["k8s"], "sme_user_id": "u1", "kb_id": "kb1"},
        ])
        with patch.object(own_mod, "_get_state", return_value=_mock_state(topic_owner_repo=repo)):
            result = _run(own_mod.search_experts(query="k8s", kb_id="kb1"))
        assert result["total"] == 1  # Same user deduped

    def test_search_exception(self):
        repo = AsyncMock()
        repo.get_by_kb = AsyncMock(side_effect=RuntimeError("db"))
        with patch.object(own_mod, "_get_state", return_value=_mock_state(topic_owner_repo=repo)):
            result = _run(own_mod.search_experts(query="test", kb_id="kb1"))
        assert result["total"] == 0
