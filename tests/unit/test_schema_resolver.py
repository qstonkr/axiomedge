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


class TestLayerMerge:
    def test_only_generic_fallback(self, schema_dir: Path):
        # No YAMLs present at all
        schema = SchemaResolver.resolve(kb_id=None, source_type=None)
        # Generic hardcoded fallback kicks in
        assert "Person" in schema.nodes
        assert schema.source_layers == ("generic",)

    def test_only_d_layer(self, schema_dir: Path):
        (schema_dir / "_defaults" / "confluence.yaml").write_text(
            "nodes: [Page, Person]\n"
            "relationships: [AUTHORED]\n"
            "prompt_focus: conf\n"
        )
        schema = SchemaResolver.resolve(kb_id=None, source_type="confluence")
        assert set(schema.nodes) == {"Page", "Person"}
        assert set(schema.relationships) == {"AUTHORED"}
        assert schema.prompt_focus == "conf"
        assert schema.source_layers == ("D:confluence",)

    def test_only_a_layer(self, schema_dir: Path):
        (schema_dir / "g-espa.yaml").write_text(
            "nodes: [Store, Person]\n"
            "relationships: [OPERATES]\n"
            "prompt_focus: espa\n"
        )
        schema = SchemaResolver.resolve(kb_id="g-espa", source_type=None)
        assert set(schema.nodes) == {"Store", "Person"}
        assert schema.prompt_focus == "espa"
        assert schema.source_layers == ("A:g-espa",)

    def test_a_plus_d_merge_union(self, schema_dir: Path):
        (schema_dir / "_defaults" / "confluence.yaml").write_text(
            "nodes: [Page, Person]\n"
            "relationships: [AUTHORED, MENTIONS]\n"
            "prompt_focus: conf\n"
        )
        (schema_dir / "g-espa.yaml").write_text(
            "nodes: [Store, Person]\n"
            "relationships: [OPERATES, MENTIONS]\n"
            "prompt_focus: espa\n"
        )
        schema = SchemaResolver.resolve(kb_id="g-espa", source_type="confluence")
        # Nodes: union of {Page, Person, Store}
        assert set(schema.nodes) == {"Page", "Person", "Store"}
        # Rels: union
        assert set(schema.relationships) == {"AUTHORED", "MENTIONS", "OPERATES"}
        # prompt_focus: A wins (last layer)
        assert schema.prompt_focus == "espa"
        # Provenance
        assert schema.source_layers == ("D:confluence", "A:g-espa")

    def test_nodes_sorted_deterministic(self, schema_dir: Path):
        (schema_dir / "_defaults" / "confluence.yaml").write_text(
            "nodes: [Zebra, Apple, Mango]\n"
            "relationships: []\n"
            "prompt_focus: c\n"
        )
        schema = SchemaResolver.resolve(kb_id=None, source_type="confluence")
        assert schema.nodes == ("Apple", "Mango", "Zebra")  # sorted

    def test_options_from_a_wins(self, schema_dir: Path):
        (schema_dir / "_defaults" / "confluence.yaml").write_text(
            "nodes: []\nrelationships: []\nprompt_focus: c\n"
            "options:\n  schema_evolution: batch\n"
        )
        (schema_dir / "special.yaml").write_text(
            "nodes: []\nrelationships: []\nprompt_focus: s\n"
            "options:\n  schema_evolution: realtime\n"
        )
        schema = SchemaResolver.resolve(kb_id="special", source_type="confluence")
        assert schema.options.schema_evolution == "realtime"

    def test_index_spec_parsed(self, schema_dir: Path):
        (schema_dir / "g-espa.yaml").write_text(
            "nodes: [Meeting]\n"
            "relationships: []\n"
            "prompt_focus: x\n"
            "indexes:\n"
            "  Meeting:\n"
            "    - property: scheduled_at\n"
            "      index_type: btree\n"
            "    - property: title\n"
            "      index_type: fulltext\n"
        )
        schema = SchemaResolver.resolve(kb_id="g-espa", source_type=None)
        specs = schema.indexes["Meeting"]
        assert len(specs) == 2
        assert specs[0].property == "scheduled_at"
        assert specs[1].index_type == "fulltext"
