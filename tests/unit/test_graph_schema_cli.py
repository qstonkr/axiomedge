"""CLI: scaffold + dry-run commands."""

from __future__ import annotations

import pytest
import yaml

from src.cli.graph_schema_cli import scaffold_source_default


class TestScaffold:
    def test_scaffold_writes_template(self, tmp_path, monkeypatch):
        schemas = tmp_path / "graph_schemas"
        (schemas / "_defaults").mkdir(parents=True)
        monkeypatch.setattr(
            "src.cli.graph_schema_cli._DEFAULTS_DIR", schemas / "_defaults",
        )
        path = scaffold_source_default("jira")
        assert path.exists()
        data = yaml.safe_load(path.read_text())
        assert "nodes" in data
        assert "relationships" in data
        assert "prompt_focus" in data

    def test_scaffold_rejects_existing(self, tmp_path, monkeypatch):
        schemas = tmp_path / "graph_schemas"
        (schemas / "_defaults").mkdir(parents=True)
        (schemas / "_defaults" / "jira.yaml").write_text("existing: yes\n")
        monkeypatch.setattr(
            "src.cli.graph_schema_cli._DEFAULTS_DIR", schemas / "_defaults",
        )
        with pytest.raises(FileExistsError):
            scaffold_source_default("jira")

    def test_scaffold_rejects_unsafe_name(self, tmp_path, monkeypatch):
        schemas = tmp_path / "graph_schemas"
        (schemas / "_defaults").mkdir(parents=True)
        monkeypatch.setattr(
            "src.cli.graph_schema_cli._DEFAULTS_DIR", schemas / "_defaults",
        )
        with pytest.raises(ValueError):
            scaffold_source_default("../evil")
