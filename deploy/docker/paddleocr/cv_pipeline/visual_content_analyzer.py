"""Visual Content Analyzer for Knowledge Pipeline.

Extracts structured knowledge from images.
CV Pipeline (OpenCV + PaddleOCR) -> Text LLM (Ollama) -> Structured Output.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class VisualAnalysisResult:
    """Image analysis result."""

    image_type: str = "unknown"  # flowchart, architecture, data_flow, etc.
    raw_text: str = ""  # PaddleOCR extracted text
    description: str = ""  # LLM summary
    entities: list[dict[str, str]] = field(default_factory=list)
    relationships: list[dict[str, str]] = field(default_factory=list)
    process_steps: list[dict[str, Any]] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    confidence: float = 0.0

    def to_text(self) -> str:
        """Serialize to text for vector search."""
        parts = []
        if self.description:
            parts.append(f"[Visual: {self.image_type}] {self.description}")
        if self.process_steps:
            steps = "\n".join(
                f"  {s.get('step', i+1)}. {s.get('action', '')}"
                for i, s in enumerate(self.process_steps)
            )
            parts.append(f"Process:\n{steps}")
        systems = [e["name"] for e in self.entities if e.get("type") == "System"]
        if systems:
            parts.append(f"Related systems: {', '.join(systems)}")
        if self.raw_text and not self.description:
            parts.append(f"[Image OCR] {self.raw_text}")
        return "\n\n".join(parts)

    def to_graph_data(self) -> dict[str, Any]:
        """Node/edge data for GraphRAG."""
        data: dict[str, Any] = {
            "nodes": self.entities,
            "relationships": self.relationships,
        }
        if self.process_steps:
            data["process_steps"] = self.process_steps
        return data


class VisualContentAnalyzer:
    """CV Pipeline + Text LLM based image structure analyzer.

    Usage:
        analyzer = VisualContentAnalyzer()
        result = await analyzer.analyze(image_bytes)
        text = result.to_text()          # for vector search
        graph = result.to_graph_data()   # for GraphRAG
    """

    def __init__(self) -> None:
        self._cv_pipeline = None  # lazy singleton
        self._lock = __import__("threading").Lock()

    async def analyze(self, image_bytes: bytes) -> VisualAnalysisResult:
        """CV Pipeline based analysis.

        Args:
            image_bytes: Image byte data

        Returns:
            VisualAnalysisResult

        Raises:
            Exception: If CV pipeline fails (no silent fallback).
        """
        from src.pipelines.cv.pipeline import CVPipeline

        # CVPipeline instance reuse (double-checked locking)
        if self._cv_pipeline is None:
            with self._lock:
                if self._cv_pipeline is None:
                    self._cv_pipeline = CVPipeline()
        return await self._cv_pipeline.analyze(image_bytes)
