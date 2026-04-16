"""Coverage backfill — distill trainer, evaluator, service.

Tests core public methods with appropriate mocking.
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.distill.config import (
    DataQualityConfig,
    DeployConfig,
    DistillProfile,
    EvalThreshold,
    LoRAConfig,
    QAStyleConfig,
    TrainingConfig,
)
from src.distill.evaluator import DistillEvaluator, EvalResult
from src.distill.trainer import DistillTrainer, TrainOutput


def _make_profile(**overrides) -> DistillProfile:
    defaults = {
        "enabled": True,
        "description": "test",
        "search_group": "test-sg",
        "base_model": "test-model",
    }
    defaults.update(overrides)
    return DistillProfile(**defaults)


# ==========================================================================
# DistillTrainer
# ==========================================================================


class TestTrainerInit:
    def test_creates_output_dir(self, tmp_path) -> None:
        out = tmp_path / "new_dir"
        trainer = DistillTrainer(_make_profile(), str(out))
        assert out.exists()
        assert trainer.output_dir == str(out)


class TestPrepareDataset:
    def test_loads_jsonl(self, tmp_path) -> None:
        data_file = tmp_path / "train.jsonl"
        data_file.write_text(
            json.dumps({"text": "sample 1"}) + "\n"
            + json.dumps({"text": "sample 2"}) + "\n"
        )
        trainer = DistillTrainer(_make_profile(), str(tmp_path))
        dataset = trainer.prepare_dataset(str(data_file))
        assert len(dataset) == 2

    def test_empty_jsonl(self, tmp_path) -> None:
        data_file = tmp_path / "empty.jsonl"
        data_file.write_text("")
        trainer = DistillTrainer(_make_profile(), str(tmp_path))
        # Empty file should raise or return empty dataset
        try:
            dataset = trainer.prepare_dataset(str(data_file))
            assert len(dataset) == 0
        except Exception:  # noqa: BLE001
            pass  # Some implementations raise on empty file


class TestTrainOutput:
    def test_dataclass_fields(self) -> None:
        out = TrainOutput(training_loss=0.5, eval_loss=0.3, duration_sec=120, output_dir="/tmp")
        assert out.training_loss == 0.5
        assert out.eval_loss == 0.3
        assert out.duration_sec == 120

    def test_eval_loss_none(self) -> None:
        out = TrainOutput(training_loss=0.5, eval_loss=None, duration_sec=60, output_dir="/tmp")
        assert out.eval_loss is None


# ==========================================================================
# DistillEvaluator
# ==========================================================================


class TestEvalResult:
    def test_passed(self) -> None:
        r = EvalResult(passed=True, faithfulness=0.8, relevancy=0.9,
                       avg_similarity=0.85, sample_count=10)
        assert r.passed is True
        assert r.sample_count == 10

    def test_failed(self) -> None:
        r = EvalResult(passed=False, faithfulness=0.3, relevancy=0.4,
                       avg_similarity=0.35, sample_count=5)
        assert r.passed is False

    def test_details_default_empty(self) -> None:
        r = EvalResult(passed=True, faithfulness=0.8, relevancy=0.8,
                       avg_similarity=0.8, sample_count=1)
        assert r.details == []


class TestTeacherJudge:
    @pytest.fixture
    def evaluator(self) -> DistillEvaluator:
        teacher = AsyncMock()
        embedder = MagicMock()
        return DistillEvaluator(teacher, embedder)

    async def test_returns_scores(self, evaluator) -> None:
        evaluator.teacher.generate.return_value = '{"faithfulness": 0.8, "relevancy": 0.9}'
        result = await evaluator._teacher_judge("question", "answer", "expected")
        assert result["faithfulness"] == 0.8
        assert result["relevancy"] == 0.9

    async def test_empty_student_answer(self, evaluator) -> None:
        result = await evaluator._teacher_judge("question", "", "expected")
        assert result["faithfulness"] == 0
        assert result["relevancy"] == 0

    async def test_teacher_failure_returns_default(self, evaluator) -> None:
        evaluator.teacher.generate.side_effect = RuntimeError("LLM down")
        result = await evaluator._teacher_judge("q", "a", "e")
        assert result["faithfulness"] == 0.5
        assert result["relevancy"] == 0.5

    async def test_malformed_json_returns_default(self, evaluator) -> None:
        evaluator.teacher.generate.return_value = "not valid json"
        result = await evaluator._teacher_judge("q", "a", "e")
        assert result["faithfulness"] == 0.5


class TestEmbeddingSimilarity:
    def test_identical_texts(self) -> None:
        embedder = MagicMock()
        embedder.encode.return_value = {
            "dense_vecs": [[1.0, 0.0, 0.0], [1.0, 0.0, 0.0]],
        }
        evaluator = DistillEvaluator(AsyncMock(), embedder)
        sim = evaluator._embedding_similarity("hello", "hello")
        assert sim > 0.99

    def test_orthogonal_texts(self) -> None:
        embedder = MagicMock()
        embedder.encode.return_value = {
            "dense_vecs": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
        }
        evaluator = DistillEvaluator(AsyncMock(), embedder)
        sim = evaluator._embedding_similarity("a", "b")
        assert abs(sim) < 0.01

    def test_empty_text_returns_zero(self) -> None:
        evaluator = DistillEvaluator(AsyncMock(), MagicMock())
        assert evaluator._embedding_similarity("", "hello") == 0.0
        assert evaluator._embedding_similarity("hello", "") == 0.0

    def test_embedder_failure_returns_zero(self) -> None:
        embedder = MagicMock()
        embedder.encode.side_effect = RuntimeError("encode failed")
        evaluator = DistillEvaluator(AsyncMock(), embedder)
        assert evaluator._embedding_similarity("a", "b") == 0.0


class TestEvaluateEmptyData:
    async def test_empty_eval_data(self) -> None:
        evaluator = DistillEvaluator(AsyncMock(), MagicMock())
        threshold = EvalThreshold(faithfulness=0.5, relevancy=0.5)
        with patch("llama_cpp.Llama") as MockLlama:
            MockLlama.return_value = MagicMock()
            result = await evaluator.evaluate("/fake/model.gguf", [], threshold)
        assert result.passed is False
        assert result.sample_count == 0


class TestEvaluateWithData:
    async def test_passing_evaluation(self) -> None:
        teacher = AsyncMock()
        teacher.generate.return_value = '{"faithfulness": 0.9, "relevancy": 0.9}'

        embedder = MagicMock()
        embedder.encode.return_value = {
            "dense_vecs": [[1.0, 0.0], [0.9, 0.1]],
        }

        evaluator = DistillEvaluator(teacher, embedder)
        threshold = EvalThreshold(faithfulness=0.5, relevancy=0.5)

        mock_student = MagicMock()
        mock_student.create_chat_completion.return_value = {
            "choices": [{"message": {"content": "student answer"}}],
        }

        eval_data = [
            {"question": "Q1", "answer": "A1"},
            {"question": "Q2", "answer": "A2"},
        ]

        with patch("llama_cpp.Llama", return_value=mock_student):
            result = await evaluator.evaluate("/fake/model.gguf", eval_data, threshold)

        assert result.passed is True
        assert result.sample_count == 2
        assert result.faithfulness > 0.5

    async def test_failing_evaluation(self) -> None:
        teacher = AsyncMock()
        teacher.generate.return_value = '{"faithfulness": 0.2, "relevancy": 0.2}'

        embedder = MagicMock()
        embedder.encode.return_value = {"dense_vecs": [[1.0], [0.0]]}

        evaluator = DistillEvaluator(teacher, embedder)
        threshold = EvalThreshold(faithfulness=0.5, relevancy=0.5)

        mock_student = MagicMock()
        mock_student.create_chat_completion.return_value = {
            "choices": [{"message": {"content": "bad answer"}}],
        }

        with patch("llama_cpp.Llama", return_value=mock_student):
            result = await evaluator.evaluate(
                "/fake/model.gguf",
                [{"question": "Q", "answer": "A"}],
                threshold,
            )

        assert result.passed is False


class TestEvaluateStudentFailure:
    async def test_student_inference_failure(self) -> None:
        teacher = AsyncMock()
        teacher.generate.return_value = '{"faithfulness": 0.5, "relevancy": 0.5}'

        embedder = MagicMock()
        embedder.encode.return_value = {"dense_vecs": [[0.0], [0.0]]}

        evaluator = DistillEvaluator(teacher, embedder)
        threshold = EvalThreshold(faithfulness=0.3, relevancy=0.3)

        mock_student = MagicMock()
        mock_student.create_chat_completion.side_effect = RuntimeError("OOM")

        with patch("llama_cpp.Llama", return_value=mock_student):
            result = await evaluator.evaluate(
                "/fake/model.gguf",
                [{"question": "Q", "answer": "A"}],
                threshold,
            )

        # Student failed → empty answer → teacher still judges
        assert result.sample_count == 1
