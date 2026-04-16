"""Search pipeline — Stage Protocol + Context + Pipeline runner.

DataGenPipeline (src/distill/pipeline/stages.py) 과 동일한 패턴.
각 stage는 SearchStage Protocol을 구현하고, SearchPipeline이 순차 실행.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Context — 단계 간 공유 state
# ---------------------------------------------------------------------------

@dataclass
class SearchContext:
    """Search pipeline 전체에서 공유되는 context bag.

    각 stage는 이 객체를 읽고 수정한 뒤 돌려준다.
    """

    # Input
    raw_query: str
    top_k: int
    state: dict[str, Any]  # AppState
    request: Any = None  # HubSearchRequest

    # Query processing
    corrected_query: str = ""
    search_query: str = ""
    display_query: str = ""
    expanded_terms: list[str] = field(default_factory=list)
    preprocess_info: dict[str, Any] = field(default_factory=dict)
    query_type: str = ""
    effective_top_k: int = 0

    # Embeddings
    dense_vector: list[float] = field(default_factory=list)
    sparse_vector: Any = None
    colbert_vectors: Any = None

    # Search results
    collections: Any = None
    all_chunks: list[Any] = field(default_factory=list)
    searched_kbs: list[str] = field(default_factory=list)

    # Reranking
    rerank_applied: bool = False
    search_chunks: list[Any] = field(default_factory=list)

    # Answer generation
    crag_evaluation: Any = None
    answer: str | None = None
    confidence: float = 0.0
    conflicts: list[Any] | None = None
    follow_ups: list[str] | None = None
    transparency: dict[str, Any] = field(default_factory=dict)

    # Timing
    start_time: float = field(default_factory=time.time)

    # Stage logs
    stage_logs: dict[str, Any] = field(default_factory=dict)

    @property
    def elapsed_ms(self) -> float:
        return (time.time() - self.start_time) * 1000


# ---------------------------------------------------------------------------
# Stage Protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class SearchStage(Protocol):
    """단일 search pipeline 단계.

    ``name``과 ``process``만 구현하면 pipeline에 등록 가능.
    """

    name: str

    async def process(self, ctx: SearchContext) -> SearchContext:
        """Context를 받아 변형하고 돌려준다."""
        ...  # pragma: no cover


# ---------------------------------------------------------------------------
# Pipeline runner
# ---------------------------------------------------------------------------

class SearchPipeline:
    """Stage 조립 + 순차 실행.

    Usage:
        pipeline = SearchPipeline(ctx)
        pipeline.add(CacheCheckStage())
        pipeline.add(PreprocessStage())
        result = await pipeline.run()
    """

    def __init__(self, ctx: SearchContext) -> None:
        self._ctx = ctx
        self._stages: list[SearchStage] = []

    def add(self, stage: SearchStage) -> "SearchPipeline":
        """Stage 추가 (builder pattern)."""
        self._stages.append(stage)
        return self

    async def run(self) -> SearchContext:
        """모든 stage를 순서대로 실행."""
        for stage in self._stages:
            stage_name = getattr(stage, "name", type(stage).__name__)
            try:
                self._ctx = await stage.process(self._ctx)
            except Exception as e:
                logger.error(
                    "Search stage [%s] failed: %s", stage_name, e,
                    exc_info=True,
                )
                self._ctx.stage_logs[stage_name] = {"error": str(e)}
        return self._ctx

    @property
    def stage_count(self) -> int:
        return len(self._stages)
