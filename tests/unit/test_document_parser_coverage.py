"""Unit tests for document_parser.py — coverage push.

Targets ~291 uncovered lines: _parse_pdf, _parse_pdf_enhanced,
_has_broken_cmap_fonts, _is_garbled_text, _extract_page_tables,
_extract_page_images, _classify_pdf_page, _process_images_ocr,
_extract_pdf_date, parse_bytes_enhanced, _table_to_markdown,
_parse_image, _convert_ppt_to_pptx, _format_docx_paragraph, etc.
"""

from __future__ import annotations

import io
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

# ---------------------------------------------------------------------------
# _extract_pdf_date
# ---------------------------------------------------------------------------

from src.pipelines.document_parser import _extract_pdf_date


class TestExtractPdfDate:
    def test_valid_date(self):
        assert _extract_pdf_date("D:20250115103045") == "2025-01-15T10:30:45"

    def test_empty(self):
        assert _extract_pdf_date("") == ""

    def test_none(self):
        assert _extract_pdf_date(None) == ""

    def test_malformed(self):
        assert _extract_pdf_date("not-a-date") == ""

    def test_with_timezone(self):
        assert _extract_pdf_date("D:20240101120000+09'00'") == "2024-01-01T12:00:00"


# ---------------------------------------------------------------------------
# _table_to_markdown
# ---------------------------------------------------------------------------

from src.pipelines.document_parser import _table_to_markdown


class TestTableToMarkdown:
    def test_empty(self):
        assert _table_to_markdown([]) == ""

    def test_single_row(self):
        result = _table_to_markdown([["A", "B"]])
        assert "| A | B |" in result
        assert "| --- | --- |" in result

    def test_multiple_rows(self):
        data = [["H1", "H2"], ["a", "b"], ["c", "d"]]
        result = _table_to_markdown(data)
        assert "| a | b |" in result
        assert "| c | d |" in result

    def test_max_rows_truncation(self):
        data = [["H"]] + [[f"r{i}"] for i in range(60)]
        result = _table_to_markdown(data, max_rows=10)
        assert "rows omitted" in result

    def test_uneven_rows(self):
        data = [["H1", "H2", "H3"], ["a"]]
        result = _table_to_markdown(data)
        assert "| a |" in result


# ---------------------------------------------------------------------------
# _extract_page_tables
# ---------------------------------------------------------------------------

from src.pipelines.document_parser import _extract_page_tables


class TestExtractPageTables:
    def test_tables_found(self):
        mock_table = MagicMock()
        mock_table.extract.return_value = [["a", "b"], [None, "d"]]
        page = MagicMock()
        page.find_tables.return_value = [mock_table]
        tables = []
        _extract_page_tables(page, tables)
        assert len(tables) == 1
        assert tables[0][1][0] == ""  # None -> ""

    def test_empty_table(self):
        mock_table = MagicMock()
        mock_table.extract.return_value = []
        page = MagicMock()
        page.find_tables.return_value = [mock_table]
        tables = []
        _extract_page_tables(page, tables)
        assert len(tables) == 0

    def test_no_tables(self):
        page = MagicMock()
        page.find_tables.return_value = []
        tables = []
        _extract_page_tables(page, tables)
        assert tables == []


# ---------------------------------------------------------------------------
# _extract_page_images
# ---------------------------------------------------------------------------

from src.pipelines.document_parser import _extract_page_images


class TestExtractPageImages:
    def test_images_extracted(self):
        page = MagicMock()
        page.get_images.return_value = [(42, "png", 100, 100, 8, "DeviceRGB", "", "", "")]
        doc = MagicMock()
        doc.extract_image.return_value = {"image": b"\x00" * 2000}
        images = []
        _extract_page_images(page, doc, images)
        assert len(images) == 1

    def test_image_too_small(self):
        page = MagicMock()
        page.get_images.return_value = [(1,)]
        doc = MagicMock()
        doc.extract_image.return_value = {"image": b"\x00" * 500}
        images = []
        _extract_page_images(page, doc, images)
        assert len(images) == 0

    def test_image_too_large(self):
        page = MagicMock()
        page.get_images.return_value = [(1,)]
        doc = MagicMock()
        doc.extract_image.return_value = {"image": b"\x00" * 11_000_000}
        images = []
        _extract_page_images(page, doc, images)
        assert len(images) == 0

    def test_no_image_data(self):
        page = MagicMock()
        page.get_images.return_value = [(1,)]
        doc = MagicMock()
        doc.extract_image.return_value = {}
        images = []
        _extract_page_images(page, doc, images)
        assert len(images) == 0


