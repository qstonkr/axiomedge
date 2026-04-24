"""Tests for SchemaResolver — YAML loader, merge, hot-reload."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from src.pipelines.graphrag.schema_resolver import (
    SchemaResolver,
    invalidate_cache,
)


@pytest.fixture(autouse=True)
def _clean_cache():
    """Each test starts with a clean resolver cache."""
    invalidate_cache()
    yield
    invalidate_cache()


@pytest.fixture
def schema_dir(tmp_path, monkeypatch):
    """Redirect resolver to a per-test temp directory."""
    d = tmp_path / "graph_schemas"
    (d / "_defaults").mkdir(parents=True)
    monkeypatch.setattr(
        "src.pipelines.graphrag.schema_resolver._SCHEMA_DIR", d,
    )
    monkeypatch.setattr(
        "src.pipelines.graphrag.schema_resolver._DEFAULTS_DIR", d / "_defaults",
    )
    return d


class TestYamlLoader:
    def test_load_valid_yaml(self, schema_dir: Path):
        (schema_dir / "_defaults" / "_generic.yaml").write_text(
            "version: 1\n"
            "prompt_focus: test\n"
            "nodes: [Person, Team]\n"
            "relationships: [MEMBER_OF]\n"
        )
        schema = SchemaResolver.resolve(kb_id=None, source_type=None)
        assert schema.nodes == ("Person", "Team")
        # Generic fallback has source_layers=("generic",) — minimum: no crash
        assert len(schema.source_layers) >= 1

    def test_load_invalid_yaml_returns_generic_fallback(self, schema_dir: Path):
        # Write a _generic.yaml so fallback has something; then break a specific one
        (schema_dir / "_defaults" / "_generic.yaml").write_text(
            "nodes: [Person]\nrelationships: []\nprompt_focus: generic\n"
        )
        (schema_dir / "_defaults" / "broken.yaml").write_text(
            "nodes: [Person\n  bad yaml :"
        )
        schema = SchemaResolver.resolve(kb_id=None, source_type="broken")
        # Parse failure falls through → source default missing → generic
        assert "Person" in schema.nodes

    def test_mtime_cache_hit(self, schema_dir: Path, monkeypatch):
        p = schema_dir / "_defaults" / "confluence.yaml"
        p.write_text("nodes: [A]\nrelationships: []\nprompt_focus: v1\n")

        s1 = SchemaResolver.resolve(kb_id=None, source_type="confluence")
        assert s1.prompt_focus == "v1"

        # Replace file content WITHOUT bumping mtime — cache should still hit
        original_mtime = p.stat().st_mtime
        p.write_text("nodes: [B]\nrelationships: []\nprompt_focus: v2\n")
        import os
        os.utime(p, (original_mtime, original_mtime))

        s2 = SchemaResolver.resolve(kb_id=None, source_type="confluence")
        assert s2.prompt_focus == "v1", "cache should still return v1"

    def test_mtime_change_invalidates_cache(self, schema_dir: Path):
        p = schema_dir / "_defaults" / "confluence.yaml"
        p.write_text("nodes: [A]\nrelationships: []\nprompt_focus: v1\n")
        s1 = SchemaResolver.resolve(kb_id=None, source_type="confluence")
        assert s1.prompt_focus == "v1"

        # Advance time + rewrite — mtime should differ
        time.sleep(0.05)
        p.write_text("nodes: [B]\nrelationships: []\nprompt_focus: v2\n")
        s2 = SchemaResolver.resolve(kb_id=None, source_type="confluence")
        assert s2.prompt_focus == "v2", "mtime change should trigger reload"

    def test_nonexistent_source_falls_back_to_generic(self, schema_dir: Path):
        (schema_dir / "_defaults" / "_generic.yaml").write_text(
            "nodes: [GenericNode]\nrelationships: []\nprompt_focus: g\n"
        )
        schema = SchemaResolver.resolve(kb_id=None, source_type="nonexistent")
        assert "GenericNode" in schema.nodes
