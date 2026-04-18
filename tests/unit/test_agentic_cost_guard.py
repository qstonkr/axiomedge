"""Tests for cost guard — budget/timeout/step limits."""

from __future__ import annotations

import time

from src.agentic.cost_guard import CostGuard, CostGuardConfig
from src.agentic.protocols import ToolResult


def _result(meta: dict | None = None) -> ToolResult:
    return ToolResult(success=True, data=None, metadata=meta or {})


def test_step_limit_per_iteration_via_config() -> None:
    g = CostGuard(CostGuardConfig(max_steps_per_iteration=2, max_iterations=1, max_total_steps=0))
    g.begin_iteration()
    g.record_step(_result())
    g.record_step(_result())
    stop, reason = g.should_stop()
    assert stop and "step" in reason


def test_total_step_limit_independent_of_iteration() -> None:
    g = CostGuard(CostGuardConfig(
        max_steps_per_iteration=10, max_iterations=10, max_total_steps=3,
    ))
    g.begin_iteration()
    g.record_step(_result())
    g.record_step(_result())
    g.record_step(_result())
    stop, reason = g.should_stop()
    assert stop and "step" in reason


def test_iteration_limit() -> None:
    g = CostGuard(CostGuardConfig(max_iterations=2))
    g.begin_iteration()
    g.begin_iteration()
    stop, reason = g.should_stop()
    assert stop and "iteration" in reason


def test_budget_exceeded_via_step_metadata() -> None:
    g = CostGuard(CostGuardConfig(budget_usd=0.01))
    g.record_step(_result(meta={"estimated_cost_usd": 0.005, "prompt_tokens": 100}))
    g.record_step(_result(meta={"estimated_cost_usd": 0.01, "prompt_tokens": 200}))
    stop, reason = g.should_stop()
    assert stop and "budget" in reason


def test_budget_exceeded_via_record_llm_call() -> None:
    g = CostGuard(CostGuardConfig(budget_usd=0.005))
    g.record_llm_call(prompt_tokens=100, completion_tokens=50, usd=0.01)
    stop, reason = g.should_stop()
    assert stop and "budget" in reason
    assert g.tokens.estimated_cost_usd >= 0.01
    assert g.tokens.prompt_tokens == 100


def test_no_stop_when_within_limits() -> None:
    g = CostGuard(CostGuardConfig(
        max_steps_per_iteration=10, max_iterations=10,
        max_total_steps=10, budget_usd=10.0, timeout_seconds=10.0,
    ))
    g.begin_iteration()
    g.record_step(_result())
    stop, _ = g.should_stop()
    assert not stop


def test_zero_or_negative_limits_disabled() -> None:
    g = CostGuard(CostGuardConfig(
        max_steps_per_iteration=0, max_iterations=0, max_total_steps=0,
        budget_usd=0.0, timeout_seconds=0.0,
    ))
    g.begin_iteration()
    for _ in range(100):
        g.record_step(_result(meta={"estimated_cost_usd": 1.0}))
    stop, _ = g.should_stop()
    assert not stop  # all limits disabled


def test_elapsed_seconds_increases() -> None:
    g = CostGuard(CostGuardConfig())
    t1 = g.elapsed_seconds
    time.sleep(0.01)
    t2 = g.elapsed_seconds
    assert t2 > t1


def test_reset_clock() -> None:
    g = CostGuard(CostGuardConfig())
    g.begin_iteration()
    g.record_step(_result())
    g.reset_clock()
    assert g.steps_executed == 0
    assert g.iterations == 0