# ---------------------------------------------------------------------------
# _classify_pdf_page
# ---------------------------------------------------------------------------

from src.pipelines.document_parser import _classify_pdf_page


class TestClassifyPdfPage:
    def _make_page(self, text="Hello world", images=None, tables=None):
        page = MagicMock()
        page.get_text.return_value = text
        page.find_tables.return_value = tables or []
        page.get_images.return_value = images or []
        return page

    def test_normal_text_page(self):
        page = self._make_page(text="Normal text content here.")
        texts, scanned, tables, images = [], [], [], []
        _classify_pdf_page(
            page, 0, "test.pdf", MagicMock(),
            lambda p: False, lambda t: False,
            texts, scanned, tables, images,
        )
        assert len(texts) == 1
        assert "[Page 1]" in texts[0]

    def test_empty_text_page(self):
        page = self._make_page(text="   ")
        texts, scanned, tables, images = [], [], [], []
        _classify_pdf_page(
            page, 2, "test.pdf", MagicMock(),
            lambda p: False, lambda t: False,
            texts, scanned, tables, images,
        )
        assert len(scanned) == 1
        assert 3 in scanned

    def test_broken_cmap_page(self):
        page = self._make_page(text="Some text")
        texts, scanned, tables, images = [], [], [], []
        _classify_pdf_page(
            page, 0, "test.pdf", MagicMock(),
            lambda p: True, lambda t: False,
            texts, scanned, tables, images,
        )
        assert len(scanned) == 1
        assert len(texts) == 0

    def test_garbled_text_page(self):
        page = self._make_page(text="폐폐폐폐폐폐폐폐폐폐폐")
        texts, scanned, tables, images = [], [], [], []
        _classify_pdf_page(
            page, 1, "test.pdf", MagicMock(),
            lambda p: False, lambda t: True,
            texts, scanned, tables, images,
        )
        assert len(scanned) == 1


# ---------------------------------------------------------------------------
# parse_bytes / parse_bytes_enhanced dispatch
# ---------------------------------------------------------------------------

from src.pipelines.document_parser import parse_bytes, parse_bytes_enhanced


class TestParseBytes:
    def test_empty_file_raises(self):
        with pytest.raises(ValueError, match="Empty file"):
            parse_bytes(b"", "test.pdf")

    def test_unsupported_extension(self):
        result = parse_bytes(b"data", "test.xyz")
        assert result == ""

    def test_doc_extension(self):
        result = parse_bytes(b"data", "test.doc")
        assert result == ""

    def test_text_file(self):
        result = parse_bytes(b"hello world", "test.txt")
        assert result == "hello world"

    def test_json_file(self):
        result = parse_bytes(b'{"key": "value"}', "test.json")
        assert '{"key": "value"}' in result

    def test_euc_kr_fallback(self):
        text = "한글 텍스트"
        encoded = text.encode("euc-kr")
        result = parse_bytes(encoded, "test.txt")
        assert "한글" in result


class TestParseBytesEnhanced:
    def test_image_routing(self):
        """Image files should route to _parse_image."""
        with patch("src.pipelines.document_parser._process_images_ocr") as mock_ocr:
            mock_ocr.return_value = ("OCR text", [])
            result = parse_bytes_enhanced(b"\x89PNG\r\n" + b"\x00" * 100, "test.png")
            assert result.ocr_text == "OCR text"

    def test_fallback_to_parse_bytes(self):
        result = parse_bytes_enhanced(b"hello", "test.txt")
        assert result.text == "hello"

    def test_ppt_conversion_failure(self):
        with patch("src.pipelines.document_parser._convert_ppt_to_pptx", return_value=None):
            result = parse_bytes_enhanced(b"ppt-data", "test.ppt")
            assert "Error" in result.text


