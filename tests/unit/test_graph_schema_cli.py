"""CLI: scaffold + dry-run commands."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import yaml

from src.cli.graph_schema_cli import dry_run, main, scaffold_source_default


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


class TestDryRun:
    def test_dry_run_returns_resolver_snapshot(self):
        fake_schema = MagicMock()
        fake_schema.version = 2
        fake_schema.source_layers = ["_defaults/confluence.yaml", "kb/test.yaml"]
        fake_schema.nodes = ("Person", "Topic")
        fake_schema.relationships = ("MENTIONS",)
        fake_schema.prompt_focus = "meeting notes"

        with patch(
            "src.pipelines.graphrag.SchemaResolver.resolve",
            return_value=fake_schema,
        ):
            out = dry_run("test")
        assert out["kb_id"] == "test"
        assert out["version"] == 2
        assert out["nodes"] == ["Person", "Topic"]
        assert out["relationships"] == ["MENTIONS"]
        assert out["prompt_focus"] == "meeting notes"


class TestMain:
    def test_main_scaffold_ok(self, tmp_path, monkeypatch, capsys):
        schemas = tmp_path / "graph_schemas"
        (schemas / "_defaults").mkdir(parents=True)
        monkeypatch.setattr(
            "src.cli.graph_schema_cli._DEFAULTS_DIR", schemas / "_defaults",
        )
        rc = main(["scaffold", "jira"])
        assert rc == 0
        captured = capsys.readouterr()
        assert "wrote" in captured.out

    def test_main_scaffold_rejects_unsafe(self, tmp_path, monkeypatch, capsys):
        schemas = tmp_path / "graph_schemas"
        (schemas / "_defaults").mkdir(parents=True)
        monkeypatch.setattr(
            "src.cli.graph_schema_cli._DEFAULTS_DIR", schemas / "_defaults",
        )
        rc = main(["scaffold", "../evil"])
        assert rc == 2
        err = capsys.readouterr().err
        assert "error" in err

    def test_main_dry_run_prints_json(self, capsys):
        fake_schema = MagicMock()
        fake_schema.version = 1
        fake_schema.source_layers = []
        fake_schema.nodes = ()
        fake_schema.relationships = ()
        fake_schema.prompt_focus = ""

        with patch(
            "src.pipelines.graphrag.SchemaResolver.resolve",
            return_value=fake_schema,
        ):
            rc = main(["dry-run", "test"])
        assert rc == 0
        out = capsys.readouterr().out
        assert '"kb_id": "test"' in out
