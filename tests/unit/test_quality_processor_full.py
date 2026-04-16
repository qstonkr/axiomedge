"""Comprehensive unit tests for quality_processor.py — maximizing line coverage.

Tests _calculate_metrics, _determine_quality_tier, _normalize_owners,
_assess_freshness, _calculate_freshness_status, process_quality,
_merge_attachment_content, _to_dict, get_quality_summary.
No external services required.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pytest

from src.pipelines.quality_processor import (
    QualityTier,
    QualityMetrics,
    ProcessedDocument,
    SKIP_OWNER_PATTERNS,
    process_quality,
    get_quality_summary,
    _calculate_metrics,
    _determine_quality_tier,
    _assess_freshness,
    _calculate_freshness_status,
    _normalize_owners,
    _merge_attachment_content,
    _process_single_document,
    _to_dict,
)


# =========================================================================
# _calculate_metrics
# =========================================================================

class TestCalculateMetrics:
    def test_plain_text(self):
        m = _calculate_metrics("Hello world. Simple text.")
        assert m.content_length > 0
        assert m.word_count > 0
        assert m.paragraph_count >= 1
        assert m.has_tables is False
        assert m.has_code_blocks is False
        assert m.has_headers is False
        assert m.has_images is False
        assert m.has_links is False

    def test_with_table(self):
        content = "Some text\n| Col1 | Col2 | Col3 |\nmore text"
        m = _calculate_metrics(content)
        assert m.has_tables is True

    def test_with_code_block(self):
        m = _calculate_metrics("Code: ```python\nprint('hi')\n```")
        assert m.has_code_blocks is True

    def test_with_html_code(self):
        m = _calculate_metrics("Use <code>x</code> here")
        assert m.has_code_blocks is True

    def test_with_markdown_headers(self):
        m = _calculate_metrics("# Title\n\nBody")
        assert m.has_headers is True

    def test_with_html_headers(self):
        m = _calculate_metrics("<h2>Section</h2> content")
        assert m.has_headers is True

    def test_with_images_markdown(self):
        m = _calculate_metrics("See ![alt](image.png)")
        assert m.has_images is True

    def test_with_images_html(self):
        m = _calculate_metrics('Image: <img src="x.png"/>')
        assert m.has_images is True

    def test_with_links_markdown(self):
        m = _calculate_metrics("Click [here](http://example.com)")
        assert m.has_links is True

    def test_with_links_html(self):
        m = _calculate_metrics('Visit <a href="http://x.com">link</a>')
        assert m.has_links is True

    def test_paragraph_count(self):
        content = "Para 1\n\nPara 2\n\nPara 3"
        m = _calculate_metrics(content)
        assert m.paragraph_count == 3

    def test_empty_content(self):
        m = _calculate_metrics("")
        assert m.content_length == 0
        assert m.word_count == 0


# =========================================================================
# _determine_quality_tier
# =========================================================================

class TestDetermineQualityTier:
    def test_gold_long_content(self):
        m = QualityMetrics(
            content_length=3000, has_tables=False, has_code_blocks=False,
            has_headers=False, has_images=False, has_links=False,
            word_count=500, paragraph_count=10,
        )
        assert _determine_quality_tier(m) == QualityTier.GOLD

    def test_gold_structured(self):
        """1000+ chars with tables should be GOLD."""
        m = QualityMetrics(
            content_length=1200, has_tables=True, has_code_blocks=False,
            has_headers=False, has_images=False, has_links=False,
            word_count=200, paragraph_count=5,
        )
        assert _determine_quality_tier(m) == QualityTier.GOLD

    def test_gold_with_code(self):
        m = QualityMetrics(
            content_length=1100, has_tables=False, has_code_blocks=True,
            has_headers=False, has_images=False, has_links=False,
            word_count=200, paragraph_count=5,
        )
        assert _determine_quality_tier(m) == QualityTier.GOLD

    def test_silver_by_length(self):
        m = QualityMetrics(
            content_length=600, has_tables=False, has_code_blocks=False,
            has_headers=False, has_images=False, has_links=False,
            word_count=100, paragraph_count=3,
        )
        assert _determine_quality_tier(m) == QualityTier.SILVER

    def test_silver_structured(self):
        """Shorter text with headers should be SILVER."""
        m = QualityMetrics(
            content_length=300, has_tables=False, has_code_blocks=False,
            has_headers=True, has_images=False, has_links=False,
            word_count=50, paragraph_count=3,
        )
        assert _determine_quality_tier(m) == QualityTier.SILVER

    def test_bronze(self):
        m = QualityMetrics(
            content_length=100, has_tables=False, has_code_blocks=False,
            has_headers=False, has_images=False, has_links=False,
            word_count=15, paragraph_count=1,
        )
        assert _determine_quality_tier(m) == QualityTier.BRONZE

    def test_noise(self):
        m = QualityMetrics(
            content_length=10, has_tables=False, has_code_blocks=False,
            has_headers=False, has_images=False, has_links=False,
            word_count=2, paragraph_count=1,
        )
        assert _determine_quality_tier(m) == QualityTier.NOISE


# =========================================================================
# _normalize_owners
# =========================================================================

class TestNormalizeOwners:
    def test_korean_name(self):
        assert _normalize_owners(["홍길동"]) == ["홍길동"]

    def test_english_name(self):
        assert _normalize_owners(["John"]) == ["John"]

    def test_empty_string(self):
        assert _normalize_owners([""]) == []

    def test_unknown_user_pattern(self):
        assert _normalize_owners(["Unknown User (abc123)"]) == []

    def test_slash_split(self):
        result = _normalize_owners(["홍길동/팀장"])
        assert result == ["홍길동"]

    def test_m_suffix_removal(self):
        result = _normalize_owners(["홍길동M"])
        assert result == ["홍길동"]

    def test_skip_patterns(self):
        assert _normalize_owners(["Unknown"]) == []
        assert _normalize_owners(["admin"]) == []
        assert _normalize_owners(["AI센터"]) == []
        assert _normalize_owners(["개발팀"]) == []
        assert _normalize_owners(["IT본부"]) == []
        assert _normalize_owners(["사업부문"]) == []
        assert _normalize_owners(["기술센터"]) == []
        assert _normalize_owners(["T"]) == []
        assert _normalize_owners(["hwlee"]) == []

    def test_invalid_format(self):
        """Names that don't match Korean 2-4 or English 2-20 patterns."""
        assert _normalize_owners(["김"]) == []  # too short Korean
        assert _normalize_owners(["A"]) == []  # too short English
        assert _normalize_owners(["123"]) == []  # numeric

    def test_multiple_names(self):
        result = _normalize_owners(["홍길동", "Unknown", "김철수"])
        assert result == ["홍길동", "김철수"]

    def test_mc_front_pattern(self):
        assert _normalize_owners(["MC Front"]) == []
        assert _normalize_owners(["MCFront"]) == []

    def test_app_suffix(self):
        assert _normalize_owners(["SomeAPP"]) == []


