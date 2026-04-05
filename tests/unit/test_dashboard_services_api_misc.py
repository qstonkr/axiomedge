"""Unit tests for dashboard/services/api/misc.py."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx

# Make dashboard modules importable

_st_mock = MagicMock()
_st_mock.cache_data = lambda **kw: lambda f: f
sys.modules.setdefault("streamlit", _st_mock)

from services.api import misc


# ===========================================================================
# Ownership
# ===========================================================================

class TestOwnership:
    def test_list_document_owners(self):
        with patch("services.api.misc._get", return_value={"items": []}) as m:
            misc.list_document_owners("kb1", status="active")
            m.assert_called_once_with("/api/v1/admin/ownership/documents", kb_id="kb1", status="active")

    def test_get_document_owner(self):
        with patch("services.api.misc._get", return_value={}) as m:
            misc.get_document_owner("doc1", "kb1")
            m.assert_called_once_with("/api/v1/admin/ownership/documents/doc1", kb_id="kb1")

    def test_assign_document_owner(self):
        with patch("services.api.misc._post", return_value={}) as m:
            misc.assign_document_owner({"doc_id": "d1", "owner": "u1"})
            m.assert_called_once()

    def test_transfer_ownership(self):
        with patch("services.api.misc._post", return_value={}) as m:
            misc.transfer_ownership("doc1", {"new_owner": "u2"})
            m.assert_called_once_with("/api/v1/admin/ownership/documents/doc1/transfer", {"new_owner": "u2"})

    def test_verify_document_owner(self):
        with patch("services.api.misc._post", return_value={}) as m:
            misc.verify_document_owner("doc1", {"verified": True})
            m.assert_called_once()

    def test_get_stale_owners(self):
        with patch("services.api.misc._get", return_value={}) as m:
            misc.get_stale_owners("kb1", days_threshold=60)
            m.assert_called_once_with("/api/v1/admin/ownership/stale", kb_id="kb1", days_threshold=60)

    def test_get_owner_availability(self):
        with patch("services.api.misc._get", return_value={}) as m:
            misc.get_owner_availability("user1")
            m.assert_called_once_with("/api/v1/admin/ownership/availability/user1")

    def test_update_owner_availability(self):
        with patch("services.api.misc._put", return_value={}) as m:
            misc.update_owner_availability("user1", {"available": True})
            m.assert_called_once()

    def test_list_topic_owners(self):
        with patch("services.api.misc._get", return_value={}) as m:
            misc.list_topic_owners("kb1")
            m.assert_called_once_with("/api/v1/admin/ownership/topics", kb_id="kb1")

    def test_assign_topic_owner(self):
        with patch("services.api.misc._post", return_value={}) as m:
            misc.assign_topic_owner({"topic": "t1", "owner": "u1"})
            m.assert_called_once()

    def test_get_owner_search(self):
        with patch("services.api.misc._get", return_value={}) as m:
            misc.get_owner_search("alice", kb_id="kb1")
            m.assert_called_once()


# ===========================================================================
# Error Reports
# ===========================================================================

class TestErrorReports:
    def test_list_error_reports(self):
        with patch("services.api.misc._get", return_value={}) as m:
            misc.list_error_reports(kb_id="kb1", status="open", page=2, page_size=10)
            m.assert_called_once()
            kwargs = m.call_args
            assert kwargs[1]["page"] == 2

    def test_get_error_report(self):
        with patch("services.api.misc._get", return_value={}) as m:
            misc.get_error_report("r1")
            m.assert_called_once_with("/api/v1/admin/error-reports/r1")

    def test_create_error_report(self):
        with patch("services.api.misc._post", return_value={}) as m:
            misc.create_error_report({"message": "bug"})
            m.assert_called_once()

    def test_get_error_report_statistics(self):
        with patch("services.api.misc._get", return_value={}) as m:
            misc.get_error_report_statistics(kb_id="kb1", days=7)
            m.assert_called_once()

    def test_resolve_error_report(self):
        with patch("services.api.misc._post", return_value={}) as m:
            misc.resolve_error_report("r1", {"resolution": "fixed"})
            m.assert_called_once_with("/api/v1/admin/error-reports/r1/resolve", {"resolution": "fixed"})

    def test_reject_error_report(self):
        with patch("services.api.misc._post", return_value={}) as m:
            misc.reject_error_report("r1", {"reason": "invalid"})
            m.assert_called_once()

    def test_escalate_error_report(self):
        with patch("services.api.misc._post", return_value={}) as m:
            misc.escalate_error_report("r1", {"priority": "high"})
            m.assert_called_once()


# ===========================================================================
# Feedback
# ===========================================================================

class TestFeedback:
    def test_list_feedback(self):
        with patch("services.api.misc._get", return_value={}) as m:
            misc.list_feedback(status="pending", feedback_type="bug")
            m.assert_called_once()

    def test_create_feedback(self):
        with patch("services.api.misc._post", return_value={}) as m:
            misc.create_feedback({"message": "great"})
            m.assert_called_once()

    def test_update_feedback_valid(self):
        with patch("services.api.misc._patch", return_value={}) as m:
            misc.update_feedback("f1", {"status": "resolved"})
            m.assert_called_once_with("/api/v1/admin/feedback/f1", {"status": "resolved"})

    def test_update_feedback_empty_id(self):
        result = misc.update_feedback("", {"status": "resolved"})
        assert result["_api_failed"] is True
        assert "non-empty" in result["error"]

    def test_get_feedback_stats(self):
        with patch("services.api.misc._get", return_value={}) as m:
            misc.get_feedback_stats()
            m.assert_called_once_with("/api/v1/admin/feedback/stats")

    def test_get_feedback_workflow_stats(self):
        with patch("services.api.misc._get", return_value={}) as m:
            misc.get_feedback_workflow_stats()
            m.assert_called_once()

    def test_get_learning_artifacts(self):
        with patch("services.api.misc._get", return_value={}) as m:
            misc.get_learning_artifacts(page=2, page_size=10)
            m.assert_called_once()


# ===========================================================================
# Knowledge Ingestion
# ===========================================================================

class TestIngestion:
    def test_list_ingestion_runs(self):
        with patch("services.api.misc._get", return_value={}) as m:
            misc.list_ingestion_runs(kb_id="kb1")
            m.assert_called_once()

    def test_get_ingestion_run(self):
        with patch("services.api.misc._get", return_value={}) as m:
            misc.get_ingestion_run("run1")
            m.assert_called_once_with("/api/v1/admin/knowledge/ingest/status/run1")

    def test_trigger_ingestion(self):
        with patch("services.api.misc._post", return_value={}) as m:
            misc.trigger_ingestion({"kb_id": "kb1"})
            m.assert_called_once()

    def test_cancel_ingestion_valid(self):
        with patch("services.api.misc._post", return_value={}) as m:
            misc.cancel_ingestion("run1")
            m.assert_called_once()

    def test_cancel_ingestion_empty_id(self):
        result = misc.cancel_ingestion("")
        assert result["_api_failed"] is True

    def test_get_ingestion_stats(self):
        with patch("services.api.misc._get", return_value={}) as m:
            misc.get_ingestion_stats(kb_id="kb1")
            m.assert_called_once()

    def test_list_ingestion_schedules(self):
        with patch("services.api.misc._get", return_value={}) as m:
            misc.list_ingestion_schedules()
            m.assert_called_once()


# ===========================================================================
# Traceability
# ===========================================================================

class TestTraceability:
    def test_get_document_provenance(self):
        with patch("services.api.misc._get", return_value={}) as m:
            misc.get_document_provenance("doc1")
            m.assert_called_once_with("/api/v1/admin/knowledge/doc1/provenance")

    def test_get_document_lineage(self):
        with patch("services.api.misc._get", return_value={}) as m:
            misc.get_document_lineage("doc1")
            m.assert_called_once_with("/api/v1/admin/knowledge/doc1/lineage")

    def test_get_document_versions(self):
        with patch("services.api.misc._get", return_value={}) as m:
            misc.get_document_versions("doc1")
            m.assert_called_once_with("/api/v1/admin/knowledge/doc1/versions")


# ===========================================================================
# Data Sources
# ===========================================================================

class TestDataSources:
    def test_list_data_sources(self):
        with patch("services.api.misc._get", return_value={}) as m:
            misc.list_data_sources()
            m.assert_called_once()

    def test_get_data_source(self):
        with patch("services.api.misc._get", return_value={}) as m:
            misc.get_data_source("src1")
            m.assert_called_once_with("/api/v1/admin/data-sources/src1")

    def test_create_data_source(self):
        with patch("services.api.misc._post", return_value={}) as m:
            misc.create_data_source({"name": "s1"})
            m.assert_called_once()

    def test_update_data_source_valid(self):
        with patch("services.api.misc._put", return_value={}) as m:
            misc.update_data_source("src1", {"name": "updated"})
            m.assert_called_once()

    def test_update_data_source_empty_id(self):
        result = misc.update_data_source("", {"name": "x"})
        assert result["_api_failed"] is True

    def test_delete_data_source(self):
        with patch("services.api.misc._delete", return_value={}) as m:
            misc.delete_data_source("src1")
            m.assert_called_once()

    def test_trigger_data_source_sync(self):
        with patch("services.api.misc._post", return_value={}) as m:
            misc.trigger_data_source_sync("src1", sync_mode="full")
            m.assert_called_once()

    def test_get_data_source_status(self):
        with patch("services.api.misc._get", return_value={}) as m:
            misc.get_data_source_status("src1")
            m.assert_called_once()

    def test_trigger_file_ingest(self):
        with patch("services.api.misc._post", return_value={}) as m:
            misc.trigger_file_ingest({"file": "test.pdf"})
            m.assert_called_once()


# ===========================================================================
# Upload and Ingest
# ===========================================================================

class TestUploadAndIngest:
    def test_upload_single_file_success(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status": "ok"}
        mock_resp.raise_for_status = MagicMock()
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_resp
        with patch("services.api.misc.httpx.Client", return_value=mock_client):
            result = misc.upload_and_ingest(b"data", "test.pdf", "kb1")
            assert result == {"status": "ok"}

    def test_upload_multi_file_success(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status": "ok", "count": 2}
        mock_resp.raise_for_status = MagicMock()
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_resp
        with patch("services.api.misc.httpx.Client", return_value=mock_client):
            result = misc.upload_and_ingest_multi(
                [("a.pdf", b"data1"), ("b.pdf", b"data2")], "kb1",
                kb_name="Test KB", enable_vision=True, tier="standard",
                organization_id="org1",
            )
            assert result["count"] == 2

    def test_upload_409_conflict(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 409
        exc = httpx.HTTPStatusError("conflict", request=MagicMock(), response=mock_resp)
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_resp
        mock_resp.raise_for_status.side_effect = exc
        with patch("services.api.misc.httpx.Client", return_value=mock_client):
            result = misc.upload_and_ingest(b"data", "test.pdf", "kb1", create_new_kb=True)
            assert result["_api_failed"] is True
            assert result["_conflict"] is True

    def test_upload_other_http_error(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        exc = httpx.HTTPStatusError("server error", request=MagicMock(), response=mock_resp)
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_resp
        mock_resp.raise_for_status.side_effect = exc
        with patch("services.api.misc.httpx.Client", return_value=mock_client):
            result = misc.upload_and_ingest(b"data", "test.pdf", "kb1")
            assert result["_api_failed"] is True
            assert "_conflict" not in result

    def test_upload_connection_error(self):
        with patch("services.api.misc.httpx.Client", side_effect=Exception("conn refused")):
            result = misc.upload_and_ingest(b"data", "test.pdf", "kb1")
            assert result["_api_failed"] is True


# ===========================================================================
# Verification / Contributors
# ===========================================================================

class TestVerification:
    def test_get_verification_pending(self):
        with patch("services.api.misc._get", return_value={}) as m:
            misc.get_verification_pending(page=1, page_size=10)
            m.assert_called_once()

    def test_submit_verification_vote(self):
        with patch("services.api.misc._post", return_value={}) as m:
            misc.submit_verification_vote("doc1", {"vote": "approve"})
            m.assert_called_once()


class TestContributors:
    def test_list_contributors(self):
        with patch("services.api.misc._get", return_value={}) as m:
            misc.list_contributors()
            m.assert_called_once()

    def test_get_transparency_stats(self):
        with patch("services.api.misc._get", return_value={}) as m:
            misc.get_transparency_stats()
            m.assert_called_once()


# ===========================================================================
# Whitelist
# ===========================================================================

class TestWhitelist:
    def test_list_whitelist(self):
        with patch("services.api.misc._get", return_value={}) as m:
            misc.list_whitelist()
            m.assert_called_once()

    def test_add_whitelist_entry(self):
        with patch("services.api.misc._post", return_value={}) as m:
            misc.add_whitelist_entry({"email": "a@b.com"})
            m.assert_called_once()

    def test_remove_whitelist_entry(self):
        with patch("services.api.misc._delete", return_value={}) as m:
            misc.remove_whitelist_entry("e1")
            m.assert_called_once()

    def test_extend_whitelist_ttl(self):
        with patch("services.api.misc._patch", return_value={}) as m:
            misc.extend_whitelist_ttl("e1", {"days": 30})
            m.assert_called_once()

    def test_sync_whitelist_to_configmap(self):
        with patch("services.api.misc._post", return_value={}) as m:
            misc.sync_whitelist_to_configmap()
            m.assert_called_once()


# ===========================================================================
# Jobs
# ===========================================================================

class TestJobs:
    def test_list_jobs(self):
        with patch("services.api.misc._get", return_value={}) as m:
            misc.list_jobs()
            m.assert_called_once()

    def test_get_job(self):
        with patch("services.api.misc._get", return_value={}) as m:
            misc.get_job("j1")
            m.assert_called_once_with("/api/v1/jobs/j1")

    def test_cancel_job(self):
        with patch("services.api.misc._post", return_value={}) as m:
            misc.cancel_job("j1")
            m.assert_called_once()


# ===========================================================================
# Config Weights
# ===========================================================================

class TestConfigWeights:
    def test_get_config_weights(self):
        with patch("services.api.misc._request", return_value={}) as m:
            misc.get_config_weights()
            m.assert_called_once_with("POST", "/api/v1/admin/config/weights", json_body={"action": "read"})

    def test_update_config_weights(self):
        with patch("services.api.misc._request", return_value={}) as m:
            misc.update_config_weights({"key": "value"})
            m.assert_called_once_with("PUT", "/api/v1/admin/config/weights", json_body={"key": "value"})

    def test_reset_config_weights(self):
        with patch("services.api.misc._post", return_value={}) as m:
            misc.reset_config_weights()
            m.assert_called_once()


# ===========================================================================
# Version Management
# ===========================================================================

class TestVersionManagement:
    def test_get_document_version_list(self):
        with patch("services.api.misc._get", return_value={}) as m:
            misc.get_document_version_list("doc1")
            m.assert_called_once_with("/api/v1/admin/knowledge/doc1/versions")

    def test_rollback_document_version(self):
        with patch("services.api.misc._post", return_value={}) as m:
            misc.rollback_document_version("doc1", {"version": 2})
            m.assert_called_once()

    def test_approve_document_version(self):
        with patch("services.api.misc._post", return_value={}) as m:
            misc.approve_document_version("doc1", {"approved_by": "admin"})
            m.assert_called_once()
