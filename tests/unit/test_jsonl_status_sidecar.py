"""JSONL stage2 status sidecar — PR-5 (B).

- update_status_sidecar 의 atomic write
- load 후 doc_id 별 status 조회
- 다른 run 의 update 가 기존 entry 를 보존
"""

from __future__ import annotations

import json

from src.pipelines.jsonl_checkpoint import (
    get_status_sidecar_path,
    load_status_sidecar,
    update_status_sidecar,
)


def test_update_creates_file_and_persists(tmp_path):
    sidecar = tmp_path / "status.json"
    update_status_sidecar(sidecar, "doc-1", status="stored")
    update_status_sidecar(
        sidecar, "doc-2", status="failed", error="boom", attempt=2,
    )
    data = load_status_sidecar(sidecar)
    assert "doc-1" in data and "doc-2" in data
    assert data["doc-1"]["status"] == "stored"
    assert data["doc-2"]["status"] == "failed"
    assert data["doc-2"]["last_error"] == "boom"
    assert data["doc-2"]["last_attempt"] == 2


def test_update_preserves_other_entries(tmp_path):
    sidecar = tmp_path / "status.json"
    update_status_sidecar(sidecar, "doc-1", status="stored")
    update_status_sidecar(sidecar, "doc-2", status="pending")
    # Now overwrite doc-1
    update_status_sidecar(sidecar, "doc-1", status="failed", error="x")
    data = load_status_sidecar(sidecar)
    assert data["doc-1"]["status"] == "failed"
    assert data["doc-2"]["status"] == "pending"


def test_load_missing_file_returns_empty(tmp_path):
    sidecar = tmp_path / "missing.json"
    assert load_status_sidecar(sidecar) == {}


def test_load_corrupt_file_returns_empty(tmp_path):
    sidecar = tmp_path / "corrupt.json"
    sidecar.write_text("{not valid json")
    assert load_status_sidecar(sidecar) == {}


def test_atomic_replace_via_tmp(tmp_path):
    sidecar = tmp_path / "status.json"
    update_status_sidecar(sidecar, "doc-1", status="stored")
    # tmp file 은 정리됨
    assert not sidecar.with_suffix(".json.tmp").exists()
    # JSON 정상 직렬화
    raw = sidecar.read_text()
    json.loads(raw)


def test_get_status_sidecar_path_unique_per_kb(monkeypatch, tmp_path):
    monkeypatch.setenv(
        "KNOWLEDGE_PIPELINE_RUNTIME_BASE_DIR", str(tmp_path),
    )
    a = get_status_sidecar_path("kb-a")
    b = get_status_sidecar_path("kb-b")
    assert a != b
    assert "kb-a" in str(a) and "kb-b" in str(b)
    assert a.parent.exists() and b.parent.exists()