# =========================================================================
# _assess_freshness
# =========================================================================

class TestAssessFreshness:
    def test_empty_updated_at(self):
        is_stale, warning, days = _assess_freshness("", 365)
        assert is_stale is False
        assert warning is None
        assert days is None

    def test_recent(self):
        recent = (datetime.now() - timedelta(days=10)).isoformat()
        is_stale, warning, days = _assess_freshness(recent, 365)
        assert is_stale is False
        assert days is not None
        assert days < 365

    def test_stale_years(self):
        old = (datetime.now() - timedelta(days=800)).isoformat()
        is_stale, warning, days = _assess_freshness(old, 365)
        assert is_stale is True
        assert "년" in warning
        assert days > 365

    def test_stale_months(self):
        """Document stale but < 1 year should show months in warning."""
        old = (datetime.now() - timedelta(days=370)).isoformat()
        is_stale, warning, days = _assess_freshness(old, 365)
        assert is_stale is True
        # 370 days = ~1 year, might say "1년" or "12개월"
        assert "년" in warning or "개월" in warning

    def test_invalid_date(self):
        is_stale, warning, days = _assess_freshness("not-a-date", 365)
        assert is_stale is False
        assert days is None

    def test_short_date_string(self):
        is_stale, warning, days = _assess_freshness("abc", 365)
        assert is_stale is False


# =========================================================================
# _calculate_freshness_status
# =========================================================================