# ---------------------------------------------------------------------------
# _parse_pdf with mock pymupdf
# ---------------------------------------------------------------------------

from src.pipelines.document_parser import _parse_pdf


def _make_mock_pymupdf(doc):
    """Create a mock pymupdf module with doc returned from open()."""
    mock_mod = MagicMock()
    mock_mod.open.return_value = doc
    return mock_mod


class TestParsePdf:
    def test_multi_page(self):
        mock_page1 = MagicMock()
        mock_page1.get_text.return_value = "Page 1 text"
        mock_page2 = MagicMock()
        mock_page2.get_text.return_value = "Page 2 text"
        mock_doc = MagicMock()
        mock_doc.__iter__ = lambda self: iter([mock_page1, mock_page2])

        mock_pymupdf = _make_mock_pymupdf(mock_doc)
        with patch.dict("sys.modules", {"pymupdf": mock_pymupdf}):
            result = _parse_pdf(b"fake-pdf", "test.pdf")
            assert "[Page 1]" in result
            assert "[Page 2]" in result

    def test_corrupt_pdf(self):
        mock_pymupdf = MagicMock()
        mock_pymupdf.open.side_effect = RuntimeError("corrupt")
        with patch.dict("sys.modules", {"pymupdf": mock_pymupdf}):
            with pytest.raises(ValueError, match="PDF open failed"):
                _parse_pdf(b"bad-data", "test.pdf")

    def test_empty_page_skipped(self):
        mock_page = MagicMock()
        mock_page.get_text.return_value = "   "
        mock_doc = MagicMock()
        mock_doc.__iter__ = lambda self: iter([mock_page])

        mock_pymupdf = _make_mock_pymupdf(mock_doc)
        with patch.dict("sys.modules", {"pymupdf": mock_pymupdf}):
            result = _parse_pdf(b"fake-pdf", "test.pdf")
            assert result == ""


# ---------------------------------------------------------------------------
# _parse_pdf_enhanced with mock pymupdf
# ---------------------------------------------------------------------------

from src.pipelines.document_parser import _parse_pdf_enhanced


