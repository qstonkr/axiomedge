"""Ingestion pipeline — stage-based architecture.

ingest()의 14단계를 독립 stage로 분리.

Usage:
    from src.pipelines.stages import IngestionPipelineRunner, IngestionStageContext
"""

from src.pipelines.stages.protocol import (
    IngestionStageContext,
    IngestionPipelineRunner,
    IngestionStage,
)

__all__ = [
    "IngestionStageContext",
    "IngestionPipelineRunner",
    "IngestionStage",
]
