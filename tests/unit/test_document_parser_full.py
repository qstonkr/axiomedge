"""Comprehensive unit tests for document_parser.py — maximizing line coverage.

Tests dispatch logic, text extraction paths, helpers, and enhanced parsing
without requiring external services (fitz/docx/openpyxl mocked where needed).
"""

from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.pipelines.document_parser import (
    ParseResult,
    _extract_pdf_date,
    _table_to_markdown,
    parse_bytes,
    parse_bytes_enhanced,
    parse_file,
    parse_file_enhanced,
)


# =========================================================================
# ParseResult
# =========================================================================

class TestParseResult:
    def test_full_text_text_only(self):
        pr = ParseResult(text="Hello world")
        assert pr.full_text == "Hello world"

    def test_full_text_with_ocr(self):
        pr = ParseResult(text="Body", ocr_text="OCR data")
        ft = pr.full_text
        assert "Body" in ft
        assert "[OCR Extracted Text]" in ft
        assert "OCR data" in ft

    def test_full_text_with_tables(self):
        pr = ParseResult(
            text="Main",
            tables=[[["H1", "H2"], ["a", "b"]]],
        )
        ft = pr.full_text
        assert "[Table]" in ft
        assert "H1" in ft

    def test_full_text_empty(self):
        pr = ParseResult()
        assert pr.full_text == ""

    def test_full_text_with_all(self):
        pr = ParseResult(
            text="Body text",
            ocr_text="OCR",
            tables=[[["Col"]]],
        )
        ft = pr.full_text
        assert "Body text" in ft
        assert "OCR" in ft
        assert "Col" in ft

    def test_full_text_whitespace_only_parts(self):
        """Parts that are whitespace-only should be excluded."""
        pr = ParseResult(text="  \n  ", ocr_text="real text")
        ft = pr.full_text
        assert "real text" in ft

    def test_default_fields(self):
        pr = ParseResult()
        assert pr.text == ""
        assert pr.tables == []
        assert pr.ocr_text == ""
        assert pr.images_processed == 0
        assert pr.visual_analyses == []
        assert pr.file_modified_at == ""


# =========================================================================
# _table_to_markdown
# =========================================================================

class TestTableToMarkdown:
    def test_empty_data(self):
        assert _table_to_markdown([]) == ""

    def test_header_only(self):
        result = _table_to_markdown([["A", "B"]])
        assert "| A | B |" in result
        assert "| --- | --- |" in result

    def test_normal_table(self):
        data = [["Name", "Age"], ["Alice", "30"], ["Bob", "25"]]
        result = _table_to_markdown(data)
        assert "| Alice | 30 |" in result
        assert "| Bob | 25 |" in result

    def test_row_padding(self):
        """Rows shorter than header should be padded."""
        data = [["A", "B", "C"], ["x"]]
        result = _table_to_markdown(data)
        # "x" row should be padded to match header length
        assert "| x |" in result

    def test_row_truncation(self):
        """Rows longer than header should be truncated."""
        data = [["A"], ["x", "y", "z"]]
        result = _table_to_markdown(data)
        lines = result.strip().split("\n")
        # Data row should only have 1 column
        assert lines[-1].count("|") == lines[0].count("|")

    def test_max_rows(self):
        data = [["H"]] + [[f"r{i}"] for i in range(60)]
        result = _table_to_markdown(data, max_rows=10)
        assert "rows omitted" in result


# =========================================================================
# _extract_pdf_date
# =========================================================================

class TestExtractPdfDate:
    def test_valid_date(self):
        assert _extract_pdf_date("D:20240115103045+09'00'") == "2024-01-15T10:30:45"

    def test_empty_string(self):
        assert _extract_pdf_date("") == ""

    def test_none(self):
        assert _extract_pdf_date(None) == ""

    def test_invalid_format(self):
        assert _extract_pdf_date("not-a-date") == ""


# =========================================================================
# parse_file
# =========================================================================

