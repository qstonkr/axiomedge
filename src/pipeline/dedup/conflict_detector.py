"""Conflict Detector - LLM-based Content Conflict Detection.

Stage 4: CARE-RAG style LLM-based content conflict analysis.

Features:
- ~100ms processing time
- Semantic conflict detection (e.g., "January implementation" vs "February implementation")
- Version conflict, policy inconsistency detection
- Uses local Ollama (exaone/qwen) instead of GPT

Conflict types:
- DATE_CONFLICT: Date/timeline inconsistency
- VERSION_CONFLICT: Version information conflict
- POLICY_CONFLICT: Policy/regulation inconsistency
- NUMERIC_CONFLICT: Number/value inconsistency
- PROCEDURAL_CONFLICT: Procedure/method inconsistency

Adapted from oreo-ecosystem infrastructure/dedup/conflict_detector.py.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class ConflictType(str, Enum):
    """Conflict type."""

    DATE_CONFLICT = "date_conflict"
    VERSION_CONFLICT = "version_conflict"
    POLICY_CONFLICT = "policy_conflict"
    NUMERIC_CONFLICT = "numeric_conflict"
    PROCEDURAL_CONFLICT = "procedural_conflict"
    FACTUAL_CONFLICT = "factual_conflict"
    NONE = "none"


class ConflictSeverity(str, Enum):
    """Conflict severity."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass
class ConflictDetail:
    """Conflict detail.

    Attributes:
        conflict_type: Conflict type
        severity: Severity
        description: Conflict description
        doc1_excerpt: Relevant text from document 1
        doc2_excerpt: Relevant text from document 2
        resolution_suggestion: Resolution suggestion
    """

    conflict_type: ConflictType
    severity: ConflictSeverity
    description: str
    doc1_excerpt: str = ""
    doc2_excerpt: str = ""
    resolution_suggestion: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize."""
        return {
            "conflict_type": self.conflict_type.value,
            "severity": self.severity.value,
            "description": self.description,
            "doc1_excerpt": self.doc1_excerpt,
            "doc2_excerpt": self.doc2_excerpt,
            "resolution_suggestion": self.resolution_suggestion,
        }


@dataclass
class ConflictAnalysisResult:
    """Conflict analysis result.

    Attributes:
        doc_id_1: First document ID
        doc_id_2: Second document ID
        has_conflict: Whether conflicts were found
        conflicts: List of detected conflicts
        confidence: Analysis confidence
        model_used: Model used for analysis
    """

    doc_id_1: str
    doc_id_2: str
    has_conflict: bool = False
    conflicts: list[ConflictDetail] = field(default_factory=list)
    confidence: float = 0.0
    model_used: str = "ollama"

    @property
    def max_severity(self) -> ConflictSeverity | None:
        """Highest severity among conflicts."""
        if not self.conflicts:
            return None

        severity_order = [
            ConflictSeverity.CRITICAL,
            ConflictSeverity.HIGH,
            ConflictSeverity.MEDIUM,
            ConflictSeverity.LOW,
        ]

        for severity in severity_order:
            if any(c.severity == severity for c in self.conflicts):
                return severity

        return None

    def to_dict(self) -> dict[str, Any]:
        """Serialize."""
        return {
            "doc_id_1": self.doc_id_1,
            "doc_id_2": self.doc_id_2,
            "has_conflict": self.has_conflict,
            "conflicts": [c.to_dict() for c in self.conflicts],
            "max_severity": self.max_severity.value if self.max_severity else None,
            "confidence": self.confidence,
            "model_used": self.model_used,
        }


class ILLMClient:
    """LLM client interface for conflict detection.

    Implementations should provide a complete() method for text generation.
    """

    async def complete(
        self, prompt: str, model: str = "", temperature: float = 0.0
    ) -> str:
        """Generate a completion for the given prompt."""
        raise NotImplementedError


class NoOpLLMClient(ILLMClient):
    """No-op LLM client with mock conflict response.

    Returns a stable JSON payload for unit tests and local runs.
    """

    async def complete(
        self, prompt: str, model: str = "", temperature: float = 0.0
    ) -> str:
        return json.dumps(
            {
                "has_conflict": False,
                "conflicts": [],
                "confidence": 0.9,
            }
        )


class OllamaLLMClient(ILLMClient):
    """Ollama-based LLM client for conflict detection.

    Uses the local Ollama instance for conflict analysis.
    """

    def __init__(
        self,
        base_url: str = "",
        model: str | None = None,
    ):
        import os as _os
        from src.config import DEFAULT_LLM_MODEL, get_settings
        self._base_url = (base_url or get_settings().ollama.base_url).rstrip("/")
        self._model = model or _os.getenv("OLLAMA_MODEL", DEFAULT_LLM_MODEL)

    async def complete(
        self, prompt: str, model: str = "", temperature: float = 0.0
    ) -> str:
        import httpx

        use_model = model or self._model
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    f"{self._base_url}/api/generate",
                    json={
                        "model": use_model,
                        "prompt": prompt,
                        "stream": False,
                        "options": {
                            "temperature": temperature,
                            "num_predict": 2048,
                        },
                    },
                )
                response.raise_for_status()
                data = response.json()
                return data.get("response", "")
        except Exception as e:
            logger.error("Ollama LLM call failed: %s", e)
            return json.dumps(
                {"has_conflict": False, "conflicts": [], "confidence": 0.0}
            )


def _parse_llm_json(response: str) -> dict:
    """Parse LLM response as JSON, handling markdown fences and fallback regex."""
    cleaned = response.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        cleaned = "\n".join(
            line for line in lines
            if not line.strip().startswith("```")
        )
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        json_match = re.search(r"\{[\s\S]*\}", response)
        if json_match:
            try:
                return json.loads(json_match.group())
            except json.JSONDecodeError:
                logger.warning("Failed to parse conflict analysis JSON")
        return {"has_conflict": False, "conflict_type": "unknown"}


def _populate_conflicts(result, conflicts_data: list) -> None:
    """Parse conflict entries and append to result."""
    for conflict_data in conflicts_data:
        try:
            conflict = ConflictDetail(
                conflict_type=ConflictType(
                    conflict_data.get("conflict_type", "factual_conflict")
                ),
                severity=ConflictSeverity(
                    conflict_data.get("severity", "medium")
                ),
                description=conflict_data.get("description", ""),
                doc1_excerpt=conflict_data.get("doc1_excerpt", ""),
                doc2_excerpt=conflict_data.get("doc2_excerpt", ""),
                resolution_suggestion=conflict_data.get(
                    "resolution_suggestion", ""
                ),
            )
            result.conflicts.append(conflict)
        except ValueError as e:
            logger.warning("Invalid conflict data: %s", e)


class ConflictDetector:
    """LLM-based content conflict detector.

    CARE-RAG style analysis of two documents for content conflicts.

    SSOT:
    - Uses local Ollama (exaone/qwen) for analysis
    - Processing time: ~100ms
    """

    DEFAULT_MODEL = None  # Resolved at runtime from src.config.DEFAULT_LLM_MODEL
    LLM_TIMEOUT_SECONDS = 60

    ANALYSIS_PROMPT = """You are an expert document analyst. Analyze two documents for potential content conflicts.

