"""Unit tests for src/pipeline/jsonl_checkpoint.py — crash-safe JSONL checkpoint."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.pipelines.document_parser import ParseResult
from src.pipelines.jsonl_checkpoint import (
    JSONL_VERSION,
    JsonlCheckpointReader,
    JsonlCheckpointWriter,
    ParsedDocumentRecord,
    deserialize_record,
    get_already_parsed_ids,
    get_jsonl_path,
    serialize_parse_result,
)


# ---------------------------------------------------------------------------
# ParsedDocumentRecord defaults
# ---------------------------------------------------------------------------


class TestParsedDocumentRecord:
    def test_default_values(self):
        record = ParsedDocumentRecord()
        assert record.version == JSONL_VERSION
        assert record.doc_id == ""
        assert record.filename == ""
        assert record.tables == []
        assert record.metadata == {}

    def test_custom_values(self):
        record = ParsedDocumentRecord(
            doc_id="d1", filename="test.pdf", text="hello"
        )
        assert record.doc_id == "d1"
        assert record.filename == "test.pdf"
        assert record.text == "hello"


# ---------------------------------------------------------------------------
# serialize_parse_result / deserialize_record
# ---------------------------------------------------------------------------


class TestSerializeDeserialize:
    def test_round_trip(self):
        pr = ParseResult(
            text="Main text content",
            tables=[[["a", "b"], ["c", "d"]]],
            ocr_text="OCR scanned text",
            images_processed=3,
            visual_analyses=[{"type": "diagram", "description": "flow chart"}],
        )
        line = serialize_parse_result(
            doc_id="doc-001",
            filename="report.pdf",
            source_path="/data/report.pdf",
            content_hash="abc123",
            parse_result=pr,
            metadata={"kb_id": "test-kb"},
        )
        assert isinstance(line, str)
        # Verify it's valid JSON
        data = json.loads(line)
        assert data["doc_id"] == "doc-001"
        assert data["version"] == JSONL_VERSION

        # Deserialize
        record, result = deserialize_record(line)
        assert record.doc_id == "doc-001"
        assert record.filename == "report.pdf"
        assert record.source_path == "/data/report.pdf"
        assert record.content_hash == "abc123"
        assert record.metadata == {"kb_id": "test-kb"}
        assert result.text == "Main text content"
        assert result.tables == [[["a", "b"], ["c", "d"]]]
        assert result.ocr_text == "OCR scanned text"
        assert result.images_processed == 3
        assert len(result.visual_analyses) == 1

    def test_minimal_parse_result(self):
        pr = ParseResult(text="short")
        line = serialize_parse_result("d1", "f.txt", "", "", pr)
        record, result = deserialize_record(line)
        assert result.text == "short"
        assert result.tables == []
        assert result.ocr_text == ""

    def test_unicode_content(self):
        pr = ParseResult(text="한글 테스트 문서입니다.")
        line = serialize_parse_result("d-kr", "korean.pdf", "", "", pr)
        record, result = deserialize_record(line)
        assert result.text == "한글 테스트 문서입니다."

    def test_parsed_at_is_set(self):
        pr = ParseResult(text="test")
        line = serialize_parse_result("d1", "f.txt", "", "", pr)
        data = json.loads(line)
        assert data["parsed_at"] != ""
        assert "T" in data["parsed_at"]  # ISO format


# ---------------------------------------------------------------------------
# get_jsonl_path
# ---------------------------------------------------------------------------


class TestGetJsonlPath:
    def test_returns_path_with_kb_id(self, monkeypatch, tmp_path):
        monkeypatch.setenv("KNOWLEDGE_PIPELINE_RUNTIME_BASE_DIR", str(tmp_path))
        path = get_jsonl_path("my-kb")
        assert path == tmp_path / "uploads" / "my-kb" / "parsed_documents.jsonl"
        # Parent directory should be created
        assert path.parent.exists()

    def test_default_base_dir(self, monkeypatch):
        monkeypatch.delenv("KNOWLEDGE_PIPELINE_RUNTIME_BASE_DIR", raising=False)
        path = get_jsonl_path("test-kb")
        assert "parsed_documents.jsonl" in str(path)
        assert "test-kb" in str(path)


# ---------------------------------------------------------------------------
# get_already_parsed_ids
# ---------------------------------------------------------------------------


class TestGetAlreadyParsedIds:
    def test_nonexistent_file_returns_empty(self, tmp_path):
        ids = get_already_parsed_ids(tmp_path / "no-such-file.jsonl")
        assert ids == set()

    def test_reads_doc_ids(self, tmp_path):
        pr = ParseResult(text="t")
        lines = [
            serialize_parse_result("doc-1", "a.pdf", "", "", pr),
            serialize_parse_result("doc-2", "b.pdf", "", "", pr),
            serialize_parse_result("doc-3", "c.pdf", "", "", pr),
        ]
        f = tmp_path / "parsed.jsonl"
        f.write_text("\n".join(lines) + "\n")
        ids = get_already_parsed_ids(f)
        assert ids == {"doc-1", "doc-2", "doc-3"}

    def test_skips_blank_lines(self, tmp_path):
        pr = ParseResult(text="t")
        line = serialize_parse_result("doc-1", "a.pdf", "", "", pr)
        f = tmp_path / "parsed.jsonl"
        f.write_text(f"\n{line}\n\n")
        ids = get_already_parsed_ids(f)
        assert ids == {"doc-1"}

    def test_skips_malformed_json(self, tmp_path):
        pr = ParseResult(text="t")
        good_line = serialize_parse_result("doc-1", "a.pdf", "", "", pr)
        f = tmp_path / "parsed.jsonl"
        f.write_text(f"{good_line}\nthis is not json\n")
        ids = get_already_parsed_ids(f)
        assert ids == {"doc-1"}

    def test_skips_records_without_doc_id(self, tmp_path):
        f = tmp_path / "parsed.jsonl"
        f.write_text('{"filename": "no_id.pdf"}\n')
        ids = get_already_parsed_ids(f)
        assert ids == set()


# ---------------------------------------------------------------------------
# JsonlCheckpointWriter
# ---------------------------------------------------------------------------


class TestJsonlCheckpointWriter:
    def test_write_and_read_back(self, tmp_path):
        jsonl_path = tmp_path / "checkpoint.jsonl"
        pr = ParseResult(text="written content")
        line = serialize_parse_result("w1", "doc.pdf", "/path", "hash1", pr)

        with JsonlCheckpointWriter(jsonl_path) as writer:
            writer.write_record(line)

        content = jsonl_path.read_text()
        assert content.strip() == line

    def test_append_multiple_records(self, tmp_path):
        jsonl_path = tmp_path / "checkpoint.jsonl"
        pr = ParseResult(text="t")

        with JsonlCheckpointWriter(jsonl_path) as writer:
            writer.write_record(serialize_parse_result("d1", "a.pdf", "", "", pr))
            writer.write_record(serialize_parse_result("d2", "b.pdf", "", "", pr))

        lines = [l for l in jsonl_path.read_text().strip().split("\n") if l]
        assert len(lines) == 2

    def test_creates_parent_directories(self, tmp_path):
        jsonl_path = tmp_path / "deep" / "nested" / "dir" / "checkpoint.jsonl"
        with JsonlCheckpointWriter(jsonl_path) as writer:
            writer.write_record('{"doc_id": "test"}')
        assert jsonl_path.exists()

    def test_append_to_existing_file(self, tmp_path):
        jsonl_path = tmp_path / "checkpoint.jsonl"
        jsonl_path.write_text('{"doc_id": "existing"}\n')

        with JsonlCheckpointWriter(jsonl_path) as writer:
            writer.write_record('{"doc_id": "new"}')

        lines = [l for l in jsonl_path.read_text().strip().split("\n") if l]
        assert len(lines) == 2

    def test_context_manager_closes_file(self, tmp_path):
        jsonl_path = tmp_path / "checkpoint.jsonl"
        writer = JsonlCheckpointWriter(jsonl_path)
        writer.write_record('{"doc_id": "test"}')
        writer.close()
        # File should still be readable after close
        assert jsonl_path.exists()


# ---------------------------------------------------------------------------
# JsonlCheckpointReader
# ---------------------------------------------------------------------------


class TestJsonlCheckpointReader:
    def test_reads_valid_records(self, tmp_path):
        jsonl_path = tmp_path / "checkpoint.jsonl"
        pr = ParseResult(text="content A")
        line = serialize_parse_result("r1", "doc.pdf", "/p", "h", pr)
        jsonl_path.write_text(line + "\n")

        reader = JsonlCheckpointReader(jsonl_path)
        records = list(reader)
        assert len(records) == 1
        record, result = records[0]
        assert record.doc_id == "r1"
        assert result.text == "content A"

    def test_nonexistent_file_yields_nothing(self, tmp_path):
        reader = JsonlCheckpointReader(tmp_path / "missing.jsonl")
        records = list(reader)
        assert records == []

    def test_skips_blank_lines(self, tmp_path):
        jsonl_path = tmp_path / "checkpoint.jsonl"
        pr = ParseResult(text="t")
        line = serialize_parse_result("r1", "f.pdf", "", "", pr)
        jsonl_path.write_text(f"\n\n{line}\n\n")

        records = list(JsonlCheckpointReader(jsonl_path))
        assert len(records) == 1

    def test_skips_malformed_json(self, tmp_path):
        jsonl_path = tmp_path / "checkpoint.jsonl"
        pr = ParseResult(text="good")
        good_line = serialize_parse_result("r1", "f.pdf", "", "", pr)
        jsonl_path.write_text(f"{good_line}\ntruncated garbage{{[\n")

        records = list(JsonlCheckpointReader(jsonl_path))
        assert len(records) == 1
        assert records[0][0].doc_id == "r1"

    def test_count_method(self, tmp_path):
        jsonl_path = tmp_path / "checkpoint.jsonl"
        jsonl_path.write_text('{"doc_id":"a"}\n{"doc_id":"b"}\n\n{"doc_id":"c"}\n')

        reader = JsonlCheckpointReader(jsonl_path)
        assert reader.count() == 3

    def test_count_nonexistent_file(self, tmp_path):
        reader = JsonlCheckpointReader(tmp_path / "missing.jsonl")
        assert reader.count() == 0

    def test_multiple_iteration(self, tmp_path):
        jsonl_path = tmp_path / "checkpoint.jsonl"
        pr = ParseResult(text="t")
        line = serialize_parse_result("r1", "f.pdf", "", "", pr)
        jsonl_path.write_text(line + "\n")

        reader = JsonlCheckpointReader(jsonl_path)
        first = list(reader)
        second = list(reader)
        assert len(first) == len(second) == 1


# ---------------------------------------------------------------------------
# Writer + Reader integration
# ---------------------------------------------------------------------------


class TestWriterReaderIntegration:
    def test_write_then_read(self, tmp_path):
        jsonl_path = tmp_path / "integration.jsonl"
        records_in = [
            ("d1", "file1.pdf", ParseResult(text="Text 1", ocr_text="OCR 1")),
            ("d2", "file2.docx", ParseResult(text="Text 2", tables=[[["x"]]])),
        ]

        with JsonlCheckpointWriter(jsonl_path) as writer:
            for doc_id, fname, pr in records_in:
                line = serialize_parse_result(doc_id, fname, f"/src/{fname}", "hash", pr)
                writer.write_record(line)

        records_out = list(JsonlCheckpointReader(jsonl_path))
        assert len(records_out) == 2
        assert records_out[0][0].doc_id == "d1"
        assert records_out[0][1].text == "Text 1"
        assert records_out[0][1].ocr_text == "OCR 1"
        assert records_out[1][0].doc_id == "d2"
        assert records_out[1][1].tables == [[["x"]]]

    def test_already_parsed_ids_matches_writer(self, tmp_path):
        jsonl_path = tmp_path / "ids.jsonl"
        pr = ParseResult(text="t")

        with JsonlCheckpointWriter(jsonl_path) as writer:
            for i in range(5):
                line = serialize_parse_result(f"doc-{i}", f"f{i}.pdf", "", "", pr)
                writer.write_record(line)

        ids = get_already_parsed_ids(jsonl_path)
        assert ids == {f"doc-{i}" for i in range(5)}
