"""Distill data generation pipeline — stage-based architecture.

Usage:
    from src.distill.pipeline import DataGenPipeline, make_context
    from src.distill.pipeline.data_gen_stages import (
        QAGenerationStage, GeneralityStage, IDAssignStage,
        ReformatStage, AugmentStage,
    )
"""

from src.distill.pipeline.stages import (
    DataGenContext,
    DataGenPipeline,
    DataGenStage,
    make_context,
)

__all__ = [
    "DataGenContext",
    "DataGenPipeline",
    "DataGenStage",
    "make_context",
]
