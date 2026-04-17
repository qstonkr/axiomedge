"""Extended unit tests for src/pipeline/document_parser.py — 364 uncovered lines."""

from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from src.pipelines.document_parser import (
    ParseResult,
    _extract_pdf_date,
    _parse_text,
    _table_to_markdown,
    parse_bytes,
    parse_bytes_enhanced,
    parse_file,
    parse_file_enhanced,
)


# ---------------------------------------------------------------------------
# ParseResult
# ---------------------------------------------------------------------------

class TestParseResult:
    def test_full_text_plain(self):
        r = ParseResult(text="hello world")
        assert r.full_text == "hello world"

    def test_full_text_with_ocr(self):
        r = ParseResult(text="body", ocr_text="ocr text here")
        assert "[OCR Extracted Text]" in r.full_text
        assert "ocr text here" in r.full_text

    def test_full_text_with_tables(self):
        r = ParseResult(text="body", tables=[
            [["A", "B"], ["1", "2"]],
        ])
        assert "[Table]" in r.full_text
        assert "| A | B |" in r.full_text

    def test_full_text_combined(self):
        r = ParseResult(text="body", ocr_text="ocr", tables=[[["H"], ["V"]]])
        ft = r.full_text
        assert "body" in ft
        assert "[OCR Extracted Text]" in ft
        assert "[Table]" in ft

    def test_empty_parse_result(self):
        r = ParseResult()
        assert r.full_text == ""


# ---------------------------------------------------------------------------
# _table_to_markdown
# ---------------------------------------------------------------------------

class TestTableToMarkdown:
    def test_basic_table(self):
        data = [["Name", "Age"], ["Alice", "30"], ["Bob", "25"]]
        md = _table_to_markdown(data)
        assert "| Name | Age |" in md
        assert "| --- | --- |" in md
        assert "| Alice | 30 |" in md

    def test_empty_table(self):
        assert _table_to_markdown([]) == ""

    def test_max_rows(self):
        data = [["H"]] + [[str(i)] for i in range(60)]
        md = _table_to_markdown(data, max_rows=5)
        assert "rows omitted" in md

    def test_padded_rows(self):
        data = [["A", "B", "C"], ["1"]]
        md = _table_to_markdown(data)
        lines = md.strip().split("\n")
        assert len(lines) == 3  # header + sep + 1 row


# ---------------------------------------------------------------------------
# _extract_pdf_date
# ---------------------------------------------------------------------------

class TestExtractPdfDate:
    def test_valid_date(self):
        assert _extract_pdf_date("D:20240101120000") == "2024-01-01T12:00:00"

    def test_empty_string(self):
        assert _extract_pdf_date("") == ""

    def test_invalid_format(self):
        assert _extract_pdf_date("not-a-date") == ""


# ---------------------------------------------------------------------------
# _parse_text
# ---------------------------------------------------------------------------

class TestParseText:
    def test_utf8(self):
        assert _parse_text("hello".encode("utf-8")) == "hello"

    def test_euckr_fallback(self):
        text = "한글 테스트"
        data = text.encode("euc-kr")
        result = _parse_text(data)
        assert "한글" in result


# ---------------------------------------------------------------------------
# parse_bytes (various MIME types)
# ---------------------------------------------------------------------------

class TestParseBytes:
    def test_empty_file_raises(self):
        with pytest.raises(ValueError, match="Empty file"):
            parse_bytes(b"", "test.txt")

    def test_unsupported_extension(self):
        result = parse_bytes(b"data", "test.xyz")
        assert result == ""

    def test_legacy_doc_returns_empty(self):
        result = parse_bytes(b"data", "test.doc")
        assert result == ""

    def test_txt_file(self):
        data = "plain text content".encode("utf-8")
        result = parse_bytes(data, "test.txt")
        assert result == "plain text content"

    def test_json_file(self):
        data = '{"key": "value"}'.encode("utf-8")
        result = parse_bytes(data, "data.json")
        assert "key" in result

    def test_csv_file(self):
        data = "a,b\n1,2".encode("utf-8")
        result = parse_bytes(data, "data.csv")
        assert "a,b" in result

    @patch("src.pipelines.document_parser._parse_pdf")
    def test_pdf_dispatch(self, mock_parse):
        mock_parse.return_value = "pdf text"
        result = parse_bytes(b"pdf-data", "test.pdf")
        assert result == "pdf text"
        mock_parse.assert_called_once()

    @patch("src.pipelines.document_parser._parse_docx")
    def test_docx_dispatch(self, mock_parse):
        mock_parse.return_value = "docx text"
        result = parse_bytes(b"docx-data", "test.docx")
        assert result == "docx text"

    @patch("src.pipelines.document_parser._parse_xlsx")
    def test_xlsx_dispatch(self, mock_parse):
        mock_parse.return_value = "xlsx text"
        result = parse_bytes(b"xlsx-data", "test.xlsx")
        assert result == "xlsx text"

    @patch("src.pipelines.document_parser._parse_pptx")
    def test_pptx_dispatch(self, mock_parse):
        mock_parse.return_value = "pptx text"
        result = parse_bytes(b"pptx-data", "test.pptx")
        assert result == "pptx text"

    @patch("src.pipelines.document_parser._convert_ppt_to_pptx", return_value=None)
    def test_ppt_no_libreoffice(self, mock_convert):
        result = parse_bytes(b"ppt-data", "test.ppt")
        assert result == ""

    @patch("src.pipelines.document_parser._parse_pptx", return_value="pptx text")
    @patch("src.pipelines.document_parser._convert_ppt_to_pptx", return_value=b"converted")
    def test_ppt_with_conversion(self, mock_convert, mock_parse):
        result = parse_bytes(b"ppt-data", "test.ppt")
        assert result == "pptx text"


