"""Unit tests for src/api/routes/quality.py — comprehensive coverage."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Pre-import to avoid circular import issues
import src.api.app  # noqa: F401
from src.api.routes import quality as quality_mod


# ============================================================================
# Helpers
# ============================================================================

def _run(coro):
    """Run async coroutine in a new event loop."""
    return asyncio.run(coro)


def _mock_state(**overrides) -> MagicMock:
    state = MagicMock()
    _map: dict[str, Any] = {}
    _map.update(overrides)
    state.get = lambda k, default=None: _map.get(k, default)
    state.setdefault = lambda k, v: _map.setdefault(k, v)
    return state


# ============================================================================
# Provenance
# ============================================================================

class TestDocumentProvenance:
    def test_provenance_with_repo(self):
        repo = AsyncMock()
        repo.get_by_knowledge_id = AsyncMock(return_value={"doc_id": "d1", "source": "file.pdf"})
        with patch.object(quality_mod, "_get_state", return_value=_mock_state(provenance_repo=repo)):
            result = _run(quality_mod.get_document_provenance("d1"))
        assert result["doc_id"] == "d1"
        assert result["source"] == "file.pdf"

    def test_provenance_repo_returns_none(self):
        repo = AsyncMock()
        repo.get_by_knowledge_id = AsyncMock(return_value=None)
        with patch.object(quality_mod, "_get_state", return_value=_mock_state(provenance_repo=repo)):
            result = _run(quality_mod.get_document_provenance("d1"))
        assert result["doc_id"] == "d1"
        assert result["source"] is None

    def test_provenance_no_repo(self):
        with patch.object(quality_mod, "_get_state", return_value=_mock_state()):
            result = _run(quality_mod.get_document_provenance("d1"))
        assert result["source"] is None

    def test_provenance_repo_exception(self):
        repo = AsyncMock()
        repo.get_by_knowledge_id = AsyncMock(side_effect=RuntimeError("db down"))
        with patch.object(quality_mod, "_get_state", return_value=_mock_state(provenance_repo=repo)):
            result = _run(quality_mod.get_document_provenance("d1"))
        assert result["source"] is None


# ============================================================================
# Lineage
# ============================================================================

class TestDocumentLineage:
    def test_lineage_with_siblings(self):
        prov = {"knowledge_id": "d1", "ingestion_run_id": "run1", "source_url": "/docs"}
        sibling = {"knowledge_id": "d2"}
        repo = AsyncMock()
        repo.get_by_knowledge_id = AsyncMock(return_value=prov)
        repo.get_by_run_id = AsyncMock(return_value=[prov, sibling])
        with patch.object(quality_mod, "_get_state", return_value=_mock_state(provenance_repo=repo)):
            result = _run(quality_mod.get_document_lineage("d1"))
        assert result["parent"] == "/docs"
        assert len(result["children"]) == 1
        assert result["children"][0]["knowledge_id"] == "d2"

    def test_lineage_no_run_id(self):
        prov = {"knowledge_id": "d1", "ingestion_run_id": None, "source_url": "/docs"}
        repo = AsyncMock()
        repo.get_by_knowledge_id = AsyncMock(return_value=prov)
        with patch.object(quality_mod, "_get_state", return_value=_mock_state(provenance_repo=repo)):
            result = _run(quality_mod.get_document_lineage("d1"))
        assert result["children"] == []

    def test_lineage_no_prov(self):
        repo = AsyncMock()
        repo.get_by_knowledge_id = AsyncMock(return_value=None)
        with patch.object(quality_mod, "_get_state", return_value=_mock_state(provenance_repo=repo)):
            result = _run(quality_mod.get_document_lineage("d1"))
        assert result["lineage"] == []

    def test_lineage_no_repo(self):
        with patch.object(quality_mod, "_get_state", return_value=_mock_state()):
            result = _run(quality_mod.get_document_lineage("d1"))
        assert result["lineage"] == []

    def test_lineage_exception(self):
        repo = AsyncMock()
        repo.get_by_knowledge_id = AsyncMock(side_effect=RuntimeError("err"))
        with patch.object(quality_mod, "_get_state", return_value=_mock_state(provenance_repo=repo)):
            result = _run(quality_mod.get_document_lineage("d1"))
        assert result["lineage"] == []


# ============================================================================
# Versions
# ============================================================================

class TestDocumentVersions:
    def test_versions_from_lifecycle(self):
        lifecycle_repo = AsyncMock()
        lifecycle_repo.get_by_document = AsyncMock(return_value={
            "transitions": [{"v": 1}],
            "status": "published",
        })
        with patch.object(quality_mod, "_get_state", return_value=_mock_state(lifecycle_repo=lifecycle_repo)):
            result = _run(quality_mod.get_document_versions("d1", kb_id=""))
        assert result["current_version"] == "published"
        assert result["versions"] == [{"v": 1}]

    def test_versions_fallback_to_provenance(self):
        lifecycle_repo = AsyncMock()
        lifecycle_repo.get_by_document = AsyncMock(return_value=None)
        prov_repo = AsyncMock()
        prov_repo.get_by_knowledge_id = AsyncMock(return_value={
            "content_hash": "abc",
            "created_at": "2024-01-01",
        })
        with patch.object(quality_mod, "_get_state", return_value=_mock_state(
            lifecycle_repo=lifecycle_repo, provenance_repo=prov_repo
        )):
            result = _run(quality_mod.get_document_versions("d1", kb_id=""))
        assert result["current_version"] == "abc"

    def test_versions_no_repos(self):
        with patch.object(quality_mod, "_get_state", return_value=_mock_state()):
            result = _run(quality_mod.get_document_versions("d1", kb_id=""))
        assert result["versions"] == []

    def test_versions_exception(self):
        lifecycle_repo = AsyncMock()
        lifecycle_repo.get_by_document = AsyncMock(side_effect=RuntimeError("err"))
        with patch.object(quality_mod, "_get_state", return_value=_mock_state(lifecycle_repo=lifecycle_repo)):
            result = _run(quality_mod.get_document_versions("d1", kb_id=""))
        assert result["versions"] == []


# ============================================================================
# Dedup Stats
# ============================================================================

class TestDedupStats:
    def test_dedup_stats_full(self):
        pipeline = MagicMock()
        metrics = MagicMock()
        metrics.to_dict.return_value = {
            "total_processed": 100,
            "stage1_filtered": 10,
            "stage2_flagged": 5,
            "stage3_confirmed": 3,
            "stage4_conflicts": 1,
        }
        pipeline.get_metrics.return_value = metrics
        pipeline.document_count = 50

        tracker = AsyncMock()
        tracker.get_stats = AsyncMock(return_value={
            "total_duplicates_found": 20,
            "total_resolved": 15,
            "pending": 5,
        })

        with patch.object(quality_mod, "_get_state", return_value=_mock_state(
            dedup_pipeline=pipeline, dedup_result_tracker=tracker
        )):
            result = _run(quality_mod.get_dedup_stats())
        assert result["total_duplicates_found"] == 20
        assert result["document_count"] == 50
        assert result["stages"]["bloom"]["flagged"] == 10

    def test_dedup_stats_no_services(self):
        with patch.object(quality_mod, "_get_state", return_value=_mock_state()):
            result = _run(quality_mod.get_dedup_stats())
        assert result["total_duplicates_found"] == 0
        assert result["document_count"] == 0

    def test_dedup_stats_pipeline_exception(self):
        pipeline = MagicMock()
        pipeline.get_metrics.side_effect = RuntimeError("err")
        pipeline.document_count = 0
        with patch.object(quality_mod, "_get_state", return_value=_mock_state(dedup_pipeline=pipeline)):
            result = _run(quality_mod.get_dedup_stats())
        assert result["document_count"] == 0


# ============================================================================
# Dedup Conflicts
# ============================================================================

class TestDedupConflicts:
    def test_get_conflicts(self):
        tracker = AsyncMock()
        tracker.get_conflicts = AsyncMock(return_value={"conflicts": [{"id": "c1"}], "total": 1})
        with patch.object(quality_mod, "_get_state", return_value=_mock_state(dedup_result_tracker=tracker)):
            result = _run(quality_mod.get_dedup_conflicts(page=1, page_size=20))
        assert result["total"] == 1

    def test_no_tracker(self):
        with patch.object(quality_mod, "_get_state", return_value=_mock_state()):
            result = _run(quality_mod.get_dedup_conflicts(page=1, page_size=20))
        assert result["conflicts"] == []

    def test_tracker_exception(self):
        tracker = AsyncMock()
        tracker.get_conflicts = AsyncMock(side_effect=RuntimeError("err"))
        with patch.object(quality_mod, "_get_state", return_value=_mock_state(dedup_result_tracker=tracker)):
            result = _run(quality_mod.get_dedup_conflicts(page=1, page_size=20))
        assert result["conflicts"] == []


# ============================================================================
# Resolve Dedup Conflict
# ============================================================================

class TestResolveDedupConflict:
    def test_resolve_success(self):
        tracker = AsyncMock()
        tracker.resolve_conflict = AsyncMock(return_value=True)
        with patch.object(quality_mod, "_get_state", return_value=_mock_state(dedup_result_tracker=tracker)):
            result = _run(quality_mod.resolve_dedup_conflict({"conflict_id": "c1", "resolution": "keep_both"}))
        assert result["success"] is True

    def test_resolve_not_found(self):
        from fastapi import HTTPException
        tracker = AsyncMock()
        tracker.resolve_conflict = AsyncMock(return_value=False)
        with patch.object(quality_mod, "_get_state", return_value=_mock_state(dedup_result_tracker=tracker)):
            with pytest.raises(HTTPException) as exc_info:
                _run(quality_mod.resolve_dedup_conflict({"conflict_id": "c1", "resolution": "keep_both"}))
            assert exc_info.value.status_code == 404

    def test_resolve_missing_fields(self):
        from fastapi import HTTPException
        with patch.object(quality_mod, "_get_state", return_value=_mock_state()):
            with pytest.raises(HTTPException) as exc_info:
                _run(quality_mod.resolve_dedup_conflict({}))
            assert exc_info.value.status_code == 400

    def test_resolve_no_tracker(self):
        from fastapi import HTTPException
        with patch.object(quality_mod, "_get_state", return_value=_mock_state()):
            with pytest.raises(HTTPException) as exc_info:
                _run(quality_mod.resolve_dedup_conflict({"conflict_id": "c1", "resolution": "keep"}))
            assert exc_info.value.status_code == 503

    def test_resolve_exception(self):
        from fastapi import HTTPException
        tracker = AsyncMock()
        tracker.resolve_conflict = AsyncMock(side_effect=RuntimeError("boom"))
        with patch.object(quality_mod, "_get_state", return_value=_mock_state(dedup_result_tracker=tracker)):
            with pytest.raises(HTTPException) as exc_info:
                _run(quality_mod.resolve_dedup_conflict({"conflict_id": "c1", "resolution": "keep"}))
            assert exc_info.value.status_code == 500


# ============================================================================
# Trust Score Calculation
# ============================================================================

class TestTrustScoreCalculation:
    def test_no_repo(self):
        from fastapi import HTTPException
        with patch.object(quality_mod, "_get_state", return_value=_mock_state()):
            with pytest.raises(HTTPException) as exc_info:
                _run(quality_mod.calculate_trust_scores(kb_id="kb1"))
            assert exc_info.value.status_code == 503

    def test_success(self):
        trust_repo = MagicMock()
        collections = MagicMock()
        collections.get_collection_name = MagicMock(return_value="kb_kb1")
        calc_mock = AsyncMock(return_value={"calculated": 10})
        with patch.object(quality_mod, "_get_state", return_value=_mock_state(
            trust_score_repo=trust_repo, qdrant_collections=collections
        )):
            with patch("src.api.services.trust_score_calculator.calculate_kb_trust_scores", calc_mock):
                result = _run(quality_mod.calculate_trust_scores(kb_id="kb1"))
        assert result["calculated"] == 10


# ============================================================================
# Eval Trigger/Status/History
# ============================================================================

class TestEvaluation:
    def setup_method(self):
        quality_mod._eval_runs.clear()

    def test_trigger(self):
        with patch.object(quality_mod, "_get_state", return_value=_mock_state()):
            result = _run(quality_mod.trigger_evaluation({"kb_id": "kb1", "eval_type": "quality_gate"}))
        assert result["success"] is True
        assert "eval_id" in result

    def test_status_idle(self):
        result = _run(quality_mod.get_evaluation_status())
        assert result["status"] == "idle"

    def test_history_empty(self):
        result = _run(quality_mod.list_evaluation_history(page=1, page_size=20))
        assert result["total"] == 0

    def test_history_after_trigger(self):
        with patch.object(quality_mod, "_get_state", return_value=_mock_state()):
            _run(quality_mod.trigger_evaluation({"kb_id": "kb1"}))
        result = _run(quality_mod.list_evaluation_history(page=1, page_size=20))
        assert result["total"] == 1
        assert result["evaluations"][0]["kb_id"] == "kb1"

    def test_history_pagination(self):
        with patch.object(quality_mod, "_get_state", return_value=_mock_state()):
            for _ in range(5):
                _run(quality_mod.trigger_evaluation({"kb_id": "kb1"}))
        result = _run(quality_mod.list_evaluation_history(page=2, page_size=2))
        assert result["page"] == 2
        assert len(result["evaluations"]) == 2
        assert result["total"] == 5


# ============================================================================
# Embedding Stats
# ============================================================================

class TestEmbeddingStats:
    def test_with_embedder(self):
        with patch.object(quality_mod, "_get_state", return_value=_mock_state(embedder=MagicMock())):
            result = _run(quality_mod.get_embedding_stats())
        assert result["ready"] is True

    def test_no_embedder(self):
        with patch.object(quality_mod, "_get_state", return_value=_mock_state()):
            result = _run(quality_mod.get_embedding_stats())
        assert result["ready"] is False


# ============================================================================
# Cache Stats
# ============================================================================

class TestCacheStats:
    def test_both_caches(self):
        search_cache = AsyncMock()
        search_cache.stats = AsyncMock(return_value={"hits": 10, "misses": 5, "size": 15})
        dedup_cache = AsyncMock()
        dedup_cache.stats = AsyncMock(return_value={"total_hashes": 100, "kbs_tracked": 3})
        with patch.object(quality_mod, "_get_state", return_value=_mock_state(
            search_cache=search_cache, dedup_cache=dedup_cache
        )):
            result = _run(quality_mod.get_cache_stats())
        assert result["hits"] == 10
        assert result["misses"] == 5
        assert result["hit_rate"] == pytest.approx(0.6667, abs=0.001)

    def test_no_caches(self):
        with patch.object(quality_mod, "_get_state", return_value=_mock_state()):
            result = _run(quality_mod.get_cache_stats())
        assert result["hit_rate"] == 0.0

    def test_cache_exception(self):
        search_cache = AsyncMock()
        search_cache.stats = AsyncMock(side_effect=RuntimeError("err"))
        with patch.object(quality_mod, "_get_state", return_value=_mock_state(search_cache=search_cache)):
            result = _run(quality_mod.get_cache_stats())
        assert result["hits"] == 0


# ============================================================================
# Vectorstore Stats
# ============================================================================

class TestVectorstoreStats:
    def test_with_collections(self):
        collections = AsyncMock()
        collections.get_existing_collection_names = AsyncMock(return_value=["col1", "col2"])
        store = AsyncMock()
        store.count = AsyncMock(side_effect=[100, 200])
        with patch.object(quality_mod, "_get_state", return_value=_mock_state(
            qdrant_collections=collections, qdrant_store=store
        )):
            result = _run(quality_mod.get_vectorstore_stats())
        assert result["total_points"] == 300
        assert len(result["collections"]) == 2

    def test_count_exception_per_collection(self):
        collections = AsyncMock()
        collections.get_existing_collection_names = AsyncMock(return_value=["col1"])
        store = AsyncMock()
        store.count = AsyncMock(side_effect=RuntimeError("err"))
        with patch.object(quality_mod, "_get_state", return_value=_mock_state(
            qdrant_collections=collections, qdrant_store=store
        )):
            result = _run(quality_mod.get_vectorstore_stats())
        assert result["collections"][0]["points"] == 0

    def test_no_store(self):
        with patch.object(quality_mod, "_get_state", return_value=_mock_state()):
            result = _run(quality_mod.get_vectorstore_stats())
        assert result["total_points"] == 0


# ============================================================================
# Verification Pending
# ============================================================================

class TestVerificationPending:
    def test_with_trust_svc(self):
        trust_svc = AsyncMock()
        trust_svc.get_needs_review = AsyncMock(return_value=[{"id": "d1"}, {"id": "d2"}])
        with patch.object(quality_mod, "_get_state", return_value=_mock_state(trust_score_service=trust_svc)):
            result = _run(quality_mod.get_verification_pending(page=1, page_size=20))
        assert result["total"] == 2

    def test_fallback_to_repo(self):
        trust_repo = AsyncMock()
        trust_repo.get_needs_review = AsyncMock(return_value=[{"id": "d1"}])
        with patch.object(quality_mod, "_get_state", return_value=_mock_state(trust_score_repo=trust_repo)):
            result = _run(quality_mod.get_verification_pending(page=1, page_size=20))
        assert result["total"] == 1

    def test_no_services(self):
        with patch.object(quality_mod, "_get_state", return_value=_mock_state()):
            result = _run(quality_mod.get_verification_pending(page=1, page_size=20))
        assert result["total"] == 0

    def test_svc_exception_falls_to_repo(self):
        trust_svc = AsyncMock()
        trust_svc.get_needs_review = AsyncMock(side_effect=RuntimeError("err"))
        trust_repo = AsyncMock()
        trust_repo.get_needs_review = AsyncMock(return_value=[{"id": "d1"}])
        with patch.object(quality_mod, "_get_state", return_value=_mock_state(
            trust_score_service=trust_svc, trust_score_repo=trust_repo
        )):
            result = _run(quality_mod.get_verification_pending(page=1, page_size=20))
        assert result["total"] == 1


# ============================================================================
# Verification Vote
# ============================================================================

class TestVerificationVote:
    def test_vote_via_trust_svc(self):
        trust_svc = AsyncMock()
        trust_svc.update_vote = AsyncMock(return_value={"kts_score": 0.85, "confidence_tier": "high"})
        with patch.object(quality_mod, "_get_state", return_value=_mock_state(trust_score_service=trust_svc)):
            result = _run(quality_mod.submit_verification_vote("d1", {"vote_type": "upvote", "kb_id": "kb1"}))
        assert result["success"] is True
        assert result["new_kts_score"] == 0.85

    def test_vote_fallback_to_feedback(self):
        feedback_repo = AsyncMock()
        feedback_repo.save = AsyncMock()
        with patch.object(quality_mod, "_get_state", return_value=_mock_state(feedback_repo=feedback_repo)):
            result = _run(quality_mod.submit_verification_vote("d1", {"vote_type": "upvote"}))
        assert result["success"] is True
        assert "feedback" in result["message"]

    def test_vote_no_services(self):
        with patch.object(quality_mod, "_get_state", return_value=_mock_state()):
            result = _run(quality_mod.submit_verification_vote("d1", {}))
        assert result["success"] is True


# ============================================================================
# Rollback
# ============================================================================

class TestDocumentRollback:
    def test_rollback_success(self):
        lifecycle_svc = AsyncMock()
        lifecycle_svc.get_or_create = AsyncMock(return_value={"status": "archived", "previous_status": "published"})
        lifecycle_svc.transition = AsyncMock(return_value={"status": "published"})
        with patch.object(quality_mod, "_get_state", return_value=_mock_state(lifecycle_service=lifecycle_svc)):
            result = _run(quality_mod.rollback_document_version("d1", {"kb_id": "kb1"}))
        assert result["success"] is True
        assert result["rolled_back_to"] == "published"

    def test_rollback_no_previous(self):
        from fastapi import HTTPException
        lifecycle_svc = AsyncMock()
        lifecycle_svc.get_or_create = AsyncMock(return_value={"status": "published", "previous_status": None})
        with patch.object(quality_mod, "_get_state", return_value=_mock_state(lifecycle_service=lifecycle_svc)):
            with pytest.raises(HTTPException) as exc_info:
                _run(quality_mod.rollback_document_version("d1", {}))
            assert exc_info.value.status_code == 404

    def test_rollback_same_status(self):
        from fastapi import HTTPException
        lifecycle_svc = AsyncMock()
        lifecycle_svc.get_or_create = AsyncMock(return_value={"status": "published", "previous_status": "published"})
        with patch.object(quality_mod, "_get_state", return_value=_mock_state(lifecycle_service=lifecycle_svc)):
            with pytest.raises(HTTPException) as exc_info:
                _run(quality_mod.rollback_document_version("d1", {}))
            assert exc_info.value.status_code == 404

    def test_rollback_no_service(self):
        from fastapi import HTTPException
        with patch.object(quality_mod, "_get_state", return_value=_mock_state()):
            with pytest.raises(HTTPException) as exc_info:
                _run(quality_mod.rollback_document_version("d1", {}))
            assert exc_info.value.status_code == 503

    def test_rollback_exception(self):
        from fastapi import HTTPException
        lifecycle_svc = AsyncMock()
        lifecycle_svc.get_or_create = AsyncMock(side_effect=RuntimeError("err"))
        with patch.object(quality_mod, "_get_state", return_value=_mock_state(lifecycle_service=lifecycle_svc)):
            with pytest.raises(HTTPException) as exc_info:
                _run(quality_mod.rollback_document_version("d1", {}))
            assert exc_info.value.status_code == 500


# ============================================================================
# Approve
# ============================================================================

class TestDocumentApprove:
    def test_approve_success(self):
        lifecycle_svc = AsyncMock()
        lifecycle_svc.publish = AsyncMock(return_value={"status": "published"})
        with patch.object(quality_mod, "_get_state", return_value=_mock_state(lifecycle_service=lifecycle_svc)):
            result = _run(quality_mod.approve_document_version("d1", {"kb_id": "kb1"}))
        assert result["success"] is True

    def test_approve_no_service(self):
        from fastapi import HTTPException
        with patch.object(quality_mod, "_get_state", return_value=_mock_state()):
            with pytest.raises(HTTPException) as exc_info:
                _run(quality_mod.approve_document_version("d1", {}))
            assert exc_info.value.status_code == 503

    def test_approve_exception(self):
        from fastapi import HTTPException
        lifecycle_svc = AsyncMock()
        lifecycle_svc.publish = AsyncMock(side_effect=RuntimeError("err"))
        with patch.object(quality_mod, "_get_state", return_value=_mock_state(lifecycle_service=lifecycle_svc)):
            with pytest.raises(HTTPException) as exc_info:
                _run(quality_mod.approve_document_version("d1", {}))
            assert exc_info.value.status_code == 500


# ============================================================================
# Contributors
# ============================================================================

class TestContributors:
    def test_no_db_session_factory(self):
        with patch.object(quality_mod, "_get_state", return_value=_mock_state()):
            result = _run(quality_mod.list_contributors(page=1, page_size=20))
        assert result["contributors"] == []

    def test_db_exception(self):
        factory = MagicMock()
        factory.side_effect = RuntimeError("db down")
        with patch.object(quality_mod, "_get_state", return_value=_mock_state(db_session_factory=factory)):
            result = _run(quality_mod.list_contributors(page=1, page_size=20))
        assert result["contributors"] == []


# ============================================================================
# Golden Set (uses DB engine — mock create_async_engine)
# ============================================================================

class TestGoldenSet:
    def test_list_golden_set(self):
        mock_engine = AsyncMock()
        mock_conn = AsyncMock()

        # count result
        count_result = MagicMock()
        count_result.scalar.return_value = 1

        # rows result
        from datetime import datetime
        row = (
            "uuid1", "kb1", "What?", "Answer", "doc.pdf", "approved",
            datetime(2024, 1, 1),
        )
        rows_result = MagicMock()
        rows_result.fetchall.return_value = [row]

        mock_conn.execute = AsyncMock(side_effect=[count_result, rows_result])
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=False)

        mock_engine.begin = MagicMock(return_value=mock_conn)
        mock_engine.dispose = AsyncMock()

        with patch("sqlalchemy.ext.asyncio.create_async_engine", return_value=mock_engine):
            with patch("src.config.get_settings"):
                result = _run(quality_mod.list_golden_set(page=1, page_size=50))
        assert result["total"] == 1
        assert result["items"][0]["question"] == "What?"

    def test_update_golden_set_item(self):
        mock_engine = AsyncMock()
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=False)
        mock_engine.begin = MagicMock(return_value=mock_conn)
        mock_engine.dispose = AsyncMock()

        with patch("sqlalchemy.ext.asyncio.create_async_engine", return_value=mock_engine):
            with patch("src.config.get_settings"):
                result = _run(quality_mod.update_golden_set_item("id1", {"status": "approved"}))
        assert result["ok"] is True

    def test_update_golden_set_item_no_valid_fields(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            _run(quality_mod.update_golden_set_item("id1", {"invalid": "field"}))
        assert exc_info.value.status_code == 400

    def test_delete_golden_set_item(self):
        mock_engine = AsyncMock()
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=False)
        mock_engine.begin = MagicMock(return_value=mock_conn)
        mock_engine.dispose = AsyncMock()

        with patch("sqlalchemy.ext.asyncio.create_async_engine", return_value=mock_engine):
            with patch("src.config.get_settings"):
                result = _run(quality_mod.delete_golden_set_item("id1"))
        assert result["ok"] is True


# ============================================================================
# Eval Results
# ============================================================================

class TestEvalResults:
    def _make_engine_mock(self, table_exists=True, rows=None, count=0):
        mock_engine = AsyncMock()
        mock_conn = AsyncMock()

        check_result = MagicMock()
        check_result.scalar.return_value = table_exists

        count_result = MagicMock()
        count_result.scalar.return_value = count

        rows_result = MagicMock()
        rows_result.fetchall.return_value = rows or []

        mock_conn.execute = AsyncMock(side_effect=[check_result, count_result, rows_result])
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=False)
        mock_engine.begin = MagicMock(return_value=mock_conn)
        mock_engine.dispose = AsyncMock()
        return mock_engine

    def test_list_eval_results_no_table(self):
        mock_conn = AsyncMock()
        check_result = MagicMock()
        check_result.scalar.return_value = False
        mock_conn.execute = AsyncMock(return_value=check_result)
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=False)
        engine = AsyncMock()
        engine.begin = MagicMock(return_value=mock_conn)
        engine.dispose = AsyncMock()

        with patch("sqlalchemy.ext.asyncio.create_async_engine", return_value=engine):
            with patch("src.config.get_settings"):
                result = _run(quality_mod.list_eval_results(page=1, page_size=50))
        assert result["items"] == []

    def test_list_eval_results_with_data(self):
        from datetime import datetime
        row = ("id1", "eval1", "kb1", "gs1", "Q?", "A", "Actual", 0.9, 0.8, 0.7, 150, datetime(2024, 1, 1))
        engine = self._make_engine_mock(table_exists=True, rows=[row], count=1)
        with patch("sqlalchemy.ext.asyncio.create_async_engine", return_value=engine):
            with patch("src.config.get_settings"):
                result = _run(quality_mod.list_eval_results(page=1, page_size=50))
        assert result["total"] == 1
        assert result["items"][0]["faithfulness"] == 0.9

    def test_eval_results_summary_no_table(self):
        mock_engine = AsyncMock()
        mock_conn = AsyncMock()
        check_result = MagicMock()
        check_result.scalar.return_value = False
        mock_conn.execute = AsyncMock(return_value=check_result)
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=False)
        mock_engine.begin = MagicMock(return_value=mock_conn)
        mock_engine.dispose = AsyncMock()

        with patch("sqlalchemy.ext.asyncio.create_async_engine", return_value=mock_engine):
            with patch("src.config.get_settings"):
                result = _run(quality_mod.eval_results_summary())
        assert result["runs"] == []

    def test_eval_results_summary_with_data(self):
        from datetime import datetime
        mock_engine = AsyncMock()
        mock_conn = AsyncMock()
        check_result = MagicMock()
        check_result.scalar.return_value = True
        rows_result = MagicMock()
        rows_result.fetchall.return_value = [
            ("eval1", "kb1", 10, 0.85, 0.90, 0.75, 120.5, datetime(2024, 1, 1))
        ]
        mock_conn.execute = AsyncMock(side_effect=[check_result, rows_result])
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=False)
        mock_engine.begin = MagicMock(return_value=mock_conn)
        mock_engine.dispose = AsyncMock()

        with patch("sqlalchemy.ext.asyncio.create_async_engine", return_value=mock_engine):
            with patch("src.config.get_settings"):
                result = _run(quality_mod.eval_results_summary())
        assert len(result["runs"]) == 1
        assert result["runs"][0]["avg_faithfulness"] == 0.85
