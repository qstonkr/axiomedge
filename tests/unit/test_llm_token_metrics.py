"""Tests for LLM token metric recording (per-KB cost attribution)."""

from __future__ import annotations

from src.api.routes import metrics as m


def _reset_llm_state() -> None:
    m._llm_prompt_tokens.clear()
    m._llm_completion_tokens.clear()
    m._llm_estimated_cost_usd.clear()
    m._llm_request_count.clear()


def test_observe_llm_tokens_accumulates() -> None:
    _reset_llm_state()
    m.observe_llm_tokens("kb_a", "sagemaker-exaone", 100, 50)
    m.observe_llm_tokens("kb_a", "sagemaker-exaone", 200, 100)
    key = ("kb_a", "sagemaker-exaone")
    assert m._llm_prompt_tokens[key] == 300
    assert m._llm_completion_tokens[key] == 150
    assert m._llm_request_count[key] == 2


def test_observe_llm_tokens_separates_kbs() -> None:
    _reset_llm_state()
    m.observe_llm_tokens("kb_a", "sagemaker-exaone", 100, 50)
    m.observe_llm_tokens("kb_b", "sagemaker-exaone", 50, 25)
    assert m._llm_prompt_tokens[("kb_a", "sagemaker-exaone")] == 100
    assert m._llm_prompt_tokens[("kb_b", "sagemaker-exaone")] == 50


def test_observe_llm_tokens_cost_for_known_model() -> None:
    _reset_llm_state()
    # sagemaker-exaone: $0.001/1K input, $0.003/1K output
    m.observe_llm_tokens("kb_a", "sagemaker-exaone", 1000, 1000)
    cost = m._llm_estimated_cost_usd[("kb_a", "sagemaker-exaone")]
    assert abs(cost - 0.004) < 1e-6  # 1.0*0.001 + 1.0*0.003


def test_observe_llm_tokens_zero_cost_for_local() -> None:
    _reset_llm_state()
    m.observe_llm_tokens("kb_a", "ollama", 1000, 1000)
    assert m._llm_estimated_cost_usd[("kb_a", "ollama")] == 0.0


def test_observe_llm_tokens_zero_cost_for_unknown() -> None:
    _reset_llm_state()
    m.observe_llm_tokens("kb_a", "unknown-model", 1000, 1000)
    assert m._llm_estimated_cost_usd[("kb_a", "unknown-model")] == 0.0


def test_kb_id_none_becomes_unknown() -> None:
    _reset_llm_state()
    m.observe_llm_tokens(None, "ollama", 100, 50)
    assert ("_unknown", "ollama") in m._llm_prompt_tokens


def test_label_truncation_prevents_cardinality_explosion() -> None:
    _reset_llm_state()
    huge_label = "x" * 200
    m.observe_llm_tokens(huge_label, huge_label, 1, 1)
    # truncated to 64 chars max
    keys = list(m._llm_prompt_tokens.keys())
    assert len(keys[0][0]) == 64
    assert len(keys[0][1]) == 64


def test_prometheus_render_includes_llm_metrics() -> None:
    _reset_llm_state()
    m.observe_llm_tokens("kb_a", "sagemaker-exaone", 100, 50)
    out = m._render_prometheus()
    assert "llm_prompt_tokens_total" in out
    assert 'kb_id="kb_a"' in out
    assert 'model="sagemaker-exaone"' in out
    assert "llm_estimated_cost_usd_total" in out
