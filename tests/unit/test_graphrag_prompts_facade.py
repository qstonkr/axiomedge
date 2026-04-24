"""Tests: legacy imports from prompts.py keep working after YAML migration."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.pipelines.graphrag.schema_resolver import invalidate_cache
from src.pipelines.graphrag.schema_types import SchemaProfile


@pytest.fixture(autouse=True)
def _clean_cache():
    invalidate_cache()
    yield
    invalidate_cache()


@pytest.fixture
def schema_dir(tmp_path, monkeypatch):
    d = tmp_path / "graph_schemas"
    (d / "_defaults").mkdir(parents=True)
    monkeypatch.setattr(
        "src.pipelines.graphrag.schema_resolver._SCHEMA_DIR", d,
    )
    monkeypatch.setattr(
        "src.pipelines.graphrag.schema_resolver._DEFAULTS_DIR", d / "_defaults",
    )
    return d


class TestLegacyKbSchemaProfilesProxy:
    """KB_SCHEMA_PROFILES behaves like a dict for existing callers."""

    def test_proxy_getitem(self, schema_dir: Path):
        (schema_dir / "g-espa.yaml").write_text(
            "nodes: [Store, Person]\n"
            "relationships: [OPERATES]\n"
            "prompt_focus: espa\n"
        )
        from src.pipelines.graphrag.prompts import KB_SCHEMA_PROFILES

        # Force a fresh proxy per-test — the module-level proxy is
        # lazily built once; clear its internal cache.
        KB_SCHEMA_PROFILES._cache = None  # type: ignore[attr-defined]
        profile = KB_SCHEMA_PROFILES["g-espa"]
        assert set(profile["nodes"]) == {"Store", "Person"}
        assert set(profile["relationships"]) == {"OPERATES"}
        assert profile["prompt_focus"] == "espa"

    def test_proxy_contains(self, schema_dir: Path):
        (schema_dir / "a-ari.yaml").write_text(
            "nodes: [Store]\nrelationships: []\nprompt_focus: x\n"
        )
        from src.pipelines.graphrag.prompts import KB_SCHEMA_PROFILES

        KB_SCHEMA_PROFILES._cache = None  # type: ignore[attr-defined]
        assert "a-ari" in KB_SCHEMA_PROFILES
        assert "nonexistent" not in KB_SCHEMA_PROFILES

    def test_proxy_get_default(self, schema_dir: Path):
        from src.pipelines.graphrag.prompts import KB_SCHEMA_PROFILES

        KB_SCHEMA_PROFILES._cache = None  # type: ignore[attr-defined]
        assert KB_SCHEMA_PROFILES.get("nonexistent") is None
        assert KB_SCHEMA_PROFILES.get("nonexistent", {"default": True}) == {
            "default": True,
        }


class TestGetKbSchema:
    def test_get_kb_schema_returns_dict(self, schema_dir: Path):
        (schema_dir / "g-espa.yaml").write_text(
            "nodes: [Store]\nrelationships: [OPERATES]\nprompt_focus: x\n"
        )
        from src.pipelines.graphrag.prompts import get_kb_schema

        result = get_kb_schema("g-espa")
        assert isinstance(result, dict)
        assert "Store" in result["nodes"]


class TestBuildExtractionPrompt:
    """Existing callers pass ``prompt_template`` to LLM; the LLM does
    ``.format(document=...)``. So the returned string must preserve the
    ``{document}`` placeholder, NOT inline the doc text.
    """

    def test_with_kb_id_string_returns_template(self, schema_dir: Path):
        (schema_dir / "g-espa.yaml").write_text(
            "nodes: [Store, Person]\n"
            "relationships: [OPERATES]\n"
            "prompt_focus: espa-focus\n"
        )
        from src.pipelines.graphrag.prompts import build_extraction_prompt

        # _document is accepted for signature compat but ignored; the returned
        # string contains the literal `{document}` placeholder for later .format
        template = build_extraction_prompt("ignored doc", "g-espa")
        assert "{document}" in template
        assert "Store" in template
        assert "OPERATES" in template
        assert "espa-focus" in template

    def test_template_formats_cleanly(self, schema_dir: Path):
        (schema_dir / "g-espa.yaml").write_text(
            "nodes: [Store]\nrelationships: [OPERATES]\nprompt_focus: x\n"
        )
        from src.pipelines.graphrag.prompts import build_extraction_prompt

        template = build_extraction_prompt("", "g-espa")
        # Must be .format() safe — any literal braces in the JSON shape
        # have to be doubled ({{ and }})
        filled = template.format(document="SAMPLE DOC")
        assert "SAMPLE DOC" in filled
        # JSON example braces survived (format call should not consume them)
        assert '"nodes":[' in filled or '"nodes": [' in filled

    def test_with_schema_profile_object(self, schema_dir: Path):
        from src.pipelines.graphrag.prompts import build_extraction_prompt

        schema = SchemaProfile(
            nodes=("X",), relationships=("Y",), prompt_focus="f",
        )
        template = build_extraction_prompt("", schema)
        assert "X" in template
        assert "Y" in template
        assert "f" in template
        assert "{document}" in template

    def test_with_none_falls_back_to_generic(self, schema_dir: Path):
        from src.pipelines.graphrag.prompts import build_extraction_prompt

        template = build_extraction_prompt("", None)
        # Hardcoded generic_fallback includes Person/Team/Topic
        assert "Person" in template
        assert "{document}" in template
