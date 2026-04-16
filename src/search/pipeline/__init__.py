"""Search pipeline — stage-based architecture.

hub_search의 13+ 단계를 독립 stage로 분리해서
각 단계를 테스트/교체/추가할 수 있게 한다.

Usage:
    from src.search.pipeline import SearchPipeline, SearchContext
    from src.search.pipeline.stages import (
        CacheCheckStage, PreprocessStage, EmbedStage, ...
    )
"""

from src.search.pipeline.protocol import (
    SearchContext,
    SearchPipeline,
    SearchStage,
)

__all__ = [
    "SearchContext",
    "SearchPipeline",
    "SearchStage",
]
