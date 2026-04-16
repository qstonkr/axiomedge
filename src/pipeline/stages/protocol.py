"""Ingestion pipeline — Stage Protocol + Context + Pipeline runner.

ingest()의 14단계를 plugin-style stage로 분리.
DataGenPipeline / SearchPipeline과 동일 패턴.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from src.domain.models import RawDocument, IngestionResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Context
# ---------------------------------------------------------------------------

@dataclass
class IngestionStageContext:
    """Ingestion pipeline 전체에서 공유되는 context.

    각 stage는 이 객체를 읽고 수정한 뒤 돌려준다.
    """

    # Input
    raw: RawDocument
    collection_name: str
    parse_result: Any = None

    # Content hash (dedup용)
    content_hash: str = ""

    # Quality
    quality_tier: Any = None
    quality_metrics: Any = None
    quality_score: float = 0.0

    # Classification
    doc_type: str = ""
    owner: str = ""
    l1_category: str = ""

    # Chunks
    typed_chunks: list[tuple[str, str, str]] = field(default_factory=list)
    heading_map: dict[str, str] = field(default_factory=dict)
    doc_summary: str = ""
    prefixed_chunks: list[str] = field(default_factory=list)
    chunk_types: list[str] = field(default_factory=list)
    chunk_heading_paths: list[str] = field(default_factory=list)
    chunk_morphemes: list[str] = field(default_factory=list)

    # Vectors
    dense_vectors: list[list[float]] = field(default_factory=list)
    sparse_vectors: list[Any] = field(default_factory=list)

    # Items to store
    items: list[dict[str, Any]] = field(default_factory=list)

    # Result
    result: IngestionResult | None = None
    content_flags: dict[str, bool] = field(default_factory=dict)

    # Stage logs
    stage_logs: dict[str, Any] = field(default_factory=dict)

    @property
    def should_stop(self) -> bool:
        """이전 stage에서 early-exit(dedup, quality gate 등)을 결정했는지."""
        return self.result is not None


# ---------------------------------------------------------------------------
# Stage Protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class IngestionStage(Protocol):
    """단일 ingestion pipeline 단계."""

    name: str

    async def process(self, ctx: IngestionStageContext) -> IngestionStageContext:
        """Context를 받아 변형하고 돌려준다.

        ``ctx.should_stop`` 이 True이면 skip 권장 (이전 stage에서 early-exit).
        """
        ...  # pragma: no cover


# ---------------------------------------------------------------------------
# Pipeline runner
# ---------------------------------------------------------------------------

class IngestionPipelineRunner:
    """Stage 조립 + 순차 실행 (early-exit 지원).

    Usage:
        runner = IngestionPipelineRunner(ctx)
        runner.add(DedupCheckStage())
        runner.add(QualityCheckStage())
        runner.add(ChunkStage())
        result_ctx = await runner.run()
    """

    def __init__(self, ctx: IngestionStageContext) -> None:
        self._ctx = ctx
        self._stages: list[IngestionStage] = []

    def add(self, stage: IngestionStage) -> "IngestionPipelineRunner":
        """Stage 추가 (builder pattern)."""
        self._stages.append(stage)
        return self

    async def run(self) -> IngestionStageContext:
        """모든 stage를 순서대로 실행. ctx.should_stop이면 중단."""
        for stage in self._stages:
            if self._ctx.should_stop:
                break
            stage_name = getattr(stage, "name", type(stage).__name__)
            try:
                self._ctx = await stage.process(self._ctx)
            except Exception as e:
                logger.error(
                    "Ingestion stage [%s] failed: %s", stage_name, e,
                    exc_info=True,
                )
                self._ctx.stage_logs[stage_name] = {"error": str(e)}
                self._ctx.result = IngestionResult.failure_result(
                    reason=str(e), stage=stage_name,
                )
                break
        return self._ctx

    @property
    def stage_count(self) -> int:
        return len(self._stages)
