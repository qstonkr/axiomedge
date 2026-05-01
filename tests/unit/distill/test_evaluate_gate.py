"""DistillService._evaluate — fail-closed + force_deploy 우회."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture
def svc():
    """DistillService instance with bound _evaluate (no full init)."""
    from src.distill.service import DistillService

    s = MagicMock(spec=DistillService)
    s._evaluate = DistillService._evaluate.__get__(s)
    s.llm = MagicMock()
    s.embedder = MagicMock()
    return s


@pytest.fixture
def profile():
    p = MagicMock()
    p.training.eval_threshold = None
    return p


@pytest.fixture
def repo():
    r = MagicMock()
    r.get_build = AsyncMock()
    r.update_build = AsyncMock()
    return r


@pytest.mark.asyncio
async def test_eval_fails_when_data_path_missing_and_no_force(
    svc, profile, repo, tmp_path: Path,
) -> None:
    repo.get_build.return_value = {"force_deploy": False}
    result = await svc._evaluate(
        "b1", profile, model_path="/x", data_path=str(tmp_path / "missing.jsonl"),
        repo=repo, gguf_path=None,
    )
    assert result is False


@pytest.mark.asyncio
async def test_eval_passes_when_data_missing_with_force_deploy(
    svc, profile, repo, tmp_path: Path,
) -> None:
    repo.get_build.return_value = {"force_deploy": True}
    result = await svc._evaluate(
        "b1", profile, model_path="/x", data_path=str(tmp_path / "missing.jsonl"),
        repo=repo, gguf_path=None,
    )
    assert result is True


@pytest.mark.asyncio
async def test_eval_fails_when_eval_data_empty(
    svc, profile, repo, tmp_path: Path,
) -> None:
    """train.jsonl 존재하지만 마지막 10% 가 0줄."""
    repo.get_build.return_value = {"force_deploy": False}
    data = tmp_path / "train.jsonl"
    data.write_text("")  # 빈 파일
    result = await svc._evaluate(
        "b1", profile, model_path="/x", data_path=str(data),
        repo=repo, gguf_path=None,
    )
    assert result is False


@pytest.mark.asyncio
async def test_eval_fails_when_gguf_missing_no_force(
    svc, profile, repo, tmp_path: Path,
) -> None:
    """GGUF 없으면 fail-closed (train_loss fallback 제거됨)."""
    repo.get_build.return_value = {"force_deploy": False, "train_loss": 0.5}
    data = tmp_path / "train.jsonl"
    line = json.dumps({"messages": [{"content": "Q"}, {"content": "A"}]}) + "\n"
    data.write_text(line * 20)
    result = await svc._evaluate(
        "b1", profile, model_path="/x", data_path=str(data),
        repo=repo, gguf_path=None,
    )
    assert result is False


@pytest.mark.asyncio
async def test_eval_passes_when_gguf_missing_with_force_deploy(
    svc, profile, repo, tmp_path: Path,
) -> None:
    repo.get_build.return_value = {"force_deploy": True}
    data = tmp_path / "train.jsonl"
    line = json.dumps({"messages": [{"content": "Q"}, {"content": "A"}]}) + "\n"
    data.write_text(line * 20)
    result = await svc._evaluate(
        "b1", profile, model_path="/x", data_path=str(data),
        repo=repo, gguf_path=None,
    )
    assert result is True


@pytest.mark.asyncio
async def test_eval_fails_when_evaluator_raises_no_force(
    svc, profile, repo, tmp_path: Path, monkeypatch,
) -> None:
    """GGUF 평가가 RuntimeError → fail (train_loss<2.0 fallback 제거 검증)."""
    repo.get_build.return_value = {"force_deploy": False, "train_loss": 0.1}
    data = tmp_path / "train.jsonl"
    line = json.dumps({"messages": [{"content": "Q"}, {"content": "A"}]}) + "\n"
    data.write_text(line * 20)
    fake_gguf = tmp_path / "model.gguf"
    fake_gguf.write_bytes(b"x")

    fake_evaluator = MagicMock()
    fake_evaluator.evaluate = AsyncMock(side_effect=RuntimeError("eval boom"))
    monkeypatch.setattr(
        "src.distill.evaluator.DistillEvaluator",
        lambda *_a, **_kw: fake_evaluator,
    )
    result = await svc._evaluate(
        "b1", profile, model_path="/x", data_path=str(data),
        repo=repo, gguf_path=str(fake_gguf),
    )
    # train_loss=0.1 (< 2.0) 이지만 fallback 으로 통과되면 안 됨
    assert result is False


@pytest.mark.asyncio
async def test_eval_passes_when_evaluator_returns_passed(
    svc, profile, repo, tmp_path: Path, monkeypatch,
) -> None:
    repo.get_build.return_value = {"force_deploy": False}
    data = tmp_path / "train.jsonl"
    line = json.dumps({"messages": [{"content": "Q"}, {"content": "A"}]}) + "\n"
    data.write_text(line * 20)
    fake_gguf = tmp_path / "model.gguf"
    fake_gguf.write_bytes(b"x")

    result_obj = MagicMock(passed=True, faithfulness=0.8, relevancy=0.85)
    fake_evaluator = MagicMock()
    fake_evaluator.evaluate = AsyncMock(return_value=result_obj)
    monkeypatch.setattr(
        "src.distill.evaluator.DistillEvaluator",
        lambda *_a, **_kw: fake_evaluator,
    )
    result = await svc._evaluate(
        "b1", profile, model_path="/x", data_path=str(data),
        repo=repo, gguf_path=str(fake_gguf),
    )
    assert result is True
    repo.update_build.assert_awaited()


def test_force_deploy_column_default_false() -> None:
    """DistillBuildModel.force_deploy 기본값 False — 명시 set 만 우회 허용."""
    from src.distill.models import DistillBuildModel

    col = DistillBuildModel.__table__.columns.get("force_deploy")
    assert col is not None
    assert col.nullable is False
    assert col.default.arg is False