class TestParsePdfEnhanced:
    def _mock_doc(self, pages, metadata=None):
        doc = MagicMock()
        doc.__iter__ = lambda self: iter(pages)
        doc.__len__ = lambda self: len(pages)
        doc.metadata = metadata or {"modDate": "D:20250301120000"}
        doc.__getitem__ = lambda self, i: pages[i]
        return doc

    def test_basic_flow(self):
        page = MagicMock()
        page.get_text.return_value = "Normal text"
        page.find_tables.return_value = []
        page.get_images.return_value = []
        page.get_fonts.return_value = []

        mock_doc = self._mock_doc([page])
        mock_pymupdf = _make_mock_pymupdf(mock_doc)

        with patch.dict("sys.modules", {"pymupdf": mock_pymupdf}):
            result = _parse_pdf_enhanced(b"pdf-data", "test.pdf")
            assert "Normal text" in result.text
            assert result.file_modified_at == "2025-03-01T12:00:00"

    def test_corrupt_pdf_raises(self):
        mock_pymupdf = MagicMock()
        mock_pymupdf.open.side_effect = RuntimeError("corrupt")
        with patch.dict("sys.modules", {"pymupdf": mock_pymupdf}):
            with pytest.raises(ValueError, match="PDF open failed"):
                _parse_pdf_enhanced(b"bad", "test.pdf")

    def test_scanned_pages_ocr(self):
        """Scanned pages should be routed to OCR."""
        page = MagicMock()
        page.get_text.return_value = ""  # empty = scanned
        page.find_tables.return_value = []
        page.get_images.return_value = []
        page.get_fonts.return_value = []

        mock_doc = self._mock_doc([page])

        # Second doc open for OCR rendering
        pix = MagicMock()
        pix.tobytes.return_value = b"png-image-data"
        pix.width = 2550
        pix.height = 3300

        page_for_ocr = MagicMock()
        page_for_ocr.get_pixmap.return_value = pix

        mock_doc2 = MagicMock()
        mock_doc2.__getitem__ = lambda self, i: page_for_ocr

        mock_pymupdf = MagicMock()
        mock_pymupdf.open.side_effect = [mock_doc, mock_doc2]

        with (
            patch.dict("sys.modules", {"pymupdf": mock_pymupdf}),
            patch("src.pipelines._parser_utils._process_images_ocr") as mock_ocr,
        ):
            mock_ocr.return_value = ("[Image 1 OCR] Scanned text", [])
            result = _parse_pdf_enhanced(b"pdf-data", "scanned.pdf")
            assert "Scanned text" in result.ocr_text
            assert result.images_processed >= 1

    def test_extracted_images_ocr(self):
        """Embedded images should be routed to OCR."""
        page = MagicMock()
        page.get_text.return_value = "Some text"
        page.find_tables.return_value = []
        page.get_images.return_value = [(42, "png", 100, 100, 8, "RGB", "", "", "")]
        page.get_fonts.return_value = []

        doc = self._mock_doc([page])
        doc.extract_image.return_value = {"image": b"\x00" * 5000}
        mock_pymupdf = _make_mock_pymupdf(doc)

        with (
            patch.dict("sys.modules", {"pymupdf": mock_pymupdf}),
            patch("src.pipelines._parser_utils._process_images_ocr") as mock_ocr,
        ):
            mock_ocr.return_value = ("[Image 1 OCR] img text", [{"shapes": []}])
            result = _parse_pdf_enhanced(b"pdf", "test.pdf")
            assert result.images_processed >= 1


# ---------------------------------------------------------------------------
# _process_images_ocr with mocked httpx + PIL
# ---------------------------------------------------------------------------

from src.pipelines.document_parser import _process_images_ocr


