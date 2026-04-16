"""CV Pipeline -- OpenCV + PaddleOCR + Text LLM based image structuring.

Uses OpenCV + PaddleOCR to extract structural info first, then passes
text to a local LLM (Ollama) for graph normalization since text-only
models cannot process images directly.

Usage:
    from src.pipelines.cv import CVPipeline

    pipeline = CVPipeline()
    result = await pipeline.analyze(image_bytes)
    text = result.to_text()
    graph = result.to_graph_data()
"""

from .models import SignalQuality
from .pipeline import CVPipeline
from .visual_content_analyzer import VisualAnalysisResult, VisualContentAnalyzer

__all__ = ["CVPipeline", "SignalQuality", "VisualAnalysisResult", "VisualContentAnalyzer"]
