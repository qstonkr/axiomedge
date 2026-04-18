"""Cost guard — agent loop 의 token/시간/단계 budget 강제.

Plan 단계 수, iteration 횟수, 누적 토큰 비용 한도. env override 가능.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

from src.agentic.protocols import TokenUsage, ToolResult


@dataclass
class CostGuardConfig:
    """env-driven 한도값. 모든 값 0 또는 음수면 제한 없음."""

    max_steps_per_iteration: int = field(
        default_factory=lambda: int(os.getenv("AGENTIC_MAX_STEPS", "5")),
    )
    max_iterations: int = field(
        default_factory=lambda: int(os.getenv("AGENTIC_MAX_ITERATIONS", "3")),
    )
    max_total_steps: int = field(
        default_factory=lambda: int(os.getenv("AGENTIC_MAX_TOTAL_STEPS", "12")),
    )
    budget_usd: float = field(
        default_factory=lambda: float(os.getenv("AGENTIC_BUDGET_USD", "0.10")),
    )
    timeout_seconds: float = field(
        default_factory=lambda: float(os.getenv("AGENTIC_TIMEOUT_SECONDS", "60")),
    )


class CostGuard:
    """누적 비용/시간/단계 추적 + 한도 초과 감지."""

    def __init__(self, config: CostGuardConfig | None = None) -> None:
        self.config = config or CostGuardConfig()
        self._started_at: float = time.monotonic()
        self._steps_executed: int = 0
        self._iterations: int = 0
        self._tokens = TokenUsage()

    def reset_clock(self) -> None:
        self._started_at = time.monotonic()
        self._steps_executed = 0
        self._iterations = 0

    @property
    def tokens(self) -> TokenUsage:
        return self._tokens

    @property
    def elapsed_seconds(self) -> float:
        return time.monotonic() - self._started_at

    @property
    def steps_executed(self) -> int:
        return self._steps_executed

    @property
    def iterations(self) -> int:
        return self._iterations

    def record_step(self, tool_result: ToolResult) -> None:
        """tool 호출 1회 누적."""
        self._steps_executed += 1
        meta = tool_result.metadata or {}
        # tool 이 token_cost 메타 채우면 누적 (LLM 호출 wrap 시)
        usd = float(meta.get("estimated_cost_usd", 0.0))
        self._tokens = TokenUsage(
            prompt_tokens=self._tokens.prompt_tokens + int(meta.get("prompt_tokens", 0)),
            completion_tokens=self._tokens.completion_tokens + int(meta.get("completion_tokens", 0)),
            estimated_cost_usd=self._tokens.estimated_cost_usd + usd,
        )

    def record_llm_call(self, prompt_tokens: int, completion_tokens: int, usd: float) -> None:
        self._tokens = TokenUsage(
            prompt_tokens=self._tokens.prompt_tokens + prompt_tokens,
            completion_tokens=self._tokens.completion_tokens + completion_tokens,
            estimated_cost_usd=self._tokens.estimated_cost_usd + usd,
        )

    def begin_iteration(self) -> None:
        self._iterations += 1

    def step_limit_reached(self) -> bool:
        cfg = self.config
        if cfg.max_steps_per_iteration > 0 and self._steps_executed >= cfg.max_steps_per_iteration * cfg.max_iterations:
            return True
        if cfg.max_total_steps > 0 and self._steps_executed >= cfg.max_total_steps:
            return True
        return False

    def iteration_limit_reached(self) -> bool:
        cfg = self.config
        return cfg.max_iterations > 0 and self._iterations >= cfg.max_iterations

    def budget_exceeded(self) -> bool:
        cfg = self.config
        return cfg.budget_usd > 0 and self._tokens.estimated_cost_usd >= cfg.budget_usd

    def timeout_exceeded(self) -> bool:
        cfg = self.config
        return cfg.timeout_seconds > 0 and self.elapsed_seconds >= cfg.timeout_seconds

    def should_stop(self) -> tuple[bool, str]:
        """현재 상태에서 정지해야 하는지 + 사유."""
        if self.timeout_exceeded():
            return True, f"timeout exceeded ({self.elapsed_seconds:.1f}s)"
        if self.budget_exceeded():
            return True, f"budget exceeded (${self._tokens.estimated_cost_usd:.4f})"
        if self.step_limit_reached():
            return True, f"step limit reached ({self._steps_executed} steps)"
        if self.iteration_limit_reached():
            return True, f"iteration limit reached ({self._iterations} iter)"
        return False, ""