# ---------------------------------------------------------------------------
# parse_bytes_enhanced
# ---------------------------------------------------------------------------

class TestParseBytesEnhanced:
    @patch("src.pipelines.document_parser._parse_pdf_enhanced")
    def test_pdf_enhanced(self, mock_parse):
        mock_parse.return_value = ParseResult(text="enhanced pdf")
        result = parse_bytes_enhanced(b"data", "test.pdf")
        assert result.text == "enhanced pdf"

    @patch("src.pipelines.document_parser._parse_pptx_enhanced")
    def test_pptx_enhanced(self, mock_parse):
        mock_parse.return_value = ParseResult(text="enhanced pptx")
        result = parse_bytes_enhanced(b"data", "test.pptx")
        assert result.text == "enhanced pptx"

    def test_txt_enhanced_wraps_plain(self):
        result = parse_bytes_enhanced(b"hello", "test.txt")
        assert result.text == "hello"

    @patch("src.pipelines.document_parser._convert_ppt_to_pptx", return_value=None)
    def test_ppt_enhanced_conversion_fail(self, mock_convert):
        result = parse_bytes_enhanced(b"data", "test.ppt")
        assert "Error" in result.text

    @patch("src.pipelines.document_parser._process_images_ocr", return_value=("ocr text", []))
    def test_image_enhanced(self, mock_ocr):
        result = parse_bytes_enhanced(b"fake-image", "test.png")
        assert result.ocr_text == "ocr text"
        assert result.images_processed == 1


# ---------------------------------------------------------------------------
# parse_file / parse_file_enhanced
# ---------------------------------------------------------------------------

class TestParseFile:
    def test_file_not_found(self):
        result = parse_file("/nonexistent/path.txt")
        assert result == ""

    def test_file_enhanced_not_found(self):
        result = parse_file_enhanced("/nonexistent/path.txt")
        assert result.text == ""

    def test_file_too_large(self, tmp_path):
        large_file = tmp_path / "big.txt"
        large_file.write_text("x" * 100)
        with patch("src.pipelines.document_parser.MAX_FILE_SIZE", 10):
            with pytest.raises(ValueError, match="File too large"):
                parse_file(str(large_file))

    def test_file_enhanced_too_large(self, tmp_path):
        large_file = tmp_path / "big.txt"
        large_file.write_text("x" * 100)
        with patch("src.pipelines.document_parser.MAX_FILE_SIZE", 10):
            with pytest.raises(ValueError, match="File too large"):
                parse_file_enhanced(str(large_file))


# ---------------------------------------------------------------------------
# _parse_pdf (mocked fitz/pymupdf)
# ---------------------------------------------------------------------------

class TestParsePdf:
    def test_parse_pdf_success(self):
        mock_page = MagicMock()
        mock_page.get_text.return_value = "Page 1 content"

        mock_doc = MagicMock()
        mock_doc.__iter__ = MagicMock(return_value=iter([(0, mock_page)]))
        mock_doc.__enter__ = MagicMock(return_value=mock_doc)
        mock_doc.__exit__ = MagicMock(return_value=False)

        # Make enumerate work on mock_doc
        mock_doc.__iter__ = MagicMock(return_value=iter([mock_page]))

        with patch("pymupdf.open", return_value=mock_doc):
            from src.pipelines.document_parser import _parse_pdf
            result = _parse_pdf(b"fake-pdf", "test.pdf")
            assert "Page 1" in result

    def test_parse_pdf_corrupt(self):
        with patch("pymupdf.open", side_effect=RuntimeError("corrupt")):
            from src.pipelines.document_parser import _parse_pdf
            with pytest.raises(ValueError, match="PDF open failed"):
                _parse_pdf(b"bad-pdf", "test.pdf")


