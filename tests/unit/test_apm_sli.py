"""Tests for APM business SLIs (cache hit, per-KB error rate, RAG stage latency)."""

from __future__ import annotations

from src.api.routes import metrics as m


def _reset_sli_state() -> None:
    m._cache_hits.clear()
    m._cache_misses.clear()
    m._kb_request_count.clear()
    m._rag_stage_duration_sum.clear()
    m._rag_stage_duration_count.clear()
    m._rag_stage_duration_buckets.clear()


def test_cache_hit_increments_layer_counter() -> None:
    _reset_sli_state()
    m.observe_cache("l1", hit=True)
    m.observe_cache("l1", hit=True)
    m.observe_cache("l1", hit=False)
    assert m._cache_hits["l1"] == 2
    assert m._cache_misses["l1"] == 1


def test_cache_separate_layers() -> None:
    _reset_sli_state()
    m.observe_cache("l1", hit=True)
    m.observe_cache("l2", hit=False)
    assert m._cache_hits == {"l1": 1}
    assert m._cache_misses == {"l2": 1}


def test_kb_request_buckets_status_codes() -> None:
    _reset_sli_state()
    m.observe_kb_request("kb_a", 200)
    m.observe_kb_request("kb_a", 201)
    m.observe_kb_request("kb_a", 404)
    m.observe_kb_request("kb_a", 500)
    m.observe_kb_request("kb_b", 502)
    assert m._kb_request_count[("kb_a", "success")] == 2
    assert m._kb_request_count[("kb_a", "client_error")] == 1
    assert m._kb_request_count[("kb_a", "server_error")] == 1
    assert m._kb_request_count[("kb_b", "server_error")] == 1


def test_kb_request_none_kb_becomes_unknown() -> None:
    _reset_sli_state()
    m.observe_kb_request(None, 200)
    assert m._kb_request_count[("_unknown", "success")] == 1


def test_kb_request_truncates_long_kb_id() -> None:
    _reset_sli_state()
    huge = "x" * 200
    m.observe_kb_request(huge, 200)
    keys = list(m._kb_request_count.keys())
    assert len(keys[0][0]) == 64


def test_rag_stage_duration_records_buckets() -> None:
    _reset_sli_state()
    m.observe_rag_stage("embed", 0.05)
    m.observe_rag_stage("embed", 0.5)
    assert m._rag_stage_duration_count["embed"] == 2
    assert abs(m._rag_stage_duration_sum["embed"] - 0.55) < 1e-6
    # le=0.1 bucket: 0.05 yes, 0.5 no — count=1
    assert m._rag_stage_duration_buckets[("embed", 0.1)] == 1
    # le=1.0 bucket: both fit — count=2
    assert m._rag_stage_duration_buckets[("embed", 1.0)] == 2


def test_rag_stage_truncates_long_name() -> None:
    _reset_sli_state()
    m.observe_rag_stage("x" * 100, 0.1)
    assert any(len(k[0]) == 32 for k in m._rag_stage_duration_buckets)


def test_prometheus_render_includes_new_slis() -> None:
    _reset_sli_state()
    m.observe_cache("l1", hit=True)
    m.observe_kb_request("kb_a", 500)
    m.observe_rag_stage("embed", 0.05)
    out = m._render_prometheus()
    assert "cache_hits_total" in out
    assert 'layer="l1"' in out
    assert "kb_request_total" in out
    assert 'kb_id="kb_a"' in out
    assert 'status="server_error"' in out
    assert "rag_stage_duration_seconds" in out
    assert 'stage="embed"' in out
