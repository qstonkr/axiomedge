"""Coverage backfill — distill route handlers.

Tests all endpoints in:
- distill.py (profiles, base models, edge logs)
- distill_builds.py (builds, deploy, rollback, retrain, reset, delete)
- distill_edge.py (edge servers, heartbeat, provision, manifest)
- distill_training_data.py (training data CRUD, smart approve, generation)

Strategy: patch each module's `_get_state` to return a dict with
mocked repos/services, then call route handler coroutines directly.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# =========================================================================
# Helpers
# =========================================================================

def _mock_repo(**extra_methods):
    """Create an AsyncMock distill_repo with common methods."""
    repo = AsyncMock()
    repo.get_profile = AsyncMock(return_value=None)
    repo.list_profiles = AsyncMock(return_value=[])
    repo.create_profile = AsyncMock(return_value={"name": "p1"})
    repo.update_profile = AsyncMock(return_value={"name": "p1"})
    repo.delete_profile = AsyncMock(return_value=True)
    repo.get_base_model = AsyncMock(return_value=None)
    repo.list_base_models = AsyncMock(return_value=[])
    repo.upsert_base_model = AsyncMock(return_value={"hf_id": "x"})
    repo.delete_base_model = AsyncMock(return_value=True)
    repo.get_build = AsyncMock(return_value=None)
    repo.create_build = AsyncMock()
    repo.create_build_unique = AsyncMock()
    repo.mark_build_deployed = AsyncMock()
    repo.update_build = AsyncMock()
    repo.list_builds = AsyncMock(return_value=[])
    repo.list_version_history = AsyncMock(return_value=[])
    repo.get_latest_build = AsyncMock(return_value=None)
    repo.rollback_to = AsyncMock()
    repo.delete_build = AsyncMock(return_value=True)
    repo.save_training_data = AsyncMock(return_value=3)
    repo.list_edge_logs = AsyncMock(
        return_value={"items": [], "total": 0}
    )
    repo.get_edge_analytics = AsyncMock(return_value={})
    repo.list_failed_queries = AsyncMock(return_value=[])
    repo.list_training_data = AsyncMock(
        return_value={"items": [], "total": 0}
    )
    repo.update_training_data_status = AsyncMock(return_value=2)
    repo.bulk_update_training_data = AsyncMock(return_value=1)
    repo.get_training_data_stats = AsyncMock(return_value={})
    repo.get_batch_stats = AsyncMock(return_value={})
    repo.delete_training_data_by_source = AsyncMock(return_value=5)
    repo.delete_training_data_by_batch = AsyncMock(return_value=3)
    repo.get_edge_server = AsyncMock(return_value=None)
    repo.register_edge_server = AsyncMock()
    repo.list_edge_servers = AsyncMock(return_value=[])
    repo.delete_edge_server = AsyncMock(return_value=True)
    repo.upsert_heartbeat = AsyncMock(
        return_value={"status": "ok"}
    )
    repo.get_fleet_stats = AsyncMock(return_value={})
    repo.request_server_update = AsyncMock(
        return_value={"pending_update": "both"}
    )
    repo.bulk_request_server_update = AsyncMock(return_value=5)
    repo.insert_base_model_if_missing = AsyncMock(return_value=True)
    for k, v in extra_methods.items():
        setattr(repo, k, v)
    return repo


def _state_dict(repo=None, svc=None, sg_repo=None):
    """Build a fake state dict."""
    d: dict = {}
    if repo is not None:
        d["distill_repo"] = repo
    if svc is not None:
        d["distill_service"] = svc
    if sg_repo is not None:
        d["search_group_repo"] = sg_repo
    return d


# =========================================================================
# distill.py — Profiles CRUD
# =========================================================================

_DISTILL_STATE = "src.api.routes.distill._get_state"


class TestListProfiles:
    async def test_returns_profiles(self):
        repo = _mock_repo()
        repo.list_profiles.return_value = [
            {"name": "a"}, {"name": "b"},
        ]
        with patch(_DISTILL_STATE, return_value=_state_dict(repo)):
            from src.api.routes.distill import list_profiles
            result = await list_profiles()
        assert "a" in result["profiles"]
        assert "b" in result["profiles"]


class TestGetProfile:
    async def test_found(self):
        repo = _mock_repo()
        repo.get_profile.return_value = {"name": "p1", "enabled": True}
        with patch(_DISTILL_STATE, return_value=_state_dict(repo)):
            from src.api.routes.distill import get_profile
            result = await get_profile("p1")
        assert result["name"] == "p1"

    async def test_not_found(self):
        repo = _mock_repo()
        with patch(_DISTILL_STATE, return_value=_state_dict(repo)):
            from src.api.routes.distill import get_profile
            with pytest.raises(Exception) as exc:
                await get_profile("nope")
            assert exc.value.status_code == 404


class TestCreateProfile:
    async def test_success(self):
        repo = _mock_repo()
        repo.get_profile.return_value = None
        repo.get_base_model.return_value = {
            "hf_id": "g/m", "enabled": True,
        }
        sg = AsyncMock()
        sg.resolve_kb_ids = AsyncMock(return_value=["kb1"])
        state = _state_dict(repo, sg_repo=sg)
        with patch(_DISTILL_STATE, return_value=state):
            from src.api.routes.distill import (
                ProfileCreateRequest,
                create_profile,
            )
            req = ProfileCreateRequest(
                name="new",
                search_group="sg",
                base_model="g/m",
            )
            result = await create_profile(req)
        assert result == {"name": "p1"}

    async def test_duplicate(self):
        repo = _mock_repo()
        repo.get_profile.return_value = {"name": "dup"}
        with patch(_DISTILL_STATE, return_value=_state_dict(repo)):
            from src.api.routes.distill import (
                ProfileCreateRequest,
                create_profile,
            )
            req = ProfileCreateRequest(
                name="dup",
                search_group="sg",
                base_model="g/m",
            )
            with pytest.raises(Exception) as exc:
                await create_profile(req)
            assert exc.value.status_code == 409

    async def test_invalid_base_model(self):
        repo = _mock_repo()
        repo.get_profile.return_value = None
        repo.get_base_model.return_value = None
        sg = AsyncMock()
        sg.resolve_kb_ids = AsyncMock(return_value=["kb1"])
        state = _state_dict(repo, sg_repo=sg)
        with patch(_DISTILL_STATE, return_value=state):
            from src.api.routes.distill import (
                ProfileCreateRequest,
                create_profile,
            )
            req = ProfileCreateRequest(
                name="new",
                search_group="sg",
                base_model="bad/model",
            )
            with pytest.raises(Exception) as exc:
                await create_profile(req)
            assert exc.value.status_code == 400

    async def test_disabled_base_model(self):
        repo = _mock_repo()
        repo.get_profile.return_value = None
        repo.get_base_model.return_value = {
            "hf_id": "g/m", "enabled": False,
        }
        sg = AsyncMock()
        sg.resolve_kb_ids = AsyncMock(return_value=["kb1"])
        state = _state_dict(repo, sg_repo=sg)
        with patch(_DISTILL_STATE, return_value=state):
            from src.api.routes.distill import (
                ProfileCreateRequest,
                create_profile,
            )
            req = ProfileCreateRequest(
                name="new",
                search_group="sg",
                base_model="g/m",
            )
            with pytest.raises(Exception) as exc:
                await create_profile(req)
            assert exc.value.status_code == 400

    async def test_empty_search_group(self):
        repo = _mock_repo()
        repo.get_profile.return_value = None
        sg = AsyncMock()
        sg.resolve_kb_ids = AsyncMock(return_value=[])
        state = _state_dict(repo, sg_repo=sg)
        with patch(_DISTILL_STATE, return_value=state):
            from src.api.routes.distill import (
                ProfileCreateRequest,
                create_profile,
            )
            req = ProfileCreateRequest(
                name="new",
                search_group="empty",
                base_model="g/m",
            )
            with pytest.raises(Exception) as exc:
                await create_profile(req)
            assert exc.value.status_code == 400


class TestUpdateProfile:
    async def test_success(self):
        repo = _mock_repo()
        repo.update_profile.return_value = {"name": "p1"}
        with patch(_DISTILL_STATE, return_value=_state_dict(repo)):
            from src.api.routes.distill import (
                ProfileUpdateRequest,
                update_profile,
            )
            req = ProfileUpdateRequest(description="updated")
            result = await update_profile("p1", req)
        assert result["name"] == "p1"

    async def test_not_found(self):
        repo = _mock_repo()
        repo.update_profile.return_value = None
        with patch(_DISTILL_STATE, return_value=_state_dict(repo)):
            from src.api.routes.distill import (
                ProfileUpdateRequest,
                update_profile,
            )
            req = ProfileUpdateRequest(description="x")
            with pytest.raises(Exception) as exc:
                await update_profile("nope", req)
            assert exc.value.status_code == 404

    async def test_validates_base_model(self):
        repo = _mock_repo()
        repo.get_base_model.return_value = None
        with patch(_DISTILL_STATE, return_value=_state_dict(repo)):
            from src.api.routes.distill import (
                ProfileUpdateRequest,
                update_profile,
            )
            req = ProfileUpdateRequest(base_model="bad/m")
            with pytest.raises(Exception) as exc:
                await update_profile("p1", req)
            assert exc.value.status_code == 400


class TestDeleteProfile:
    async def test_success(self):
        repo = _mock_repo()
        with patch(_DISTILL_STATE, return_value=_state_dict(repo)):
            from src.api.routes.distill import delete_profile
            result = await delete_profile("p1")
        assert result["success"] is True

    async def test_not_found(self):
        repo = _mock_repo()
        repo.delete_profile.return_value = False
        with patch(_DISTILL_STATE, return_value=_state_dict(repo)):
            from src.api.routes.distill import delete_profile
            with pytest.raises(Exception) as exc:
                await delete_profile("nope")
            assert exc.value.status_code == 404


class TestListSearchGroups:
    async def test_with_repo(self):
        sg = AsyncMock()
        sg.list_all = AsyncMock(return_value=["sg1", "sg2"])
        state = _state_dict(_mock_repo(), sg_repo=sg)
        with patch(_DISTILL_STATE, return_value=state):
            from src.api.routes.distill import list_search_groups
            result = await list_search_groups()
        assert result["groups"] == ["sg1", "sg2"]

    async def test_no_repo(self):
        state = _state_dict(_mock_repo())
        with patch(_DISTILL_STATE, return_value=state):
            from src.api.routes.distill import list_search_groups
            result = await list_search_groups()
        assert result["groups"] == []


# =========================================================================
# distill.py — Base Model Registry
# =========================================================================


class TestListBaseModels:
    async def test_returns_models(self):
        repo = _mock_repo()
        repo.list_base_models.return_value = [{"hf_id": "a"}]
        with patch(_DISTILL_STATE, return_value=_state_dict(repo)):
            from src.api.routes.distill import list_base_models
            result = await list_base_models()
        assert len(result["models"]) == 1


class TestUpsertBaseModel:
    async def test_success(self):
        repo = _mock_repo()
        with patch(_DISTILL_STATE, return_value=_state_dict(repo)):
            from src.api.routes.distill import (
                BaseModelUpsertRequest,
                upsert_base_model_endpoint,
            )
            req = BaseModelUpsertRequest(
                hf_id="g/m",
                display_name="Gemma",
            )
            await upsert_base_model_endpoint(req)
        repo.upsert_base_model.assert_awaited_once()


class TestDeleteBaseModel:
    async def test_success(self):
        repo = _mock_repo()
        with patch(_DISTILL_STATE, return_value=_state_dict(repo)):
            from src.api.routes.distill import delete_base_model_endpoint
            result = await delete_base_model_endpoint("g/m")
        assert result["success"] is True

    async def test_not_found(self):
        repo = _mock_repo()
        repo.delete_base_model.return_value = False
        with patch(_DISTILL_STATE, return_value=_state_dict(repo)):
            from src.api.routes.distill import delete_base_model_endpoint
            with pytest.raises(Exception) as exc:
                await delete_base_model_endpoint("g/m")
            assert exc.value.status_code == 404


# =========================================================================
# distill.py — Edge Logs
# =========================================================================


class TestCollectEdgeLogs:
    async def test_no_profiles(self):
        repo = _mock_repo()
        with patch(_DISTILL_STATE, return_value=_state_dict(repo)):
            from src.api.routes.distill import collect_edge_logs
            result = await collect_edge_logs()
        assert result["collected"] == 0

    async def test_with_profile_success(self):
        repo = _mock_repo()
        repo.list_profiles.return_value = [
            {"name": "p1", "enabled": True},
        ]
        mock_collector = AsyncMock()
        mock_collector.collect = AsyncMock(return_value=10)

        with (
            patch(_DISTILL_STATE, return_value=_state_dict(repo)),
            patch(
                "src.distill.config.dict_to_profile",
                return_value=MagicMock(),
            ),
            patch(
                "src.distill.edge_log_collector.EdgeLogCollector",
                return_value=mock_collector,
            ),
        ):
            from src.api.routes.distill import collect_edge_logs
            result = await collect_edge_logs()
        assert result["collected"] == 10

    async def test_collection_exception_continues(self):
        repo = _mock_repo()
        repo.list_profiles.return_value = [
            {"name": "p1", "enabled": True},
            {"name": "p2", "enabled": True},
        ]
        with (
            patch(_DISTILL_STATE, return_value=_state_dict(repo)),
            patch(
                "src.distill.config.dict_to_profile",
                side_effect=RuntimeError("bad"),
            ),
        ):
            from src.api.routes.distill import collect_edge_logs
            result = await collect_edge_logs()
        assert result["collected"] == 0

    async def test_filter_by_profile_name(self):
        repo = _mock_repo()
        repo.list_profiles.return_value = [
            {"name": "p1", "enabled": True},
            {"name": "p2", "enabled": True},
        ]
        mock_collector = AsyncMock()
        mock_collector.collect = AsyncMock(return_value=5)

        with (
            patch(_DISTILL_STATE, return_value=_state_dict(repo)),
            patch(
                "src.distill.config.dict_to_profile",
                return_value=MagicMock(),
            ),
            patch(
                "src.distill.edge_log_collector.EdgeLogCollector",
                return_value=mock_collector,
            ),
        ):
            from src.api.routes.distill import collect_edge_logs
            result = await collect_edge_logs(profile_name="p2")
        assert result["collected"] == 5


class TestListEdgeLogs:
    async def test_returns_items(self):
        repo = _mock_repo()
        repo.list_edge_logs.return_value = {
            "items": [{"id": "1"}], "total": 1,
        }
        with patch(_DISTILL_STATE, return_value=_state_dict(repo)):
            from src.api.routes.distill import list_edge_logs
            result = await list_edge_logs(profile_name="p1")
        assert result["total"] == 1


class TestEdgeAnalytics:
    async def test_returns_stats(self):
        repo = _mock_repo()
        repo.get_edge_analytics.return_value = {"queries": 100}
        with patch(_DISTILL_STATE, return_value=_state_dict(repo)):
            from src.api.routes.distill import edge_analytics
            result = await edge_analytics(profile_name="p1")
        assert result["queries"] == 100


class TestFailedEdgeQueries:
    async def test_returns_items(self):
        repo = _mock_repo()
        repo.list_failed_queries.return_value = [{"id": "f1"}]
        with patch(_DISTILL_STATE, return_value=_state_dict(repo)):
            from src.api.routes.distill import failed_edge_queries
            result = await failed_edge_queries(profile_name="p1")
        assert len(result["items"]) == 1


# =========================================================================
# distill.py — _get_distill_repo guard
# =========================================================================


class TestGetDistillRepoGuard:
    async def test_raises_503_when_no_repo(self):
        with patch(_DISTILL_STATE, return_value={}):
            from src.api.routes.distill import _get_distill_repo
            with pytest.raises(Exception) as exc:
                _get_distill_repo()
            assert exc.value.status_code == 503


# =========================================================================
# distill_builds.py — Build CRUD
# =========================================================================

_BUILDS_STATE = "src.api.routes.distill_builds._get_state"
_BUILDS_PREFLIGHT = "src.api.routes.distill_builds._preflight_or_400"
_ENQUEUE_JOB = "src.jobs.queue.enqueue_job"


class TestTriggerBuild:
    async def test_success_with_service(self):
        """trigger_build 가 arq enqueue_job 호출 + 200 반환 확인."""
        repo = _mock_repo()
        repo.get_profile.return_value = {
            "name": "p1", "enabled": True,
            "search_group": "sg", "base_model": "g/m",
        }
        state = _state_dict(repo)
        enqueue_mock = AsyncMock()
        with patch(_BUILDS_STATE, return_value=state), patch(
            _BUILDS_PREFLIGHT,
        ), patch(_ENQUEUE_JOB, enqueue_mock):
            from src.api.routes.distill_builds import (
                BuildTriggerRequest,
                trigger_build,
            )
            req = BuildTriggerRequest(profile_name="p1")
            result = await trigger_build(req)
        assert result["status"] == "pending"
        assert "build_id" in result
        # arq job enqueue 검증 — pre_train 함수명 + build_id + profile.
        enqueue_mock.assert_awaited_once()
        call_args = enqueue_mock.await_args
        assert call_args.args[0] == "distill_pipeline_pre_train"
        assert call_args.args[2] == "p1"  # profile_name

    async def test_profile_not_found(self):
        repo = _mock_repo()
        with patch(_BUILDS_STATE, return_value=_state_dict(repo)):
            from src.api.routes.distill_builds import (
                BuildTriggerRequest,
                trigger_build,
            )
            req = BuildTriggerRequest(profile_name="nope")
            with pytest.raises(Exception) as exc:
                await trigger_build(req)
            assert exc.value.status_code == 404

    async def test_profile_disabled(self):
        repo = _mock_repo()
        repo.get_profile.return_value = {
            "name": "p1", "enabled": False,
        }
        with patch(_BUILDS_STATE, return_value=_state_dict(repo)):
            from src.api.routes.distill_builds import (
                BuildTriggerRequest,
                trigger_build,
            )
            req = BuildTriggerRequest(profile_name="p1")
            with pytest.raises(Exception) as exc:
                await trigger_build(req)
            assert exc.value.status_code == 400

    async def test_enqueue_failure_marks_build_failed(self):
        """arq enqueue 실패 시 build row 가 status='failed' 로 업데이트되고 500 raise."""
        repo = _mock_repo()
        repo.get_profile.return_value = {
            "name": "p1", "enabled": True,
            "search_group": "sg", "base_model": "g/m",
        }
        state = _state_dict(repo)
        enqueue_mock = AsyncMock(side_effect=RuntimeError("redis connection refused"))
        with patch(_BUILDS_STATE, return_value=state), patch(
            _BUILDS_PREFLIGHT,
        ), patch(_ENQUEUE_JOB, enqueue_mock):
            from src.api.routes.distill_builds import (
                BuildTriggerRequest,
                trigger_build,
            )
            req = BuildTriggerRequest(profile_name="p1")
            with pytest.raises(Exception) as exc:
                await trigger_build(req)
            assert exc.value.status_code == 500
        # build row 는 생성됐고 failed 로 업데이트됨 — 고아 row 추적 가능.
        repo.create_build_unique.assert_awaited_once()
        repo.update_build.assert_awaited()
        # update kwargs 에 status='failed' 포함
        update_kwargs = repo.update_build.await_args.kwargs
        assert update_kwargs.get("status") == "failed"
        assert "enqueue" in update_kwargs.get("error_step", "")


class TestListBuilds:
    async def test_returns_items(self):
        repo = _mock_repo()
        repo.list_builds.return_value = [{"id": "b1"}]
        with patch(_BUILDS_STATE, return_value=_state_dict(repo)):
            from src.api.routes.distill_builds import list_builds
            result = await list_builds()
        assert result["items"] == [{"id": "b1"}]


class TestListVersionHistory:
    async def test_returns_items(self):
        repo = _mock_repo()
        repo.list_version_history.return_value = [
            {"version": "v1"},
        ]
        with patch(_BUILDS_STATE, return_value=_state_dict(repo)):
            from src.api.routes.distill_builds import (
                list_version_history,
            )
            result = await list_version_history("p1")
        assert len(result["items"]) == 1


class TestGetBuild:
    async def test_found(self):
        repo = _mock_repo()
        repo.get_build.return_value = {"id": "b1", "status": "completed"}
        with patch(_BUILDS_STATE, return_value=_state_dict(repo)):
            from src.api.routes.distill_builds import get_build
            result = await get_build("b1")
        assert result["id"] == "b1"

    async def test_not_found(self):
        repo = _mock_repo()
        with patch(_BUILDS_STATE, return_value=_state_dict(repo)):
            from src.api.routes.distill_builds import get_build
            with pytest.raises(Exception) as exc:
                await get_build("nope")
            assert exc.value.status_code == 404


class TestDeployBuild:
    async def test_build_not_found(self):
        repo = _mock_repo()
        with patch(_BUILDS_STATE, return_value=_state_dict(repo)):
            from src.api.routes.distill_builds import deploy_build
            with pytest.raises(Exception) as exc:
                await deploy_build("nope")
            assert exc.value.status_code == 404

    async def test_not_completed(self):
        repo = _mock_repo()
        repo.get_build.return_value = {
            "id": "b1", "status": "pending",
        }
        with patch(_BUILDS_STATE, return_value=_state_dict(repo)):
            from src.api.routes.distill_builds import deploy_build
            with pytest.raises(Exception) as exc:
                await deploy_build("b1")
            assert exc.value.status_code == 400

    async def test_no_profile(self):
        repo = _mock_repo()
        repo.get_build.return_value = {
            "id": "b1", "status": "completed",
            "profile_name": "p1",
        }
        repo.get_profile.return_value = None
        with patch(_BUILDS_STATE, return_value=_state_dict(repo)):
            from src.api.routes.distill_builds import deploy_build
            with pytest.raises(Exception) as exc:
                await deploy_build("b1")
            assert exc.value.status_code == 404

    async def test_no_s3_uri(self):
        repo = _mock_repo()
        repo.get_build.return_value = {
            "id": "b1", "status": "completed",
            "profile_name": "p1", "s3_uri": None,
        }
        repo.get_profile.return_value = {"name": "p1"}
        with patch(_BUILDS_STATE, return_value=_state_dict(repo)):
            from src.api.routes.distill_builds import deploy_build
            with pytest.raises(Exception) as exc:
                await deploy_build("b1")
            assert exc.value.status_code == 400

    async def test_invalid_s3_uri(self):
        repo = _mock_repo()
        repo.get_build.return_value = {
            "id": "b1", "status": "completed",
            "profile_name": "p1",
            "s3_uri": "not-an-s3-uri",
            "version": "v1",
        }
        repo.get_profile.return_value = {
            "name": "p1", "search_group": "sg",
        }
        mock_dp = MagicMock()
        mock_dp.deploy.s3_prefix = "p1/"
        mock_dp.deploy.s3_bucket = "bucket"

        with (
            patch(_BUILDS_STATE, return_value=_state_dict(repo)),
            patch(
                "src.distill.config.dict_to_profile",
                return_value=mock_dp,
            ),
            patch(
                "src.distill.deployer._parse_s3_uri",
                side_effect=ValueError("bad uri"),
            ),
        ):
            from src.api.routes.distill_builds import deploy_build
            with pytest.raises(Exception) as exc:
                await deploy_build("b1")
            assert exc.value.status_code == 400

    async def test_deploy_success(self):
        repo = _mock_repo()
        repo.get_build.return_value = {
            "id": "b1", "status": "completed",
            "profile_name": "p1",
            "s3_uri": "s3://bucket/p1/v1/model.gguf",
            "version": "v1",
        }
        repo.get_profile.return_value = {
            "name": "p1", "search_group": "sg",
        }
        mock_dp = MagicMock()
        mock_dp.deploy.s3_prefix = "p1/"
        mock_dp.deploy.s3_bucket = "bucket"
        mock_deployer = AsyncMock()

        with (
            patch(_BUILDS_STATE, return_value=_state_dict(repo)),
            patch(
                "src.distill.config.dict_to_profile",
                return_value=mock_dp,
            ),
            patch(
                "src.distill.deployer._parse_s3_uri",
                return_value=("bucket", "p1/v1/model.gguf"),
            ),
            patch(
                "src.distill.deployer.DistillDeployer",
                return_value=mock_deployer,
            ),
        ):
            from src.api.routes.distill_builds import deploy_build
            result = await deploy_build("b1")
        assert result["success"] is True

    async def test_deploy_exception(self):
        repo = _mock_repo()
        repo.get_build.return_value = {
            "id": "b1", "status": "completed",
            "profile_name": "p1",
            "s3_uri": "s3://bucket/key",
            "version": "v1",
        }
        repo.get_profile.return_value = {"name": "p1"}
        mock_dp = MagicMock()
        mock_dp.deploy.s3_prefix = "p1/"
        mock_dp.deploy.s3_bucket = "bucket"

        with (
            patch(_BUILDS_STATE, return_value=_state_dict(repo)),
            patch(
                "src.distill.config.dict_to_profile",
                return_value=mock_dp,
            ),
            patch(
                "src.distill.deployer._parse_s3_uri",
                return_value=("bucket", "key"),
            ),
            patch(
                "src.distill.deployer.DistillDeployer",
                side_effect=RuntimeError("boom"),
            ),
        ):
            from src.api.routes.distill_builds import deploy_build
            with pytest.raises(Exception) as exc:
                await deploy_build("b1")
            assert exc.value.status_code == 500


class TestRollbackBuild:
    async def test_not_found(self):
        repo = _mock_repo()
        with patch(_BUILDS_STATE, return_value=_state_dict(repo)):
            from src.api.routes.distill_builds import rollback_build
            with pytest.raises(Exception) as exc:
                await rollback_build("nope")
            assert exc.value.status_code == 404

    async def test_no_s3_uri(self):
        repo = _mock_repo()
        repo.get_build.return_value = {
            "id": "b1", "s3_uri": None,
        }
        with patch(_BUILDS_STATE, return_value=_state_dict(repo)):
            from src.api.routes.distill_builds import rollback_build
            with pytest.raises(Exception) as exc:
                await rollback_build("b1")
            assert exc.value.status_code == 400

    async def test_profile_not_found(self):
        repo = _mock_repo()
        repo.get_build.return_value = {
            "id": "b1", "s3_uri": "s3://b/k",
            "profile_name": "p1", "version": "v1",
        }
        repo.get_profile.return_value = None
        with patch(_BUILDS_STATE, return_value=_state_dict(repo)):
            from src.api.routes.distill_builds import rollback_build
            with pytest.raises(Exception) as exc:
                await rollback_build("b1")
            assert exc.value.status_code == 404

    async def test_success_with_current(self):
        repo = _mock_repo()
        repo.get_build.return_value = {
            "id": "b1", "s3_uri": "s3://b/k",
            "profile_name": "p1", "version": "v1",
        }
        repo.get_profile.return_value = {"name": "p1"}
        repo.get_latest_build.return_value = {
            "id": "b2", "deployed_at": "2026-01-01",
        }
        mock_deployer = AsyncMock()
        mock_dp = MagicMock()

        with (
            patch(_BUILDS_STATE, return_value=_state_dict(repo)),
            patch(
                "src.distill.config.dict_to_profile",
                return_value=mock_dp,
            ),
            patch(
                "src.distill.deployer.DistillDeployer",
                return_value=mock_deployer,
            ),
        ):
            from src.api.routes.distill_builds import rollback_build
            result = await rollback_build("b1")
        assert result["success"] is True
        repo.rollback_to.assert_awaited_once_with("b1", "b2")

    async def test_success_no_current(self):
        repo = _mock_repo()
        repo.get_build.return_value = {
            "id": "b1", "s3_uri": "s3://b/k",
            "profile_name": "p1", "version": "v1",
        }
        repo.get_profile.return_value = {"name": "p1"}
        repo.get_latest_build.return_value = None
        mock_deployer = AsyncMock()
        mock_dp = MagicMock()

        with (
            patch(_BUILDS_STATE, return_value=_state_dict(repo)),
            patch(
                "src.distill.config.dict_to_profile",
                return_value=mock_dp,
            ),
            patch(
                "src.distill.deployer.DistillDeployer",
                return_value=mock_deployer,
            ),
        ):
            from src.api.routes.distill_builds import rollback_build
            result = await rollback_build("b1")
        assert result["success"] is True
        repo.update_build.assert_awaited()

    async def test_deployer_failure(self):
        repo = _mock_repo()
        repo.get_build.return_value = {
            "id": "b1", "s3_uri": "s3://b/k",
            "profile_name": "p1", "version": "v1",
        }
        repo.get_profile.return_value = {"name": "p1"}
        with (
            patch(_BUILDS_STATE, return_value=_state_dict(repo)),
            patch(
                "src.distill.config.dict_to_profile",
                side_effect=RuntimeError("fail"),
            ),
        ):
            from src.api.routes.distill_builds import rollback_build
            with pytest.raises(Exception) as exc:
                await rollback_build("b1")
            assert exc.value.status_code == 500


class TestDeleteBuild:
    async def test_not_found(self):
        repo = _mock_repo()
        with patch(_BUILDS_STATE, return_value=_state_dict(repo)):
            from src.api.routes.distill_builds import delete_build
            with pytest.raises(Exception) as exc:
                await delete_build("nope")
            assert exc.value.status_code == 404

    async def test_deployed(self):
        repo = _mock_repo()
        repo.get_build.return_value = {
            "id": "b1", "deployed_at": "2026-01-01",
            "status": "completed",
        }
        with patch(_BUILDS_STATE, return_value=_state_dict(repo)):
            from src.api.routes.distill_builds import delete_build
            with pytest.raises(Exception) as exc:
                await delete_build("b1")
            assert exc.value.status_code == 400

    async def test_in_progress(self):
        repo = _mock_repo()
        repo.get_build.return_value = {
            "id": "b1", "status": "training",
            "deployed_at": None,
        }
        with patch(_BUILDS_STATE, return_value=_state_dict(repo)):
            from src.api.routes.distill_builds import delete_build
            with pytest.raises(Exception) as exc:
                await delete_build("b1")
            assert exc.value.status_code == 400

    async def test_success_no_s3(self):
        repo = _mock_repo()
        repo.get_build.return_value = {
            "id": "b1", "status": "completed",
            "deployed_at": None, "s3_uri": None,
            "profile_name": "p1",
        }
        with patch(_BUILDS_STATE, return_value=_state_dict(repo)):
            from src.api.routes.distill_builds import delete_build
            result = await delete_build("b1")
        assert result["success"] is True

    async def test_success_with_s3_cleanup(self):
        repo = _mock_repo()
        repo.get_build.return_value = {
            "id": "b1", "status": "completed",
            "deployed_at": None,
            "s3_uri": "s3://bucket/key",
            "profile_name": "p1",
        }
        repo.get_profile.return_value = {"name": "p1"}
        mock_deployer = AsyncMock()
        mock_dp = MagicMock()

        with (
            patch(_BUILDS_STATE, return_value=_state_dict(repo)),
            patch(
                "src.distill.config.dict_to_profile",
                return_value=mock_dp,
            ),
            patch(
                "src.distill.deployer.DistillDeployer",
                return_value=mock_deployer,
            ),
        ):
            from src.api.routes.distill_builds import delete_build
            result = await delete_build("b1")
        assert result["success"] is True
        mock_deployer.delete_s3_object.assert_awaited_once()

    async def test_s3_cleanup_failure_still_deletes(self):
        repo = _mock_repo()
        repo.get_build.return_value = {
            "id": "b1", "status": "completed",
            "deployed_at": None,
            "s3_uri": "s3://bucket/key",
            "profile_name": "p1",
        }
        repo.get_profile.return_value = {"name": "p1"}

        with (
            patch(_BUILDS_STATE, return_value=_state_dict(repo)),
            patch(
                "src.distill.config.dict_to_profile",
                side_effect=RuntimeError("s3 fail"),
            ),
        ):
            from src.api.routes.distill_builds import delete_build
            result = await delete_build("b1")
        assert result["success"] is True

    async def test_delete_repo_failure(self):
        repo = _mock_repo()
        repo.get_build.return_value = {
            "id": "b1", "status": "completed",
            "deployed_at": None, "s3_uri": None,
            "profile_name": "p1",
        }
        repo.delete_build.return_value = False
        with patch(_BUILDS_STATE, return_value=_state_dict(repo)):
            from src.api.routes.distill_builds import delete_build
            with pytest.raises(Exception) as exc:
                await delete_build("b1")
            assert exc.value.status_code == 500


class TestResetToBaseModel:
    async def test_profile_not_found(self):
        repo = _mock_repo()
        with patch(_BUILDS_STATE, return_value=_state_dict(repo)):
            from src.api.routes.distill_builds import (
                reset_to_base_model,
            )
            with pytest.raises(Exception) as exc:
                await reset_to_base_model("nope")
            assert exc.value.status_code == 404

    # NOTE: 기존 ``test_no_service`` 는 reset_to_base_model 가 distill_service
    # 를 직접 호출할 때만 의미. 신구조에서 라우트는 arq enqueue 만 함 — 503
    # 분기 자체가 사라짐. enqueue 실패는 ``test_enqueue_failure`` 로 대체.

    async def test_success(self):
        repo = _mock_repo()
        repo.get_profile.return_value = {
            "name": "p1", "search_group": "sg",
            "base_model": "g/m",
        }
        state = _state_dict(repo)
        enqueue_mock = AsyncMock()
        with patch(_BUILDS_STATE, return_value=state), patch(
            _BUILDS_PREFLIGHT,
        ), patch(_ENQUEUE_JOB, enqueue_mock):
            from src.api.routes.distill_builds import (
                reset_to_base_model,
            )
            result = await reset_to_base_model("p1")
        assert "build_id" in result
        assert "base" in result["version"]
        # arq enqueue 검증 — steps=["quantize", "deploy"] (train skip).
        enqueue_mock.assert_awaited_once()
        call_args = enqueue_mock.await_args
        assert call_args.args[0] == "distill_pipeline_pre_train"
        assert call_args.args[3] == ["quantize", "deploy"]  # steps
        await asyncio.sleep(0)


class TestTriggerRetrain:
    async def test_profile_not_found(self):
        repo = _mock_repo()
        with patch(_BUILDS_STATE, return_value=_state_dict(repo)):
            from src.api.routes.distill_builds import (
                RetrainRequest,
                trigger_retrain,
            )
            req = RetrainRequest(
                profile_name="nope",
                edge_log_ids=["l1"],
            )
            with pytest.raises(Exception) as exc:
                await trigger_retrain(req)
            assert exc.value.status_code == 404

    async def test_with_corrected_answers(self):
        repo = _mock_repo()
        repo.get_profile.return_value = {"name": "p1"}
        repo.list_edge_logs.return_value = {
            "items": [
                {"id": "l1", "query": "question1"},
            ],
        }
        repo.save_training_data.return_value = 1
        state = _state_dict(repo)
        mock_settings = MagicMock()
        mock_settings.distill.rag_api_url = "http://x"
        with (
            patch(_BUILDS_STATE, return_value=state),
            patch(
                "src.config.get_settings",
                return_value=mock_settings,
            ),
        ):
            from src.api.routes.distill_builds import (
                RetrainRequest,
                trigger_retrain,
            )
            req = RetrainRequest(
                profile_name="p1",
                edge_log_ids=["l1"],
                corrected_answers={"l1": "fixed answer"},
            )
            result = await trigger_retrain(req)
        assert result["added"] == 1

    async def test_no_generate_answers_skips(self):
        repo = _mock_repo()
        repo.get_profile.return_value = {"name": "p1"}
        repo.list_edge_logs.return_value = {
            "items": [
                {"id": "l1", "query": "q1"},
            ],
        }
        state = _state_dict(repo)
        mock_settings = MagicMock()
        mock_settings.distill.rag_api_url = "http://x"
        with (
            patch(_BUILDS_STATE, return_value=state),
            patch(
                "src.config.get_settings",
                return_value=mock_settings,
            ),
        ):
            from src.api.routes.distill_builds import (
                RetrainRequest,
                trigger_retrain,
            )
            req = RetrainRequest(
                profile_name="p1",
                edge_log_ids=["l1"],
                generate_answers=False,
            )
            result = await trigger_retrain(req)
        # No corrected, no generate → skip → 0 added
        assert result["added"] == 0

    async def test_missing_log_skipped(self):
        repo = _mock_repo()
        repo.get_profile.return_value = {"name": "p1"}
        repo.list_edge_logs.return_value = {"items": []}
        state = _state_dict(repo)
        mock_settings = MagicMock()
        mock_settings.distill.rag_api_url = "http://x"
        with (
            patch(_BUILDS_STATE, return_value=state),
            patch(
                "src.config.get_settings",
                return_value=mock_settings,
            ),
        ):
            from src.api.routes.distill_builds import (
                RetrainRequest,
                trigger_retrain,
            )
            req = RetrainRequest(
                profile_name="p1",
                edge_log_ids=["missing"],
            )
            result = await trigger_retrain(req)
        assert result["added"] == 0


class TestGetAppInfo:
    async def test_profile_not_found(self):
        repo = _mock_repo()
        with patch(_BUILDS_STATE, return_value=_state_dict(repo)):
            from src.api.routes.distill_builds import get_app_info
            with pytest.raises(Exception) as exc:
                await get_app_info("nope")
            assert exc.value.status_code == 404

    async def test_s3_error_returns_fallback(self):
        repo = _mock_repo()
        repo.get_profile.return_value = {"name": "p1"}
        mock_dp = MagicMock()
        mock_dp.deploy.s3_prefix = "p1/"
        mock_dp.deploy.s3_bucket = "bucket"

        with (
            patch(_BUILDS_STATE, return_value=_state_dict(repo)),
            patch(
                "src.distill.config.dict_to_profile",
                return_value=mock_dp,
            ),
            patch(
                "src.distill.deployer._s3_client",
                side_effect=RuntimeError("no s3"),
            ),
        ):
            from src.api.routes.distill_builds import get_app_info
            result = await get_app_info("p1")
        assert "error" in result


class TestBuildTriggerRequestValidation:
    def test_valid_steps(self):
        from src.api.routes.distill_builds import BuildTriggerRequest
        req = BuildTriggerRequest(
            profile_name="p1",
            steps=["generate", "train"],
        )
        assert req.steps == ["generate", "train"]

    def test_invalid_steps(self):
        from src.api.routes.distill_builds import BuildTriggerRequest
        with pytest.raises(Exception):
            BuildTriggerRequest(
                profile_name="p1",
                steps=["invalid_step"],
            )

    def test_none_steps(self):
        from src.api.routes.distill_builds import BuildTriggerRequest
        req = BuildTriggerRequest(profile_name="p1")
        assert req.steps is None


# =========================================================================
# distill_edge.py — Edge Server Management
# =========================================================================

_EDGE_STATE = "src.api.routes.distill_edge._get_state"


class TestRegisterEdgeServer:
    async def test_invalid_store_id(self):
        repo = _mock_repo()
        with patch(_EDGE_STATE, return_value=_state_dict(repo)):
            from src.api.routes.distill_edge import (
                StoreRegisterRequest,
                register_edge_server,
            )
            req = StoreRegisterRequest(
                store_id="BAD ID!",
                profile_name="p1",
            )
            with pytest.raises(Exception) as exc:
                await register_edge_server(req)
            assert exc.value.status_code == 400

    async def test_already_exists(self):
        repo = _mock_repo()
        repo.get_edge_server.return_value = {"store_id": "s1"}
        with patch(_EDGE_STATE, return_value=_state_dict(repo)):
            from src.api.routes.distill_edge import (
                StoreRegisterRequest,
                register_edge_server,
            )
            req = StoreRegisterRequest(
                store_id="store-01",
                profile_name="p1",
            )
            with pytest.raises(Exception) as exc:
                await register_edge_server(req)
            assert exc.value.status_code == 409

    async def test_success(self):
        repo = _mock_repo()
        repo.get_edge_server.return_value = None
        with patch(_EDGE_STATE, return_value=_state_dict(repo)):
            from src.api.routes.distill_edge import (
                StoreRegisterRequest,
                register_edge_server,
            )
            req = StoreRegisterRequest(
                store_id="store-01",
                profile_name="p1",
            )
            result = await register_edge_server(req)
        assert result["store_id"] == "store-01"
        assert "api_key" in result
        assert "provision_command" in result

    async def test_register_value_error(self):
        repo = _mock_repo()
        repo.get_edge_server.return_value = None
        repo.register_edge_server.side_effect = ValueError("dup")
        with patch(_EDGE_STATE, return_value=_state_dict(repo)):
            from src.api.routes.distill_edge import (
                StoreRegisterRequest,
                register_edge_server,
            )
            req = StoreRegisterRequest(
                store_id="store-01",
                profile_name="p1",
            )
            with pytest.raises(Exception) as exc:
                await register_edge_server(req)
            assert exc.value.status_code == 409


class TestProvisionEdgeServer:
    async def test_not_found(self):
        repo = _mock_repo()
        with patch(_EDGE_STATE, return_value=_state_dict(repo)):
            from src.api.routes.distill_edge import (
                provision_edge_server,
            )
            with pytest.raises(Exception) as exc:
                await provision_edge_server("nope")
            assert exc.value.status_code == 404

    async def test_success(self):
        repo = _mock_repo()
        repo.get_edge_server.return_value = {
            "store_id": "s1", "profile_name": "p1",
        }
        with patch(_EDGE_STATE, return_value=_state_dict(repo)):
            from src.api.routes.distill_edge import (
                provision_edge_server,
            )
            result = await provision_edge_server("s1")
        assert result["store_id"] == "s1"
        assert "command" in result


class TestHeartbeat:
    async def test_no_auth(self):
        repo = _mock_repo()
        with patch(_EDGE_STATE, return_value=_state_dict(repo)):
            from src.api.routes.distill_edge import (
                HeartbeatRequest,
                edge_server_heartbeat,
            )
            req = HeartbeatRequest(store_id="s1")
            with pytest.raises(Exception) as exc:
                await edge_server_heartbeat(req, authorization=None)
            assert exc.value.status_code == 401

    async def test_bad_token_format(self):
        repo = _mock_repo()
        with patch(_EDGE_STATE, return_value=_state_dict(repo)):
            from src.api.routes.distill_edge import (
                HeartbeatRequest,
                edge_server_heartbeat,
            )
            req = HeartbeatRequest(store_id="s1")
            with pytest.raises(Exception) as exc:
                await edge_server_heartbeat(
                    req, authorization="Basic abc",
                )
            assert exc.value.status_code == 401

    async def test_success(self):
        repo = _mock_repo()
        with patch(_EDGE_STATE, return_value=_state_dict(repo)):
            from src.api.routes.distill_edge import (
                HeartbeatRequest,
                edge_server_heartbeat,
            )
            req = HeartbeatRequest(store_id="s1")
            result = await edge_server_heartbeat(
                req, authorization="Bearer valid-key",
            )
        assert result["status"] == "ok"

    async def test_permission_error(self):
        repo = _mock_repo()
        repo.upsert_heartbeat.side_effect = PermissionError("bad")
        with patch(_EDGE_STATE, return_value=_state_dict(repo)):
            from src.api.routes.distill_edge import (
                HeartbeatRequest,
                edge_server_heartbeat,
            )
            req = HeartbeatRequest(store_id="s1")
            with pytest.raises(Exception) as exc:
                await edge_server_heartbeat(
                    req, authorization="Bearer key",
                )
            assert exc.value.status_code == 401

    async def test_value_error(self):
        repo = _mock_repo()
        repo.upsert_heartbeat.side_effect = ValueError("bad data")
        with patch(_EDGE_STATE, return_value=_state_dict(repo)):
            from src.api.routes.distill_edge import (
                HeartbeatRequest,
                edge_server_heartbeat,
            )
            req = HeartbeatRequest(store_id="s1")
            with pytest.raises(Exception) as exc:
                await edge_server_heartbeat(
                    req, authorization="Bearer key",
                )
            assert exc.value.status_code == 400


class TestListEdgeServers:
    async def test_returns_items(self):
        repo = _mock_repo()
        repo.list_edge_servers.return_value = [
            {"store_id": "s1"},
        ]
        with patch(_EDGE_STATE, return_value=_state_dict(repo)):
            from src.api.routes.distill_edge import list_edge_servers
            result = await list_edge_servers()
        assert len(result["items"]) == 1


class TestGetEdgeServer:
    async def test_found(self):
        repo = _mock_repo()
        repo.get_edge_server.return_value = {"store_id": "s1"}
        with patch(_EDGE_STATE, return_value=_state_dict(repo)):
            from src.api.routes.distill_edge import get_edge_server
            result = await get_edge_server("s1")
        assert result["store_id"] == "s1"

    async def test_not_found(self):
        repo = _mock_repo()
        with patch(_EDGE_STATE, return_value=_state_dict(repo)):
            from src.api.routes.distill_edge import get_edge_server
            with pytest.raises(Exception) as exc:
                await get_edge_server("nope")
            assert exc.value.status_code == 404


class TestDeleteEdgeServer:
    async def test_success(self):
        repo = _mock_repo()
        with patch(_EDGE_STATE, return_value=_state_dict(repo)):
            from src.api.routes.distill_edge import delete_edge_server
            result = await delete_edge_server("s1")
        assert result["success"] is True

    async def test_not_found(self):
        repo = _mock_repo()
        repo.delete_edge_server.return_value = False
        with patch(_EDGE_STATE, return_value=_state_dict(repo)):
            from src.api.routes.distill_edge import delete_edge_server
            with pytest.raises(Exception) as exc:
                await delete_edge_server("nope")
            assert exc.value.status_code == 404


class TestRequestServerUpdate:
    async def test_success(self):
        repo = _mock_repo()
        with patch(_EDGE_STATE, return_value=_state_dict(repo)):
            from src.api.routes.distill_edge import (
                ServerUpdateRequest,
                request_server_update,
            )
            req = ServerUpdateRequest(update_type="model")
            result = await request_server_update("s1", req)
        assert "pending_update" in result

    async def test_value_error(self):
        repo = _mock_repo()
        repo.request_server_update.side_effect = ValueError("bad")
        with patch(_EDGE_STATE, return_value=_state_dict(repo)):
            from src.api.routes.distill_edge import (
                ServerUpdateRequest,
                request_server_update,
            )
            req = ServerUpdateRequest(update_type="bad")
            with pytest.raises(Exception) as exc:
                await request_server_update("s1", req)
            assert exc.value.status_code == 400


class TestBulkRequestUpdate:
    async def test_success(self):
        repo = _mock_repo()
        with patch(_EDGE_STATE, return_value=_state_dict(repo)):
            from src.api.routes.distill_edge import (
                BulkServerUpdateRequest,
                bulk_request_update,
            )
            req = BulkServerUpdateRequest(
                profile_name="p1", update_type="both",
            )
            result = await bulk_request_update(req)
        assert result["updated"] == 5


class TestFleetStats:
    async def test_returns_stats(self):
        repo = _mock_repo()
        repo.get_fleet_stats.return_value = {"total": 10}
        with patch(_EDGE_STATE, return_value=_state_dict(repo)):
            from src.api.routes.distill_edge import fleet_stats
            result = await fleet_stats("p1")
        assert result["total"] == 10


class TestDownloadProvisionScript:
    async def test_file_not_found(self):
        with (
            patch(_EDGE_STATE, return_value=_state_dict(_mock_repo())),
            patch("pathlib.Path.exists", return_value=False),
        ):
            from src.api.routes.distill_edge import (
                download_provision_script,
            )
            with pytest.raises(Exception) as exc:
                await download_provision_script()
            assert exc.value.status_code == 404


class TestDownloadEdgeFile:
    async def test_not_allowed(self):
        with patch(_EDGE_STATE, return_value=_state_dict(_mock_repo())):
            from src.api.routes.distill_edge import (
                download_edge_file,
            )
            with pytest.raises(Exception) as exc:
                await download_edge_file("malicious.py")
            assert exc.value.status_code == 404

    async def test_file_missing(self):
        with (
            patch(
                _EDGE_STATE,
                return_value=_state_dict(_mock_repo()),
            ),
            patch("pathlib.Path.exists", return_value=False),
        ):
            from src.api.routes.distill_edge import (
                download_edge_file,
            )
            with pytest.raises(Exception) as exc:
                await download_edge_file("server.py")
            assert exc.value.status_code == 404


class TestBuildProvisionConfig:
    def test_with_api_key(self):
        from src.api.routes.distill_edge import (
            _build_provision_config,
        )
        with patch.dict(
            "os.environ", {"EXTERNAL_API_URL": "http://test:8000"},
        ):
            config = _build_provision_config(
                "store-01", "p1", "key123",
            )
        assert config["store_id"] == "store-01"
        assert "EDGE_API_KEY=key123" in config["command"]

    def test_without_api_key(self):
        from src.api.routes.distill_edge import (
            _build_provision_config,
        )
        with patch.dict(
            "os.environ", {"EXTERNAL_API_URL": "http://test:8000"},
        ):
            config = _build_provision_config("store-01", "p1")
        assert "EDGE_API_KEY" not in config["command"]

    def test_local_ip_fallback(self):
        from src.api.routes.distill_edge import (
            _build_provision_config,
        )
        with (
            patch.dict("os.environ", {}, clear=True),
            patch("socket.socket") as mock_sock,
        ):
            inst = MagicMock()
            inst.getsockname.return_value = ("192.168.1.1", 0)
            mock_sock.return_value = inst
            config = _build_provision_config("s1", "p1")
        assert "192.168.1.1" in config["command"]

    def test_socket_failure_fallback(self):
        from src.api.routes.distill_edge import (
            _build_provision_config,
        )
        with (
            patch.dict("os.environ", {}, clear=True),
            patch(
                "socket.socket",
                side_effect=OSError("no network"),
            ),
        ):
            config = _build_provision_config("s1", "p1")
        assert "localhost" in config["command"]


class TestEdgeGetDistillRepoGuard:
    def test_raises_503(self):
        with patch(_EDGE_STATE, return_value={}):
            from src.api.routes.distill_edge import (
                _get_distill_repo,
            )
            with pytest.raises(Exception) as exc:
                _get_distill_repo()
            assert exc.value.status_code == 503


# =========================================================================
# distill_training_data.py — Training Data Endpoints
# =========================================================================

_TD_STATE = "src.api.routes.distill_training_data._get_state"


class TestListTrainingData:
    async def test_returns_items(self):
        repo = _mock_repo()
        repo.list_training_data.return_value = {
            "items": [{"id": "t1"}], "total": 1,
        }
        with patch(_TD_STATE, return_value=_state_dict(repo)):
            from src.api.routes.distill_training_data import (
                list_training_data,
            )
            result = await list_training_data(profile_name="p1")
        assert result["total"] == 1


class TestAddTrainingData:
    async def test_success(self):
        repo = _mock_repo()
        with patch(_TD_STATE, return_value=_state_dict(repo)):
            from src.api.routes.distill_training_data import (
                TrainingDataAddRequest,
                add_training_data,
            )
            req = TrainingDataAddRequest(
                profile_name="p1",
                question="Q?",
                answer="A.",
            )
            result = await add_training_data(req)
        assert result["added"] == 3


class TestReviewTrainingData:
    async def test_approved(self):
        repo = _mock_repo()
        with patch(_TD_STATE, return_value=_state_dict(repo)):
            from src.api.routes.distill_training_data import (
                TrainingDataReviewRequest,
                review_training_data,
            )
            req = TrainingDataReviewRequest(
                ids=["t1"], status="approved",
            )
            result = await review_training_data(req)
        assert result["updated"] == 2

    async def test_invalid_status(self):
        repo = _mock_repo()
        with patch(_TD_STATE, return_value=_state_dict(repo)):
            from src.api.routes.distill_training_data import (
                TrainingDataReviewRequest,
                review_training_data,
            )
            req = TrainingDataReviewRequest(
                ids=["t1"], status="invalid",
            )
            with pytest.raises(Exception) as exc:
                await review_training_data(req)
            assert exc.value.status_code == 400


class TestReviewEditTrainingData:
    async def test_success(self):
        repo = _mock_repo()
        with patch(_TD_STATE, return_value=_state_dict(repo)):
            from src.api.routes.distill_training_data import (
                TrainingDataEditReviewRequest,
                TrainingDataUpdateItem,
                review_edit_training_data,
            )
            req = TrainingDataEditReviewRequest(
                updates=[
                    TrainingDataUpdateItem(
                        id="t1", status="approved",
                    ),
                ],
            )
            result = await review_edit_training_data(req)
        assert result["updated"] == 1


class TestTrainingDataStats:
    async def test_returns_stats(self):
        repo = _mock_repo()
        repo.get_training_data_stats.return_value = {"total": 100}
        with patch(_TD_STATE, return_value=_state_dict(repo)):
            from src.api.routes.distill_training_data import (
                training_data_stats,
            )
            result = await training_data_stats("p1")
        assert result["total"] == 100


class TestGetBatchStats:
    async def test_returns_stats(self):
        repo = _mock_repo()
        repo.get_batch_stats.return_value = {"batch_id": "b1"}
        with patch(_TD_STATE, return_value=_state_dict(repo)):
            from src.api.routes.distill_training_data import (
                get_batch_stats,
            )
            result = await get_batch_stats("b1")
        assert result["batch_id"] == "b1"


class TestSmartApprove:
    async def test_no_items(self):
        repo = _mock_repo()
        with patch(_TD_STATE, return_value=_state_dict(repo)):
            from src.api.routes.distill_training_data import (
                smart_approve,
            )
            result = await smart_approve("p1")
        assert result["total"] == 0

    async def test_approve_reject_cleanup(self):
        repo = _mock_repo()
        repo.list_training_data.return_value = {
            "items": [
                {
                    "id": "good",
                    "answer": (
                        "This is a perfectly fine answer "
                        "with more than twenty characters."
                    ),
                },
                {
                    "id": "bad_pattern",
                    "answer": (
                        "제공된 문서들에 명시되어 있지 않습니다. "
                        "주어진 문서들에서 찾을 수 없습니다."
                    ),
                },
                {"id": "too_short", "answer": "짧음"},
                {
                    "id": "markdown_item",
                    "answer": (
                        "**Bold** answer with enough chars "
                        "to pass the length check."
                    ),
                },
            ],
        }
        with (
            patch(_TD_STATE, return_value=_state_dict(repo)),
            patch(
                "src.distill.data_gen.quality_filter"
                ".cleanup_answer_text",
                side_effect=lambda t: t.replace("**", ""),
            ),
        ):
            from src.api.routes.distill_training_data import (
                smart_approve,
            )
            result = await smart_approve("p1")
        assert result["approved"] >= 2
        assert result["rejected"] >= 1
        assert result["cleaned"] >= 1


class TestGenerateTrainingData:
    async def test_no_service(self):
        state = _state_dict(_mock_repo())
        with patch(_TD_STATE, return_value=state):
            from src.api.routes.distill_training_data import (
                GenerateDataRequest,
                generate_training_data,
            )
            with pytest.raises(Exception) as exc:
                req = GenerateDataRequest(profile_name="p1")
                await generate_training_data(req)
            assert exc.value.status_code == 503

    async def test_success(self):
        svc = AsyncMock()
        state = _state_dict(_mock_repo(), svc=svc)
        with patch(_TD_STATE, return_value=state):
            from src.api.routes.distill_training_data import (
                GenerateDataRequest,
                generate_training_data,
            )
            req = GenerateDataRequest(profile_name="p1")
            result = await generate_training_data(req)
        assert result["status"] == "generating"
        await asyncio.sleep(0)


class TestGenerateTestData:
    async def test_success(self):
        svc = AsyncMock()
        state = _state_dict(_mock_repo(), svc=svc)
        with patch(_TD_STATE, return_value=state):
            from src.api.routes.distill_training_data import (
                GenerateTestDataRequest,
                generate_test_data,
            )
            req = GenerateTestDataRequest(
                profile_name="p1", count=10,
            )
            result = await generate_test_data(req)
        assert result["status"] == "generating"
        await asyncio.sleep(0)


class TestAugmentTrainingData:
    async def test_success(self):
        svc = AsyncMock()
        state = _state_dict(_mock_repo(), svc=svc)
        with patch(_TD_STATE, return_value=state):
            from src.api.routes.distill_training_data import (
                AugmentRequest,
                augment_training_data,
            )
            req = AugmentRequest(profile_name="p1")
            result = await augment_training_data(req)
        assert result["status"] == "augmenting"
        await asyncio.sleep(0)


class TestGenerateTermQA:
    async def test_success(self):
        svc = AsyncMock()
        state = _state_dict(_mock_repo(), svc=svc)
        with patch(_TD_STATE, return_value=state):
            from src.api.routes.distill_training_data import (
                GenerateTermQARequest,
                generate_term_qa,
            )
            req = GenerateTermQARequest(profile_name="p1")
            result = await generate_term_qa(req)
        assert result["status"] == "generating_terms"
        await asyncio.sleep(0)


class TestCleanupAnswers:
    async def test_no_items(self):
        repo = _mock_repo()
        with patch(_TD_STATE, return_value=_state_dict(repo)):
            from src.api.routes.distill_training_data import (
                cleanup_answers,
            )
            result = await cleanup_answers("p1")
        assert result["cleaned"] == 0

    async def test_with_cleanup(self):
        repo = _mock_repo()
        repo.list_training_data.return_value = {
            "items": [
                {"id": "t1", "answer": "**bold** text"},
                {"id": "t2", "answer": "plain text"},
            ],
        }
        with (
            patch(_TD_STATE, return_value=_state_dict(repo)),
            patch(
                "src.distill.data_gen.quality_filter"
                ".cleanup_answer_text",
                side_effect=lambda t: t.replace("**", ""),
            ),
        ):
            from src.api.routes.distill_training_data import (
                cleanup_answers,
            )
            result = await cleanup_answers("p1")
        assert result["cleaned"] == 1
        assert result["total"] == 2


class TestDeleteBySourceType:
    async def test_valid_type(self):
        repo = _mock_repo()
        with patch(_TD_STATE, return_value=_state_dict(repo)):
            from src.api.routes.distill_training_data import (
                delete_by_source_type,
            )
            result = await delete_by_source_type(
                "p1", "test_seed",
            )
        assert result["deleted"] == 5

    async def test_invalid_type(self):
        repo = _mock_repo()
        with patch(_TD_STATE, return_value=_state_dict(repo)):
            from src.api.routes.distill_training_data import (
                delete_by_source_type,
            )
            with pytest.raises(Exception) as exc:
                await delete_by_source_type("p1", "bad_type")
            assert exc.value.status_code == 400


class TestDeleteBatchData:
    async def test_success(self):
        repo = _mock_repo()
        with patch(_TD_STATE, return_value=_state_dict(repo)):
            from src.api.routes.distill_training_data import (
                delete_batch_data,
            )
            result = await delete_batch_data("batch-1")
        assert result["deleted"] == 3


class TestTDGetDistillRepoGuard:
    def test_raises_503(self):
        with patch(_TD_STATE, return_value={}):
            from src.api.routes.distill_training_data import (
                _get_distill_repo,
            )
            with pytest.raises(Exception) as exc:
                _get_distill_repo()
            assert exc.value.status_code == 503


class TestSpawnBackground:
    async def test_logs_error_on_failure(self):
        """_spawn_background catches exceptions and logs."""
        from src.api.routes.distill_training_data import (
            _spawn_background,
        )
        called = False

        async def _fail():
            nonlocal called
            called = True
            raise RuntimeError("boom")

        _spawn_background(lambda: _fail(), label="test")
        await asyncio.sleep(0.05)
        assert called


class TestRequireDistillService:
    def test_raises_503(self):
        state = _state_dict(_mock_repo())
        with patch(_TD_STATE, return_value=state):
            from src.api.routes.distill_training_data import (
                _require_distill_service,
            )
            with pytest.raises(Exception) as exc:
                _require_distill_service()
            assert exc.value.status_code == 503

    def test_returns_service(self):
        svc = MagicMock()
        state = _state_dict(_mock_repo(), svc=svc)
        with patch(_TD_STATE, return_value=state):
            from src.api.routes.distill_training_data import (
                _require_distill_service,
            )
            assert _require_distill_service() is svc


# =========================================================================
# distill_edge.py — Manifest & App Version (heavy S3 mocking)
# =========================================================================


class TestGetManifest:
    async def test_profile_not_found(self):
        repo = _mock_repo()
        with patch(_EDGE_STATE, return_value=_state_dict(repo)):
            from src.api.routes.distill_edge import get_manifest
            with pytest.raises(Exception) as exc:
                await get_manifest("nope")
            assert exc.value.status_code == 404

    async def test_manifest_s3_error(self):
        repo = _mock_repo()
        repo.get_profile.return_value = {
            "name": "p1", "config": "{}",
        }
        mock_s3 = MagicMock()
        mock_s3.get_object.side_effect = RuntimeError("s3 err")

        with (
            patch(_EDGE_STATE, return_value=_state_dict(repo)),
            patch(
                "src.distill.deployer._s3_client",
                return_value=mock_s3,
            ),
        ):
            from src.api.routes.distill_edge import get_manifest
            with pytest.raises(Exception) as exc:
                await get_manifest("p1")
            assert exc.value.status_code == 404

    async def test_success_with_presigned_url(self):
        import json as _json

        repo = _mock_repo()
        repo.get_profile.return_value = {
            "name": "p1",
            "config": _json.dumps({
                "deploy": {
                    "s3_bucket": "bkt",
                    "s3_prefix": "p1/",
                },
            }),
        }
        body = MagicMock()
        body.read.return_value = _json.dumps({
            "version": "v1",
            "s3_uri": "s3://bkt/p1/v1/model.gguf",
        }).encode()
        mock_s3 = MagicMock()
        mock_s3.get_object.return_value = {"Body": body}
        mock_s3.generate_presigned_url.return_value = (
            "https://signed.url"
        )

        with (
            patch(_EDGE_STATE, return_value=_state_dict(repo)),
            patch(
                "src.distill.deployer._s3_client",
                return_value=mock_s3,
            ),
            patch(
                "src.distill.deployer._parse_s3_uri",
                return_value=("bkt", "p1/v1/model.gguf"),
            ),
        ):
            from src.api.routes.distill_edge import get_manifest
            result = await get_manifest("p1")
        assert result["download_url"] == "https://signed.url"


class TestSetAppVersion:
    async def test_profile_not_found(self):
        repo = _mock_repo()
        with patch(_EDGE_STATE, return_value=_state_dict(repo)):
            from src.api.routes.distill_edge import (
                AppVersionRequest,
                set_app_version,
            )
            req = AppVersionRequest(version="1.0")
            with pytest.raises(Exception) as exc:
                await set_app_version("nope", req)
            assert exc.value.status_code == 404

    async def test_success(self):
        import json as _json

        repo = _mock_repo()
        repo.get_profile.return_value = {
            "name": "p1",
            "config": _json.dumps({
                "deploy": {
                    "s3_bucket": "bkt",
                    "s3_prefix": "p1/",
                },
            }),
        }
        body = MagicMock()
        body.read.return_value = b'{"version":"v1"}'
        mock_s3 = MagicMock()
        mock_s3.get_object.return_value = {"Body": body}

        with (
            patch(_EDGE_STATE, return_value=_state_dict(repo)),
            patch(
                "src.distill.deployer._s3_client",
                return_value=mock_s3,
            ),
        ):
            from src.api.routes.distill_edge import (
                AppVersionRequest,
                set_app_version,
            )
            req = AppVersionRequest(version="2.0")
            result = await set_app_version("p1", req)
        assert result["success"] is True
        assert result["app_version"] == "2.0"

    async def test_s3_failure(self):
        repo = _mock_repo()
        repo.get_profile.return_value = {
            "name": "p1", "config": "{}",
        }
        with (
            patch(_EDGE_STATE, return_value=_state_dict(repo)),
            patch(
                "src.distill.deployer._s3_client",
                side_effect=RuntimeError("s3 down"),
            ),
        ):
            from src.api.routes.distill_edge import (
                AppVersionRequest,
                set_app_version,
            )
            req = AppVersionRequest(version="2.0")
            with pytest.raises(Exception) as exc:
                await set_app_version("p1", req)
            assert exc.value.status_code == 500


# =========================================================================
# distill_builds.py — _get_distill_repo guard
# =========================================================================


class TestBuildsGetDistillRepoGuard:
    def test_raises_503(self):
        with patch(_BUILDS_STATE, return_value={}):
            from src.api.routes.distill_builds import (
                _get_distill_repo,
            )
            with pytest.raises(Exception) as exc:
                _get_distill_repo()
            assert exc.value.status_code == 503