class TestProcessImagesOcr:
    def test_empty_images(self):
        text, analyses = _process_images_ocr([])
        assert text == ""
        assert analyses == []

    def test_successful_ocr(self):
        """Mock the full OCR pipeline: PIL decode + httpx POST."""
        fake_response = MagicMock()
        fake_response.status_code = 200
        fake_response.json.return_value = {
            "boxes": [
                {"text": "Hello", "confidence": 0.95, "polygon": []},
                {"text": "World", "confidence": 0.90, "polygon": []},
            ]
        }
        fake_response.raise_for_status = MagicMock()

        # Create a minimal PNG image
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (100, 100), "white").save(buf, format="PNG")
        png_bytes = buf.getvalue()

        with patch("httpx.Client") as MockClient:
            mock_client = MagicMock()
            MockClient.return_value = mock_client
            mock_client.post.return_value = fake_response
            text, analyses = _process_images_ocr([png_bytes])

        assert "Hello" in text
        assert "World" in text

    def test_ocr_low_confidence_filtered(self):
        fake_response = MagicMock()
        fake_response.status_code = 200
        fake_response.json.return_value = {
            "boxes": [
                {"text": "Good", "confidence": 0.90},
                {"text": "Bad", "confidence": 0.30},
            ]
        }
        fake_response.raise_for_status = MagicMock()

        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (100, 100)).save(buf, format="PNG")
        png_bytes = buf.getvalue()

        with patch("httpx.Client") as MockClient:
            mock_client = MagicMock()
            MockClient.return_value = mock_client
            mock_client.post.return_value = fake_response
            text, _ = _process_images_ocr([png_bytes])

        assert "Good" in text
        assert "Bad" not in text

    def test_image_too_small_skipped(self):
        """Images < 20x20 should be skipped."""
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (10, 10)).save(buf, format="PNG")
        png_bytes = buf.getvalue()

        with patch("httpx.Client") as MockClient:
            mock_client = MagicMock()
            MockClient.return_value = mock_client
            text, _ = _process_images_ocr([png_bytes])

        assert text == ""
        mock_client.post.assert_not_called()

    def test_ocr_failure_retry(self):
        """OCR failure should trigger resize retry."""
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (200, 200)).save(buf, format="PNG")
        png_bytes = buf.getvalue()

        success_resp = MagicMock()
        success_resp.json.return_value = {"boxes": [{"text": "Retried", "confidence": 0.9}]}
        success_resp.raise_for_status = MagicMock()

        with patch("httpx.Client") as MockClient:
            mock_client = MagicMock()
            MockClient.return_value = mock_client
            # First call fails, second (retry) succeeds
            mock_client.post.side_effect = [RuntimeError("timeout"), success_resp]
            text, _ = _process_images_ocr([png_bytes])

        assert "Retried" in text

    def test_ocr_no_boxes_fallback(self):
        """When no boxes, should fallback to texts/result fields."""
        fake_response = MagicMock()
        fake_response.json.return_value = {"texts": ["fallback text"]}
        fake_response.raise_for_status = MagicMock()

        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (100, 100)).save(buf, format="PNG")
        png_bytes = buf.getvalue()

        with patch("httpx.Client") as MockClient:
            mock_client = MagicMock()
            MockClient.return_value = mock_client
            mock_client.post.return_value = fake_response
            text, _ = _process_images_ocr([png_bytes])

        assert "fallback text" in text

    def test_vision_analysis_enabled(self):
        """When vision analysis is enabled, shapes/arrows should be captured."""
        fake_response = MagicMock()
        fake_response.json.return_value = {
            "boxes": [{"text": "Box", "confidence": 0.9}],
            "shapes": [{"type": "rectangle"}],
            "arrows": [{"from": "A", "to": "B"}],
            "text_shape_mappings": [{"shape_type": "box", "texts": "label"}],
        }
        fake_response.raise_for_status = MagicMock()

        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (100, 100)).save(buf, format="PNG")
        png_bytes = buf.getvalue()

        with (
            patch("httpx.Client") as MockClient,
            patch("src.pipelines._parser_utils._w") as mock_w,
        ):
            mock_w.ocr.enable_vision_analysis = True
            mock_client = MagicMock()
            MockClient.return_value = mock_client
            mock_client.post.return_value = fake_response
            text, analyses = _process_images_ocr([png_bytes])

        assert len(analyses) == 1
        assert analyses[0]["shape_count"] == 1
        assert "Diagram" in text


# ---------------------------------------------------------------------------
# ParseResult
# ---------------------------------------------------------------------------

from src.pipelines.document_parser import ParseResult


class TestParseResult:
    def test_full_text_with_ocr(self):
        r = ParseResult(text="Main", ocr_text="OCR data")
        assert "Main" in r.full_text
        assert "[OCR Extracted Text]" in r.full_text

    def test_full_text_with_tables(self):
        r = ParseResult(text="Main", tables=[[ ["H"], ["V"] ]])
        assert "[Table]" in r.full_text

    def test_full_text_empty(self):
        r = ParseResult()
        assert r.full_text == ""


# ---------------------------------------------------------------------------
# _format_docx_paragraph
# ---------------------------------------------------------------------------

from src.pipelines.document_parser import _format_docx_paragraph


class TestFormatDocxParagraph:
    def test_empty_para(self):
        para = MagicMock()
        para.text = ""
        assert _format_docx_paragraph(para) is None

    def test_heading_para(self):
        para = MagicMock()
        para.text = "Title"
        para.style.name = "Heading2"
        result = _format_docx_paragraph(para)
        assert result == "## Title"

    def test_heading_non_digit(self):
        para = MagicMock()
        para.text = "Title"
        para.style.name = "HeadingA"
        result = _format_docx_paragraph(para)
        assert result == "# Title"

    def test_normal_para(self):
        para = MagicMock()
        para.text = "Normal text"
        para.style.name = "Normal"
        result = _format_docx_paragraph(para)
        assert result == "Normal text"


# ---------------------------------------------------------------------------
# _convert_ppt_to_pptx
# ---------------------------------------------------------------------------

