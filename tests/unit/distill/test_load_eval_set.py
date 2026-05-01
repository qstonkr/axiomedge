"""service._load_eval_set — build_id 기반 deterministic shuffle."""

import json
from pathlib import Path

from src.distill.service import _load_eval_set


def _write_train(path: Path, n: int) -> None:
    """messages 형식 n 줄."""
    lines = [
        json.dumps({"messages": [{"content": f"Q{i}"}, {"content": f"A{i}"}]}) + "\n"
        for i in range(n)
    ]
    path.write_text("".join(lines))


def test_load_eval_set_returns_10_percent(tmp_path: Path) -> None:
    train = tmp_path / "train.jsonl"
    _write_train(train, 100)
    eval_set = _load_eval_set(train, build_id="b1")
    assert len(eval_set) == 10


def test_load_eval_set_deterministic_per_build(tmp_path: Path) -> None:
    """같은 build_id → 같은 eval set."""
    train = tmp_path / "train.jsonl"
    _write_train(train, 100)
    a = _load_eval_set(train, build_id="b1")
    b = _load_eval_set(train, build_id="b1")
    assert [x["question"] for x in a] == [x["question"] for x in b]


def test_load_eval_set_differs_across_builds(tmp_path: Path) -> None:
    """다른 build_id → (높은 확률로) 다른 eval set."""
    train = tmp_path / "train.jsonl"
    _write_train(train, 100)
    a = _load_eval_set(train, build_id="b1")
    c = _load_eval_set(train, build_id="b2")
    assert [x["question"] for x in a] != [x["question"] for x in c]


def test_load_eval_set_handles_empty_file(tmp_path: Path) -> None:
    train = tmp_path / "train.jsonl"
    train.write_text("")
    assert _load_eval_set(train, build_id="b1") == []


def test_load_eval_set_skips_malformed_lines(tmp_path: Path) -> None:
    train = tmp_path / "train.jsonl"
    train.write_text(
        "not json\n"
        + json.dumps({"messages": [{"content": "Q"}, {"content": "A"}]}) + "\n"
    )
    eval_set = _load_eval_set(train, build_id="b1", ratio=1.0)
    assert len(eval_set) == 1
    assert eval_set[0]["question"] == "Q"


def test_load_eval_set_returns_at_least_one(tmp_path: Path) -> None:
    """매우 작은 train.jsonl 도 최소 1개 반환."""
    train = tmp_path / "train.jsonl"
    _write_train(train, 3)
    eval_set = _load_eval_set(train, build_id="b1")
    assert len(eval_set) >= 1