# ---------------------------------------------------------------------------
# _parse_docx (mocked docx)
# ---------------------------------------------------------------------------

class TestParseDocx:
    def test_parse_docx_success(self):
        mock_para = MagicMock()
        mock_para.text = "Hello paragraph"
        mock_para.style = MagicMock()
        mock_para.style.name = "Normal"

        mock_heading = MagicMock()
        mock_heading.text = "Heading Text"
        mock_heading.style = MagicMock()
        mock_heading.style.name = "Heading 2"

        mock_doc = MagicMock()
        mock_doc.paragraphs = [mock_para, mock_heading]
        mock_doc.tables = []

        with patch("docx.Document", return_value=mock_doc):
            from src.pipelines.document_parser import _parse_docx
            result = _parse_docx(b"fake-docx", "test.docx")
            assert "Hello paragraph" in result
            assert "## Heading Text" in result

    def test_parse_docx_with_tables(self):
        mock_cell = MagicMock()
        mock_cell.text = "cell value"
        mock_row = MagicMock()
        mock_row.cells = [mock_cell]
        mock_table = MagicMock()
        mock_table.rows = [mock_row]

        mock_doc = MagicMock()
        mock_doc.paragraphs = []
        mock_doc.tables = [mock_table]

        with patch("docx.Document", return_value=mock_doc):
            from src.pipelines.document_parser import _parse_docx
            result = _parse_docx(b"fake-docx", "test.docx")
            assert "cell value" in result

    def test_parse_docx_corrupt(self):
        with patch("docx.Document", side_effect=RuntimeError("corrupt")):
            from src.pipelines.document_parser import _parse_docx
            with pytest.raises(ValueError, match="DOCX open failed"):
                _parse_docx(b"bad-docx", "test.docx")


# ---------------------------------------------------------------------------
# _parse_xlsx (mocked openpyxl)
# ---------------------------------------------------------------------------

class TestParseXlsx:
    def test_parse_xlsx_success(self):
        mock_sheet = MagicMock()
        mock_sheet.iter_rows.return_value = [
            ("Header1", "Header2"),
            ("val1", "val2"),
            (None, "val3"),
        ]

        mock_wb = MagicMock()
        mock_wb.sheetnames = ["Sheet1"]
        mock_wb.__getitem__ = MagicMock(return_value=mock_sheet)

        with patch("openpyxl.load_workbook", return_value=mock_wb):
            from src.pipelines.document_parser import _parse_xlsx
            result = _parse_xlsx(b"fake-xlsx", "test.xlsx")
            assert "Sheet1" in result
            assert "Header1" in result


# ---------------------------------------------------------------------------
# _parse_pptx (mocked pptx)
# ---------------------------------------------------------------------------

class TestParsePptx:
    def test_parse_pptx_success(self):
        mock_shape = MagicMock()
        mock_shape.text = "Slide content"
        mock_shape.has_table = False
        mock_shape.shape_type = 1  # not GROUP

        mock_shapes = MagicMock()
        mock_shapes.__iter__ = MagicMock(return_value=iter([mock_shape]))
        mock_shapes.title = None  # No title placeholder

        mock_slide = MagicMock()
        mock_slide.shapes = mock_shapes
        mock_slide.has_notes_slide = False

        mock_prs = MagicMock()
        mock_prs.slides = [mock_slide]

        with patch("pptx.Presentation", return_value=mock_prs), \
             patch("pptx.enum.shapes.MSO_SHAPE_TYPE") as mock_enum:
            mock_enum.GROUP = 999  # something that won't match
            from src.pipelines.document_parser import _parse_pptx
            result = _parse_pptx(b"fake-pptx", "test.pptx")
            assert "Slide 1" in result


# ---------------------------------------------------------------------------
# _convert_ppt_to_pptx
# ---------------------------------------------------------------------------

class TestConvertPptToPptx:
    @patch("shutil.which", return_value=None)
    def test_no_soffice(self, mock_which):
        from src.pipelines.document_parser import _convert_ppt_to_pptx
        result = _convert_ppt_to_pptx(b"data", "test.ppt")
        assert result is None

    @patch("shutil.which", return_value="/usr/bin/soffice")
    @patch("subprocess.run")
    def test_conversion_failure(self, mock_run, mock_which):
        mock_run.return_value = MagicMock(returncode=1, stderr=b"error")
        from src.pipelines.document_parser import _convert_ppt_to_pptx
        result = _convert_ppt_to_pptx(b"data", "test.ppt")
        assert result is None
