"""Unit tests for src/nlp/korean_processor.py

Tests KoreanProcessor: sentence splitting (regex fallback), morpheme analysis,
chunking, and preprocessing. KSS and Kiwi are mocked to avoid NLP dependencies.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.nlp.korean_processor import ChunkResult, KoreanProcessor, MorphemeResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def processor() -> KoreanProcessor:
    """Processor with KSS disabled (regex fallback)."""
    return KoreanProcessor(use_kss_sentence_splitter=False)


# ---------------------------------------------------------------------------
# split_sentences (regex fallback)
# ---------------------------------------------------------------------------

class TestSplitSentences:
    def test_empty_string(self, processor: KoreanProcessor) -> None:
        assert processor.split_sentences("") == []

    def test_whitespace_only(self, processor: KoreanProcessor) -> None:
        assert processor.split_sentences("   ") == []

    def test_single_sentence(self, processor: KoreanProcessor) -> None:
        result = processor.split_sentences("안녕하세요.")
        assert len(result) >= 1
        assert "안녕하세요." in result[0]

    def test_multiple_sentences_korean(self, processor: KoreanProcessor) -> None:
        text = "서비스가 중단되었습니다. 원인을 조사 중입니다."
        result = processor.split_sentences(text)
        assert len(result) == 2

    def test_english_sentence_split(self, processor: KoreanProcessor) -> None:
        text = "Hello world. This is a test! Are you sure?"
        result = processor.split_sentences(text)
        assert len(result) == 3

    def test_regex_split_korean_endings(self, processor: KoreanProcessor) -> None:
        """Korean sentence endings: 다. 요. 음."""
        text = "처리가 완료되었습니다. 감사합니다."
        result = processor.split_sentences(text)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# analyze_morphemes
# ---------------------------------------------------------------------------

class TestAnalyzeMorphemes:
    def test_no_kiwi_returns_empty(self) -> None:
        """Without Kiwi, morpheme analysis returns empty results."""
        proc = KoreanProcessor(use_kss_sentence_splitter=False)
        proc._initialized = True
        proc._kiwi = None  # Simulate missing Kiwi

        result = proc.analyze_morphemes("테스트 문장입니다")
        assert isinstance(result, MorphemeResult)
        assert result.morphemes == []
        assert result.nouns == []
        assert result.text == "테스트 문장입니다"

    def test_with_mocked_kiwi(self) -> None:
        """With mocked Kiwi, morphemes and nouns are extracted."""
        proc = KoreanProcessor(use_kss_sentence_splitter=False)

        mock_token_noun = MagicMock()
        mock_token_noun.form = "서버"
        mock_token_noun.tag = "NNG"

        mock_token_verb = MagicMock()
        mock_token_verb.form = "장애"
        mock_token_verb.tag = "NNG"

        mock_token_josa = MagicMock()
        mock_token_josa.form = "가"
        mock_token_josa.tag = "JKS"

        mock_kiwi = MagicMock()
        mock_kiwi.tokenize.return_value = [mock_token_noun, mock_token_verb, mock_token_josa]
        proc._kiwi = mock_kiwi
        proc._initialized = True

        result = proc.analyze_morphemes("서버 장애가 발생")
        assert len(result.morphemes) == 3
        assert result.nouns == ["서버", "장애"]

    def test_kiwi_exception_returns_empty(self) -> None:
        proc = KoreanProcessor(use_kss_sentence_splitter=False)
        mock_kiwi = MagicMock()
        mock_kiwi.tokenize.side_effect = RuntimeError("model error")
        proc._kiwi = mock_kiwi
        proc._initialized = True

        result = proc.analyze_morphemes("에러 테스트")
        assert result.morphemes == []
        assert result.nouns == []


# ---------------------------------------------------------------------------
# chunk_text
# ---------------------------------------------------------------------------

class TestChunkText:
    def test_empty_text(self, processor: KoreanProcessor) -> None:
        result = processor.chunk_text("")
        assert isinstance(result, ChunkResult)
        assert result.chunks == []
        assert result.total_chunks == 0

    def test_short_text_single_chunk(self, processor: KoreanProcessor) -> None:
        result = processor.chunk_text("짧은 문장입니다.")
        assert result.total_chunks == 1
        assert "짧은 문장" in result.chunks[0]

    def test_long_text_splits_into_multiple_chunks(self) -> None:
        """Text exceeding max_chunk_chars should be split."""
        proc = KoreanProcessor(
            max_chunk_tokens=10,
            avg_chars_per_token=1.0,
            chunk_overlap_sentences=0,
            use_kss_sentence_splitter=False,
        )
        # Each sentence ~15 chars, max_chunk_chars = 10
        text = "첫 번째 문장입니다. 두 번째 문장입니다. 세 번째 문장입니다."
        result = proc.chunk_text(text)
        assert result.total_chunks >= 2

    def test_metadata_populated(self, processor: KoreanProcessor) -> None:
        result = processor.chunk_text("문장 하나. 문장 둘.")
        assert "total_sentences" in result.metadata
        assert "avg_chunk_chars" in result.metadata


# ---------------------------------------------------------------------------
# preprocess
# ---------------------------------------------------------------------------

class TestPreprocess:
    def test_empty_string(self, processor: KoreanProcessor) -> None:
        assert processor.preprocess("") == ""

    def test_html_tags_removed(self, processor: KoreanProcessor) -> None:
        assert processor.preprocess("<b>볼드</b> 텍스트") == "볼드 텍스트"

    def test_consecutive_whitespace_collapsed(self, processor: KoreanProcessor) -> None:
        assert processor.preprocess("a   b\t\nc") == "a b c"

    def test_strips_leading_trailing(self, processor: KoreanProcessor) -> None:
        assert processor.preprocess("  hello  ") == "hello"
