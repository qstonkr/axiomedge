"""Backfill unit tests for src/api/routes/admin.py — missed lines coverage."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import src.api.app  # noqa: F401
from src.api.routes import admin as admin_mod


# ============================================================================
# Helpers
# ============================================================================

def _run(coro):
    return asyncio.run(coro)


def _mock_state(**overrides) -> MagicMock:
    state = MagicMock()
    _map: dict[str, Any] = {}
    _map.update(overrides)
    state.get = lambda k, default=None: _map.get(k, default)
    return state


# ============================================================================
# Graph Path — exception branch (lines 268-270)
# ============================================================================

class TestGraphPathException:
    def test_path_exception_returns_error(self):
        graph = AsyncMock()
        graph.shortest_path = AsyncMock(
            side_effect=RuntimeError("path boom"),
        )
        state = _mock_state(graph_repo=graph)
        with patch.object(admin_mod, "_get_state", return_value=state):
            result = _run(admin_mod.graph_path(
                {"from_node_id": "a", "to_node_id": "b"},
            ))
        assert "error" in result
        assert result["from_node_id"] == "a"
        assert result["to_node_id"] == "b"


# ============================================================================
# Graph Communities — exception branch (lines 291-293)
# ============================================================================

class TestGraphCommunitiesException:
    def test_communities_exception_returns_error(self):
        graph = AsyncMock()
        graph.get_communities = AsyncMock(
            side_effect=RuntimeError("comm err"),
        )
        state = _mock_state(graph_repo=graph)
        with patch.object(admin_mod, "_get_state", return_value=state):
            result = _run(admin_mod.graph_communities())
        assert "error" in result
        assert result["total"] == 0


# ============================================================================
# Graph Cleanup (lines 513-547)
# ============================================================================

class TestGraphCleanup:
    def test_cleanup_no_graph(self):
        state = _mock_state()
        with patch.object(admin_mod, "_get_state", return_value=state):
            result = _run(admin_mod.graph_cleanup())
        assert result["success"] is False
        assert "not available" in result["error"]

    def test_cleanup_dry_run(self):
        graph = AsyncMock()
        state = _mock_state(graph_repo=graph)
        fake_results = [
            {"task": "remove_placeholders", "found": 5, "fixed": 0},
            {"task": "reclassify", "found": 3, "fixed": 0},
        ]
        mock_module = MagicMock()
        mock_module.run_cleanup = MagicMock(return_value=fake_results)
        with (
            patch.object(admin_mod, "_get_state", return_value=state),
            patch.dict("sys.modules", {"scripts.graph_cleanup": mock_module}),
            patch(
                "src.api.routes.admin.asyncio.to_thread",
                new_callable=AsyncMock,
                return_value=fake_results,
            ),
        ):
            result = _run(admin_mod.graph_cleanup({"apply": False}))
        assert result["success"] is True
        assert result["mode"] == "dry_run"
        assert result["total_found"] == 8
        assert result["total_fixed"] == 0

    def test_cleanup_apply(self):
        graph = AsyncMock()
        state = _mock_state(graph_repo=graph)
        fake_results = [
            {"task": "remove_placeholders", "found": 5, "fixed": 5},
        ]
        mock_module = MagicMock()
        mock_module.run_cleanup = MagicMock(return_value=fake_results)
        with (
            patch.object(admin_mod, "_get_state", return_value=state),
            patch.dict("sys.modules", {"scripts.graph_cleanup": mock_module}),
            patch(
                "src.api.routes.admin.asyncio.to_thread",
                new_callable=AsyncMock,
                return_value=fake_results,
            ),
        ):
            result = _run(admin_mod.graph_cleanup(
                {"apply": True, "kb_id": "kb1"},
            ))
        assert result["success"] is True
        assert result["mode"] == "apply"
        assert result["kb_id"] == "kb1"
        assert result["total_fixed"] == 5

    def test_cleanup_exception(self):
        graph = AsyncMock()
        state = _mock_state(graph_repo=graph)
        mock_module = MagicMock()
        mock_module.run_cleanup = MagicMock()
        with (
            patch.object(admin_mod, "_get_state", return_value=state),
            patch.dict("sys.modules", {"scripts.graph_cleanup": mock_module}),
            patch(
                "src.api.routes.admin.asyncio.to_thread",
                new_callable=AsyncMock,
                side_effect=ImportError("no script"),
            ),
        ):
            result = _run(admin_mod.graph_cleanup())
        assert result["success"] is False
        assert "error" in result


# ============================================================================
# Graph Cleanup Analyze (lines 560-591)
# ============================================================================

class TestGraphCleanupAnalyze:
    def test_analyze_no_graph(self):
        state = _mock_state()
        with patch.object(admin_mod, "_get_state", return_value=state):
            result = _run(admin_mod.graph_cleanup_analyze())
        assert result["success"] is False
        assert "not available" in result["error"]

    def test_analyze_success(self):
        graph = AsyncMock()
        state = _mock_state(graph_repo=graph)
        fake_results = [{"task": "a", "found": 10}]
        mock_module = MagicMock()
        mock_module.run_cleanup = MagicMock(return_value=fake_results)
        with (
            patch.object(admin_mod, "_get_state", return_value=state),
            patch.dict("sys.modules", {"scripts.graph_cleanup": mock_module}),
            patch(
                "src.api.routes.admin.asyncio.to_thread",
                new_callable=AsyncMock,
                return_value=fake_results,
            ),
        ):
            result = _run(admin_mod.graph_cleanup_analyze(
                {"kb_id": "kb-x"},
            ))
        assert result["success"] is True
        assert result["mode"] == "dry_run"
        assert result["total_found"] == 10
        assert result["total_fixed"] == 0

    def test_analyze_none_body(self):
        graph = AsyncMock()
        state = _mock_state(graph_repo=graph)
        mock_module = MagicMock()
        mock_module.run_cleanup = MagicMock(return_value=[])
        with (
            patch.object(admin_mod, "_get_state", return_value=state),
            patch.dict("sys.modules", {"scripts.graph_cleanup": mock_module}),
            patch(
                "src.api.routes.admin.asyncio.to_thread",
                new_callable=AsyncMock,
                return_value=[],
            ),
        ):
            result = _run(admin_mod.graph_cleanup_analyze(None))
        assert result["success"] is True
        assert result["kb_id"] is None

    def test_analyze_exception(self):
        graph = AsyncMock()
        state = _mock_state(graph_repo=graph)
        mock_module = MagicMock()
        mock_module.run_cleanup = MagicMock()
        with (
            patch.object(admin_mod, "_get_state", return_value=state),
            patch.dict("sys.modules", {"scripts.graph_cleanup": mock_module}),
            patch(
                "src.api.routes.admin.asyncio.to_thread",
                new_callable=AsyncMock,
                side_effect=RuntimeError("boom"),
            ),
        ):
            result = _run(admin_mod.graph_cleanup_analyze())
        assert result["success"] is False
        assert "boom" in result["error"]


# ============================================================================
# _parse_llm_json_response (lines 745-779)
# ============================================================================

class TestParseLlmJsonResponse:
    def test_direct_json_array(self):
        text = '[{"name": "A", "type": "Person"}]'
        result = admin_mod._parse_llm_json_response(text)
        assert len(result) == 1
        assert result[0]["name"] == "A"

    def test_direct_json_not_list(self):
        text = '{"name": "A"}'
        result = admin_mod._parse_llm_json_response(text)
        assert result == []

    def test_markdown_code_block(self):
        text = '```json\n[{"name": "B", "type": "Store"}]\n```'
        result = admin_mod._parse_llm_json_response(text)
        assert len(result) == 1
        assert result[0]["type"] == "Store"

    def test_markdown_block_not_list(self):
        text = '```json\n{"single": true}\n```'
        result = admin_mod._parse_llm_json_response(text)
        assert result == []

    def test_regex_array_extract(self):
        text = 'Some preamble\n[{"name": "C", "type": "Team"}]\nSuffix'
        result = admin_mod._parse_llm_json_response(text)
        assert len(result) == 1

    def test_regex_array_not_list(self):
        # Edge case: regex finds brackets but parse yields non-list
        # This is hard to trigger naturally, so test total failure
        text = "no json here at all"
        result = admin_mod._parse_llm_json_response(text)
        assert result == []

    def test_invalid_json_all_fallbacks_fail(self):
        text = "[{broken json"
        result = admin_mod._parse_llm_json_response(text)
        assert result == []

    def test_markdown_block_invalid_json(self):
        text = "```json\n[{bad}]\n```"
        result = admin_mod._parse_llm_json_response(text)
        assert result == []

    def test_regex_array_invalid_json(self):
        text = "prefix [{invalid}] suffix"
        result = admin_mod._parse_llm_json_response(text)
        assert result == []


# ============================================================================
# _resolve_llm_client (lines 784-796)
# ============================================================================

class TestResolveLlmClient:
    def test_returns_state_llm_if_present(self):
        llm = MagicMock()
        state = {"llm_client": llm}
        result = admin_mod._resolve_llm_client(state)
        assert result is llm

    def test_no_llm_sagemaker_disabled(self):
        state: dict[str, Any] = {}
        with patch.dict(
            "os.environ",
            {"USE_SAGEMAKER_LLM": "false"},
            clear=False,
        ):
            result = admin_mod._resolve_llm_client(state)
        assert result is None

    def test_sagemaker_enabled_success(self):
        state: dict[str, Any] = {}
        mock_client = MagicMock()
        with (
            patch.dict(
                "os.environ",
                {"USE_SAGEMAKER_LLM": "true"},
                clear=False,
            ),
            patch(
                "src.api.routes.admin.SageMakerLLMClient",
                mock_client,
                create=True,
            ),
            patch(
                "src.nlp.llm.sagemaker_client.SageMakerLLMClient",
                mock_client,
                create=True,
            ),
        ):
            result = admin_mod._resolve_llm_client(state)
        assert result is not None

    def test_sagemaker_enabled_import_error(self):
        state: dict[str, Any] = {}
        with (
            patch.dict(
                "os.environ",
                {"USE_SAGEMAKER_LLM": "true"},
                clear=False,
            ),
            patch.dict(
                "sys.modules",
                {"src.nlp.llm.sagemaker_client": None},
            ),
        ):
            result = admin_mod._resolve_llm_client(state)
        assert result is None


# ============================================================================
# _apply_single_classification (lines 708-735)
# ============================================================================

class TestApplySingleClassification:
    def test_delete(self):
        session = MagicMock()
        stats = {"relabeled": 0, "deleted": 0, "skipped": 0, "errors": 0}
        item = {"eid": "e1", "type": "DELETE", "current_label": "Person"}
        admin_mod._apply_single_classification(session, item, stats)
        assert stats["deleted"] == 1
        session.run.assert_called_once()

    def test_relabel(self):
        session = MagicMock()
        stats = {"relabeled": 0, "deleted": 0, "skipped": 0, "errors": 0}
        item = {
            "eid": "e2",
            "type": "Store",
            "current_label": "Person",
        }
        admin_mod._apply_single_classification(session, item, stats)
        assert stats["relabeled"] == 1

    def test_relabel_from_entity_only(self):
        session = MagicMock()
        stats = {"relabeled": 0, "deleted": 0, "skipped": 0, "errors": 0}
        item = {
            "eid": "e3",
            "type": "Team",
            "current_label": "__Entity__",
        }
        admin_mod._apply_single_classification(session, item, stats)
        assert stats["relabeled"] == 1
        # Should NOT have REMOVE clause for __Entity__
        call_args = session.run.call_args[0][0]
        assert "REMOVE" not in call_args

    def test_skip_same_label(self):
        session = MagicMock()
        stats = {"relabeled": 0, "deleted": 0, "skipped": 0, "errors": 0}
        item = {"eid": "e4", "type": "Person", "current_label": "Person"}
        admin_mod._apply_single_classification(session, item, stats)
        assert stats["skipped"] == 1

    def test_skip_invalid_label(self):
        session = MagicMock()
        stats = {"relabeled": 0, "deleted": 0, "skipped": 0, "errors": 0}
        item = {
            "eid": "e5",
            "type": "InvalidLabel",
            "current_label": "Person",
        }
        admin_mod._apply_single_classification(session, item, stats)
        assert stats["skipped"] == 1

    def test_skip_missing_eid(self):
        session = MagicMock()
        stats = {"relabeled": 0, "deleted": 0, "skipped": 0, "errors": 0}
        item = {"eid": "", "type": "Store", "current_label": "Person"}
        admin_mod._apply_single_classification(session, item, stats)
        assert stats["skipped"] == 1

    def test_skip_missing_type(self):
        session = MagicMock()
        stats = {"relabeled": 0, "deleted": 0, "skipped": 0, "errors": 0}
        item = {"eid": "e6", "type": "", "current_label": "Person"}
        admin_mod._apply_single_classification(session, item, stats)
        assert stats["skipped"] == 1

    def test_session_error(self):
        session = MagicMock()
        session.run.side_effect = RuntimeError("neo4j write fail")
        stats = {"relabeled": 0, "deleted": 0, "skipped": 0, "errors": 0}
        item = {"eid": "e7", "type": "Store", "current_label": "Person"}
        admin_mod._apply_single_classification(session, item, stats)
        assert stats["errors"] == 1


# ============================================================================
# _apply_ai_classifications (lines 679-699)
# ============================================================================

class TestApplyAiClassifications:
    def test_apply_classifications(self):
        mock_driver = MagicMock()
        mock_session = MagicMock()
        mock_driver.session.return_value.__enter__ = MagicMock(
            return_value=mock_session,
        )
        mock_driver.session.return_value.__exit__ = MagicMock(
            return_value=False,
        )

        classifications = [
            {
                "eid": "e1",
                "type": "DELETE",
                "current_label": "Person",
            },
            {
                "eid": "e2",
                "type": "Store",
                "current_label": "Person",
            },
        ]

        with (
            patch(
                "src.api.routes.admin.get_settings",
            ) as mock_settings,
            patch(
                "neo4j.GraphDatabase.driver",
                return_value=mock_driver,
            ),
            patch.dict(
                "os.environ",
                {"NEO4J_PASSWORD": "pw", "NEO4J_DATABASE": "neo4j"},
            ),
        ):
            mock_settings.return_value.neo4j.uri = "bolt://localhost:7687"
            result = admin_mod._apply_ai_classifications(classifications)

        assert isinstance(result, dict)
        assert "relabeled" in result
        mock_driver.close.assert_called_once()


# ============================================================================
# _fetch_ai_classify_candidates (lines 625-672)
# ============================================================================

class TestFetchAiClassifyCandidates:
    def test_fetch_candidates(self):
        mock_driver = MagicMock()
        mock_session = MagicMock()
        mock_driver.session.return_value.__enter__ = MagicMock(
            return_value=mock_session,
        )
        mock_driver.session.return_value.__exit__ = MagicMock(
            return_value=False,
        )

        # Two queries: person misclassified + entity-only
        record1 = {
            "eid": "e1",
            "name": "TestNode",
            "current_label": "Person",
            "kb_id": "kb1",
        }
        record2 = {
            "eid": "e2",
            "name": "Unlabeled",
            "current_label": "__Entity__",
            "kb_id": "kb1",
        }
        mock_result1 = MagicMock()
        mock_result1.__iter__ = MagicMock(
            return_value=iter([MagicMock(**{"__iter__": iter, "items": lambda: record1.items()})]),
        )
        mock_result2 = MagicMock()
        mock_result2.__iter__ = MagicMock(
            return_value=iter([MagicMock(**{"__iter__": iter, "items": lambda: record2.items()})]),
        )

        # Use side_effect for two session.run calls
        fake_rec1 = MagicMock()
        fake_rec1.__getitem__ = lambda self, k: record1[k]
        fake_rec1.keys = lambda: record1.keys()
        fake_rec1.items = lambda: record1.items()

        fake_rec2 = MagicMock()
        fake_rec2.__getitem__ = lambda self, k: record2[k]
        fake_rec2.keys = lambda: record2.keys()
        fake_rec2.items = lambda: record2.items()

        run_results = [
            [fake_rec1],  # query 1 results
            [fake_rec2],  # query 2 results
        ]
        mock_session.run = MagicMock(side_effect=run_results)

        with (
            patch(
                "src.api.routes.admin.get_settings",
            ) as mock_settings,
            patch(
                "neo4j.GraphDatabase.driver",
                return_value=mock_driver,
            ),
            patch.dict(
                "os.environ",
                {
                    "NEO4J_PASSWORD": "pw",
                    "NEO4J_USER": "neo4j",
                    "NEO4J_DATABASE": "neo4j",
                },
            ),
        ):
            mock_settings.return_value.neo4j.uri = "bolt://localhost:7687"
            result = admin_mod._fetch_ai_classify_candidates(
                kb_id="kb1", limit=100,
            )

        assert isinstance(result, list)
        mock_driver.close.assert_called_once()

    def test_fetch_candidates_no_kb_id(self):
        mock_driver = MagicMock()
        mock_session = MagicMock()
        mock_driver.session.return_value.__enter__ = MagicMock(
            return_value=mock_session,
        )
        mock_driver.session.return_value.__exit__ = MagicMock(
            return_value=False,
        )
        mock_session.run = MagicMock(side_effect=[[], []])

        with (
            patch(
                "src.api.routes.admin.get_settings",
            ) as mock_settings,
            patch(
                "neo4j.GraphDatabase.driver",
                return_value=mock_driver,
            ),
            patch.dict(
                "os.environ",
                {"NEO4J_PASSWORD": "", "NEO4J_DATABASE": "neo4j"},
            ),
        ):
            mock_settings.return_value.neo4j.uri = "bolt://localhost:7687"
            result = admin_mod._fetch_ai_classify_candidates(
                kb_id=None, limit=0,
            )

        assert result == []

    def test_fetch_candidates_limit_zero(self):
        mock_driver = MagicMock()
        mock_session = MagicMock()
        mock_driver.session.return_value.__enter__ = MagicMock(
            return_value=mock_session,
        )
        mock_driver.session.return_value.__exit__ = MagicMock(
            return_value=False,
        )
        mock_session.run = MagicMock(side_effect=[[], []])

        with (
            patch(
                "src.api.routes.admin.get_settings",
            ) as mock_settings,
            patch(
                "neo4j.GraphDatabase.driver",
                return_value=mock_driver,
            ),
            patch.dict(
                "os.environ",
                {"NEO4J_PASSWORD": "pw", "NEO4J_DATABASE": "neo4j"},
            ),
        ):
            mock_settings.return_value.neo4j.uri = "bolt://localhost:7687"
            result = admin_mod._fetch_ai_classify_candidates(
                kb_id=None, limit=0,
            )

        assert result == []


# ============================================================================
# _classify_batch (lines 803-824)
# ============================================================================

class TestClassifyBatch:
    def test_classify_batch_success(self):
        llm = AsyncMock()
        llm.generate = AsyncMock(
            return_value=json.dumps([
                {"name": "Node1", "type": "Store", "reason": "store name"},
                {"name": "Unknown", "type": "Term", "reason": "unknown"},
            ]),
        )
        candidates = [
            {
                "eid": "e1",
                "name": "Node1",
                "current_label": "Person",
                "kb_id": "kb1",
            },
            {
                "eid": "e2",
                "name": "Node2",
                "current_label": "__Entity__",
                "kb_id": "kb1",
            },
        ]
        result = _run(admin_mod._classify_batch(llm, candidates))
        # Only "Node1" matches a candidate name
        assert len(result) == 1
        assert result[0]["eid"] == "e1"
        assert result[0]["type"] == "Store"

    def test_classify_batch_no_match(self):
        llm = AsyncMock()
        llm.generate = AsyncMock(return_value="[]")
        candidates = [
            {
                "eid": "e1",
                "name": "Node1",
                "current_label": "Person",
                "kb_id": "kb1",
            },
        ]
        result = _run(admin_mod._classify_batch(llm, candidates))
        assert result == []


# ============================================================================
# graph_ai_classify route (lines 836-899)
# ============================================================================

class TestGraphAiClassify:
    def test_no_llm(self):
        state = _mock_state()
        with (
            patch.object(admin_mod, "_get_state", return_value=state),
            patch.object(
                admin_mod,
                "_resolve_llm_client",
                return_value=None,
            ),
        ):
            result = _run(admin_mod.graph_ai_classify())
        assert result["success"] is False
        assert "LLM client not available" in result["error"]

    def test_no_candidates(self):
        llm = AsyncMock()
        state = _mock_state(llm_client=llm)
        with (
            patch.object(admin_mod, "_get_state", return_value=state),
            patch.object(
                admin_mod,
                "_resolve_llm_client",
                return_value=llm,
            ),
            patch(
                "src.api.routes.admin.asyncio.to_thread",
                new_callable=AsyncMock,
                return_value=[],
            ),
        ):
            result = _run(admin_mod.graph_ai_classify({"limit": 50}))
        assert result["success"] is True
        assert result["candidates"] == 0

    def test_dry_run_with_candidates(self):
        llm = AsyncMock()
        llm.generate = AsyncMock(
            return_value='[{"name":"N1","type":"Store","reason":"r"}]',
        )
        state = _mock_state(llm_client=llm)
        candidates = [
            {
                "eid": "e1",
                "name": "N1",
                "current_label": "Person",
                "kb_id": "kb1",
            },
        ]

        async def fake_to_thread(fn, *args, **kwargs):
            # First call is _fetch_ai_classify_candidates
            if fn is admin_mod._fetch_ai_classify_candidates:
                return candidates
            # _apply should not be called in dry_run
            return fn(*args, **kwargs)  # pragma: no cover

        with (
            patch.object(admin_mod, "_get_state", return_value=state),
            patch.object(
                admin_mod,
                "_resolve_llm_client",
                return_value=llm,
            ),
            patch(
                "src.api.routes.admin.asyncio.to_thread",
                side_effect=fake_to_thread,
            ),
        ):
            result = _run(admin_mod.graph_ai_classify(
                {"limit": 50, "apply": False},
            ))
        assert result["success"] is True
        assert result["mode"] == "dry_run"
        assert result["candidates"] == 1
        assert len(result["classifications"]) == 1
        assert result["classifications"][0]["new_type"] == "Store"

    def test_apply_with_candidates(self):
        llm = AsyncMock()
        llm.generate = AsyncMock(
            return_value='[{"name":"N1","type":"Store","reason":"r"}]',
        )
        state = _mock_state(llm_client=llm)
        candidates = [
            {
                "eid": "e1",
                "name": "N1",
                "current_label": "Person",
                "kb_id": "kb1",
            },
        ]
        apply_stats = {
            "relabeled": 1,
            "deleted": 0,
            "skipped": 0,
            "errors": 0,
        }
        call_count = 0

        async def fake_to_thread(fn, *args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return candidates
            return apply_stats

        with (
            patch.object(admin_mod, "_get_state", return_value=state),
            patch.object(
                admin_mod,
                "_resolve_llm_client",
                return_value=llm,
            ),
            patch(
                "src.api.routes.admin.asyncio.to_thread",
                side_effect=fake_to_thread,
            ),
        ):
            result = _run(admin_mod.graph_ai_classify(
                {"limit": 50, "apply": True},
            ))
        assert result["success"] is True
        assert result["mode"] == "apply"
        assert result["stats"]["relabeled"] == 1

    def test_classify_exception(self):
        llm = AsyncMock()
        state = _mock_state(llm_client=llm)
        with (
            patch.object(admin_mod, "_get_state", return_value=state),
            patch.object(
                admin_mod,
                "_resolve_llm_client",
                return_value=llm,
            ),
            patch(
                "src.api.routes.admin.asyncio.to_thread",
                new_callable=AsyncMock,
                side_effect=RuntimeError("fetch fail"),
            ),
        ):
            result = _run(admin_mod.graph_ai_classify())
        assert result["success"] is False
        assert "fetch fail" in result["error"]

    def test_classify_batch_llm_error(self):
        """LLM batch failure is caught and skipped."""
        llm = AsyncMock()
        llm.generate = AsyncMock(
            side_effect=RuntimeError("llm timeout"),
        )
        state = _mock_state(llm_client=llm)
        candidates = [
            {
                "eid": "e1",
                "name": "N1",
                "current_label": "Person",
                "kb_id": "kb1",
            },
        ]

        async def fake_to_thread(fn, *args, **kwargs):
            return candidates

        with (
            patch.object(admin_mod, "_get_state", return_value=state),
            patch.object(
                admin_mod,
                "_resolve_llm_client",
                return_value=llm,
            ),
            patch(
                "src.api.routes.admin.asyncio.to_thread",
                side_effect=fake_to_thread,
            ),
        ):
            result = _run(admin_mod.graph_ai_classify(
                {"limit": 50, "apply": False},
            ))
        assert result["success"] is True
        assert result["classifications"] == []

    def test_limit_clamping(self):
        """Limit is clamped to [10, 10000]."""
        llm = AsyncMock()
        state = _mock_state(llm_client=llm)
        with (
            patch.object(admin_mod, "_get_state", return_value=state),
            patch.object(
                admin_mod,
                "_resolve_llm_client",
                return_value=llm,
            ),
            patch(
                "src.api.routes.admin.asyncio.to_thread",
                new_callable=AsyncMock,
                return_value=[],
            ) as mock_thread,
        ):
            _run(admin_mod.graph_ai_classify({"limit": 3}))
        # limit=3 should be clamped to 10
        call_args = mock_thread.call_args
        assert call_args[0][2] == 10  # second positional arg = limit

    def test_limit_zero_passthrough(self):
        """limit=0 means fetch all (no clamping)."""
        llm = AsyncMock()
        state = _mock_state(llm_client=llm)
        with (
            patch.object(admin_mod, "_get_state", return_value=state),
            patch.object(
                admin_mod,
                "_resolve_llm_client",
                return_value=llm,
            ),
            patch(
                "src.api.routes.admin.asyncio.to_thread",
                new_callable=AsyncMock,
                return_value=[],
            ) as mock_thread,
        ):
            _run(admin_mod.graph_ai_classify({"limit": 0}))
        call_args = mock_thread.call_args
        assert call_args[0][2] == 0