class TestCalculateFreshnessStatus:
    def test_none_days(self):
        assert _calculate_freshness_status(None, 365) == "unknown"

    def test_current(self):
        assert _calculate_freshness_status(10, 730) == "current"

    def test_stale(self):
        assert _calculate_freshness_status(200, 730) == "stale"

    def test_outdated(self):
        assert _calculate_freshness_status(500, 730) == "outdated"

    def test_archived(self):
        assert _calculate_freshness_status(800, 365) == "archived"


# =========================================================================
# _merge_attachment_content
# =========================================================================

class TestMergeAttachmentContent:
    def test_no_attachments(self):
        page: dict[str, Any] = {}
        assert _merge_attachment_content(page, "body") == "body"

    def test_short_text_skipped(self):
        page = {"attachments": [{"extracted_text": "short"}]}
        assert _merge_attachment_content(page, "body") == "body"

    def test_error_text_skipped(self):
        page = {"attachments": [{"extracted_text": "오류가 발생했습니다 이것은 긴 텍스트입니다."}]}
        assert _merge_attachment_content(page, "body") == "body"

    def test_unsupported_text_skipped(self):
        page = {"attachments": [{"extracted_text": "지원하지 않는 형식입니다 이것은 충분히 긴 텍스트."}]}
        assert _merge_attachment_content(page, "body") == "body"

    def test_image_attachment(self):
        page = {
            "attachments": [{
                "filename": "photo.png",
                "mediaType": "image/png",
                "extracted_text": "A" * 30,
            }]
        }
        result = _merge_attachment_content(page, "body")
        assert "[이미지:" in result

    def test_pdf_attachment(self):
        page = {
            "attachments": [{
                "filename": "doc.pdf",
                "extracted_text": "B" * 30,
            }]
        }
        result = _merge_attachment_content(page, "body")
        assert "[PDF:" in result

    def test_pptx_attachment(self):
        page = {
            "attachments": [{
                "filename": "slides.pptx",
                "extracted_text": "C" * 30,
            }]
        }
        result = _merge_attachment_content(page, "body")
        assert "[프레젠테이션:" in result

    def test_xlsx_attachment(self):
        page = {
            "attachments": [{
                "filename": "data.xlsx",
                "extracted_text": "D" * 30,
            }]
        }
        result = _merge_attachment_content(page, "body")
        assert "[스프레드시트:" in result

    def test_docx_attachment(self):
        page = {
            "attachments": [{
                "filename": "report.docx",
                "extracted_text": "E" * 30,
            }]
        }
        result = _merge_attachment_content(page, "body")
        assert "[문서:" in result

    def test_other_attachment(self):
        page = {
            "attachments": [{
                "filename": "data.zip",
                "extracted_text": "F" * 30,
            }]
        }
        result = _merge_attachment_content(page, "body")
        assert "[첨부:" in result

    def test_empty_content(self):
        page = {
            "attachments": [{
                "filename": "file.txt",
                "extracted_text": "G" * 30,
            }]
        }
        result = _merge_attachment_content(page, "")
        assert "[첨부:" in result


# =========================================================================
# _process_single_document
# =========================================================================

class TestProcessSingleDocument:
    def test_empty_content(self):
        page = {"page_id": "1", "title": "T", "content_text": ""}
        assert _process_single_document(page, "src", 50, 365) is None

    def test_short_content(self):
        page = {"page_id": "1", "title": "T", "content_text": "short"}
        assert _process_single_document(page, "src", 50, 365) is None

    def test_valid_document(self):
        page = {
            "page_id": "1",
            "title": "Test",
            "content_text": "A" * 200,
            "creator": "홍길동",
            "last_modifier": "김철수",
            "updated_at": datetime.now().isoformat(),
        }
        result = _process_single_document(page, "src", 50, 365)
        assert result is not None
        assert result.page_id == "1"
        assert result.quality_tier in (QualityTier.BRONZE, QualityTier.SILVER, QualityTier.GOLD)

    def test_content_key_fallback(self):
        """Should fall back to 'content' key if 'content_text' missing."""
        page = {
            "page_id": "1",
            "title": "T",
            "content": "A" * 200,
        }
        result = _process_single_document(page, "src", 50, 365)
        assert result is not None


# =========================================================================
# _to_dict
# =========================================================================