DOCUMENT 1 (ID: {doc_id_1}):
{doc1_content}

DOCUMENT 2 (ID: {doc_id_2}):
{doc2_content}

Analyze for conflicts in the following categories:
1. DATE_CONFLICT: Different dates/timelines for the same event
2. VERSION_CONFLICT: Different version information
3. POLICY_CONFLICT: Contradicting policies or rules
4. NUMERIC_CONFLICT: Different numbers/values for the same metric
5. PROCEDURAL_CONFLICT: Different procedures/steps for the same task
6. FACTUAL_CONFLICT: Contradicting facts

Respond in JSON format:
{{
    "has_conflict": true/false,
    "confidence": 0.0-1.0,
    "conflicts": [
        {{
            "conflict_type": "date_conflict|version_conflict|policy_conflict|numeric_conflict|procedural_conflict|factual_conflict",
            "severity": "critical|high|medium|low",
            "description": "Clear description of the conflict",
            "doc1_excerpt": "Relevant text from doc 1",
            "doc2_excerpt": "Relevant text from doc 2",
            "resolution_suggestion": "How to resolve this conflict"
        }}
    ]
}}

Only report actual conflicts, not minor differences in wording.
Focus on information that could cause confusion or errors if both documents are used."""

    def __init__(
        self,
        llm_client: ILLMClient | None = None,
        model: str | None = None,
        max_content_length: int = 4000,
    ):
        """Initialize.

        Args:
            llm_client: LLM client (defaults to NoOp for testing)
            model: Analysis model (default: exaone3.5:7.8b)
            max_content_length: Max content length (token savings)
        """
        from src.config import DEFAULT_LLM_MODEL
        self._llm_client = llm_client or NoOpLLMClient()
        self._model = model or DEFAULT_LLM_MODEL
        self._max_content_length = max_content_length

    async def analyze(
        self,
        doc_id_1: str,
        doc1_content: str,
        doc_id_2: str,
        doc2_content: str,
    ) -> ConflictAnalysisResult:
        """Analyze two documents for conflicts.

        Args:
            doc_id_1: First document ID
            doc1_content: First document content
            doc_id_2: Second document ID
            doc2_content: Second document content

        Returns:
            Conflict analysis result
        """
        # Truncate content
        doc1_truncated = doc1_content[: self._max_content_length]
        doc2_truncated = doc2_content[: self._max_content_length]

        # Build prompt
        prompt = self.ANALYSIS_PROMPT.format(
            doc_id_1=doc_id_1,
            doc1_content=doc1_truncated,
            doc_id_2=doc_id_2,
            doc2_content=doc2_truncated,
        )

        result = ConflictAnalysisResult(
            doc_id_1=doc_id_1,
            doc_id_2=doc_id_2,
            model_used=self._model,
        )

        try:
            response = await asyncio.wait_for(
                self._llm_client.complete(
                    prompt, model=self._model, temperature=0.0
                ),
                timeout=self.LLM_TIMEOUT_SECONDS,
            )

            analysis = _parse_llm_json(response)
            if analysis:
                result.has_conflict = analysis.get("has_conflict", False)
                result.confidence = analysis.get("confidence", 0.0)
                _populate_conflicts(result, analysis.get("conflicts", []))

        except asyncio.TimeoutError:
            logger.error(
                "Conflict analysis timed out after %ds", self.LLM_TIMEOUT_SECONDS
            )
            result.confidence = 0.0
        except Exception as e:
            logger.error("Conflict analysis failed: %s", e)
            result.confidence = 0.0

        return result

    async def analyze_batch(
        self, document_pairs: list[tuple[str, str, str, str]]
    ) -> list[ConflictAnalysisResult]:
        """Batch conflict analysis.

        Args:
            document_pairs: [(doc_id_1, content_1, doc_id_2, content_2), ...]

        Returns:
            Analysis results
        """
        results: list[ConflictAnalysisResult] = []

        for doc_id_1, content_1, doc_id_2, content_2 in document_pairs:
            result = await self.analyze(doc_id_1, content_1, doc_id_2, content_2)
            results.append(result)

        return results

    def quick_conflict_check(self, text1: str, text2: str) -> list[str]:
        """Quick conflict check without LLM.

        Regex-based detection of obvious conflict patterns.

        Args:
            text1: First text
            text2: Second text

        Returns:
            List of conflict hints
        """
        hints: list[str] = []

        # Date pattern extraction
        date_pattern = r"\d{4}[-/\ub144]\s*\d{1,2}[-/\uc6d4]\s*\d{1,2}\uc77c?"
        dates1 = set(re.findall(date_pattern, text1))
        dates2 = set(re.findall(date_pattern, text2))

        if dates1 and dates2 and dates1 != dates2:
            hints.append(f"Date difference detected: {dates1} vs {dates2}")

        # Version pattern extraction
        version_pattern = r"v?\d+\.\d+(?:\.\d+)?"
        versions1 = set(re.findall(version_pattern, text1, re.IGNORECASE))
        versions2 = set(re.findall(version_pattern, text2, re.IGNORECASE))

        if versions1 and versions2 and versions1 != versions2:
            hints.append(f"Version difference detected: {versions1} vs {versions2}")

        # Numeric+unit pattern extraction
        numeric_pattern = r"\d+(?:,\d{3})*(?:\.\d+)?\s*(?:\uc6d0|\ub2ec\ub7ec|%|\uac1c|\uba85|\uac74)"
        numbers1 = set(re.findall(numeric_pattern, text1))
        numbers2 = set(re.findall(numeric_pattern, text2))

        if numbers1 and numbers2 and numbers1 != numbers2:
            hints.append(f"Numeric difference detected: {numbers1} vs {numbers2}")

        return hints
