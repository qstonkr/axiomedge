"""Regression tests for GraphRAG extractor bugs.

Bug A: LLM non-deterministic output occasionally sets id/source/target/type as
a JSON list instead of a string. Unprotected set/dict-key operations raise
TypeError: unhashable type: 'list' and the entire document's graph is
discarded silently.

Bug B: "경쟁점" / "자점" / "경쟁사" etc. are generic nouns, not real stores.
They get extracted as Store entities and collide on Neo4j's name UNIQUE
constraint (store_name_unique) because the MERGE key is `id` while the
registry-level uniqueness constraint is on `name`.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from src.pipelines.graphrag.extractor import (
    GraphRAGExtractor,
    _validate_entity,
)


def _make_extractor() -> GraphRAGExtractor:
    return GraphRAGExtractor(llm_client=MagicMock(), neo4j_driver=MagicMock())


# =============================================================================
# Bug A — list-typed LLM fields must not raise
# =============================================================================


class TestBugAListTypedFields:
    """LLM occasionally emits list where a string is expected. Parser must
    skip the offending entry without aborting the whole document."""

    def test_list_node_id_skipped_others_kept(self) -> None:
        ext = _make_extractor()
        content = json.dumps({
            "nodes": [
                {"id": ["홍길동", "김철수"], "type": "Person"},  # bad
                {"id": "개발팀", "type": "Team"},               # good
            ],
            "relationships": [],
        })
        result = ext._parse_response(content)
        assert result.node_count == 1
        assert result.nodes[0].id == "개발팀"

    def test_list_node_type_skipped(self) -> None:
        ext = _make_extractor()
        content = json.dumps({
            "nodes": [
                {"id": "홍길동", "type": ["Person", "Team"]},  # bad
                {"id": "개발팀", "type": "Team"},              # good
            ],
            "relationships": [],
        })
        result = ext._parse_response(content)
        assert result.node_count == 1
        assert result.nodes[0].type == "Team"

    def test_list_relationship_source_skipped(self) -> None:
        ext = _make_extractor()
        content = json.dumps({
            "nodes": [
                {"id": "홍길동", "type": "Person"},
                {"id": "개발팀", "type": "Team"},
            ],
            "relationships": [
                {"source": ["홍길동"], "type": "MEMBER_OF", "target": "개발팀"},   # bad
                {"source": "홍길동", "type": "MEMBER_OF", "target": "개발팀"},     # good
            ],
        })
        result = ext._parse_response(content)
        assert result.relationship_count == 1
        assert result.relationships[0].source == "홍길동"

    def test_list_relationship_target_skipped(self) -> None:
        ext = _make_extractor()
        content = json.dumps({
            "nodes": [
                {"id": "홍길동", "type": "Person"},
                {"id": "개발팀", "type": "Team"},
            ],
            "relationships": [
                {"source": "홍길동", "type": "MEMBER_OF", "target": ["개발팀"]},  # bad
            ],
        })
        result = ext._parse_response(content)
        assert result.relationship_count == 0

    def test_list_relationship_type_skipped(self) -> None:
        ext = _make_extractor()
        content = json.dumps({
            "nodes": [
                {"id": "홍길동", "type": "Person"},
                {"id": "개발팀", "type": "Team"},
            ],
            "relationships": [
                {"source": "홍길동", "type": ["MEMBER_OF"], "target": "개발팀"},   # bad
            ],
        })
        result = ext._parse_response(content)
        assert result.relationship_count == 0

    def test_all_nodes_list_id_returns_empty_not_raise(self) -> None:
        """Even when every node is malformed, extractor must return an empty
        result without raising unhashable/TypeError to upstream save path."""
        ext = _make_extractor()
        content = json.dumps({
            "nodes": [
                {"id": ["a"], "type": "Person"},
                {"id": ["b"], "type": "Team"},
            ],
            "relationships": [
                {"source": ["x"], "type": "MEMBER_OF", "target": ["y"]},
            ],
        })
        # Must not raise TypeError: unhashable type: 'list'
        result = ext._parse_response(content)
        assert result.node_count == 0
        assert result.relationship_count == 0

    def test_dict_node_id_skipped(self) -> None:
        """Cover dict values too — same class of bug as list."""
        ext = _make_extractor()
        content = json.dumps({
            "nodes": [
                {"id": {"nested": "value"}, "type": "Person"},
                {"id": "개발팀", "type": "Team"},
            ],
            "relationships": [],
        })
        result = ext._parse_response(content)
        assert result.node_count == 1
        assert result.nodes[0].id == "개발팀"


# =============================================================================
# Bug B part 1 — generic-term Store filter
# =============================================================================


class TestBugBGenericStoreFilter:
    """Generic nouns like "경쟁점" (competitor store) are not actual store
    names. They must be rejected at entity-validation time."""

    def test_경쟁점_rejected(self) -> None:
        validated_id, _ = _validate_entity("경쟁점", "Store")
        assert validated_id is None

    def test_자점_rejected(self) -> None:
        validated_id, _ = _validate_entity("자점", "Store")
        assert validated_id is None

    def test_경쟁사_rejected(self) -> None:
        validated_id, _ = _validate_entity("경쟁사", "Store")
        assert validated_id is None

    def test_타점_rejected(self) -> None:
        validated_id, _ = _validate_entity("타점", "Store")
        assert validated_id is None

    def test_점포_rejected(self) -> None:
        validated_id, _ = _validate_entity("점포", "Store")
        assert validated_id is None

    def test_자사_rejected(self) -> None:
        validated_id, _ = _validate_entity("자사", "Store")
        assert validated_id is None

    def test_real_store_preserved(self) -> None:
        validated_id, validated_type = _validate_entity("GS25 논현역점", "Store")
        assert validated_id == "GS25 논현역점"
        assert validated_type == "Store"


# =============================================================================
# Bug B part 2 — MERGE key must align with node_registry unique_property
# =============================================================================


class TestBugBMergeKeyAlignment:
    """The registry declares `name` as Store's UNIQUE property but the
    extractor's UNWIND MERGE used `id`. Two documents producing the same
    Store name but different id values collide on `store_name_unique`.
    The fix aligns MERGE key with the registry."""

    def test_upsert_store_merges_on_name(self) -> None:
        ext = _make_extractor()
        mock_session = MagicMock()
        mock_session.run.return_value = iter([])  # simulate no records returned

        ext._upsert_node_batches(
            mock_session,
            {"Store": [{"id": "경쟁점", "name": "경쟁점"}]},
            now="2026-04-24T00:00:00+00:00",
        )

        assert mock_session.run.call_count == 1
        called_query = mock_session.run.call_args[0][0]
        # MERGE key must be `name` for Store (matches registry UNIQUE)
        assert "MERGE (n:Store {name: props.name})" in called_query

    def test_upsert_person_merges_on_id(self) -> None:
        """Person in registry has email UNIQUE, but the extractor does not
        populate email — fall back to id to avoid breaking the pipeline."""
        ext = _make_extractor()
        mock_session = MagicMock()
        mock_session.run.return_value = iter([])

        ext._upsert_node_batches(
            mock_session,
            {"Person": [{"id": "홍길동", "name": "홍길동"}]},
            now="2026-04-24T00:00:00+00:00",
        )

        called_query = mock_session.run.call_args[0][0]
        assert "MERGE (n:Person {id: props.id})" in called_query

    def test_upsert_team_merges_on_name(self) -> None:
        """Team also has name UNIQUE in registry."""
        ext = _make_extractor()
        mock_session = MagicMock()
        mock_session.run.return_value = iter([])

        ext._upsert_node_batches(
            mock_session,
            {"Team": [{"id": "개발팀", "name": "개발팀"}]},
            now="2026-04-24T00:00:00+00:00",
        )

        called_query = mock_session.run.call_args[0][0]
        assert "MERGE (n:Team {name: props.name})" in called_query

    def test_upsert_system_merges_on_name(self) -> None:
        ext = _make_extractor()
        mock_session = MagicMock()
        mock_session.run.return_value = iter([])

        ext._upsert_node_batches(
            mock_session,
            {"System": [{"id": "포스", "name": "포스"}]},
            now="2026-04-24T00:00:00+00:00",
        )

        called_query = mock_session.run.call_args[0][0]
        assert "MERGE (n:System {name: props.name})" in called_query
