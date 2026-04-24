"""Admin graph-schema API — list/approve/reject/merge/rename/bootstrap-run."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def mock_candidate_repo():
    repo = AsyncMock()
    repo.list_pending = AsyncMock(return_value=[])
    repo.decide = AsyncMock()
    return repo


@pytest.fixture
def mock_run_repo():
    repo = AsyncMock()
    repo.has_running = AsyncMock(return_value=False)
    return repo


@pytest.fixture
def app(mock_candidate_repo, mock_run_repo):
    from src.api.routes.graph_schema import _get_repos, router

    fast = FastAPI()
    fast.dependency_overrides[_get_repos] = lambda: (
        mock_candidate_repo, mock_run_repo,
    )
    fast.include_router(router)
    return fast


class TestListCandidates:
    def test_empty_list(self, app):
        client = TestClient(app)
        resp = client.get(
            "/api/v1/admin/graph-schema/candidates?kb_id=test",
        )
        assert resp.status_code == 200
        assert resp.json() == {"candidates": []}

    def test_returns_candidates(self, app, mock_candidate_repo):
        mock_row = MagicMock()
        mock_row.id = uuid4()
        mock_row.kb_id = "test"
        mock_row.candidate_type = "node"
        mock_row.label = "Meeting"
        mock_row.frequency = 10
        mock_row.confidence_avg = 0.9
        mock_row.confidence_min = 0.85
        mock_row.confidence_max = 0.95
        mock_row.source_label = None
        mock_row.target_label = None
        mock_row.examples = [{"sample": "회의"}]
        mock_row.similar_labels = []
        mock_candidate_repo.list_pending.return_value = [mock_row]

        client = TestClient(app)
        resp = client.get(
            "/api/v1/admin/graph-schema/candidates?kb_id=test",
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["candidates"]) == 1
        assert body["candidates"][0]["label"] == "Meeting"
        assert body["candidates"][0]["frequency"] == 10


class TestApprove:
    def test_approve_writes_yaml_and_marks_decided(
        self, app, mock_candidate_repo, tmp_path, monkeypatch,
    ):
        schema_dir = tmp_path / "graph_schemas"
        schema_dir.mkdir()
        (schema_dir / "test.yaml").write_text(
            "version: 1\nprompt_focus: x\nnodes: [Person]\nrelationships: []\n"
        )
        monkeypatch.setattr(
            "src.api.routes.graph_schema_helpers._SCHEMA_DIR", schema_dir,
        )
        monkeypatch.setattr(
            "src.api.routes.graph_schema.git_commit_and_push",
            lambda **kw: {
                "branch": kw["branch"], "commit_sha": "abc", "pushed": False,
            },
        )

        client = TestClient(app)
        resp = client.post(
            "/api/v1/admin/graph-schema/candidates/approve",
            json={
                "kb_id": "test",
                "candidate_type": "node",
                "label": "Meeting",
                "approved_by": "admin@test",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert "yaml_path" in body
        mock_candidate_repo.decide.assert_awaited_once()
        call_kwargs = mock_candidate_repo.decide.await_args.kwargs
        assert call_kwargs["status"] == "approved"


class TestReject:
    def test_reject_marks_decided(self, app, mock_candidate_repo):
        client = TestClient(app)
        resp = client.post(
            "/api/v1/admin/graph-schema/candidates/reject",
            json={
                "kb_id": "test",
                "candidate_type": "node",
                "label": "Junk",
                "decided_by": "admin@test",
                "reason": "not a real concept",
            },
        )
        assert resp.status_code == 200
        mock_candidate_repo.decide.assert_awaited_once()
        assert (
            mock_candidate_repo.decide.await_args.kwargs["status"] == "rejected"
        )


class TestMerge:
    def test_merge_records_merged_into(self, app, mock_candidate_repo):
        client = TestClient(app)
        resp = client.post(
            "/api/v1/admin/graph-schema/candidates/merge",
            json={
                "kb_id": "test",
                "candidate_type": "node",
                "label": "Employee",
                "merge_into": "Person",
                "decided_by": "admin@test",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["merged_into"] == "Person"
        kwargs = mock_candidate_repo.decide.await_args.kwargs
        assert kwargs["status"] == "merged"
        assert kwargs["merged_into"] == "Person"


class TestRename:
    def test_rename_writes_new_label_to_yaml(
        self, app, mock_candidate_repo, tmp_path, monkeypatch,
    ):
        schema_dir = tmp_path / "graph_schemas"
        schema_dir.mkdir()
        (schema_dir / "test.yaml").write_text(
            "version: 1\nprompt_focus: x\nnodes: [Person]\nrelationships: []\n"
        )
        monkeypatch.setattr(
            "src.api.routes.graph_schema_helpers._SCHEMA_DIR", schema_dir,
        )
        client = TestClient(app)
        resp = client.post(
            "/api/v1/admin/graph-schema/candidates/rename",
            json={
                "kb_id": "test",
                "candidate_type": "node",
                "label": "MeetingRoom",
                "new_label": "Room",
                "approved_by": "admin@test",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["new_label"] == "Room"
        import yaml
        data = yaml.safe_load((schema_dir / "test.yaml").read_text())
        assert "Room" in data["nodes"]
        assert "MeetingRoom" not in data["nodes"]


class TestBootstrapRunTrigger:
    def test_trigger_enqueues_job(self, app, mock_run_repo):
        with patch(
            "src.api.routes.graph_schema._enqueue_bootstrap",
            new=AsyncMock(return_value={"job_id": "j1"}),
        ) as enq:
            client = TestClient(app)
            resp = client.post(
                "/api/v1/admin/graph-schema/bootstrap/test/run",
                json={"triggered_by_user": "admin@test"},
            )
        assert resp.status_code == 200
        assert resp.json()["job_id"] == "j1"
        enq.assert_awaited_once()

    def test_trigger_returns_409_when_already_running(
        self, app, mock_run_repo,
    ):
        mock_run_repo.has_running = AsyncMock(return_value=True)
        client = TestClient(app)
        resp = client.post(
            "/api/v1/admin/graph-schema/bootstrap/test/run",
            json={"triggered_by_user": "admin@test"},
        )
        assert resp.status_code == 409