from src.pipelines.document_parser import _convert_ppt_to_pptx


class TestConvertPptToPptx:
    def test_no_soffice(self):
        with patch("shutil.which", return_value=None):
            result = _convert_ppt_to_pptx(b"data", "test.ppt")
            assert result is None

    def test_soffice_failure(self):
        with (
            patch("shutil.which", return_value="/usr/bin/soffice"),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=1, stderr=b"error")
            result = _convert_ppt_to_pptx(b"data", "test.ppt")
            assert result is None

    def test_soffice_timeout(self):
        import subprocess
        with (
            patch("shutil.which", return_value="/usr/bin/soffice"),
            patch("subprocess.run", side_effect=subprocess.TimeoutExpired("soffice", 30)),
        ):
            result = _convert_ppt_to_pptx(b"data", "test.ppt")
            assert result is None

    def test_soffice_generic_error(self):
        with (
            patch("shutil.which", return_value="/usr/bin/soffice"),
            patch("subprocess.run", side_effect=OSError("boom")),
        ):
            result = _convert_ppt_to_pptx(b"data", "test.ppt")
            assert result is None


# ---------------------------------------------------------------------------
# parse_file / parse_file_enhanced with real tmp files
# ---------------------------------------------------------------------------

from src.pipelines.document_parser import parse_file, parse_file_enhanced
import tempfile
import os


