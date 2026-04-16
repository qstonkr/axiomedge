"""Data generation pipeline — Stage Protocol + Context.

``generate_data_for_review()`` 의 6 단계를 plugin-style stage 로 분리해서
각 단계를 독립적으로 테스트/교체/추가할 수 있게 한다.

### Protocol

각 stage 는 ``DataGenStage`` Protocol 을 구현:

```python
class MyStage:
    name = "my_stage"

    async def process(self, ctx: DataGenContext) -> DataGenContext:
        # ctx.rows 를 변형/추가하고 돌려준다
        return ctx
```

### Context

``DataGenContext`` 는 단계 간 공유 state bag. 각 단계가 필요한 것만 읽고
필요한 것만 변형한다. ``ctx.rows`` 가 핵심 — QA pair 리스트.

### Pipeline builder

```python
pipeline = DataGenPipeline(profile, generator, batch_id)
pipeline.add(QAGenerationStage(...))
pipeline.add(GeneralityStage(...))
pipeline.add(ReformatStage(...))
pipeline.add(AugmentStage(...))
result = await pipeline.run()
```
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from src.distill.config import DistillProfile

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Context — 단계 간 공유 state
# ---------------------------------------------------------------------------

@dataclass
class DataGenContext:
    """Pipeline 전체에서 공유되는 context bag.

    각 stage 는 이 객체를 읽고 수정한 뒤 돌려준다. ``rows`` 가 핵심 —
    모든 QA pair 가 여기에 담김.
    """

    profile_name: str
    profile: DistillProfile
    batch_id: str
    kb_ids: list[str]
    search_group: str

    # 핵심 데이터 — QA pair 리스트. 각 dict 는 최소 {question, answer}.
    rows: list[dict[str, Any]] = field(default_factory=list)

    # Phase 1.5 산출물 (reformat / augment). 최종 저장 시 rows + 이 두 리스트를 합친다.
    reformatted_rows: list[dict[str, Any]] = field(default_factory=list)
    augmented_rows: list[dict[str, Any]] = field(default_factory=list)

    # 각 단계의 summary 로그
    stage_logs: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Stage Protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class DataGenStage(Protocol):
    """단일 data generation 단계.

    이름(``name``)과 처리(``process``)만 구현하면 pipeline 에 등록 가능.
    """

    name: str

    async def process(self, ctx: DataGenContext) -> DataGenContext:
        """Context 를 받아 변형하고 돌려준다.

        Stage 가 실패하면 exception 을 raise 하지 않고 ctx 를 그대로 반환
        + ``ctx.stage_logs[self.name] = {"error": ...}`` 로 기록 권장.
        """
        ...  # pragma: no cover


# ---------------------------------------------------------------------------
# Pipeline builder
# ---------------------------------------------------------------------------

class DataGenPipeline:
    """Stage 조립 + 순차 실행.

    Usage:
        pipeline = DataGenPipeline(ctx)
        pipeline.add(StageA())
        pipeline.add(StageB())
        result_ctx = await pipeline.run()
    """

    def __init__(self, ctx: DataGenContext) -> None:
        self._ctx = ctx
        self._stages: list[DataGenStage] = []

    def add(self, stage: DataGenStage) -> "DataGenPipeline":
        """Stage 추가 (builder pattern, chaining 가능)."""
        self._stages.append(stage)
        return self

    async def run(self) -> DataGenContext:
        """모든 stage 를 순서대로 실행.

        각 stage 는 ctx 를 변형해 반환. 실패 시 해당 stage 만 skip +
        로그 기록, 다음 stage 는 계속 진행 (fail-open).
        """
        for stage in self._stages:
            stage_name = getattr(stage, "name", type(stage).__name__)
            try:
                logger.info("Pipeline stage [%s] starting (rows=%d)", stage_name, len(self._ctx.rows))
                self._ctx = await stage.process(self._ctx)
                logger.info(
                    "Pipeline stage [%s] done (rows=%d, reformatted=%d, augmented=%d)",
                    stage_name, len(self._ctx.rows),
                    len(self._ctx.reformatted_rows), len(self._ctx.augmented_rows),
                )
            except Exception as e:
                logger.error("Pipeline stage [%s] failed: %s", stage_name, e, exc_info=True)
                self._ctx.stage_logs[stage_name] = {"error": str(e)}
        return self._ctx


def make_context(
    profile_name: str,
    profile: DistillProfile,
    kb_ids: list[str],
    search_group: str,
    batch_id: str | None = None,
) -> DataGenContext:
    """Context factory — batch_id 자동 생성."""
    return DataGenContext(
        profile_name=profile_name,
        profile=profile,
        batch_id=batch_id or str(uuid.uuid4()),
        kb_ids=kb_ids,
        search_group=search_group,
    )
