"""Unit tests for dashboard/services/api/admin.py."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Make dashboard modules importable
_DASHBOARD_DIR = str(Path(__file__).resolve().parents[2] / "dashboard")
if _DASHBOARD_DIR not in sys.path:
    sys.path.insert(0, _DASHBOARD_DIR)

# Patch streamlit before importing the module
_st_mock = MagicMock()
_st_mock.cache_data = lambda **kw: lambda f: f
sys.modules.setdefault("streamlit", _st_mock)

from services.api import admin
import services.api.admin as _admin_module


class TestGraphFunctions:
    def test_get_graph_integrity(self, monkeypatch):
        m = MagicMock(return_value={"ok": True})
        monkeypatch.setattr(_admin_module, "_get", m)
        result = admin.get_graph_integrity()
        m.assert_called_once_with("/api/v1/admin/graph/integrity")
        assert result == {"ok": True}

    def test_run_graph_integrity_check(self, monkeypatch):
        m = MagicMock(return_value={"ok": True})
        monkeypatch.setattr(_admin_module, "_post", m)
        result = admin.run_graph_integrity_check()
        m.assert_called_once_with("/api/v1/admin/graph/integrity/run")

    def test_get_graph_stats(self, monkeypatch):
        m = MagicMock(return_value={"nodes": 10})
        monkeypatch.setattr(_admin_module, "_get", m)
        admin.get_graph_stats()
        m.assert_called_once_with("/api/v1/admin/graph/stats")

    def test_get_graph_communities(self, monkeypatch):
        m = MagicMock(return_value={})
        monkeypatch.setattr(_admin_module, "_get", m)
        admin.get_graph_communities()
        m.assert_called_once_with("/api/v1/admin/graph/communities")

    def test_graph_search_basic(self, monkeypatch):
        m = MagicMock(return_value={"nodes": []})
        monkeypatch.setattr(_admin_module, "_post", m)
        admin.graph_search("test query")
        m.assert_called_once()
        args = m.call_args
        assert args[0][0] == "/api/v1/admin/graph/search"
        body = args[0][1]
        assert body["query"] == "test query"
        assert body["max_nodes"] == 50
        assert body["max_hops"] == 2

    def test_graph_search_with_node_types(self, monkeypatch):
        m = MagicMock(return_value={})
        monkeypatch.setattr(_admin_module, "_post", m)
        admin.graph_search("q", node_types=["Person"])
        body = m.call_args[0][1]
        assert body["node_types"] == ["Person"]

    def test_graph_search_clamps_max_nodes(self, monkeypatch):
        m = MagicMock(return_value={})
        monkeypatch.setattr(_admin_module, "_post", m)
        admin.graph_search("q", max_nodes=999)
        body = m.call_args[0][1]
        assert body["max_nodes"] == 200  # clamped

    def test_graph_search_clamps_max_hops(self, monkeypatch):
        m = MagicMock(return_value={})
        monkeypatch.setattr(_admin_module, "_post", m)
        admin.graph_search("q", max_hops=10)
        body = m.call_args[0][1]
        assert body["max_hops"] == 5  # clamped

    def test_graph_search_clamps_low(self, monkeypatch):
        m = MagicMock(return_value={})
        monkeypatch.setattr(_admin_module, "_post", m)
        admin.graph_search("q", max_nodes=0, max_hops=0)
        body = m.call_args[0][1]
        assert body["max_nodes"] == 1
        assert body["max_hops"] == 1

    def test_graph_expand_basic(self, monkeypatch):
        m = MagicMock(return_value={})
        monkeypatch.setattr(_admin_module, "_post", m)
        admin.graph_expand("node1")
        body = m.call_args[0][1]
        assert body["node_id"] == "node1"
        assert body["max_neighbors"] == 30

    def test_graph_expand_with_types(self, monkeypatch):
        m = MagicMock(return_value={})
        monkeypatch.setattr(_admin_module, "_post", m)
        admin.graph_expand("node1", node_types=["Team"])
        body = m.call_args[0][1]
        assert body["node_types"] == ["Team"]

    def test_graph_experts(self, monkeypatch):
        m = MagicMock(return_value={})
        monkeypatch.setattr(_admin_module, "_post", m)
        admin.graph_experts("ML", limit=5)
        body = m.call_args[0][1]
        assert body["topic"] == "ML"
        assert body["limit"] == 5

    def test_graph_path(self, monkeypatch):
        m = MagicMock(return_value={})
        monkeypatch.setattr(_admin_module, "_post", m)
        admin.graph_path("A", "B")
        body = m.call_args[0][1]
        assert body["from_node_id"] == "A"
        assert body["to_node_id"] == "B"

    def test_graph_impact(self, monkeypatch):
        m = MagicMock(return_value={})
        monkeypatch.setattr(_admin_module, "_post", m)
        admin.graph_impact("node1", max_hops=3)
        body = m.call_args[0][1]
        assert body["node_id"] == "node1"
        assert body["max_hops"] == 3

    def test_graph_health(self, monkeypatch):
        m = MagicMock(return_value={"status": "ok"})
        monkeypatch.setattr(_admin_module, "_get", m)
        admin.graph_health()
        m.assert_called_once_with("/api/v1/admin/graph/health")

    def test_graph_timeline(self, monkeypatch):
        m = MagicMock(return_value={})
        monkeypatch.setattr(_admin_module, "_post", m)
        admin.graph_timeline("node1")
        body = m.call_args[0][1]
        assert body["node_id"] == "node1"

    def test_graph_integrity_check_post(self, monkeypatch):
        m = MagicMock(return_value={})
        monkeypatch.setattr(_admin_module, "_post", m)
        admin.graph_integrity_check()
        m.assert_called_once_with("/api/v1/admin/graph/integrity/check")

    def test_graph_experts_search(self, monkeypatch):
        m = MagicMock(return_value={})
        monkeypatch.setattr(_admin_module, "_get", m)
        admin.graph_experts_search("topic")
        m.assert_called_once_with("/api/v1/admin/graph/experts", topic="topic")

    def test_graph_cleanup_analyze_no_kb(self, monkeypatch):
        m = MagicMock(return_value={})
        monkeypatch.setattr(_admin_module, "_post", m)
        admin.graph_cleanup_analyze()
        body = m.call_args[0][1]
        assert "kb_id" not in body

    def test_graph_cleanup_analyze_with_kb(self, monkeypatch):
        m = MagicMock(return_value={})
        monkeypatch.setattr(_admin_module, "_post", m)
        admin.graph_cleanup_analyze(kb_id="kb1")
        body = m.call_args[0][1]
        assert body["kb_id"] == "kb1"

    def test_graph_cleanup_apply(self, monkeypatch):
        m = MagicMock(return_value={})
        monkeypatch.setattr(_admin_module, "_post", m)
        admin.graph_cleanup_apply(kb_id="kb1")
        body = m.call_args[0][1]
        assert body["apply"] is True
        assert body["kb_id"] == "kb1"

    def test_graph_ai_classify_defaults(self, monkeypatch):
        m = MagicMock(return_value={})
        monkeypatch.setattr(_admin_module, "_post", m)
        admin.graph_ai_classify()
        body = m.call_args[0][1]
        assert body["limit"] == 200
        assert body["apply"] is False
        assert "kb_id" not in body

    def test_graph_ai_classify_with_params(self, monkeypatch):
        m = MagicMock(return_value={})
        monkeypatch.setattr(_admin_module, "_post", m)
        admin.graph_ai_classify(limit=50, kb_id="kb2", apply=True)
        body = m.call_args[0][1]
        assert body["limit"] == 50
        assert body["apply"] is True
        assert body["kb_id"] == "kb2"


class TestEmbeddingAndCache:
    def test_get_embedding_stats(self, monkeypatch):
        m = MagicMock(return_value={})
        monkeypatch.setattr(_admin_module, "_get", m)
        admin.get_embedding_stats()
        m.assert_called_once_with("/api/v1/admin/embedding/stats")

    def test_get_cache_stats(self, monkeypatch):
        m = MagicMock(return_value={})
        monkeypatch.setattr(_admin_module, "_get", m)
        admin.get_cache_stats()
        m.assert_called_once_with("/api/v1/admin/cache/stats")


class TestPipelineFunctions:
    def test_get_pipeline_status(self, monkeypatch):
        m = MagicMock(return_value={})
        monkeypatch.setattr(_admin_module, "_get", m)
        admin.get_pipeline_status()
        m.assert_called_once_with("/api/v1/admin/pipeline/status")

    def test_get_pipeline_run_detail(self, monkeypatch):
        m = MagicMock(return_value={})
        monkeypatch.setattr(_admin_module, "_get", m)
        admin.get_pipeline_run_detail("run123")
        m.assert_called_once_with("/api/v1/admin/pipeline/runs/run123")

    def test_get_latest_experiment_run(self, monkeypatch):
        m = MagicMock(return_value={})
        monkeypatch.setattr(_admin_module, "_get", m)
        admin.get_latest_experiment_run("kb1")
        m.assert_called_once_with("/api/v1/admin/pipeline/experiments/kb1/latest")

    def test_trigger_kb_sync_minimal(self, monkeypatch):
        m = MagicMock(return_value={})
        monkeypatch.setattr(_admin_module, "_post", m)
        admin.trigger_kb_sync("kb1")
        body = m.call_args[0][1]
        assert body["mode"] == "canonical"
        assert "source_type" not in body

    def test_trigger_kb_sync_full(self, monkeypatch):
        m = MagicMock(return_value={})
        monkeypatch.setattr(_admin_module, "_post", m)
        admin.trigger_kb_sync("kb1", mode="incremental", source_type="jira",
                              sync_source_name="proj")
        body = m.call_args[0][1]
        assert body["mode"] == "incremental"
        assert body["source_type"] == "jira"
        assert body["sync_source_name"] == "proj"

    def test_validate_kb_sync(self, monkeypatch):
        m = MagicMock(return_value={})
        monkeypatch.setattr(_admin_module, "_post", m)
        admin.validate_kb_sync("kb1", source_type="confluence")
        body = m.call_args[0][1]
        assert body["source_type"] == "confluence"

    def test_publish_experiment_dry_run(self, monkeypatch):
        m = MagicMock(return_value={})
        monkeypatch.setattr(_admin_module, "_post", m)
        admin.publish_experiment_dry_run("kb1", run_id="r1")
        body = m.call_args[0][1]
        assert body["kb_id"] == "kb1"
        assert body["run_id"] == "r1"

    def test_publish_experiment_execute(self, monkeypatch):
        m = MagicMock(return_value={})
        monkeypatch.setattr(_admin_module, "_post", m)
        admin.publish_experiment_execute("kb1")
        body = m.call_args[0][1]
        assert body["kb_id"] == "kb1"

    def test_get_pipeline_metrics(self, monkeypatch):
        m = MagicMock(return_value={})
        monkeypatch.setattr(_admin_module, "_get", m)
        admin.get_pipeline_metrics()
        m.assert_called_once_with("/api/v1/admin/pipeline/metrics")

    def test_get_pipeline_gates_stats(self, monkeypatch):
        m = MagicMock(return_value={})
        monkeypatch.setattr(_admin_module, "_get", m)
        admin.get_pipeline_gates_stats()
        m.assert_called_once_with("/api/v1/admin/pipeline/gates/stats")

    def test_get_pipeline_gate_blocked(self, monkeypatch):
        m = MagicMock(return_value={})
        monkeypatch.setattr(_admin_module, "_get", m)
        admin.get_pipeline_gate_blocked("g1")
        m.assert_called_once_with("/api/v1/admin/pipeline/gates/g1/blocked")

    def test_get_pipeline_gates_blocked(self, monkeypatch):
        m = MagicMock(return_value={})
        monkeypatch.setattr(_admin_module, "_get", m)
        admin.get_pipeline_gates_blocked()
        m.assert_called_once_with("/api/v1/admin/pipeline/gates/blocked")
