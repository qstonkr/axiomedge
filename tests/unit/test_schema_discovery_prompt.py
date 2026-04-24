"""Schema discovery prompt + strict JSON parser."""

from __future__ import annotations

import pytest

from src.pipelines.graphrag.schema_prompts import (
    SCHEMA_DISCOVERY_PROMPT,
    parse_discovery_response,
)


class TestPromptShape:
    def test_prompt_contains_all_placeholders(self):
        # .format() must accept all 5 named placeholders
        filled = SCHEMA_DISCOVERY_PROMPT.format(
            kb_id="test",
            n=3,
            existing_nodes="Person, Team",
            existing_rels="MEMBER_OF",
            docs="[doc 1] content",
        )
        assert "test" in filled
        assert "Person, Team" in filled


class TestParser:
    def test_valid_json_parsed(self):
        raw = (
            '{"new_node_types":['
            '{"label":"Meeting","reason":"x","confidence":0.9,"examples":["sample"]}'
            '],"new_relation_types":[]}'
        )
        out = parse_discovery_response(raw)
        assert len(out.node_candidates) == 1
        assert out.node_candidates[0].label == "Meeting"
        assert out.node_candidates[0].confidence == 0.9
        assert out.relation_candidates == []

    def test_code_fence_stripped(self):
        raw = '```json\n{"new_node_types":[],"new_relation_types":[]}\n```'
        out = parse_discovery_response(raw)
        assert out.node_candidates == []

    def test_malformed_raises_valueerror(self):
        with pytest.raises(ValueError):
            parse_discovery_response("not json")

    def test_missing_label_silently_dropped(self):
        raw = '{"new_node_types":[{"confidence":0.9}],"new_relation_types":[]}'
        out = parse_discovery_response(raw)
        # Silently drop malformed individual entries
        assert out.node_candidates == []

    def test_invalid_node_label_dropped(self):
        # Label must match [A-Z][a-zA-Z0-9_]*
        raw = (
            '{"new_node_types":['
            '{"label":"lowercase","confidence":0.9,"examples":[]},'
            '{"label":"UPPER_CASE","confidence":0.9,"examples":[]},'
            '{"label":"Valid","confidence":0.9,"examples":[]}'
            '],"new_relation_types":[]}'
        )
        out = parse_discovery_response(raw)
        labels = {c.label for c in out.node_candidates}
        # lowercase rejected; UPPER_CASE and Valid pass node regex
        assert "Valid" in labels
        assert "lowercase" not in labels

    def test_invalid_relation_label_dropped(self):
        # Relation label must match SCREAMING_SNAKE
        raw = (
            '{"new_node_types":[],"new_relation_types":['
            '{"label":"camelCase","source":"A","target":"B","confidence":0.9,"examples":[]},'
            '{"label":"VALID_REL","source":"A","target":"B","confidence":0.9,"examples":[]}'
            ']}'
        )
        out = parse_discovery_response(raw)
        labels = {c.label for c in out.relation_candidates}
        assert "VALID_REL" in labels
        assert "camelCase" not in labels

    def test_relation_source_target_preserved(self):
        raw = (
            '{"new_node_types":[],"new_relation_types":['
            '{"label":"ATTENDED","source":"Person","target":"Meeting",'
            '"confidence":0.95,"examples":["김철수가 회의 참석"]}'
            ']}'
        )
        out = parse_discovery_response(raw)
        assert out.relation_candidates[0].source == "Person"
        assert out.relation_candidates[0].target == "Meeting"
