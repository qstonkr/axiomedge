"""Tests for SchemaProfile / SchemaOptions / IndexSpec dataclasses."""

from __future__ import annotations

import pytest

from src.pipelines.graphrag.schema_types import (
    IndexSpec,
    SchemaOptions,
    SchemaProfile,
)


class TestIndexSpec:
    def test_defaults(self):
        spec = IndexSpec(property="scheduled_at")
        assert spec.property == "scheduled_at"
        assert spec.index_type == "btree"

    def test_explicit_type(self):
        spec = IndexSpec(property="title", index_type="fulltext")
        assert spec.index_type == "fulltext"

    def test_immutable(self):
        spec = IndexSpec(property="x")
        with pytest.raises(Exception):  # FrozenInstanceError
            spec.property = "y"  # type: ignore[misc]


class TestSchemaOptions:
    def test_defaults(self):
        opts = SchemaOptions()
        assert opts.disable_bootstrap is False
        assert opts.schema_evolution == "batch"
        assert opts.bootstrap_sample_size == 100

    def test_immutable(self):
        opts = SchemaOptions()
        with pytest.raises(Exception):
            opts.disable_bootstrap = True  # type: ignore[misc]


class TestSchemaProfile:
    def test_minimal(self):
        profile = SchemaProfile(
            nodes=("Person",),
            relationships=("MEMBER_OF",),
            prompt_focus="사람",
        )
        assert profile.nodes == ("Person",)
        assert profile.relationships == ("MEMBER_OF",)
        assert profile.prompt_focus == "사람"
        assert profile.version == 1
        assert profile.source_layers == ()

    def test_with_indexes(self):
        profile = SchemaProfile(
            nodes=("Meeting",),
            relationships=(),
            prompt_focus="",
            indexes={"Meeting": (IndexSpec(property="scheduled_at"),)},
        )
        assert profile.indexes["Meeting"][0].property == "scheduled_at"

    def test_options_composition(self):
        opts = SchemaOptions(schema_evolution="realtime")
        profile = SchemaProfile(
            nodes=(), relationships=(), prompt_focus="", options=opts,
        )
        assert profile.options.schema_evolution == "realtime"

    def test_immutable(self):
        profile = SchemaProfile(nodes=(), relationships=(), prompt_focus="")
        with pytest.raises(Exception):
            profile.nodes = ("X",)  # type: ignore[misc]
