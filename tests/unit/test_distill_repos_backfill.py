"""Coverage backfill — distill repositories (all 6 modules).

Covers methods NOT already tested in test_distill_repositories.py / test_distill_base_model.py.
Uses mocked async SQLAlchemy sessions throughout.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.exc import SQLAlchemyError


# ======================================================================
# Helpers
# ======================================================================

def _session_maker():
    """Return (maker, session) with async context-manager wiring."""
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    maker = MagicMock()
    maker.return_value = session
    return maker, session


def _model(**fields):
    """Build a mock SA model with attributes."""
    m = MagicMock()
    for k, v in fields.items():
        setattr(m, k, v)
    return m


_NOW = datetime(2026, 4, 17, 12, 0, 0, tzinfo=timezone.utc)


# ======================================================================
# EdgeLogRepository
# ======================================================================

class TestEdgeLogSaveBatch:
    def _repo(self):
        from src.distill.repositories.edge_log import (
            DistillEdgeLogRepository,
        )
        mk, sess = _session_maker()
        return DistillEdgeLogRepository(mk), sess

    async def test_save_batch_success(self):
        repo, sess = self._repo()
        logs = [
            {
                "profile_name": "p1",
                "store_id": "s1",
                "query": "hello",
                "answer": "world",
            },
            {
                "profile_name": "p1",
                "store_id": "s2",
                "query": "q2",
            },
        ]
        with patch(
            "src.distill.repositories.edge_log.DistillEdgeLogModel",
        ):
            count = await repo.save_batch(logs)
        assert count == 2
        sess.commit.assert_awaited_once()

    async def test_save_batch_commit_error_returns_zero(self):
        repo, sess = self._repo()
        sess.commit.side_effect = SQLAlchemyError("db err")
        logs = [
            {
                "profile_name": "p1",
                "store_id": "s1",
                "query": "q",
            },
        ]
        with patch(
            "src.distill.repositories.edge_log.DistillEdgeLogModel",
        ):
            count = await repo.save_batch(logs)
        assert count == 0
        sess.rollback.assert_awaited_once()

    async def test_save_batch_empty(self):
        repo, sess = self._repo()
        count = await repo.save_batch([])
        # empty list → loop doesn't execute, commit returns 0
        assert count == 0


class TestEdgeLogListLogs:
    def _repo(self):
        from src.distill.repositories.edge_log import (
            DistillEdgeLogRepository,
        )
        mk, sess = _session_maker()
        return DistillEdgeLogRepository(mk), sess

    async def test_list_logs_with_filters(self):
        repo, sess = self._repo()
        count_result = MagicMock()
        count_result.scalar.return_value = 1

        row = _model(
            id="id1",
            profile_name="p1",
            store_id="s1",
            query="q",
            answer="a",
            confidence=0.9,
            latency_ms=50,
            success=True,
            model_version="v1",
            edge_timestamp=_NOW,
            collected_at=_NOW,
        )
        data_result = MagicMock()
        data_result.scalars.return_value.all.return_value = [row]

        sess.execute.side_effect = [count_result, data_result]

        result = await repo.list_logs(
            "p1", store_id="s1", success=True, limit=10, offset=0,
        )
        assert result["total"] == 1
        assert len(result["items"]) == 1
        assert result["items"][0]["store_id"] == "s1"

    async def test_list_logs_no_filters(self):
        repo, sess = self._repo()
        count_result = MagicMock()
        count_result.scalar.return_value = 0
        data_result = MagicMock()
        data_result.scalars.return_value.all.return_value = []
        sess.execute.side_effect = [count_result, data_result]

        result = await repo.list_logs("p1")
        assert result["total"] == 0
        assert result["items"] == []


class TestEdgeLogAnalytics:
    def _repo(self):
        from src.distill.repositories.edge_log import (
            DistillEdgeLogRepository,
        )
        mk, sess = _session_maker()
        return DistillEdgeLogRepository(mk), sess

    async def test_get_analytics(self):
        repo, sess = self._repo()
        # total, success_count, avg_latency, store_count
        results = [MagicMock() for _ in range(4)]
        results[0].scalar.return_value = 100
        results[1].scalar.return_value = 90
        results[2].scalar.return_value = 45.6
        results[3].scalar.return_value = 5
        sess.execute.side_effect = results

        analytics = await repo.get_analytics("p1", days=7)
        assert analytics["total_queries"] == 100
        assert analytics["success_count"] == 90
        assert analytics["success_rate"] == 0.9
        assert analytics["avg_latency_ms"] == 45.6
        assert analytics["store_count"] == 5
        assert analytics["period_days"] == 7

    async def test_get_analytics_zero_total(self):
        repo, sess = self._repo()
        results = [MagicMock() for _ in range(4)]
        for r in results:
            r.scalar.return_value = 0
        sess.execute.side_effect = results

        analytics = await repo.get_analytics("p1")
        assert analytics["success_rate"] == 0
        assert analytics["total_queries"] == 0


class TestEdgeLogListFailed:
    def _repo(self):
        from src.distill.repositories.edge_log import (
            DistillEdgeLogRepository,
        )
        mk, sess = _session_maker()
        return DistillEdgeLogRepository(mk), sess

    async def test_list_failed_returns_items(self):
        repo, sess = self._repo()
        row = _model(
            id="id1",
            profile_name="p1",
            store_id="s1",
            query="q",
            answer=None,
            confidence=None,
            latency_ms=200,
            success=False,
            model_version="v1",
            edge_timestamp=_NOW,
            collected_at=_NOW,
        )
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [row]
        sess.execute.return_value = mock_result

        items = await repo.list_failed("p1", limit=10)
        assert len(items) == 1
        assert items[0]["success"] is False

    async def test_list_failed_empty(self):
        repo, sess = self._repo()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        sess.execute.return_value = mock_result

        items = await repo.list_failed("p1")
        assert items == []


class TestEdgeLogToDict:
    def test_none_timestamps(self):
        from src.distill.repositories.edge_log import (
            DistillEdgeLogRepository,
        )
        row = _model(
            id="id1",
            profile_name="p1",
            store_id="s1",
            query="q",
            answer="a",
            confidence=0.5,
            latency_ms=100,
            success=True,
            model_version="v1",
            edge_timestamp=None,
            collected_at=None,
        )
        d = DistillEdgeLogRepository._to_dict(row)
        assert d["edge_timestamp"] is None
        assert d["collected_at"] is None


# ======================================================================
# BuildRepository
# ======================================================================

def _build_model(**overrides):
    defaults = dict(
        id="b1",
        profile_name="p1",
        status="pending",
        version="v1",
        search_group="sg",
        base_model="model",
        training_samples=100,
        data_sources="{}",
        train_loss=0.5,
        eval_loss=0.3,
        training_duration_sec=600,
        eval_faithfulness=0.8,
        eval_relevancy=0.9,
        eval_passed=True,
        gguf_size_mb=500.0,
        gguf_sha256="abc123",
        model_name="test-model",
        quantize_method="Q4_K_M",
        s3_uri="s3://bucket/key",
        deployed_at=_NOW,
        rollback_from=None,
        error_message=None,
        error_step=None,
        created_at=_NOW,
        updated_at=_NOW,
    )
    defaults.update(overrides)
    return _model(**defaults)


class TestBuildUpdate:
    def _repo(self):
        from src.distill.repositories.build import DistillBuildRepository
        mk, sess = _session_maker()
        return DistillBuildRepository(mk), sess

    async def test_update_found(self):
        repo, sess = self._repo()
        model = _build_model(status="completed")
        # execute called twice: UPDATE, then SELECT
        select_result = MagicMock()
        select_result.scalar_one_or_none.return_value = model
        sess.execute.side_effect = [MagicMock(), select_result]

        result = await repo.update("b1", status="completed")
        assert result is not None
        assert result["status"] == "completed"

    async def test_update_not_found(self):
        repo, sess = self._repo()
        select_result = MagicMock()
        select_result.scalar_one_or_none.return_value = None
        sess.execute.side_effect = [MagicMock(), select_result]

        result = await repo.update("nonexistent", status="x")
        assert result is None


class TestBuildGet:
    def _repo(self):
        from src.distill.repositories.build import DistillBuildRepository
        mk, sess = _session_maker()
        return DistillBuildRepository(mk), sess

    async def test_get_found(self):
        repo, sess = self._repo()
        model = _build_model()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = model
        sess.execute.return_value = mock_result

        result = await repo.get("b1")
        assert result is not None
        assert result["id"] == "b1"

    async def test_get_not_found(self):
        repo, sess = self._repo()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        sess.execute.return_value = mock_result

        result = await repo.get("nonexistent")
        assert result is None


class TestBuildListAll:
    def _repo(self):
        from src.distill.repositories.build import DistillBuildRepository
        mk, sess = _session_maker()
        return DistillBuildRepository(mk), sess

    async def test_list_all_with_profile(self):
        repo, sess = self._repo()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [
            _build_model(),
        ]
        sess.execute.return_value = mock_result

        result = await repo.list_all(profile_name="p1", limit=10)
        assert len(result) == 1

    async def test_list_all_no_profile(self):
        repo, sess = self._repo()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        sess.execute.return_value = mock_result

        result = await repo.list_all()
        assert result == []


class TestBuildGetLatest:
    def _repo(self):
        from src.distill.repositories.build import DistillBuildRepository
        mk, sess = _session_maker()
        return DistillBuildRepository(mk), sess

    async def test_get_latest_found(self):
        repo, sess = self._repo()
        model = _build_model(status="completed")
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = model
        sess.execute.return_value = mock_result

        result = await repo.get_latest("p1", status="completed")
        assert result is not None
        assert result["status"] == "completed"

    async def test_get_latest_not_found(self):
        repo, sess = self._repo()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        sess.execute.return_value = mock_result

        result = await repo.get_latest("p1")
        assert result is None


class TestBuildDelete:
    def _repo(self):
        from src.distill.repositories.build import DistillBuildRepository
        mk, sess = _session_maker()
        return DistillBuildRepository(mk), sess

    async def test_delete_found(self):
        repo, sess = self._repo()
        model = _build_model()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = model
        sess.execute.return_value = mock_result

        assert await repo.delete("b1") is True
        sess.delete.assert_awaited_once()
        sess.commit.assert_awaited_once()

    async def test_delete_not_found(self):
        repo, sess = self._repo()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        sess.execute.return_value = mock_result

        assert await repo.delete("nonexistent") is False


class TestBuildVersionHistory:
    def _repo(self):
        from src.distill.repositories.build import DistillBuildRepository
        mk, sess = _session_maker()
        return DistillBuildRepository(mk), sess

    async def test_list_version_history(self):
        repo, sess = self._repo()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [
            _build_model(status="completed"),
            _build_model(id="b2", status="deployed"),
        ]
        sess.execute.return_value = mock_result

        result = await repo.list_version_history("p1")
        assert len(result) == 2


class TestBuildRollback:
    def _repo(self):
        from src.distill.repositories.build import DistillBuildRepository
        mk, sess = _session_maker()
        return DistillBuildRepository(mk), sess

    async def test_rollback_to_found(self):
        repo, sess = self._repo()
        model = _build_model(
            id="b1", rollback_from="b2", deployed_at=_NOW,
        )
        select_result = MagicMock()
        select_result.scalar_one_or_none.return_value = model
        # 2 updates + 1 select
        sess.execute.side_effect = [
            MagicMock(), MagicMock(), select_result,
        ]

        result = await repo.rollback_to("b1", "b2")
        assert result is not None
        assert result["rollback_from"] == "b2"

    async def test_rollback_to_not_found(self):
        repo, sess = self._repo()
        select_result = MagicMock()
        select_result.scalar_one_or_none.return_value = None
        sess.execute.side_effect = [
            MagicMock(), MagicMock(), select_result,
        ]

        result = await repo.rollback_to("b1", "b2")
        assert result is None


class TestBuildToDict:
    def test_none_timestamps(self):
        from src.distill.repositories.build import DistillBuildRepository
        model = _build_model(
            deployed_at=None, created_at=None, updated_at=None,
        )
        d = DistillBuildRepository._to_dict(model)
        assert d["deployed_at"] is None
        assert d["created_at"] is None
        assert d["updated_at"] is None


# ======================================================================
# ProfileRepository
# ======================================================================

def _profile_model(**overrides):
    defaults = dict(
        name="test-profile",
        enabled=True,
        description="desc",
        search_group="sg",
        base_model="google/gemma-3-4b-it",
        config='{"lora": {}}',
        created_at=_NOW,
        updated_at=_NOW,
    )
    defaults.update(overrides)
    return _model(**defaults)


class TestProfileCreate:
    def _repo(self):
        from src.distill.repositories.profile import (
            DistillProfileRepository,
        )
        mk, sess = _session_maker()
        return DistillProfileRepository(mk), sess

    async def test_create_success(self):
        repo, sess = self._repo()
        model = _profile_model()
        sess.refresh = AsyncMock()

        with patch(
            "src.distill.repositories.profile.DistillProfileModel",
            return_value=model,
        ):
            result = await repo.create({
                "name": "test-profile",
                "search_group": "sg",
                "base_model": "google/gemma-3-4b-it",
                "lora": {"r": 8},
            })
        assert result["name"] == "test-profile"
        sess.commit.assert_awaited_once()

    async def test_create_no_base_model_raises(self):
        repo, sess = self._repo()
        with pytest.raises(ValueError, match="base_model is required"):
            await repo.create({
                "name": "test",
                "search_group": "sg",
            })

    async def test_create_db_error_rollback(self):
        repo, sess = self._repo()
        sess.commit.side_effect = SQLAlchemyError("dup key")

        with patch(
            "src.distill.repositories.profile.DistillProfileModel",
        ):
            with pytest.raises(SQLAlchemyError):
                await repo.create({
                    "name": "test",
                    "search_group": "sg",
                    "base_model": "m",
                })
        sess.rollback.assert_awaited_once()


class TestProfileUpdate:
    def _repo(self):
        from src.distill.repositories.profile import (
            DistillProfileRepository,
        )
        mk, sess = _session_maker()
        return DistillProfileRepository(mk), sess

    async def test_update_found(self):
        repo, sess = self._repo()
        model = _profile_model()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = model
        sess.execute.return_value = mock_result
        sess.refresh = AsyncMock()

        result = await repo.update("test-profile", {
            "enabled": False,
            "lora": {"r": 16},
            "description": "updated",
        })
        assert result is not None
        sess.commit.assert_awaited_once()

    async def test_update_not_found(self):
        repo, sess = self._repo()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        sess.execute.return_value = mock_result

        result = await repo.update("nonexistent", {"enabled": True})
        assert result is None

    async def test_update_with_empty_config(self):
        """config is None/empty string → start fresh dict."""
        repo, sess = self._repo()
        model = _profile_model(config=None)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = model
        sess.execute.return_value = mock_result
        sess.refresh = AsyncMock()

        result = await repo.update("test-profile", {
            "training": {"epochs": 3},
        })
        assert result is not None


class TestProfileDelete:
    def _repo(self):
        from src.distill.repositories.profile import (
            DistillProfileRepository,
        )
        mk, sess = _session_maker()
        return DistillProfileRepository(mk), sess

    async def test_delete_not_found(self):
        repo, sess = self._repo()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        sess.execute.return_value = mock_result

        assert await repo.delete("nonexistent") is False

    async def test_delete_db_error_returns_false(self):
        repo, sess = self._repo()
        model = _profile_model()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = model
        sess.execute.return_value = mock_result
        sess.delete.side_effect = SQLAlchemyError("fk constraint")

        assert await repo.delete("test-profile") is False
        sess.rollback.assert_awaited_once()


class TestProfileToDict:
    def test_invalid_json_config(self):
        from src.distill.repositories.profile import (
            DistillProfileRepository,
        )
        model = _profile_model(config="not valid json")
        d = DistillProfileRepository._to_dict(model)
        # invalid JSON → empty config, no keys leak
        assert d["name"] == "test-profile"
        assert "lora" not in d

    def test_none_config(self):
        from src.distill.repositories.profile import (
            DistillProfileRepository,
        )
        model = _profile_model(config=None)
        d = DistillProfileRepository._to_dict(model)
        assert d["name"] == "test-profile"

    def test_non_string_config(self):
        """config is not a string (unexpected) → empty dict."""
        from src.distill.repositories.profile import (
            DistillProfileRepository,
        )
        model = _profile_model(config=12345)
        d = DistillProfileRepository._to_dict(model)
        assert d["name"] == "test-profile"

    def test_none_timestamps(self):
        from src.distill.repositories.profile import (
            DistillProfileRepository,
        )
        model = _profile_model(created_at=None, updated_at=None)
        d = DistillProfileRepository._to_dict(model)
        assert d["created_at"] is None
        assert d["updated_at"] is None


# ======================================================================
# TrainingDataRepository
# ======================================================================

def _td_model(**overrides):
    defaults = dict(
        id="td1",
        profile_name="p1",
        question="Q?",
        answer="A.",
        source_type="chunk_qa",
        source_id="src1",
        kb_id="kb1",
        status="approved",
        used_in_build=None,
        created_at=_NOW,
        consistency_score=0.9,
        generality_score=0.8,
        augmentation_verified=True,
        augmented_from=None,
        generation_batch_id="batch1",
        reviewed_at=None,
        review_comment=None,
    )
    defaults.update(overrides)
    return _model(**defaults)


class TestTrainingDataSaveBatch:
    def _repo(self):
        from src.distill.repositories.training_data import (
            DistillTrainingDataRepository,
        )
        mk, sess = _session_maker()
        return DistillTrainingDataRepository(mk), sess

    async def test_save_batch_empty(self):
        repo, sess = self._repo()
        assert await repo.save_batch([]) == 0

    async def test_save_batch_success(self):
        repo, sess = self._repo()
        entries = [
            {
                "profile_name": "p1",
                "question": "Q?",
                "answer": "A.",
            },
        ]
        with patch(
            "src.distill.repositories.training_data"
            ".DistillTrainingDataModel",
        ):
            count = await repo.save_batch(entries)
        assert count == 1
        sess.commit.assert_awaited_once()

    async def test_save_batch_db_error_returns_zero(self):
        repo, sess = self._repo()
        sess.commit.side_effect = SQLAlchemyError("err")
        entries = [
            {
                "profile_name": "p1",
                "question": "Q",
                "answer": "A",
            },
        ]
        with patch(
            "src.distill.repositories.training_data"
            ".DistillTrainingDataModel",
        ):
            count = await repo.save_batch(entries)
        assert count == 0
        sess.rollback.assert_awaited_once()


class TestTrainingDataListData:
    def _repo(self):
        from src.distill.repositories.training_data import (
            DistillTrainingDataRepository,
        )
        mk, sess = _session_maker()
        return DistillTrainingDataRepository(mk), sess

    async def test_list_data_with_all_filters(self):
        repo, sess = self._repo()
        count_result = MagicMock()
        count_result.scalar.return_value = 1
        row = _td_model()
        data_result = MagicMock()
        data_result.scalars.return_value.all.return_value = [row]
        sess.execute.side_effect = [count_result, data_result]

        result = await repo.list_data(
            profile_name="p1",
            status="approved",
            source_type="chunk_qa",
            batch_id="batch1",
            sort_by="consistency_score",
            sort_order="asc",
            limit=10,
            offset=0,
        )
        assert result["total"] == 1
        assert len(result["items"]) == 1

    async def test_list_data_invalid_sort_falls_back(self):
        repo, sess = self._repo()
        count_result = MagicMock()
        count_result.scalar.return_value = 0
        data_result = MagicMock()
        data_result.scalars.return_value.all.return_value = []
        sess.execute.side_effect = [count_result, data_result]

        result = await repo.list_data(
            profile_name="p1",
            sort_by="INVALID_COLUMN",
        )
        assert result["total"] == 0

    async def test_list_data_desc_order(self):
        repo, sess = self._repo()
        count_result = MagicMock()
        count_result.scalar.return_value = 0
        data_result = MagicMock()
        data_result.scalars.return_value.all.return_value = []
        sess.execute.side_effect = [count_result, data_result]

        result = await repo.list_data(
            profile_name="p1",
            sort_order="desc",
        )
        assert result["items"] == []


class TestTrainingDataGetStats:
    def _repo(self):
        from src.distill.repositories.training_data import (
            DistillTrainingDataRepository,
        )
        mk, sess = _session_maker()
        return DistillTrainingDataRepository(mk), sess

    async def test_get_stats(self):
        repo, sess = self._repo()
        # total, chunk_qa, usage_log, retrain, manual,
        # reformatted_approved, reformatted_pending = 7 queries
        scalars = [100, 40, 20, 10, 5, 15, 10]
        results = []
        for s in scalars:
            r = MagicMock()
            r.scalar.return_value = s
            results.append(r)
        sess.execute.side_effect = results

        stats = await repo.get_stats("p1")
        assert stats["total"] == 100
        assert stats["chunk_qa"] == 40
        assert stats["usage_log"] == 20
        assert stats["retrain"] == 10
        assert stats["manual"] == 5
        assert stats["reformatted_approved"] == 15
        assert stats["reformatted_pending"] == 10


class TestTrainingDataUpdateStatus:
    def _repo(self):
        from src.distill.repositories.training_data import (
            DistillTrainingDataRepository,
        )
        mk, sess = _session_maker()
        return DistillTrainingDataRepository(mk), sess

    async def test_update_status(self):
        repo, sess = self._repo()
        mock_result = MagicMock()
        mock_result.rowcount = 3
        sess.execute.return_value = mock_result

        count = await repo.update_status(
            ["id1", "id2", "id3"], "rejected",
        )
        assert count == 3
        sess.commit.assert_awaited_once()


class TestTrainingDataGetBatchStats:
    def _repo(self):
        from src.distill.repositories.training_data import (
            DistillTrainingDataRepository,
        )
        mk, sess = _session_maker()
        return DistillTrainingDataRepository(mk), sess

    async def test_get_batch_stats(self):
        repo, sess = self._repo()
        # total, pending, approved, rejected,
        # avg_consistency, avg_generality = 6 queries
        results = []
        for val in [50, 10, 35, 5]:
            r = MagicMock()
            r.scalar.return_value = val
            results.append(r)
        for avg_val in [0.85, 0.72]:
            r = MagicMock()
            r.scalar.return_value = avg_val
            results.append(r)
        sess.execute.side_effect = results

        stats = await repo.get_batch_stats("batch1")
        assert stats["batch_id"] == "batch1"
        assert stats["total"] == 50
        assert stats["pending"] == 10
        assert stats["approved"] == 35
        assert stats["rejected"] == 5
        assert stats["avg_consistency_score"] == 0.85
        assert stats["avg_generality_score"] == 0.72

    async def test_get_batch_stats_null_avg(self):
        repo, sess = self._repo()
        results = []
        for val in [0, 0, 0, 0]:
            r = MagicMock()
            r.scalar.return_value = val
            results.append(r)
        for _ in range(2):
            r = MagicMock()
            r.scalar.return_value = None
            results.append(r)
        sess.execute.side_effect = results

        stats = await repo.get_batch_stats("batch-empty")
        assert stats["avg_consistency_score"] is None
        assert stats["avg_generality_score"] is None


class TestTrainingDataBulkUpdate:
    def _repo(self):
        from src.distill.repositories.training_data import (
            DistillTrainingDataRepository,
        )
        mk, sess = _session_maker()
        return DistillTrainingDataRepository(mk), sess

    async def test_bulk_update_with_edit(self):
        repo, sess = self._repo()
        updates = [
            {
                "id": "td1",
                "status": "approved",
                "question": "new Q",
                "answer": "new A",
                "review_comment": "LGTM",
            },
            {
                "id": "td2",
                "status": "rejected",
            },
        ]

        count = await repo.bulk_update_with_edit(updates)
        assert count == 2
        sess.commit.assert_awaited_once()

    async def test_bulk_update_skips_no_id(self):
        repo, sess = self._repo()
        updates = [
            {"status": "approved"},  # no id → skip
            {"id": "td1", "status": "approved"},
        ]

        count = await repo.bulk_update_with_edit(updates)
        assert count == 1

    async def test_bulk_update_skips_empty_values(self):
        repo, sess = self._repo()
        updates = [
            {"id": "td1"},  # no status/question/answer → no values
        ]

        count = await repo.bulk_update_with_edit(updates)
        # no values dict → not counted
        assert count == 0


class TestTrainingDataDeleteBySourceType:
    def _repo(self):
        from src.distill.repositories.training_data import (
            DistillTrainingDataRepository,
        )
        mk, sess = _session_maker()
        return DistillTrainingDataRepository(mk), sess

    async def test_delete_by_source_type(self):
        repo, sess = self._repo()
        mock_result = MagicMock()
        mock_result.rowcount = 5
        sess.execute.return_value = mock_result

        count = await repo.delete_by_source_type("p1", "chunk_qa")
        assert count == 5
        sess.commit.assert_awaited_once()


class TestTrainingDataDeleteByBatch:
    def _repo(self):
        from src.distill.repositories.training_data import (
            DistillTrainingDataRepository,
        )
        mk, sess = _session_maker()
        return DistillTrainingDataRepository(mk), sess

    async def test_delete_by_batch(self):
        repo, sess = self._repo()
        mock_result = MagicMock()
        mock_result.rowcount = 10
        sess.execute.return_value = mock_result

        count = await repo.delete_by_batch("batch1")
        assert count == 10
        sess.commit.assert_awaited_once()


class TestTrainingDataToDict:
    def test_none_timestamps(self):
        from src.distill.repositories.training_data import (
            DistillTrainingDataRepository,
        )
        row = _td_model(created_at=None, reviewed_at=None)
        d = DistillTrainingDataRepository._to_dict(row)
        assert d["created_at"] is None
        assert d["reviewed_at"] is None

    def test_with_timestamps(self):
        from src.distill.repositories.training_data import (
            DistillTrainingDataRepository,
        )
        row = _td_model(reviewed_at=_NOW)
        d = DistillTrainingDataRepository._to_dict(row)
        assert d["reviewed_at"] == _NOW.isoformat()


# ======================================================================
# EdgeServerRepository
# ======================================================================

def _server_model(**overrides):
    defaults = dict(
        id="srv1",
        store_id="store-1",
        profile_name="p1",
        display_name="Store 1",
        status="online",
        last_heartbeat=_NOW,
        server_ip="10.0.0.1",
        os_type="linux",
        app_version="1.0.0",
        model_version="v1",
        model_sha256="sha",
        cpu_info="i5",
        ram_total_mb=8192,
        ram_used_mb=4096,
        disk_free_mb=50000,
        avg_latency_ms=100,
        total_queries=500,
        success_rate=0.95,
        pending_model_update=False,
        pending_app_update=False,
        api_key_hash="hash123",
        created_at=_NOW,
        updated_at=_NOW,
    )
    defaults.update(overrides)
    return _model(**defaults)


class TestEdgeServerRegister:
    def _repo(self):
        from src.distill.repositories.edge_server import (
            DistillEdgeServerRepository,
        )
        mk, sess = _session_maker()
        return DistillEdgeServerRepository(mk), sess

    async def test_register_success(self):
        repo, sess = self._repo()
        with patch(
            "src.distill.repositories.edge_server"
            ".DistillEdgeServerModel",
        ):
            result = await repo.register_edge_server(
                store_id="s1",
                profile_name="p1",
                display_name="Store 1",
                api_key_hash="hash",
            )
        assert result["store_id"] == "s1"
        assert result["status"] == "pending"

    async def test_register_db_error_raises(self):
        repo, sess = self._repo()
        sess.commit.side_effect = SQLAlchemyError("dup")
        with patch(
            "src.distill.repositories.edge_server"
            ".DistillEdgeServerModel",
        ):
            with pytest.raises(ValueError, match="Failed to register"):
                await repo.register_edge_server(
                    store_id="s1",
                    profile_name="p1",
                    display_name="Store 1",
                    api_key_hash="hash",
                )
        sess.rollback.assert_awaited_once()


class TestEdgeServerUpsertHeartbeat:
    def _repo(self):
        from src.distill.repositories.edge_server import (
            DistillEdgeServerRepository,
        )
        mk, sess = _session_maker()
        return DistillEdgeServerRepository(mk), sess

    async def test_heartbeat_new_server(self):
        repo, sess = self._repo()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        sess.execute.return_value = mock_result

        # Patch both select and Model to avoid SA argument error
        with (
            patch(
                "src.distill.repositories.edge_server.select",
            ) as mock_sel,
            patch(
                "src.distill.repositories.edge_server"
                ".DistillEdgeServerModel",
            ),
        ):
            mock_sel.return_value.where.return_value = MagicMock()
            result = await repo.upsert_heartbeat(
                {"store_id": "s1", "status": "online"},
                api_key="secret",
            )
        assert result["status"] == "ok"
        assert result["pending_model_update"] is False

    async def test_heartbeat_existing_server(self):
        repo, sess = self._repo()
        from src.distill.repositories.edge_server import _hash_key
        key_hash = _hash_key("secret")
        existing = _server_model(
            api_key_hash=key_hash,
            pending_model_update=True,
            pending_app_update=False,
        )
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = existing
        # first execute = select, second = update
        sess.execute.side_effect = [mock_result, MagicMock()]

        result = await repo.upsert_heartbeat(
            {"store_id": "store-1", "status": "online"},
            api_key="secret",
        )
        assert result["status"] == "ok"
        assert result["pending_model_update"] is True

    async def test_heartbeat_invalid_key_raises(self):
        repo, sess = self._repo()
        existing = _server_model(api_key_hash="different_hash")
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = existing
        sess.execute.return_value = mock_result

        with pytest.raises(PermissionError, match="Invalid API key"):
            await repo.upsert_heartbeat(
                {"store_id": "store-1"},
                api_key="wrong-key",
            )

    async def test_heartbeat_empty_store_id_raises(self):
        repo, sess = self._repo()
        with pytest.raises(ValueError, match="store_id is required"):
            await repo.upsert_heartbeat(
                {"store_id": ""},
                api_key="key",
            )

    async def test_heartbeat_new_server_db_error(self):
        repo, sess = self._repo()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        sess.execute.return_value = mock_result
        sess.commit.side_effect = SQLAlchemyError("err")

        with (
            patch(
                "src.distill.repositories.edge_server.select",
            ) as mock_sel,
            patch(
                "src.distill.repositories.edge_server"
                ".DistillEdgeServerModel",
            ),
        ):
            mock_sel.return_value.where.return_value = MagicMock()
            with pytest.raises(
                ValueError, match="Failed to register",
            ):
                await repo.upsert_heartbeat(
                    {"store_id": "s-new"},
                    api_key="key",
                )
        sess.rollback.assert_awaited_once()

    async def test_heartbeat_existing_no_key_hash(self):
        """Existing server without api_key_hash → skip check."""
        repo, sess = self._repo()
        existing = _server_model(api_key_hash=None)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = existing
        sess.execute.side_effect = [mock_result, MagicMock()]

        result = await repo.upsert_heartbeat(
            {"store_id": "store-1"},
            api_key="any-key",
        )
        assert result["status"] == "ok"


class TestEdgeServerListServers:
    def _repo(self):
        from src.distill.repositories.edge_server import (
            DistillEdgeServerRepository,
        )
        mk, sess = _session_maker()
        return DistillEdgeServerRepository(mk), sess

    async def test_list_with_status_filter(self):
        repo, sess = self._repo()
        # mark_stale_servers_offline: 1 execute
        # list_servers: 1 execute
        stale_result = MagicMock()
        stale_result.rowcount = 0
        server = _server_model()
        list_result = MagicMock()
        list_result.scalars.return_value.all.return_value = [server]
        sess.execute.side_effect = [
            stale_result, list_result,
        ]

        result = await repo.list_servers(
            profile_name="p1", status="online",
        )
        assert len(result) == 1

    async def test_list_no_filters(self):
        repo, sess = self._repo()
        stale_result = MagicMock()
        stale_result.rowcount = 0
        list_result = MagicMock()
        list_result.scalars.return_value.all.return_value = []
        sess.execute.side_effect = [stale_result, list_result]

        result = await repo.list_servers()
        assert result == []


class TestEdgeServerGetServer:
    def _repo(self):
        from src.distill.repositories.edge_server import (
            DistillEdgeServerRepository,
        )
        mk, sess = _session_maker()
        return DistillEdgeServerRepository(mk), sess

    async def test_get_found(self):
        repo, sess = self._repo()
        model = _server_model()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = model
        sess.execute.return_value = mock_result

        result = await repo.get_server("store-1")
        assert result is not None
        assert result["store_id"] == "store-1"


class TestEdgeServerDelete:
    def _repo(self):
        from src.distill.repositories.edge_server import (
            DistillEdgeServerRepository,
        )
        mk, sess = _session_maker()
        return DistillEdgeServerRepository(mk), sess

    async def test_delete_found(self):
        repo, sess = self._repo()
        model = _server_model()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = model
        sess.execute.return_value = mock_result

        assert await repo.delete_server("store-1") is True
        sess.delete.assert_awaited_once()

    async def test_delete_not_found(self):
        repo, sess = self._repo()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        sess.execute.return_value = mock_result

        assert await repo.delete_server("nope") is False


class TestEdgeServerRequestUpdate:
    def _repo(self):
        from src.distill.repositories.edge_server import (
            DistillEdgeServerRepository,
        )
        mk, sess = _session_maker()
        return DistillEdgeServerRepository(mk), sess

    async def test_request_model_update(self):
        repo, sess = self._repo()
        result = await repo.request_update("store-1", "model")
        assert result["requested"] is True
        assert result["update_type"] == "model"
        sess.commit.assert_awaited_once()

    async def test_request_app_update(self):
        repo, sess = self._repo()
        result = await repo.request_update("store-1", "app")
        assert result["requested"] is True

    async def test_request_both_update(self):
        repo, sess = self._repo()
        result = await repo.request_update("store-1", "both")
        assert result["requested"] is True

    async def test_request_invalid_type_raises(self):
        repo, sess = self._repo()
        with pytest.raises(ValueError, match="Invalid update_type"):
            await repo.request_update("store-1", "invalid")


class TestEdgeServerBulkRequestUpdate:
    def _repo(self):
        from src.distill.repositories.edge_server import (
            DistillEdgeServerRepository,
        )
        mk, sess = _session_maker()
        return DistillEdgeServerRepository(mk), sess

    async def test_bulk_request_model(self):
        repo, sess = self._repo()
        mock_result = MagicMock()
        mock_result.rowcount = 5
        sess.execute.return_value = mock_result

        count = await repo.bulk_request_update("p1", "model")
        assert count == 5

    async def test_bulk_request_both(self):
        repo, sess = self._repo()
        mock_result = MagicMock()
        mock_result.rowcount = 3
        sess.execute.return_value = mock_result

        count = await repo.bulk_request_update("p1", "both")
        assert count == 3


class TestEdgeServerFleetStats:
    def _repo(self):
        from src.distill.repositories.edge_server import (
            DistillEdgeServerRepository,
        )
        mk, sess = _session_maker()
        return DistillEdgeServerRepository(mk), sess

    async def test_fleet_stats(self):
        repo, sess = self._repo()
        # mark_stale: 1 execute
        stale_result = MagicMock()
        stale_result.rowcount = 0
        # fleet stats group by: 1 execute
        fleet_result = MagicMock()
        fleet_result.all.return_value = [
            ("online", 8),
            ("offline", 2),
        ]
        sess.execute.side_effect = [stale_result, fleet_result]

        stats = await repo.get_fleet_stats("p1")
        assert stats["online"] == 8
        assert stats["offline"] == 2
        assert stats["total"] == 10

    async def test_fleet_stats_empty(self):
        repo, sess = self._repo()
        stale_result = MagicMock()
        stale_result.rowcount = 0
        fleet_result = MagicMock()
        fleet_result.all.return_value = []
        sess.execute.side_effect = [stale_result, fleet_result]

        stats = await repo.get_fleet_stats("p1")
        assert stats["total"] == 0


class TestEdgeServerMarkStale:
    def _repo(self):
        from src.distill.repositories.edge_server import (
            DistillEdgeServerRepository,
        )
        mk, sess = _session_maker()
        return DistillEdgeServerRepository(mk), sess

    async def test_mark_stale_some(self):
        repo, sess = self._repo()
        mock_result = MagicMock()
        mock_result.rowcount = 3
        sess.execute.return_value = mock_result

        count = await repo.mark_stale_servers_offline(
            timeout_minutes=5,
        )
        assert count == 3

    async def test_mark_stale_none(self):
        repo, sess = self._repo()
        mock_result = MagicMock()
        mock_result.rowcount = 0
        sess.execute.return_value = mock_result

        count = await repo.mark_stale_servers_offline()
        assert count == 0


class TestEdgeServerToDict:
    def test_none_timestamps(self):
        from src.distill.repositories.edge_server import (
            DistillEdgeServerRepository,
        )
        model = _server_model(
            last_heartbeat=None, created_at=None, updated_at=None,
        )
        d = DistillEdgeServerRepository._to_dict(model)
        assert d["last_heartbeat"] is None
        assert d["created_at"] is None
        assert d["updated_at"] is None


# ======================================================================
# BaseModelRepository
# ======================================================================

def _base_model_entry(**overrides):
    defaults = dict(
        hf_id="google/gemma-3-4b-it",
        display_name="Gemma 3 4B it",
        params="4B",
        license="Gemma",
        commercial_use=True,
        verified=True,
        notes="recommended",
        enabled=True,
        sort_order=10,
        created_at=_NOW,
        updated_at=_NOW,
    )
    defaults.update(overrides)
    return _model(**defaults)


class TestBaseModelListAll:
    def _repo(self):
        from src.distill.repositories.base_model import (
            DistillBaseModelRepository,
        )
        mk, sess = _session_maker()
        return DistillBaseModelRepository(mk), sess

    async def test_list_enabled_only(self):
        repo, sess = self._repo()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [
            _base_model_entry(),
        ]
        sess.execute.return_value = mock_result

        result = await repo.list_all(enabled_only=True)
        assert len(result) == 1

    async def test_list_all_disabled_included(self):
        repo, sess = self._repo()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [
            _base_model_entry(),
            _base_model_entry(
                hf_id="org/disabled", enabled=False,
            ),
        ]
        sess.execute.return_value = mock_result

        result = await repo.list_all(enabled_only=False)
        assert len(result) == 2


class TestBaseModelGet:
    def _repo(self):
        from src.distill.repositories.base_model import (
            DistillBaseModelRepository,
        )
        mk, sess = _session_maker()
        return DistillBaseModelRepository(mk), sess

    async def test_get_found(self):
        repo, sess = self._repo()
        model = _base_model_entry()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = model
        sess.execute.return_value = mock_result

        result = await repo.get("google/gemma-3-4b-it")
        assert result is not None
        assert result["hf_id"] == "google/gemma-3-4b-it"


class TestBaseModelInsertIfMissing:
    def _repo(self):
        from src.distill.repositories.base_model import (
            DistillBaseModelRepository,
        )
        mk, sess = _session_maker()
        return DistillBaseModelRepository(mk), sess

    async def test_insert_new_row(self):
        repo, sess = self._repo()
        mock_result = MagicMock()
        mock_result.rowcount = 1
        sess.execute.return_value = mock_result

        inserted = await repo.insert_if_missing(
            {"hf_id": "org/new-model", "display_name": "New"},
        )
        assert inserted is True
        sess.commit.assert_awaited_once()

    async def test_insert_already_exists(self):
        repo, sess = self._repo()
        mock_result = MagicMock()
        mock_result.rowcount = 0
        sess.execute.return_value = mock_result

        inserted = await repo.insert_if_missing(
            {"hf_id": "org/existing", "display_name": "Old"},
        )
        assert inserted is False

    async def test_insert_no_hf_id_raises(self):
        repo, sess = self._repo()
        with pytest.raises(ValueError, match="hf_id"):
            await repo.insert_if_missing(
                {"display_name": "No hf_id"},
            )

    async def test_insert_db_error_raises(self):
        repo, sess = self._repo()
        sess.execute.side_effect = SQLAlchemyError("db err")

        with pytest.raises(SQLAlchemyError):
            await repo.insert_if_missing(
                {"hf_id": "org/fail"},
            )
        sess.rollback.assert_awaited_once()


class TestBaseModelUpsert:
    def _repo(self):
        from src.distill.repositories.base_model import (
            DistillBaseModelRepository,
        )
        mk, sess = _session_maker()
        return DistillBaseModelRepository(mk), sess

    async def test_upsert_success(self):
        repo, sess = self._repo()
        model = _base_model_entry()
        select_result = MagicMock()
        select_result.scalar_one.return_value = model
        # first execute = INSERT ON CONFLICT, second = SELECT
        sess.execute.side_effect = [MagicMock(), select_result]

        result = await repo.upsert(
            {"hf_id": "google/gemma-3-4b-it", "display_name": "G3"},
        )
        assert result["hf_id"] == "google/gemma-3-4b-it"
        sess.commit.assert_awaited_once()

    async def test_upsert_no_hf_id_raises(self):
        repo, sess = self._repo()
        with pytest.raises(ValueError, match="hf_id"):
            await repo.upsert({"display_name": "no id"})

    async def test_upsert_db_error_raises(self):
        repo, sess = self._repo()
        sess.execute.side_effect = SQLAlchemyError("conflict")

        with pytest.raises(SQLAlchemyError):
            await repo.upsert({"hf_id": "org/fail"})
        sess.rollback.assert_awaited_once()


class TestBaseModelDelete:
    def _repo(self):
        from src.distill.repositories.base_model import (
            DistillBaseModelRepository,
        )
        mk, sess = _session_maker()
        return DistillBaseModelRepository(mk), sess

    async def test_delete_found(self):
        repo, sess = self._repo()
        model = _base_model_entry()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = model
        sess.execute.return_value = mock_result

        assert await repo.delete("google/gemma-3-4b-it") is True
        sess.delete.assert_awaited_once()

    async def test_delete_not_found(self):
        repo, sess = self._repo()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        sess.execute.return_value = mock_result

        assert await repo.delete("nonexistent") is False

    async def test_delete_db_error_returns_false(self):
        repo, sess = self._repo()
        model = _base_model_entry()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = model
        sess.execute.return_value = mock_result
        sess.delete.side_effect = SQLAlchemyError("fk")

        assert await repo.delete("google/gemma-3-4b-it") is False
        sess.rollback.assert_awaited_once()


# ======================================================================
# _hash_key utility
# ======================================================================

class TestHashKey:
    def test_deterministic(self):
        from src.distill.repositories.edge_server import _hash_key
        assert _hash_key("secret") == _hash_key("secret")

    def test_different_inputs(self):
        from src.distill.repositories.edge_server import _hash_key
        assert _hash_key("a") != _hash_key("b")

    def test_returns_hex(self):
        from src.distill.repositories.edge_server import _hash_key
        h = _hash_key("test")
        assert len(h) == 64  # SHA-256 hex
        assert all(c in "0123456789abcdef" for c in h)
