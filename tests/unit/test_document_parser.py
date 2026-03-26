"""Unit tests for the document parser."""

import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from src.pipeline.document_parser import parse_file, parse_bytes, ParseResult


class TestDocumentParser:
    """Test document parsing functions."""

    def test_parse_txt_file(self, tmp_path: Path) -> None:
        txt_file = tmp_path / "test.txt"
        txt_file.write_text("Hello, knowledge base!\nLine two.", encoding="utf-8")

        result = parse_file(str(txt_file))
        assert "Hello, knowledge base!" in result
        assert "Line two." in result

    def test_parse_txt_bytes(self) -> None:
        data = b"Simple text content"
        result = parse_bytes(data, "test.txt")
        assert result == "Simple text content"

    def test_parse_empty_file(self) -> None:
        """Empty file should raise ValueError."""
        with pytest.raises(ValueError, match="Empty file"):
            parse_bytes(b"", "empty.txt")

    def test_parse_nonexistent_file(self) -> None:
        result = parse_file("/nonexistent/path/file.txt")
        assert result == ""

    def test_parse_md_file(self, tmp_path: Path) -> None:
        md_file = tmp_path / "readme.md"
        md_file.write_text("# Title\n\nBody text.", encoding="utf-8")

        result = parse_file(str(md_file))
        assert "# Title" in result
        assert "Body text." in result

    def test_parse_csv_file(self, tmp_path: Path) -> None:
        csv_file = tmp_path / "data.csv"
        csv_file.write_text("col1,col2\nval1,val2", encoding="utf-8")

        result = parse_file(str(csv_file))
        assert "col1,col2" in result

    def test_parse_json_file(self, tmp_path: Path) -> None:
        json_file = tmp_path / "config.json"
        json_file.write_text('{"key": "value"}', encoding="utf-8")

        result = parse_file(str(json_file))
        assert '"key": "value"' in result

    def test_parse_unsupported_extension(self, tmp_path: Path) -> None:
        weird_file = tmp_path / "data.xyz"
        weird_file.write_bytes(b"some binary")

        result = parse_file(str(weird_file))
        assert result == ""

    def test_parse_euc_kr_fallback(self) -> None:
        """Korean EUC-KR encoded text should be decoded via fallback."""
        korean_text = "한글 테스트"
        euc_kr_bytes = korean_text.encode("euc-kr")
        result = parse_bytes(euc_kr_bytes, "korean.txt")
        assert "한글" in result

    def test_ppt_without_libreoffice(self) -> None:
        """Verify graceful degradation when LibreOffice is not installed."""
        with patch("shutil.which", return_value=None):
            result = parse_bytes(b"fake ppt data", "presentation.ppt")
            assert result == ""

    def test_ppt_without_libreoffice_enhanced(self) -> None:
        """Enhanced parser should return error message for .ppt without LibreOffice."""
        from src.pipeline.document_parser import parse_bytes_enhanced

        with patch("shutil.which", return_value=None):
            result = parse_bytes_enhanced(b"fake ppt data", "presentation.ppt")
            assert isinstance(result, ParseResult)
            assert "LibreOffice" in result.text or "Error" in result.text

    def test_parse_file_too_large(self, tmp_path: Path) -> None:
        """Files exceeding MAX_FILE_SIZE should raise ValueError."""
        from src.pipeline.document_parser import MAX_FILE_SIZE

        large_file = tmp_path / "huge.txt"
        # Create a file slightly over max
        large_file.write_bytes(b"x" * (MAX_FILE_SIZE + 1))

        with pytest.raises(ValueError, match="File too large"):
            parse_file(str(large_file))

    def test_parse_doc_legacy_not_supported(self) -> None:
        """Legacy .doc format returns empty string."""
        result = parse_bytes(b"fake doc", "legacy.doc")
        assert result == ""

    def test_parse_yaml_file(self, tmp_path: Path) -> None:
        yaml_file = tmp_path / "config.yaml"
        yaml_file.write_text("key: value\nlist:\n  - item1", encoding="utf-8")

        result = parse_file(str(yaml_file))
        assert "key: value" in result

    def test_parse_result_full_text(self) -> None:
        """Test ParseResult.full_text combines text, OCR, and tables."""
        pr = ParseResult(
            text="Main text",
            ocr_text="OCR extracted",
            tables=[
                [["Header1", "Header2"], ["val1", "val2"]],
            ],
        )
        full = pr.full_text
        assert "Main text" in full
        assert "OCR Extracted Text" in full
        assert "OCR extracted" in full
        assert "Table" in full
        assert "Header1" in full
