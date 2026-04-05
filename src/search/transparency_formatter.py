"""Transparency Formatter.

Source transparency display service.
Clearly labels the source of each response section:
- [document-based]: Directly cited from retrieved documents
- [inference]: Document-based inference
- [general knowledge]: General knowledge outside documents

Extracted from oreo-ecosystem transparency_formatter.py.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from .citation_formatter import CitationFormatter
from .confidence_thresholds import KnowledgeConfidenceThresholds
from .tiered_response import TieredResponse


class SourceType(StrEnum):
    """Source type."""

    DOCUMENT = "document"
    INFERENCE = "inference"
    GENERAL = "general"


@dataclass
class FormattedSection:
    """Formatted section."""

    content: str
    source_type: SourceType
    citations: list[dict]


@dataclass
class TransparentResponse:
    """Transparency-labeled response."""

    formatted_content: str
    sections: list[FormattedSection]
    summary_label: str
    citations_section: str | None
    disclaimer: str | None
    confidence_indicator: str


class TransparencyFormatter:
    """Source transparency formatter.

    Clearly labels the source type of each response section.
    """

    # Source labels
    SOURCE_LABELS = {
        SourceType.DOCUMENT: "📚 [문서 기반]",
        SourceType.INFERENCE: "💭 [추론]",
        SourceType.GENERAL: "💡 [일반 지식]",
    }

    # Confidence indicators
    CONFIDENCE_INDICATORS = {
        "high": "🟢 높은 신뢰도",
        "medium": "🟡 중간 신뢰도",
        "low": "🟠 낮은 신뢰도",
        "uncertain": "🔴 확인 필요",
    }

    # Section patterns
    SECTION_PATTERNS = {
        SourceType.DOCUMENT: [r"\[문서 기반\]", r"\[출처\]", r"\[인용\]"],
        SourceType.INFERENCE: [r"\[분석\]", r"\[추론\]", r"\[해석\]"],
        SourceType.GENERAL: [r"\[권장 사항\]", r"\[일반\]", r"\[참고\]"],
    }

    def format(self, response: TieredResponse) -> TransparentResponse:
        """Apply transparency labels to response.

        Args:
            response: Tiered response

        Returns:
            Transparency-labeled response
        """
        sections = self._split_into_sections(response.content)
        formatted_content = self._format_sections(sections, response.source_type)
        citations_section = self._format_citations(response.citations)
        confidence_level = self._get_confidence_level(response.confidence)
        confidence_indicator = self.CONFIDENCE_INDICATORS.get(confidence_level, "")
        summary_label = self._generate_summary_label(response)

        return TransparentResponse(
            formatted_content=formatted_content,
            sections=sections,
            summary_label=summary_label,
            citations_section=citations_section,
            disclaimer=response.disclaimer,
            confidence_indicator=confidence_indicator,
        )

    def format_simple(self, response: TieredResponse) -> str:
        """Simple transparency formatting (markdown).

        Args:
            response: Tiered response

        Returns:
            Formatted string
        """
        source_label = self.SOURCE_LABELS.get(SourceType(response.source_type), "")
        confidence_level = self._get_confidence_level(response.confidence)
        confidence_indicator = self.CONFIDENCE_INDICATORS.get(confidence_level, "")

        parts = [
            f"{source_label}",
            "",
            response.content,
        ]

        citation_block = self._format_citations(response.citations)
        if citation_block:
            parts.append("")
            parts.append("---")
            parts.append(citation_block)

        if response.disclaimer:
            parts.append("")
            parts.append(f"*{response.disclaimer}*")

        if confidence_indicator:
            parts.append("")
            parts.append(confidence_indicator)

        return "\n".join(parts)

    def _detect_line_source_type(self, line: str) -> SourceType | None:
        """Detect the source type of a line based on section patterns."""
        for source_type, patterns in self.SECTION_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, line, re.IGNORECASE):
                    return source_type
        return None

    def _has_section_markers(self, content: str) -> bool:
        """Check if content contains any section marker patterns."""
        return any(
            re.search(pattern, content)
            for patterns in self.SECTION_PATTERNS.values()
            for pattern in patterns
        )

    def _split_marked_sections(self, content: str) -> list[FormattedSection]:
        """Split content with section markers into typed sections."""
        sections: list[FormattedSection] = []
        current_type = SourceType.DOCUMENT
        current_content: list[str] = []

        for line in content.split("\n"):
            found_type = self._detect_line_source_type(line)

            if found_type and found_type != current_type:
                if current_content:
                    sections.append(FormattedSection(
                        content="\n".join(current_content),
                        source_type=current_type,
                        citations=[],
                    ))
                current_type = found_type
                current_content = [line]
            else:
                current_content.append(line)

        if current_content:
            sections.append(FormattedSection(
                content="\n".join(current_content),
                source_type=current_type,
                citations=[],
            ))
        return sections

    def _split_into_sections(self, content: str) -> list[FormattedSection]:
        """Split content into sections."""
        if self._has_section_markers(content):
            return self._split_marked_sections(content)

        return [FormattedSection(
            content=content,
            source_type=SourceType.DOCUMENT,
            citations=[],
        )]

    def _format_sections(self, sections: list[FormattedSection], default_source: str) -> str:
        """Format sections."""
        if len(sections) == 1:
            section = sections[0]
            source_type = SourceType(default_source)
            label = self.SOURCE_LABELS.get(source_type, "")
            return f"{label}\n\n{section.content}"

        parts = []
        for section in sections:
            label = self.SOURCE_LABELS.get(section.source_type, "")
            parts.append(f"{label}\n{section.content}")

        return "\n\n".join(parts)

    def _format_citations(self, citations: list[dict]) -> str | None:
        """Format citations section."""
        entries = CitationFormatter.from_response_citations(citations)
        citation_block = CitationFormatter.format_markdown(entries)
        if not citation_block:
            return None

        freshness_warnings: list[str] = []
        for citation in citations:
            warning = citation.get("freshness_warning")
            if warning:
                ref = str(citation.get("ref") or "")
                label = f"[{ref}] " if ref else ""
                freshness_warnings.append(f"- {label}{warning}")

        if freshness_warnings:
            warning_block = "\n".join(
                [
                    "",
                    "**🕒 문서 최신성 알림:**",
                    *freshness_warnings,
                ]
            )
            return f"{citation_block}{warning_block}"

        return citation_block

    def _get_confidence_level(self, confidence: float) -> str:
        """Determine confidence level."""
        if confidence >= KnowledgeConfidenceThresholds.HIGH:
            return "high"
        elif confidence >= KnowledgeConfidenceThresholds.MEDIUM:
            return "medium"
        elif confidence >= KnowledgeConfidenceThresholds.LOW:
            return "low"
        else:
            return "uncertain"

    def _generate_summary_label(self, response: TieredResponse) -> str:
        """Generate summary label."""
        source_label = self.SOURCE_LABELS.get(SourceType(response.source_type), "")
        query_type_labels = {
            "factual": "사실 확인",
            "analytical": "분석",
            "advisory": "조언",
        }
        query_label = query_type_labels.get(response.query_type.value, "")

        return f"{source_label} | {query_label} 응답"


class NoOpTransparencyFormatter:
    """TransparencyFormatter NoOp implementation (for testing/development)."""

    def format(self, response: TieredResponse) -> TransparentResponse:
        return TransparentResponse(
            formatted_content=f"[NoOp] {response.content}",
            sections=[],
            summary_label="[NoOp]",
            citations_section=None,
            disclaimer="[NoOp] 테스트 모드",
            confidence_indicator="",
        )

    def format_simple(self, response: TieredResponse) -> str:
        return f"[NoOp] {response.content}"


__all__ = [
    "FormattedSection",
    "NoOpTransparencyFormatter",
    "SourceType",
    "TransparencyFormatter",
    "TransparentResponse",
]
