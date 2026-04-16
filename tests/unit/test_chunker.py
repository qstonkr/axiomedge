"""Unit tests for src/pipeline/chunker.py."""

from src.pipelines.chunker import ChunkStrategy, Chunker, ChunkResult, extract_heading_sections


class TestChunkStrategy:
    """Test ChunkStrategy enum."""

    def test_enum_values(self) -> None:
        assert ChunkStrategy.FIXED.value == "fixed"
        assert ChunkStrategy.SEMANTIC.value == "semantic"


class TestChunkerEmpty:
    """Test Chunker with empty / trivial inputs."""

    def setup_method(self) -> None:
        self.chunker = Chunker(max_chunk_chars=500, overlap_sentences=1, strategy=ChunkStrategy.FIXED)

    def test_chunk_empty_text(self) -> None:
        result = self.chunker.chunk("")
        assert isinstance(result, ChunkResult)
        assert result.chunks == []
        assert result.total_chunks == 0

    def test_chunk_whitespace_only(self) -> None:
        result = self.chunker.chunk("   \n\n  ")
        assert result.chunks == []
        assert result.total_chunks == 0

    def test_chunk_none_like(self) -> None:
        result = self.chunker.chunk("")
        assert result.total_chunks == 0


class TestChunkerFixed:
    """Test fixed-size chunking strategy."""

    def setup_method(self) -> None:
        self.chunker = Chunker(
            max_chunk_chars=100,
            overlap_sentences=0,
            strategy=ChunkStrategy.FIXED,
        )

    def test_short_text_single_chunk(self) -> None:
        text = "This is a short sentence."
        result = self.chunker.chunk(text)
        assert result.total_chunks == 1
        assert result.chunks[0].strip() == text

    def test_long_text_multiple_chunks(self) -> None:
        # Each sentence ~30 chars, 100 char limit -> multiple chunks
        sentences = ["This is sentence number one.", "Here comes sentence two.", "And a third sentence.", "Plus a fourth sentence here.", "Finally the fifth one."]
        text = " ".join(sentences)
        result = self.chunker.chunk(text)
        assert result.total_chunks >= 2
        # All original text should be present across chunks
        combined = " ".join(result.chunks)
        for s in sentences:
            assert s in combined

    def test_metadata_present(self) -> None:
        text = "Some text content for testing the metadata output."
        result = self.chunker.chunk(text)
        assert "strategy" in result.metadata
        assert result.metadata["strategy"] == "fixed"


class TestChunkerOverlap:
    """Test overlap behavior between chunks."""

    def test_overlap_sentences_carry_over(self) -> None:
        chunker = Chunker(max_chunk_chars=60, overlap_sentences=1, strategy=ChunkStrategy.FIXED)
        # Provide clearly separable sentences
        text = "First sentence here. Second sentence here. Third sentence here. Fourth sentence here."
        result = chunker.chunk(text)
        if result.total_chunks >= 2:
            # With overlap=1, the last sentence of chunk N should appear
            # at the start of chunk N+1
            first_chunks_words = result.chunks[0].split()
            second_chunks_words = result.chunks[1].split()
            # At least some overlap should exist
            assert len(set(first_chunks_words) & set(second_chunks_words)) > 0

    def test_no_overlap(self) -> None:
        chunker = Chunker(max_chunk_chars=60, overlap_sentences=0, strategy=ChunkStrategy.FIXED)
        text = "Alpha sentence. Beta sentence. Gamma sentence. Delta sentence."
        result = chunker.chunk(text)
        assert result.total_chunks >= 1


class TestChunkerSemantic:
    """Test semantic (paragraph-aware) chunking."""

    def setup_method(self) -> None:
        self.chunker = Chunker(
            max_chunk_chars=100,
            overlap_sentences=0,
            strategy=ChunkStrategy.SEMANTIC,
        )

    def test_paragraph_splitting(self) -> None:
        text = "First paragraph content here.\n\nSecond paragraph content here.\n\nThird paragraph content."
        result = self.chunker.chunk(text)
        assert result.metadata["strategy"] == "semantic"
        assert result.total_chunks >= 1

    def test_single_paragraph_under_limit(self) -> None:
        text = "Short paragraph."
        result = self.chunker.chunk(text)
        assert result.total_chunks == 1
        assert result.chunks[0] == "Short paragraph."

    def test_large_single_paragraph_gets_split(self) -> None:
        # A single paragraph exceeding max_chunk_chars should be sentence-split
        text = "A" * 50 + ". " + "B" * 50 + ". " + "C" * 50 + "."
        result = self.chunker.chunk(text)
        assert result.total_chunks >= 2


class TestSplitSentences:
    """Test sentence splitting (regex fallback)."""

    def setup_method(self) -> None:
        self.chunker = Chunker()

    def test_empty_returns_empty(self) -> None:
        assert self.chunker.split_sentences("") == []
        assert self.chunker.split_sentences("   ") == []

    def test_single_sentence(self) -> None:
        result = self.chunker.split_sentences("Hello world.")
        assert len(result) >= 1

    def test_korean_sentence_endings(self) -> None:
        text = "첫 번째 문장입니다. 두 번째 문장이에요."
        result = self.chunker.split_sentences(text)
        assert len(result) >= 1


class TestExtractHeadingSections:
    """Test heading extraction utility."""

    def test_simple_headings(self) -> None:
        content = "# Guide\n## Install\nDo this step.\n## Configure\nSet that option."
        sections = extract_heading_sections(content)
        assert len(sections) >= 2
        paths = [s[0] for s in sections]
        assert any("Install" in p for p in paths)
        assert any("Configure" in p for p in paths)

    def test_no_headings(self) -> None:
        content = "Just plain text without any headings."
        sections = extract_heading_sections(content)
        # Should return at least the text itself
        assert len(sections) >= 1
        assert sections[0][0] == ""  # no heading path

    def test_nested_headings_build_path(self) -> None:
        content = "# Top\n## Mid\n### Low\nContent here."
        sections = extract_heading_sections(content)
        # The last section should have a path like "Top > Mid > Low"
        paths = [s[0] for s in sections]
        assert any("Top > Mid > Low" in p for p in paths)