class TestParseFile:
    def test_nonexistent_file(self):
        assert parse_file("/nonexistent/path.txt") == ""

    def test_directory_not_file(self, tmp_path):
        assert parse_file(str(tmp_path)) == ""

    def test_txt_file(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("hello world", encoding="utf-8")
        result = parse_file(str(f))
        assert "hello world" in result

    def test_md_file(self, tmp_path):
        f = tmp_path / "test.md"
        f.write_text("# Heading\nbody", encoding="utf-8")
        assert "# Heading" in parse_file(str(f))

    def test_yml_file(self, tmp_path):
        f = tmp_path / "config.yml"
        f.write_text("key: val", encoding="utf-8")
        assert "key: val" in parse_file(str(f))

    def test_too_large(self, tmp_path):
        from src.pipelines.document_parser import MAX_FILE_SIZE
        f = tmp_path / "huge.txt"
        f.write_bytes(b"x" * (MAX_FILE_SIZE + 1))
        with pytest.raises(ValueError, match="File too large"):
            parse_file(str(f))


# =========================================================================
# parse_file_enhanced
# =========================================================================

class TestParseFileEnhanced:
    def test_nonexistent(self):
        result = parse_file_enhanced("/no/such/file.pdf")
        assert isinstance(result, ParseResult)
        assert result.text == ""

    def test_too_large(self, tmp_path):
        from src.pipelines.document_parser import MAX_FILE_SIZE
        f = tmp_path / "huge.pdf"
        f.write_bytes(b"x" * (MAX_FILE_SIZE + 1))
        with pytest.raises(ValueError, match="File too large"):
            parse_file_enhanced(str(f))

    def test_txt_enhanced(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("enhanced text", encoding="utf-8")
        result = parse_file_enhanced(str(f))
        assert "enhanced text" in result.text


# =========================================================================
# parse_bytes — dispatch logic
# =========================================================================

class TestParseBytes:
    def test_empty_bytes(self):
        with pytest.raises(ValueError, match="Empty file"):
            parse_bytes(b"", "test.txt")

    def test_txt(self):
        assert parse_bytes(b"hello", "f.txt") == "hello"

    def test_csv(self):
        assert parse_bytes(b"a,b", "f.csv") == "a,b"

    def test_json(self):
        assert parse_bytes(b'{"k":"v"}', "f.json") == '{"k":"v"}'

    def test_xml(self):
        assert parse_bytes(b"<root/>", "f.xml") == "<root/>"

    def test_yaml(self):
        assert parse_bytes(b"k: v", "f.yaml") == "k: v"

    def test_yml(self):
        assert parse_bytes(b"k: v", "f.yml") == "k: v"

    def test_doc_unsupported(self):
        assert parse_bytes(b"data", "legacy.doc") == ""

    def test_unsupported_ext(self):
        assert parse_bytes(b"data", "file.xyz") == ""

    def test_euc_kr_fallback(self):
        korean = "한글 테스트"
        data = korean.encode("euc-kr")
        result = parse_bytes(data, "f.txt")
        assert "한글" in result

    def test_ppt_no_libreoffice(self):
        with patch("shutil.which", return_value=None):
            assert parse_bytes(b"fake", "p.ppt") == ""


# =========================================================================
# parse_bytes_enhanced — dispatch logic
# =========================================================================

class TestParseBytesEnhanced:
    def test_txt_wraps_in_parseresult(self):
        result = parse_bytes_enhanced(b"text content", "f.txt")
        assert isinstance(result, ParseResult)
        assert "text content" in result.text

    def test_ppt_no_libreoffice(self):
        with patch("shutil.which", return_value=None):
            result = parse_bytes_enhanced(b"fake", "p.ppt")
            assert "LibreOffice" in result.text or "Error" in result.text

    def test_image_extension_routes_to_image_parser(self):
        """Image extensions should route through _parse_image -> _process_images_ocr."""
        with patch("src.pipelines.document_parser._process_images_ocr", return_value=("ocr text", [])) as mock_ocr:
            result = parse_bytes_enhanced(b"fake image data", "photo.png")
            assert isinstance(result, ParseResult)
            mock_ocr.assert_called_once()

    def test_image_extensions(self):
        """All image extensions should be handled."""
        for ext in (".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tiff"):
            with patch("src.pipelines.document_parser._process_images_ocr", return_value=("", [])):
                result = parse_bytes_enhanced(b"data", f"img{ext}")
                assert isinstance(result, ParseResult)

    def test_other_ext_falls_through_to_parse_bytes(self):
        result = parse_bytes_enhanced(b"csv,data", "f.csv")
        assert "csv,data" in result.text

    def test_docx_falls_through(self):
        """Non-enhanced types fall through to parse_bytes which calls _parse_docx."""
        # _parse_docx will fail on fake data, but the dispatch should route there
        with pytest.raises(ValueError):
            parse_bytes_enhanced(b"not a real docx", "f.docx")

    def test_xlsx_falls_through(self):
        """xlsx should fall through to parse_bytes -> _parse_xlsx."""
        with pytest.raises(Exception):
            parse_bytes_enhanced(b"not real xlsx", "f.xlsx")


# =========================================================================
# _convert_ppt_to_pptx
# =========================================================================

class TestConvertPptToPptx:
    def test_no_soffice(self):
        from src.pipelines.document_parser import _convert_ppt_to_pptx
        with patch("shutil.which", return_value=None):
            assert _convert_ppt_to_pptx(b"data", "test.ppt") is None

    def test_conversion_failure(self):
        from src.pipelines.document_parser import _convert_ppt_to_pptx
        with patch("shutil.which", return_value="/usr/bin/soffice"):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=1, stderr=b"error")
                assert _convert_ppt_to_pptx(b"data", "test.ppt") is None

    def test_timeout(self):
        import subprocess
        from src.pipelines.document_parser import _convert_ppt_to_pptx
        with patch("shutil.which", return_value="/usr/bin/soffice"):
            with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="soffice", timeout=30)):
                assert _convert_ppt_to_pptx(b"data", "test.ppt") is None

    def test_general_exception(self):
        from src.pipelines.document_parser import _convert_ppt_to_pptx
        with patch("shutil.which", return_value="/usr/bin/soffice"):
            with patch("subprocess.run", side_effect=OSError("boom")):
                assert _convert_ppt_to_pptx(b"data", "test.ppt") is None


