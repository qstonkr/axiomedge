"""edge/server._log_query — PII 마스킹 적용 검증."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest


@pytest.fixture
def server_module(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """server.py 를 tmp_path 의 LOG_DIR 로 reload — 모듈 import side effect 안전."""
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("EDGE_API_KEY", "test-key")
    monkeypatch.setenv("MODEL_PATH", str(tmp_path / "missing.gguf"))
    sys.modules.pop("src.edge.server", None)
    return importlib.import_module("src.edge.server")


@pytest.mark.asyncio
async def test_log_query_masks_phone_in_query(server_module, tmp_path: Path) -> None:
    """query 의 전화번호가 마스킹된 채 로그 파일에 기록돼야."""
    await server_module._log_query(
        query="고객 010-1234-5678 연락처",
        answer="OK",
        latency_ms=42,
        success=True,
    )

    content = (tmp_path / "logs" / "queries.jsonl").read_text(encoding="utf-8")
    assert "[PHONE]" in content
    assert "010-1234-5678" not in content


@pytest.mark.asyncio
async def test_log_query_masks_email_in_answer(server_module, tmp_path: Path) -> None:
    """answer 의 이메일도 마스킹돼야."""
    await server_module._log_query(
        query="이메일",
        answer="담당자 a@b.com 으로 보내세요",
        latency_ms=10,
        success=True,
    )

    content = (tmp_path / "logs" / "queries.jsonl").read_text(encoding="utf-8")
    assert "[EMAIL]" in content
    assert "a@b.com" not in content


@pytest.mark.asyncio
async def test_log_query_preserves_normal_text(server_module, tmp_path: Path) -> None:
    """PII 가 없으면 원본 보존."""
    await server_module._log_query(
        query="영업시간 알려줘",
        answer="9시부터 22시",
        latency_ms=15,
        success=True,
    )

    line = (tmp_path / "logs" / "queries.jsonl").read_text(encoding="utf-8").strip()
    entry = json.loads(line)
    assert entry["query"] == "영업시간 알려줘"
    assert entry["answer"] == "9시부터 22시"