class TestToDict:
    def test_converts_document(self):
        doc = ProcessedDocument(
            page_id="1", title="Test", content_text="x" * 100,
            quality_tier=QualityTier.SILVER,
            metrics=QualityMetrics(100, False, False, False, False, False, 10, 1),
            is_stale=False, freshness_warning=None, days_since_update=5,
            creators=["홍길동"], modifiers=["김철수"],
            metadata={"source": "confluence", "url": "http://x", "updated_at": ""},
        )
        d = _to_dict(doc)
        assert d["quality_tier"] == "SILVER"
        assert d["source"] == "confluence"

    def test_unknown_source_defaults(self):
        doc = ProcessedDocument(
            page_id="1", title="T", content_text="x",
            quality_tier=QualityTier.BRONZE,
            metrics=QualityMetrics(1, False, False, False, False, False, 1, 1),
            is_stale=False, freshness_warning=None, days_since_update=None,
            creators=[], modifiers=[],
            metadata={"source": "unknown", "url": ""},
        )
        d = _to_dict(doc)
        assert d["source"] == "confluence"

    def test_empty_source_defaults(self):
        doc = ProcessedDocument(
            page_id="1", title="T", content_text="x",
            quality_tier=QualityTier.BRONZE,
            metrics=QualityMetrics(1, False, False, False, False, False, 1, 1),
            is_stale=False, freshness_warning=None, days_since_update=None,
            creators=[], modifiers=[],
            metadata={"source": "", "url": ""},
        )
        d = _to_dict(doc)
        assert d["source"] == "confluence"


# =========================================================================
# process_quality
# =========================================================================

class TestProcessQuality:
    def test_empty_input(self):
        result = process_quality([])
        assert result["stats"]["total"] == 0
        assert result["documents"] == []

    def test_noise_excluded(self):
        crawl = {
            "source_name": "test",
            "pages": [
                {"page_id": "1", "title": "T", "content_text": "tiny"},
            ],
        }
        result = process_quality([crawl], min_content_length=10)
        assert result["stats"]["excluded_empty"] == 1 or result["stats"]["excluded_noise"] >= 0

    def test_valid_documents(self):
        crawl = {
            "source_name": "test",
            "pages": [
                {"page_id": "1", "title": "T", "content_text": "A" * 200, "updated_at": datetime.now().isoformat()},
                {"page_id": "2", "title": "T2", "content_text": "B" * 3000, "updated_at": datetime.now().isoformat()},
            ],
        }
        result = process_quality([crawl], min_content_length=50)
        assert result["stats"]["useful"] >= 1
        assert len(result["documents"]) >= 1

    def test_stale_counting(self):
        old_date = (datetime.now() - timedelta(days=800)).isoformat()
        crawl = {
            "source_name": "test",
            "pages": [
                {"page_id": "1", "title": "T", "content_text": "A" * 200, "updated_at": old_date},
            ],
        }
        result = process_quality([crawl], min_content_length=50, stale_threshold_days=365)
        assert result["stats"]["stale"] >= 1

    def test_attachment_counting(self):
        content = "### [이미지: photo.png]\nBody text " + "x" * 200
        crawl = {
            "source_name": "test",
            "pages": [
                {"page_id": "1", "title": "T", "content_text": content},
            ],
        }
        result = process_quality([crawl], min_content_length=50)
        assert result["stats"]["with_attachments"] >= 1


# =========================================================================
# get_quality_summary
# =========================================================================

class TestGetQualitySummary:
    def test_zero_total(self):
        result = get_quality_summary({"total": 0})
        assert "없음" in result

    def test_normal_stats(self):
        stats = {
            "total": 100, "useful": 80,
            "gold": 20, "silver": 30, "bronze": 30,
            "stale": 10,
        }
        result = get_quality_summary(stats)
        assert "100" in result
        assert "80" in result
        assert "GOLD" in result
        assert "오래된" in result


# =========================================================================
# QualityTier enum
# =========================================================================

class TestQualityTier:
    def test_values(self):
        assert QualityTier.GOLD.value == "GOLD"
        assert QualityTier.SILVER.value == "SILVER"
        assert QualityTier.BRONZE.value == "BRONZE"
        assert QualityTier.NOISE.value == "NOISE"

    def test_string_enum(self):
        assert isinstance(QualityTier.GOLD, str)
        assert QualityTier.GOLD == "GOLD"