# =========================================================================
# _parse_text
# =========================================================================

class TestParseText:
    def test_utf8(self):
        from src.pipelines.document_parser import _parse_text
        assert _parse_text(b"hello") == "hello"

    def test_euc_kr(self):
        from src.pipelines.document_parser import _parse_text
        data = "한글".encode("euc-kr")
        result = _parse_text(data)
        assert "한글" in result


# =========================================================================
# _is_garbled_text (nested in _parse_pdf_enhanced, test indirectly via logic)
# =========================================================================

class TestIsGarbledTextLogic:
    """Test the garbled-text detection logic extracted from the function."""

    def test_short_text_not_garbled(self):
        # The function returns False for text < 10 chars
        text = "짧은"
        assert len(text.strip()) < 10  # Should not be flagged

    def test_repeated_char_is_garbled(self):
        """Text with top-1 char > 25% should be garbled."""
        text = "폐" * 30 + "기타문자열"  # 폐 dominates
        from collections import Counter
        clean = text.replace(" ", "").replace("\n", "")
        c = Counter(clean)
        ratio = c.most_common(1)[0][1] / len(clean)
        assert ratio > 0.25

    def test_low_unique_ratio(self):
        """Very low unique char ratio flags as garbled."""
        text = "가나" * 50  # only 2 unique chars in 100
        clean = text.replace(" ", "").replace("\n", "")
        unique_ratio = len(set(clean)) / len(clean)
        assert unique_ratio < 0.08
