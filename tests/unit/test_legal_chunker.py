"""Tests for Chunker.chunk_legal_articles — legal article boundary preservation."""

from __future__ import annotations

from src.pipeline.chunker import Chunker, ChunkStrategy


_SAMPLE_LAW = """# 119구조ㆍ구급에 관한 법률

## 제1장 총칙

##### 제1조 (목적)

이 법은 화재, 재난ㆍ재해 및 테러, 그 밖의 위급한 상황에서 119구조ㆍ구급의 효율적 운영에 관하여 필요한 사항을 규정함으로써 국가의 구조ㆍ구급 업무 역량을 강화하고 국민의 생명ㆍ신체 및 재산을 보호하며 삶의 질 향상에 이바지함을 목적으로 한다.

##### 제2조 (정의)

이 법에서 사용하는 용어의 뜻은 다음과 같다. <개정 2016.1.27, 2020.10.20>

  1. "구조"란 화재, 재난ㆍ재해 및 테러, 그 밖의 위급한 상황에서 외부의 도움을 필요로 하는 사람의 생명, 신체 및 재산을 보호하기 위하여 수행하는 모든 활동을 말한다.
  2. "119구조대"란 탐색 및 구조활동에 필요한 장비를 갖추고 소방공무원으로 편성된 단위조직을 말한다.

## 제2장 구조ㆍ구급 기본계획 등

##### 제6조 (구조ㆍ구급 기본계획 등의 수립ㆍ시행)

소방청장은 제3조의 업무를 수행하기 위하여 관계 중앙행정기관의 장과 협의하여 대통령령으로 정하는 바에 따라 구조ㆍ구급 기본계획을 수립ㆍ시행하여야 한다.
"""


class TestChunkLegalArticles:
    def test_each_article_becomes_one_chunk(self):
        chunker = Chunker(max_chunk_chars=200, strategy=ChunkStrategy.SEMANTIC)
        # max_chunk_chars is tiny, but chunk_legal_articles should NOT split
        # sub-6000-char articles — that's the whole point.
        result = chunker.chunk_legal_articles(_SAMPLE_LAW)

        assert result.total_chunks == 3  # 제1조, 제2조, 제6조
        assert len(result.heading_chunks) == 3
        assert result.metadata["strategy"] == "legal_articles"

    def test_heading_paths_include_chapter_and_article(self):
        chunker = Chunker(max_chunk_chars=200)
        result = chunker.chunk_legal_articles(_SAMPLE_LAW)

        paths = [hc.heading_path for hc in result.heading_chunks]
        # Level 1 > Level 2 > Level 5 — intermediate levels are trimmed.
        assert paths[0] == "119구조ㆍ구급에 관한 법률 > 제1장 총칙 > 제1조 (목적)"
        assert paths[1] == "119구조ㆍ구급에 관한 법률 > 제1장 총칙 > 제2조 (정의)"
        assert paths[2].endswith("> 제6조 (구조ㆍ구급 기본계획 등의 수립ㆍ시행)")

    def test_article_content_preserved(self):
        chunker = Chunker(max_chunk_chars=200)
        result = chunker.chunk_legal_articles(_SAMPLE_LAW)

        article_1 = result.heading_chunks[0].text
        assert "이 법은 화재" in article_1
        assert "제1장" not in article_1  # 장 제목은 조에 포함되지 않음

        article_2 = result.heading_chunks[1].text
        assert "\"구조\"란" in article_2
        assert "<개정 2016" in article_2  # 개정 마커는 보존

    def test_oversized_article_falls_back_to_sentence_chunking(self):
        # Build a very long article exceeding max_article_chars
        long_body = "이 조는 매우 긴 조문입니다. " * 500  # ~7000+ chars
        text = f"""# 테스트법

## 제1장 총칙

##### 제1조 (긴 조항)

{long_body}
"""
        chunker = Chunker(max_chunk_chars=1000)
        result = chunker.chunk_legal_articles(text, max_article_chars=4000)
        assert result.total_chunks >= 2
        # Every chunk keeps the same heading path
        paths = {hc.heading_path for hc in result.heading_chunks}
        assert len(paths) == 1
        assert "제1조 (긴 조항)" in next(iter(paths))

    def test_no_headings_falls_back(self):
        plain = "제목 없는 문서입니다. 여러 문장이 있습니다."
        chunker = Chunker(max_chunk_chars=500)
        result = chunker.chunk_legal_articles(plain)
        assert result.total_chunks >= 1
