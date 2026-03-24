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


class ChunkStrategy(str, Enum):
    """Chunking strategy."""

    FIXED = "fixed"
    SEMANTIC = "semantic"


@dataclass
class ChunkResult:
    """Chunking result."""

    chunks: list[str]
    total_chunks: int
    metadata: dict[str, Any] = field(default_factory=dict)


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

    def split_sentences(self, text: str) -> list[str]:
        """Split text into sentences. Uses KSS for Korean, regex fallback."""
        if not text or not text.strip():
            return []

        self._ensure_initialized()

        if self._kss_available:
            try:
                import kss
                sentences = kss.split_sentences(text)
                return [s.strip() for s in sentences if s.strip()]
            except Exception as e:
                logger.warning("KSS sentence split failed, using fallback: %s", e)

        return self._regex_split_sentences(text)

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
                # If a single paragraph exceeds max, further split by sentences
                if len(para) > self._max_chunk_chars:
                    sentences = self.split_sentences(para)
                    sub_result = self._group_sentences(sentences)
                    chunks.extend(sub_result.chunks)
                    current_chunk = ""
                else:
                    current_chunk = para
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

    @staticmethod
    def _regex_split_sentences(text: str) -> list[str]:
        """Regex-based sentence splitting for Korean and English."""
        pattern = r'(?<=[.!?])\s+|(?<=다\.)\s+|(?<=요\.)\s+|(?<=음\.)\s+'
        parts = re.split(pattern, text)
        return [p.strip() for p in parts if p.strip()]