class TestParseFile:
    def test_nonexistent_file(self):
        assert parse_file("/nonexistent/file.txt") == ""

    def test_txt_file(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("hello")
        assert parse_file(str(f)) == "hello"

    def test_file_too_large(self, tmp_path):
        f = tmp_path / "big.txt"
        f.write_bytes(b"x" * 10)
        with patch("src.pipelines.document_parser.MAX_FILE_SIZE", 5):
            with pytest.raises(ValueError, match="File too large"):
                parse_file(str(f))


class TestParseFileEnhanced:
    def test_nonexistent(self):
        result = parse_file_enhanced("/nonexistent/file.txt")
        assert result.text == ""

    def test_txt_file(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("content")
        result = parse_file_enhanced(str(f))
        assert result.text == "content"

    def test_file_too_large(self, tmp_path):
        f = tmp_path / "big.txt"
        f.write_bytes(b"x" * 10)
        with patch("src.pipelines.document_parser.MAX_FILE_SIZE", 5):
            with pytest.raises(ValueError, match="File too large"):
                parse_file_enhanced(str(f))


# ---------------------------------------------------------------------------
# _has_broken_cmap_fonts (inner function) — recreated for direct testing
# ---------------------------------------------------------------------------

class TestHasBrokenCmapFonts:
    """Test the _has_broken_cmap_fonts logic (defined inside _parse_pdf_enhanced).

    Since the function is inner-scoped, we exercise it through _parse_pdf_enhanced
    with carefully crafted mocks.
    """

    def test_broken_cmap_detected_via_enhanced(self):
        """Page with Type0/Identity-H font with broken CMap should be routed to OCR."""
        # The key test: _has_broken_cmap_fonts returns True for this page
        page = MagicMock()
        page.get_text.return_value = "Some Korean text"
        # Font: (xref, ext, ftype, basefont, name, encoding)
        page.get_fonts.return_value = [(100, "CFF", "Type0", "NanumGothic", "F1", "Identity-H")]
        page.find_tables.return_value = []
        page.get_images.return_value = []

        doc = MagicMock()
        doc.__iter__ = lambda self: iter([page])
        doc.__len__ = lambda self: 1
        doc.metadata = {"modDate": ""}
        # /ToUnicode not found in xref_object -> return True (broken)
        doc.xref_object.return_value = "/DescendantFonts 200 0 R"  # No /ToUnicode

        # For OCR rendering of the scanned page
        pix = MagicMock()
        pix.tobytes.return_value = b"\x89PNG" + b"\x00" * 100
        pix.width = 300
        pix.height = 400
        scanned_page = MagicMock()
        scanned_page.get_pixmap.return_value = pix

        doc2 = MagicMock()
        doc2.__getitem__ = MagicMock(return_value=scanned_page)

        mock_pymupdf = MagicMock()
        mock_pymupdf.open.side_effect = [doc, doc2]

        with patch.dict("sys.modules", {"pymupdf": mock_pymupdf}):
            with patch("src.pipelines._parser_utils._process_images_ocr", return_value=("OCR text", [])):
                result = _parse_pdf_enhanced(b"fake", "test.pdf")
                assert result.images_processed >= 1

    def test_non_type0_font_not_broken(self):
        """Non-Type0 fonts should not trigger broken CMap detection."""
        page = MagicMock()
        page.get_text.return_value = "Normal text"
        page.get_fonts.return_value = [(100, "CFF", "TrueType", "Arial", "F1", "WinAnsi")]
        page.find_tables.return_value = []
        page.get_images.return_value = []

        doc = MagicMock()
        doc.__iter__ = lambda self: iter([page])
        doc.__len__ = lambda self: 1
        doc.metadata = {"modDate": ""}

        mock_pymupdf = MagicMock()
        mock_pymupdf.open.return_value = doc

        with patch.dict("sys.modules", {"pymupdf": mock_pymupdf}):
            result = _parse_pdf_enhanced(b"fake", "test.pdf")
            assert "Normal text" in result.text

    def test_broken_cmap_with_tounicode_few_mappings(self):
        """Font with ToUnicode but very few mappings = broken CMap."""
        import re

        page = MagicMock()
        page.get_text.return_value = "폐폐폐폐폐"
        page.get_fonts.return_value = [(100, "CFF", "Type0", "Font", "F1", "Identity-H")]
        page.find_tables.return_value = []
        page.get_images.return_value = []

        doc = MagicMock()
        doc.__iter__ = lambda self: iter([page])
        doc.__len__ = lambda self: 1
        doc.metadata = {"modDate": ""}
        # xref_object returns: has /ToUnicode and /DescendantFonts
        doc.xref_object.side_effect = lambda xref: {
            100: "/ToUnicode 101 0 R\n/DescendantFonts 200 0 R",
            200: "201 0 R",
            201: "/FontDescriptor 300 0 R",
            300: "/FontFile2 400 0 R",
        }.get(xref, "")
        # CMap with very few mappings
        doc.xref_stream.side_effect = lambda xref: {
            101: b"2 beginbfchar\n<0041> <0041>\n<0042> <0042>\nendbfchar\n",
            400: b"\x00" * 20000,  # Large font file
        }.get(xref, b"")

        pix = MagicMock()
        pix.tobytes.return_value = b"\x89PNG" + b"\x00" * 100
        pix.width = 300
        pix.height = 400
        scanned_page = MagicMock()
        scanned_page.get_pixmap.return_value = pix

        doc2 = MagicMock()
        doc2.__getitem__ = MagicMock(return_value=scanned_page)

        mock_pymupdf = MagicMock()
        mock_pymupdf.open.side_effect = [doc, doc2]

        with patch.dict("sys.modules", {"pymupdf": mock_pymupdf}):
            with patch("src.pipelines._parser_utils._process_images_ocr", return_value=("OCR", [])):
                result = _parse_pdf_enhanced(b"fake", "test.pdf")
                assert result.images_processed >= 1


# ---------------------------------------------------------------------------
# _is_garbled_text edge cases
# ---------------------------------------------------------------------------

class TestIsGarbledEdgeCases:
    """Additional garbled text tests that exercise branches in the inner function."""

    def _call_enhanced_with_text(self, text):
        """Call _parse_pdf_enhanced with a page containing the given text."""
        page = MagicMock()
        page.get_text.return_value = text
        page.get_fonts.return_value = []
        page.find_tables.return_value = []
        page.get_images.return_value = []

        doc = MagicMock()
        doc.__iter__ = lambda self: iter([page])
        doc.__len__ = lambda self: 1
        doc.metadata = {"modDate": ""}

        mock_pymupdf = MagicMock()
        mock_pymupdf.open.return_value = doc

        with patch.dict("sys.modules", {"pymupdf": mock_pymupdf}):
            return _parse_pdf_enhanced(b"fake", "test.pdf")

    def test_normal_korean_text_not_garbled(self):
        result = self._call_enhanced_with_text(
            "이것은 정상적인 한국어 텍스트입니다. 여러 문장이 있습니다."
        )
        assert result.text != ""
        assert result.images_processed == 0


# ---------------------------------------------------------------------------
# _process_images_ocr CMYK conversion
# ---------------------------------------------------------------------------

class TestProcessImagesOcrConversion:
    def test_cmyk_image_converted(self):
        """CMYK images should be converted to RGB before OCR."""
        from PIL import Image

        buf = io.BytesIO()
        img = Image.new("CMYK", (100, 100))
        img.save(buf, format="TIFF")
        img_bytes = buf.getvalue()

        fake_response = MagicMock()
        fake_response.json.return_value = {"boxes": [{"text": "CMYK text", "confidence": 0.9}]}
        fake_response.raise_for_status = MagicMock()

        with patch("httpx.Client") as MockClient:
            mock_client = MagicMock()
            MockClient.return_value = mock_client
            mock_client.post.return_value = fake_response
            text, _ = _process_images_ocr([img_bytes])

        assert "CMYK text" in text

    def test_undecoded_image_skipped(self):
        """Invalid image bytes should be skipped."""
        with patch("httpx.Client") as MockClient:
            mock_client = MagicMock()
            MockClient.return_value = mock_client
            text, _ = _process_images_ocr([b"invalid_not_an_image"])

        assert text == ""

    def test_ocr_all_failures(self):
        """When both OCR attempts fail, image should be skipped."""
        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGB", (100, 100)).save(buf, format="PNG")
        png_bytes = buf.getvalue()

        with patch("httpx.Client") as MockClient:
            mock_client = MagicMock()
            MockClient.return_value = mock_client
            mock_client.post.side_effect = RuntimeError("always fails")
            text, _ = _process_images_ocr([png_bytes])

        assert text == ""

    def test_ocr_text_fallback_string(self):
        """When texts is a string, should be used directly."""
        fake_response = MagicMock()
        fake_response.json.return_value = {"result": "string result"}
        fake_response.raise_for_status = MagicMock()

        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (100, 100)).save(buf, format="PNG")
        png_bytes = buf.getvalue()

        with patch("httpx.Client") as MockClient:
            mock_client = MagicMock()
            MockClient.return_value = mock_client
            mock_client.post.return_value = fake_response
            text, _ = _process_images_ocr([png_bytes])

        assert "string result" in text


# ---------------------------------------------------------------------------
# _extract_pptx_modified_date
# ---------------------------------------------------------------------------

from src.pipelines.document_parser import _extract_pptx_modified_date


class TestExtractPptxModifiedDate:
    def test_with_modified(self):
        from datetime import datetime
        prs = MagicMock()
        prs.core_properties.modified = datetime(2025, 3, 1, 12, 0, 0)
        result = _extract_pptx_modified_date(prs)
        assert "2025-03-01" in result

    def test_no_modified(self):
        prs = MagicMock()
        prs.core_properties.modified = None
        result = _extract_pptx_modified_date(prs)
        assert result == ""

    def test_error(self):
        prs = MagicMock()
        prs.core_properties = property(lambda s: 1/0)
        type(prs).core_properties = PropertyMock(side_effect=RuntimeError("fail"))
        result = _extract_pptx_modified_date(prs)
        assert result == ""


# ---------------------------------------------------------------------------
# _extract_table_data
# ---------------------------------------------------------------------------

from src.pipelines.document_parser import _extract_table_data


class TestExtractTableData:
    def test_basic(self):
        cell1 = MagicMock()
        cell1.text = " hello "
        cell2 = MagicMock()
        cell2.text = " world "
        row = MagicMock()
        row.cells = [cell1, cell2]
        table = MagicMock()
        table.rows = [row]
        result = _extract_table_data(table)
        assert result == [["hello", "world"]]
