"""Tests for YAML writer + label merge + (stubbed) git commit."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from src.api.routes.graph_schema_helpers import merge_label_into_yaml


@pytest.fixture
def kb_yaml_dir(tmp_path, monkeypatch):
    d = tmp_path / "graph_schemas"
    d.mkdir()
    monkeypatch.setattr(
        "src.api.routes.graph_schema_helpers._SCHEMA_DIR", d,
    )
    return d


class TestMergeLabelIntoYaml:
    def test_adds_new_node_label(self, kb_yaml_dir: Path):
        (kb_yaml_dir / "test.yaml").write_text(
            "version: 1\nprompt_focus: x\n"
            "nodes: [Person]\nrelationships: [MEMBER_OF]\n"
            "options:\n  disable_bootstrap: false\n"
        )
        path = merge_label_into_yaml(
            kb_id="test", candidate_type="node", label="Meeting",
            approved_by="admin@test",
        )
        assert path.name == "test.yaml"
        data = yaml.safe_load(path.read_text())
        assert "Meeting" in data["nodes"]
        assert "Person" in data["nodes"]  # preserved
        assert data["version"] == 2
        assert data["_metadata"]["last_approved_by"] == "admin@test"
        assert any(
            e["label"] == "Meeting" and e["type"] == "node"
            for e in data["_metadata"]["approved_candidates"]
        )

    def test_adds_new_relationship(self, kb_yaml_dir: Path):
        (kb_yaml_dir / "test.yaml").write_text(
            "version: 1\nprompt_focus: x\n"
            "nodes: [Person]\nrelationships: [MEMBER_OF]\n"
        )
        path = merge_label_into_yaml(
            kb_id="test", candidate_type="relationship", label="ATTENDED",
            approved_by="admin@test",
        )
        data = yaml.safe_load(path.read_text())
        assert "ATTENDED" in data["relationships"]

    def test_idempotent_when_label_already_present(self, kb_yaml_dir: Path):
        (kb_yaml_dir / "test.yaml").write_text(
            "version: 3\nprompt_focus: x\n"
            "nodes: [Person, Meeting]\nrelationships: []\n"
        )
        merge_label_into_yaml(
            kb_id="test", candidate_type="node", label="Meeting",
            approved_by="admin@test",
        )
        data = yaml.safe_load((kb_yaml_dir / "test.yaml").read_text())
        assert data["nodes"].count("Meeting") == 1

    def test_creates_new_yaml_for_unknown_kb(self, kb_yaml_dir: Path):
        path = merge_label_into_yaml(
            kb_id="brand-new", candidate_type="node", label="Topic",
            approved_by="admin@test",
        )
        assert path.exists()
        data = yaml.safe_load(path.read_text())
        assert data["kb_id"] == "brand-new"
        assert "Topic" in data["nodes"]
        assert data["version"] == 1
