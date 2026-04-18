# pyright: reportAttributeAccessIssue=false, reportGeneralTypeIssues=false
"""Korean NLP Processor

한글 처리 파이프라인:
- Kiwi 형태소 분석 (kiwipiepy)
- KSS 문장 분리 (kss)
- 한글 인식 청킹 (문장 경계 존중)
"""

from __future__ import annotations

import logging
import os
import re
import threading
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ChunkResult:
    """청킹 결과

    Attributes:
        chunks: 분할된 텍스트 청크 목록
        total_chunks: 총 청크 수
        metadata: 청킹 메타데이터 (평균 길이, 총 문장 수 등)
    """

    chunks: list[str]
    total_chunks: int
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class MorphemeResult:
    """형태소 분석 결과

    Attributes:
        morphemes: (형태소, 품사) 쌍 리스트
        nouns: 추출된 명사 리스트
        text: 원본 텍스트
    """

    morphemes: list[tuple[str, str]]
    nouns: list[str]
    text: str


class KoreanProcessor:
    """한글 NLP 프로세서

    - Kiwi 형태소 분석: 한국어 형태소 분석 (kiwipiepy 0.22.2+)
    - KSS 문장 분리: 한국어 문장 분리 (kss 4.0+)
    - 한글 인식 청킹: 문장 경계를 존중하는 청킹

    비용: $0 (로컬 실행, GPU 불필요)
    """

    def __init__(
        self,
        max_chunk_tokens: int = 1000,
        chunk_overlap_sentences: int = 1,
        avg_chars_per_token: float = 2.5,
        use_kss_sentence_splitter: bool | None = None,
    ) -> None:
        """Initialize KoreanProcessor

        Args:
            max_chunk_tokens: 청크당 최대 토큰 수
            chunk_overlap_sentences: 청크 간 겹치는 문장 수
            avg_chars_per_token: 한글 토큰당 평균 글자 수 (한글 ≈ 2.5)
        """
        self._max_chunk_tokens = max_chunk_tokens
        self._chunk_overlap_sentences = chunk_overlap_sentences
        self._avg_chars_per_token = avg_chars_per_token
        self._max_chunk_chars = int(max_chunk_tokens * avg_chars_per_token)
        self._kiwi = None
        self._use_kss_sentence_splitter = (
            use_kss_sentence_splitter
            if use_kss_sentence_splitter is not None
            else self._env_bool("KNOWLEDGE_USE_KSS_SENTENCE_SPLITTER", True)
        )
        self._kss_available = False
        self._initialized = False
        self._init_lock = threading.Lock()

    @staticmethod
    def _env_bool(name: str, default: bool) -> bool:
        value = os.getenv(name, str(default)).strip().lower()
        return value in {"1", "true", "yes", "on"}

    def _ensure_initialized(self) -> None:
        """Lazy initialization of NLP models (thread-safe)"""
        if self._initialized:
            return

        with self._init_lock:
            if self._initialized:
                return

            try:
                from kiwipiepy import Kiwi
                self._kiwi = Kiwi()
                logger.info("Kiwi morphological analyzer initialized")
            except ImportError:
                logger.warning("kiwipiepy not available, morpheme analysis disabled")

            if not self._use_kss_sentence_splitter:
                logger.info("KSS sentence splitter disabled by config")
                self._kss_available = False
            else:
                try:
                    import kss  # noqa: F401
                    self._kss_available = True
                    logger.info("KSS sentence splitter available")
                except ImportError:
                    logger.warning("kss not available, falling back to regex sentence splitting")

            self._initialized = True

    def split_sentences(self, text: str) -> list[str]:
        """문장 분리 (KSS 우선, fallback to regex)

        Args:
            text: 입력 텍스트

        Returns:
            문장 리스트
        """
        if not text or not text.strip():
            return []

        self._ensure_initialized()

        if self._use_kss_sentence_splitter and self._kss_available:
            try:
                import kss
                sentences = kss.split_sentences(text)
                return [s.strip() for s in sentences if s.strip()]
            except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
                logger.warning("KSS sentence split failed, using fallback: %s", e)

        return self._regex_split_sentences(text)

    def analyze_morphemes(self, text: str) -> MorphemeResult:
        """형태소 분석 (Kiwi)

        Args:
            text: 입력 텍스트

        Returns:
            형태소 분석 결과
        """
        self._ensure_initialized()

        if self._kiwi is None:
            return MorphemeResult(morphemes=[], nouns=[], text=text)

        try:
            result = self._kiwi.tokenize(text)
            morphemes = [(token.form, token.tag) for token in result]
            nouns = [
                token.form for token in result
                if token.tag.startswith("NN")  # NNG, NNP, NNB, etc.
            ]
            return MorphemeResult(morphemes=morphemes, nouns=nouns, text=text)
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.warning("Kiwi morpheme analysis failed: %s", e)
            return MorphemeResult(morphemes=[], nouns=[], text=text)

    def chunk_text(self, text: str) -> ChunkResult:
        """한글 인식 청킹 (문장 경계 존중)

        문장 경계를 존중하며, max_tokens를 초과하지 않도록 청킹.
        각 청크는 완전한 문장으로 구성됩니다.

        Args:
            text: 입력 텍스트

        Returns:
            청킹 결과
        """
        if not text or not text.strip():
            return ChunkResult(chunks=[], total_chunks=0)

        sentences = self.split_sentences(text)
        if not sentences:
            return ChunkResult(chunks=[text.strip()], total_chunks=1)

        chunks: list[str] = []
        current_sentences: list[str] = []
        current_chars = 0

        for sentence in sentences:
            sentence_chars = len(sentence)

            if current_chars + sentence_chars > self._max_chunk_chars and current_sentences:
                chunk_text = " ".join(current_sentences)
                chunks.append(chunk_text)

                # Overlap: keep last N sentences for context continuity
                if self._chunk_overlap_sentences > 0:
                    overlap = current_sentences[-self._chunk_overlap_sentences:]
                    current_sentences = list(overlap)
                    current_chars = sum(len(s) for s in current_sentences)
                else:
                    current_sentences = []
                    current_chars = 0

            current_sentences.append(sentence)
            current_chars += sentence_chars

        # Flush remaining sentences
        if current_sentences:
            chunk_text = " ".join(current_sentences)
            chunks.append(chunk_text)

        total_sentences = len(sentences)
        avg_chunk_len = sum(len(c) for c in chunks) / len(chunks) if chunks else 0

        return ChunkResult(
            chunks=chunks,
            total_chunks=len(chunks),
            metadata={
                "total_sentences": total_sentences,
                "avg_chunk_chars": round(avg_chunk_len, 1),
                "max_chunk_tokens": self._max_chunk_tokens,
                "overlap_sentences": self._chunk_overlap_sentences,
            },
        )

    def preprocess(self, text: str) -> str:
        """한글 텍스트 전처리

        - 불필요한 공백 정리
        - HTML 태그 제거
        - 특수문자 정규화

        Args:
            text: 원본 텍스트

        Returns:
            전처리된 텍스트
        """
        if not text:
            return ""

        # HTML 태그 제거
        text = re.sub(r"<[^>]+>", "", text)

        # 연속 공백 정리
        text = re.sub(r"\s+", " ", text)

        # 앞뒤 공백 제거
        text = text.strip()

        return text

    @staticmethod
    def _regex_split_sentences(text: str) -> list[str]:
        """Regex 기반 문장 분리 (KSS fallback)

        한국어와 영어 문장 경계를 모두 처리합니다.
        """
        # Korean/English sentence-ending patterns
        pattern = r'(?<=[.!?。])\s+|(?<=다\.)\s+|(?<=요\.)\s+|(?<=음\.)\s+'
        parts = re.split(pattern, text)
        return [p.strip() for p in parts if p.strip()]
