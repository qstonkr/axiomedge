"""Simplified text chunker with Korean sentence-boundary awareness.

Extracted from oreo-ecosystem KoreanProcessor and ChunkProcessor.
Supports fixed-size and semantic chunking strategies.
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from src.config_weights import weights

logger = logging.getLogger(__name__)

# Pre-compiled regex patterns
_SENTENCE_SPLIT_PATTERN = re.compile(
    r'(?<=[.!?])\s+|(?<=다\.)\s+|(?<=요\.)\s+|(?<=음\.)\s+'
)
_HEADING_PATTERN = re.compile(r'^(#{1,6})\s+(.+)$')


class ChunkStrategy(str, Enum):
    """Chunking strategy."""

    FIXED = "fixed"
    SEMANTIC = "semantic"


@dataclass
class HeadingChunk:
    """A chunk with its heading hierarchy path."""

    text: str
    heading_path: str  # e.g. "설치 가이드 > 사전 요구사항 > Python 설정"


@dataclass
class ChunkResult:
    """Chunking result."""

    chunks: list[str]
    total_chunks: int
    metadata: dict[str, Any] = field(default_factory=dict)
    heading_chunks: list[HeadingChunk] = field(default_factory=list)


class Chunker:
    """Sentence-boundary-aware text chunker with Korean support.

    Uses KSS (Korean Sentence Splitter) when available, falls back to regex.

    Args:
        max_chunk_chars: Maximum characters per chunk.
        overlap_sentences: Number of overlapping sentences between chunks.
        strategy: Chunking strategy (fixed or semantic).
    """

    def __init__(
        self,
        max_chunk_chars: int = weights.chunking.max_chunk_chars,
        overlap_sentences: int = weights.chunking.overlap_sentences,
        strategy: ChunkStrategy = ChunkStrategy.SEMANTIC,
    ) -> None:
        self._max_chunk_chars = max_chunk_chars
        self._overlap_sentences = overlap_sentences
        self._strategy = strategy
        self._kss_available = False
        self._initialized = False
        self._init_lock = threading.Lock()

    @property
    def strategy_name(self) -> str:
        """Public accessor for the chunking strategy name."""
        return self._strategy.value

    def _ensure_initialized(self) -> None:
        if self._initialized:
            return
        with self._init_lock:
            if self._initialized:
                return
            try:
                import kss  # noqa: F401
                self._kss_available = True
                logger.info("KSS sentence splitter available")
            except ImportError:
                logger.info("kss not available, using regex sentence splitting")
            self._initialized = True

    _KSS_MAX_CHARS = weights.chunking.kss_max_chars
    _kss_executor = None  # Shared executor for KSS timeout

    def split_sentences(self, text: str) -> list[str]:
        """Split text into sentences. Uses KSS for Korean, regex fallback.

        Long texts are split into segments (by page markers or paragraphs)
        before KSS processing to prevent pecab hang on very large inputs.
        """
        if not text or not text.strip():
            return []

        self._ensure_initialized()

        if self._kss_available:
            try:
                import kss
                # Split long text into segments to prevent KSS/pecab hang
                if len(text) > self._KSS_MAX_CHARS:
                    return self._split_sentences_chunked(text, kss)
                sentences = kss.split_sentences(text)
                return [s.strip() for s in sentences if s.strip()]
            except Exception as e:  # noqa: BLE001
                logger.warning("KSS sentence split failed, using fallback: %s", e)

        return self._regex_split_sentences(text)

    def _kss_split_with_timeout(self, sub: str, kss) -> list[str]:
        """Split a sub-segment with KSS using a timeout, falling back to regex."""
        import concurrent.futures

        try:
            if self._kss_executor is None:
                self.__class__._kss_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
            future = self._kss_executor.submit(kss.split_sentences, sub)
            sentences = future.result(timeout=weights.timeouts.httpx_default)
            return [s.strip() for s in sentences if s.strip()]
        except Exception as e:  # noqa: BLE001
            if "timeout" in str(e).lower() or isinstance(e, concurrent.futures.TimeoutError):
                logger.warning("KSS timeout on %d chars, using regex fallback", len(sub))
            return self._regex_split_sentences(sub)

    def _split_sentences_chunked(self, text: str, kss) -> list[str]:
        """Split long text into segments, apply KSS to each segment."""
        # Split by page/slide markers or double newlines
        segments = re.split(r'\n\[(?:Page|Slide|Image)\s+\d+', text)
        if len(segments) <= 1:
            segments = text.split("\n\n")

        all_sentences: list[str] = []
        for seg in segments:
            seg = seg.strip()
            if not seg:
                continue
            # Further split if segment still too long
            if len(seg) > self._KSS_MAX_CHARS:
                sub_segs = [seg[i:i + self._KSS_MAX_CHARS]
                            for i in range(0, len(seg), self._KSS_MAX_CHARS)]
            else:
                sub_segs = [seg]

            for sub in sub_segs:
                all_sentences.extend(self._kss_split_with_timeout(sub, kss))

        return all_sentences

    def chunk(self, text: str) -> ChunkResult:
        """Chunk text using the configured strategy.

        Both strategies respect sentence boundaries. The semantic strategy
        additionally splits on paragraph boundaries (double newlines) before
        grouping into chunks.
        """
        if not text or not text.strip():
            return ChunkResult(chunks=[], total_chunks=0)

        if self._strategy == ChunkStrategy.FIXED:
            return self._fixed_chunk(text)
        return self._semantic_chunk(text)

    def _fixed_chunk(self, text: str) -> ChunkResult:
        """Sentence-boundary-aware fixed-size chunking."""
        sentences = self.split_sentences(text)
        if not sentences:
            return ChunkResult(chunks=[text.strip()], total_chunks=1)

        return self._group_sentences(sentences)

    def _flush_oversized_paragraph(self, para: str, chunks: list[str]) -> str:
        """Handle a paragraph that exceeds max_chunk_chars by sentence splitting."""
        if len(para) > self._max_chunk_chars:
            sentences = self.split_sentences(para)
            sub_result = self._group_sentences(sentences)
            chunks.extend(sub_result.chunks)
            return ""
        return para

    def _semantic_chunk(self, text: str) -> ChunkResult:
        """Paragraph-aware semantic chunking.

        Splits by paragraph boundaries first, then groups paragraphs
        into chunks respecting the max_chunk_chars limit.
        """
        paragraphs = text.split("\n\n")
        chunks: list[str] = []
        current_chunk = ""

        for para in paragraphs:
            para = para.strip()
            if not para:
                continue

            if len(current_chunk) + len(para) + 2 > self._max_chunk_chars:
                if current_chunk:
                    chunks.append(current_chunk)
                current_chunk = self._flush_oversized_paragraph(para, chunks)
            else:
                current_chunk = f"{current_chunk}\n\n{para}" if current_chunk else para

        if current_chunk:
            chunks.append(current_chunk)

        if not chunks:
            chunks = [text.strip()]

        avg_len = sum(len(c) for c in chunks) / len(chunks) if chunks else 0
        return ChunkResult(
            chunks=chunks,
            total_chunks=len(chunks),
            metadata={
                "strategy": self._strategy.value,
                "avg_chunk_chars": round(avg_len, 1),
                "max_chunk_chars": self._max_chunk_chars,
            },
        )

    def _group_sentences(self, sentences: list[str]) -> ChunkResult:
        """Group sentences into chunks respecting max_chunk_chars and overlap."""
        chunks: list[str] = []
        current_sentences: list[str] = []
        current_chars = 0

        for sentence in sentences:
            sentence_chars = len(sentence)
            if current_chars + sentence_chars > self._max_chunk_chars and current_sentences:
                chunks.append(" ".join(current_sentences))
                if self._overlap_sentences > 0:
                    overlap = current_sentences[-self._overlap_sentences:]
                    current_sentences = list(overlap)
                    current_chars = sum(len(s) for s in current_sentences)
                else:
                    current_sentences = []
                    current_chars = 0

            current_sentences.append(sentence)
            current_chars += sentence_chars

        if current_sentences:
            chunks.append(" ".join(current_sentences))

        avg_len = sum(len(c) for c in chunks) / len(chunks) if chunks else 0
        return ChunkResult(
            chunks=chunks,
            total_chunks=len(chunks),
            metadata={
                "strategy": self._strategy.value,
                "total_sentences": len(sentences),
                "avg_chunk_chars": round(avg_len, 1),
                "max_chunk_chars": self._max_chunk_chars,
                "overlap_sentences": self._overlap_sentences,
            },
        )

    def chunk_legal_articles(
        self,
        text: str,
        *,
        max_article_chars: int = 6000,
    ) -> ChunkResult:
        """Chunk Korean legal markdown while preserving 제N조 integrity.

        Unlike :meth:`chunk_with_headings`, a single legal article (a heading
        section) is emitted as one chunk as long as it fits within
        ``max_article_chars``. Only articles larger than this threshold are
        further split by the normal sentence/paragraph chunker. This prevents
        semantic fragmentation inside a 조, which hurts retrieval precision
        for queries like "119법 제8조에 의한 구조대 편성 요건".

        Each produced chunk carries the full heading path as its
        ``HeadingChunk.heading_path`` (e.g. "119구조ㆍ구급에 관한 법률 >
        제1장 총칙 > 제2조 (정의)"), which the ingestion pipeline converts
        into the contextual prefix prepended before embedding.
        """
        sections = extract_legal_sections(text)
        if not sections:
            return self.chunk(text)

        all_chunks: list[str] = []
        heading_chunks: list[HeadingChunk] = []

        for heading_path, section_text in sections:
            stripped = section_text.strip()
            if not stripped:
                continue

            # Short/medium articles: keep whole, never split mid-article.
            if len(stripped) <= max_article_chars:
                all_chunks.append(stripped)
                heading_chunks.append(
                    HeadingChunk(text=stripped, heading_path=heading_path)
                )
                continue

            # Oversized article — fall back to sentence/paragraph chunking.
            sub_result = self.chunk(section_text)
            for sub in sub_result.chunks:
                all_chunks.append(sub)
                heading_chunks.append(HeadingChunk(text=sub, heading_path=heading_path))

        if not all_chunks:
            return self.chunk(text)

        avg_len = sum(len(c) for c in all_chunks) / len(all_chunks)
        return ChunkResult(
            chunks=all_chunks,
            total_chunks=len(all_chunks),
            metadata={
                "strategy": "legal_articles",
                "avg_chunk_chars": round(avg_len, 1),
                "max_chunk_chars": self._max_chunk_chars,
                "max_article_chars": max_article_chars,
                "has_heading_paths": True,
            },
            heading_chunks=heading_chunks,
        )

    def chunk_with_headings(self, text: str) -> ChunkResult:
        """Chunk text while preserving heading hierarchy paths.

        Extracts heading structure (# / ## / ###) from the content,
        then chunks each section. Each chunk carries its heading_path
        (e.g. "설치 가이드 > 사전 요구사항").

        Falls back to normal chunking if no headings are found.
        """
        sections = extract_heading_sections(text)
        if not sections:
            return self.chunk(text)

        all_chunks: list[str] = []
        heading_chunks: list[HeadingChunk] = []

        for heading_path, section_text in sections:
            if not section_text.strip():
                continue
            section_result = self.chunk(section_text)
            for chunk_text in section_result.chunks:
                all_chunks.append(chunk_text)
                heading_chunks.append(HeadingChunk(text=chunk_text, heading_path=heading_path))

        if not all_chunks:
            return self.chunk(text)

        avg_len = sum(len(c) for c in all_chunks) / len(all_chunks) if all_chunks else 0
        return ChunkResult(
            chunks=all_chunks,
            total_chunks=len(all_chunks),
            metadata={
                "strategy": self._strategy.value,
                "avg_chunk_chars": round(avg_len, 1),
                "max_chunk_chars": self._max_chunk_chars,
                "has_heading_paths": True,
            },
            heading_chunks=heading_chunks,
        )

    @staticmethod
    def _regex_split_sentences(text: str) -> list[str]:
        """Regex-based sentence splitting for Korean and English."""
        parts = _SENTENCE_SPLIT_PATTERN.split(text)
        return [p.strip() for p in parts if p.strip()]


def extract_legal_sections(content: str) -> list[tuple[str, str]]:
    """Extract heading sections with correct sibling handling.

    Unlike :func:`extract_heading_sections`, which maintains a flat
    ``current_path`` list and therefore stacks siblings whenever levels
    jump (e.g. ``#`` → ``##`` → ``#####`` → another ``#####``), this
    function stores one heading slot per level index so that a new heading
    at level L correctly *replaces* everything at levels ≥ L. This matches
    the mental model needed for Korean legal markdown where every article
    is ``##### 제N조`` under a ``## 제N장`` chapter.
    """
    lines = content.split("\n")
    slots: dict[int, str] = {}
    sections: list[tuple[str, str]] = []
    current_text: list[str] = []
    current_max_level = 0

    def _flush() -> None:
        if not current_text:
            return
        path_parts = [slots[lvl] for lvl in sorted(slots) if lvl <= current_max_level]
        path_str = " > ".join(path_parts)
        sections.append((path_str, "\n".join(current_text)))

    for line in lines:
        heading_match = _HEADING_PATTERN.match(line)
        if heading_match:
            _flush()
            current_text = []

            level = len(heading_match.group(1))
            heading_text = heading_match.group(2).strip()

            # Drop all slots at or below this level (sibling replacement).
            for lvl in list(slots):
                if lvl >= level:
                    del slots[lvl]
            slots[level] = heading_text
            current_max_level = level
        else:
            current_text.append(line)

    _flush()
    return sections


def extract_heading_sections(content: str) -> list[tuple[str, str]]:
    """Extract heading hierarchy for each section.

    Parses markdown headings (# / ## / ###) and builds a hierarchy path
    for each section of text. Returns a list of (heading_path, section_text)
    tuples.

    Example:
        "# Guide\\n## Install\\nDo this..." ->
        [("Guide > Install", "Do this...")]
    """
    lines = content.split("\n")
    current_path: list[str] = []
    sections: list[tuple[str, str]] = []
    current_text: list[str] = []

    for line in lines:
        heading_match = _HEADING_PATTERN.match(line)
        if heading_match:
            # Flush current section
            if current_text:
                path_str = " > ".join(current_path) if current_path else ""
                sections.append((path_str, "\n".join(current_text)))
                current_text = []

            level = len(heading_match.group(1))  # 1 for #, 2 for ##, etc.
            heading_text = heading_match.group(2).strip()

            # Trim path to parent level, then append current heading
            current_path = current_path[:level - 1] + [heading_text]
        else:
            current_text.append(line)

    # Flush remaining text
    if current_text:
        path_str = " > ".join(current_path) if current_path else ""
        sections.append((path_str, "\n".join(current_text)))

    return sections
