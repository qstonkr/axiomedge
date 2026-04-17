"""Unit tests for dashboard/services/api/glossary.py."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx

# Make dashboard modules importable

_st_mock = MagicMock()
_st_mock.cache_data = lambda **kw: lambda f: f
sys.modules.setdefault("streamlit", _st_mock)

from services.api import glossary


class TestGlossaryCRUD:
    def test_list_glossary_terms_defaults(self):
        with patch("services.api.glossary._get", return_value={"items": []}) as m:
            glossary.list_glossary_terms()
            m.assert_called_once()
            kwargs = m.call_args[1]
            assert kwargs["kb_id"] == "all"
            assert kwargs["page"] == 1
            assert kwargs["page_size"] == 100

    def test_list_glossary_terms_with_filters(self):
        with patch("services.api.glossary._get", return_value={}) as m:
            glossary.list_glossary_terms(
                kb_id="kb1", status="approved", scope="local",
                term_type="abbreviation", page=2, page_size=50,
            )
            m.assert_called_once()
            kwargs = m.call_args[1]
            assert kwargs["kb_id"] == "kb1"
            assert kwargs["status"] == "approved"

    def test_get_glossary_term(self):
        with patch("services.api.glossary._get", return_value={"id": "t1"}) as m:
            result = glossary.get_glossary_term("t1")
            m.assert_called_once_with("/api/v1/admin/glossary/t1")
            assert result["id"] == "t1"

    def test_create_glossary_term(self):
        with patch("services.api.glossary._post", return_value={"id": "new"}) as m:
            glossary.create_glossary_term({"term": "API", "definition": "Application..."})
            m.assert_called_once()

    def test_update_glossary_term(self):
        with patch("services.api.glossary._patch", return_value={}) as m:
            glossary.update_glossary_term("t1", {"definition": "updated"})
            m.assert_called_once_with("/api/v1/admin/glossary/t1", {"definition": "updated"})

    def test_approve_glossary_term(self):
        with patch("services.api.glossary._post", return_value={}) as m:
            glossary.approve_glossary_term("t1", "admin")
            m.assert_called_once_with("/api/v1/admin/glossary/t1/approve", {"approved_by": "admin"})

    def test_reject_glossary_term(self):
        with patch("services.api.glossary._post", return_value={}) as m:
            glossary.reject_glossary_term("t1", "admin", reason="duplicate")
            body = m.call_args[0][1]
            assert body["reason"] == "duplicate"

    def test_delete_glossary_term(self):
        with patch("services.api.glossary._delete", return_value={}) as m:
            glossary.delete_glossary_term("t1")
            m.assert_called_once_with("/api/v1/admin/glossary/t1")

    def test_promote_glossary_term_to_global(self):
        with patch("services.api.glossary._post", return_value={}) as m:
            glossary.promote_glossary_term_to_global("t1")
            m.assert_called_once_with("/api/v1/admin/glossary/t1/promote-global")


class TestImportGlossaryCsv:
    def test_success(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"imported": 10}
        mock_resp.raise_for_status = MagicMock()
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_resp
        with patch("services.api.glossary.httpx.Client", return_value=mock_client):
            result = glossary.import_glossary_csv(b"csv,data", "terms.csv")
            assert result == {"imported": 10}

    def test_failure(self):
        with patch("services.api.glossary.httpx.Client", side_effect=RuntimeError("connection error")):
            result = glossary.import_glossary_csv(b"csv,data", "terms.csv")
            assert result["_api_failed"] is True


class TestDeleteGlossaryByType:
    def test_default_kb(self):
        with patch("services.api.glossary._request", return_value={}) as m:
            glossary.delete_glossary_by_type("abbreviation")
            m.assert_called_once()
            call_args = m.call_args[0]
            assert "DELETE" == call_args[0]
            assert "abbreviation" in call_args[1]
            assert "global-standard" in call_args[1]

    def test_custom_kb(self):
        with patch("services.api.glossary._request", return_value={}) as m:
            glossary.delete_glossary_by_type("term", kb_id="kb1")
            assert "kb1" in m.call_args[0][1]


class TestSynonyms:
    def test_add_synonym_to_standard(self):
        with patch("services.api.glossary._post", return_value={}) as m:
            glossary.add_synonym_to_standard("t1", "synonym1")
            body = m.call_args[0][1]
            assert body["standard_term_id"] == "t1"
            assert body["synonym"] == "synonym1"
            assert "delete_pending_id" not in body

    def test_add_synonym_with_delete_pending(self):
        with patch("services.api.glossary._post", return_value={}) as m:
            glossary.add_synonym_to_standard("t1", "syn", delete_pending_id="p1")
            body = m.call_args[0][1]
            assert body["delete_pending_id"] == "p1"

    def test_list_synonyms(self):
        with patch("services.api.glossary._get", return_value={}) as m:
            glossary.list_synonyms("t1")
            m.assert_called_once_with("/api/v1/admin/glossary/t1/synonyms")

    def test_remove_synonym(self):
        with patch("services.api.glossary._request", return_value={}) as m:
            glossary.remove_synonym("t1", "test synonym")
            call_args = m.call_args[0]
            assert "DELETE" == call_args[0]
            assert "test%20synonym" in call_args[1]


class TestDiscoveredSynonyms:
    def test_list_discovered_synonyms(self):
        with patch("services.api.glossary._get", return_value={}) as m:
            glossary.list_discovered_synonyms(status="approved", page=2)
            m.assert_called_once()

    def test_approve_discovered_synonyms(self):
        with patch("services.api.glossary._post", return_value={}) as m:
            glossary.approve_discovered_synonyms(["s1", "s2"])
            body = m.call_args[0][1]
            assert body["synonym_ids"] == ["s1", "s2"]

    def test_reject_discovered_synonyms(self):
        with patch("services.api.glossary._post", return_value={}) as m:
            glossary.reject_discovered_synonyms(["s1"])
            body = m.call_args[0][1]
            assert body["synonym_ids"] == ["s1"]


class TestSimilarity:
    def test_check_pending_similarity(self):
        with patch("services.api.glossary._request", return_value={}) as m:
            glossary.check_pending_similarity(threshold=0.8, page=2, page_size=25)
            call_args = m.call_args
            assert "POST" == call_args[0][0]
            assert "0.8" in call_args[0][1]

    def test_cleanup_pending_by_similarity(self):
        with patch("services.api.glossary._request", return_value={}) as m:
            glossary.cleanup_pending_by_similarity(threshold=0.9, term_ids=["t1"])
            body = m.call_args[1]["json_body"]
            assert body["term_ids"] == ["t1"]

    def test_cleanup_pending_no_term_ids(self):
        with patch("services.api.glossary._request", return_value={}) as m:
            glossary.cleanup_pending_by_similarity()
            body = m.call_args[1]["json_body"]
            assert body["term_ids"] == []

    def test_get_similarity_distribution(self):
        with patch("services.api.glossary._get", return_value={}) as m:
            glossary.get_similarity_distribution()
            m.assert_called_once()


class TestGlossaryStats:
    def test_get_glossary_domain_stats(self):
        with patch("services.api.glossary._get", return_value={}) as m:
            glossary.get_glossary_domain_stats()
            m.assert_called_once_with("/api/v1/admin/glossary/domain-stats")

    def test_get_glossary_source_stats(self):
        with patch("services.api.glossary._get", return_value={}) as m:
            glossary.get_glossary_source_stats()
            m.assert_called_once_with("/api/v1/admin/glossary/source-stats")
