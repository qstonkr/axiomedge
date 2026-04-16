"""Unit tests for scripts/run_rag_evaluation.py."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from scripts.distill.run_rag_evaluation import (
    _build_eval_result,
    _check_recall,
    _get_auth_headers,
    _log_final_report,
    _log_judge_result,
)


# ---------------------------------------------------------------------------
# _check_recall
# ---------------------------------------------------------------------------


class TestCheckRecall:
    def test_empty_source_doc(self) -> None:
        assert _check_recall("", [{"document_name": "doc1"}]) is False

    def test_empty_chunks(self) -> None:
        assert _check_recall("doc1", []) is False

    def test_exact_match(self) -> None:
        chunks = [
            {"document_name": "report_2024.pdf"},
            {"document_name": "notes.txt"},
        ]
        assert _check_recall("report_2024.pdf", chunks) is True

    def test_partial_match(self) -> None:
        chunks = [{"document_name": "GS25_report_2024_v2.pdf"}]
        assert _check_recall("report_2024", chunks) is True

    def test_no_match(self) -> None:
        chunks = [{"document_name": "other_doc.pdf"}]
        assert _check_recall("report_2024", chunks) is False

    def test_missing_document_name_key(self) -> None:
        chunks = [{"content": "some text"}]
        assert _check_recall("report", chunks) is False


# ---------------------------------------------------------------------------
# _build_eval_result
# ---------------------------------------------------------------------------


class TestBuildEvalResult:
    def test_basic_result(self) -> None:
        gs = {
            "kb_id": "test-kb",
            "id": "gs-123",
            "question": "test question?",
            "expected": "expected answer",
        }
        scores = {"faithfulness": 0.8, "relevancy": 0.9, "completeness": 0.7}
        search_result = {
            "metadata": {
                "crag_action": "correct",
                "crag_confidence": 0.85,
                "crag_recommendation": "OK",
            },
        }

        result = _build_eval_result(gs, "actual answer", scores, 150.0, search_result, True)

        assert result["kb_id"] == "test-kb"
        assert result["golden_set_id"] == "gs-123"
        assert result["question"] == "test question?"
        assert result["expected"] == "expected answer"
        assert result["actual"] == "actual answer"
        assert result["faithfulness"] == 0.8
        assert result["relevancy"] == 0.9
        assert result["completeness"] == 0.7
        assert result["search_time_ms"] == 150.0
        assert result["crag_action"] == "correct"
        assert result["crag_confidence"] == 0.85
        assert result["recall_hit"] is True

    def test_long_actual_answer_truncated(self) -> None:
        gs = {"kb_id": "kb", "id": "id", "question": "q", "expected": "e"}
        scores = {"faithfulness": 0.5, "relevancy": 0.5, "completeness": 0.5}
        long_answer = "x" * 1000
        result = _build_eval_result(gs, long_answer, scores, 100.0, {"metadata": {}}, False)
        assert len(result["actual"]) == 500

    def test_missing_metadata(self) -> None:
        gs = {"kb_id": "kb", "id": "id", "question": "q", "expected": "e"}
        scores = {"faithfulness": 0.5, "relevancy": 0.5, "completeness": 0.5}
        result = _build_eval_result(gs, "answer", scores, 100.0, {}, False)
        assert result["crag_action"] == ""
        assert result["crag_confidence"] == 0.0


# ---------------------------------------------------------------------------
# _log_final_report
# ---------------------------------------------------------------------------


class TestLogFinalReport:
    def test_with_results(self) -> None:
        results = [
            {
                "faithfulness": 0.8,
                "relevancy": 0.9,
                "completeness": 0.7,
                "search_time_ms": 200,
                "crag_action": "correct",
                "crag_confidence": 0.9,
                "recall_hit": True,
            },
            {
                "faithfulness": 0.6,
                "relevancy": 0.7,
                "completeness": 0.5,
                "search_time_ms": 300,
                "crag_action": "ambiguous",
                "crag_confidence": 0.5,
                "recall_hit": False,
            },
        ]
        scores_sum = {"faithfulness": 1.4, "relevancy": 1.6, "completeness": 1.2}

        # Should not raise
        _log_final_report("test-eval-001", results, scores_sum)

    def test_empty_results(self) -> None:
        # Should handle n=0 gracefully
        _log_final_report("test-eval-002", [], {"faithfulness": 0, "relevancy": 0, "completeness": 0})


# ---------------------------------------------------------------------------
# _get_auth_headers
# ---------------------------------------------------------------------------


class TestGetAuthHeaders:
    def test_auth_disabled(self) -> None:
        with patch.dict(os.environ, {"AUTH_ENABLED": "false"}, clear=False):
            assert _get_auth_headers() == {}

    def test_auth_with_token(self) -> None:
        with patch.dict(os.environ, {"AUTH_ENABLED": "true", "EVAL_API_TOKEN": "mytoken"}, clear=False):
            headers = _get_auth_headers()
            assert headers["Authorization"] == "Bearer mytoken"

    def test_auth_with_api_key(self) -> None:
        with patch.dict(
            os.environ,
            {"AUTH_ENABLED": "true", "EVAL_API_TOKEN": "", "EVAL_API_KEY": "mykey"},
            clear=False,
        ):
            headers = _get_auth_headers()
            assert headers["X-API-Key"] == "mykey"

    def test_auth_no_credentials(self) -> None:
        with patch.dict(
            os.environ,
            {"AUTH_ENABLED": "true", "EVAL_API_TOKEN": "", "EVAL_API_KEY": ""},
            clear=False,
        ):
            headers = _get_auth_headers()
            assert headers == {}


# ---------------------------------------------------------------------------
# _log_judge_result (smoke test)
# ---------------------------------------------------------------------------


class TestLogJudgeResult:
    def test_basic_log(self) -> None:
        scores = {"faithfulness": 0.8, "relevancy": 0.9, "completeness": 0.7}
        search_result = {
            "metadata": {"crag_action": "correct", "crag_confidence": 0.9},
        }
        # Should not raise
        _log_judge_result(1, 150.0, scores, True, search_result)

    def test_no_crag(self) -> None:
        scores = {"faithfulness": 0.5, "relevancy": 0.5, "completeness": 0.5}
        _log_judge_result(1, 100.0, scores, False, {"metadata": {}})
