"""JSONL checkpoint for crash-safe ingestion pipeline.

Stage 1 (parse/OCR) writes one JSONL line per document.
Stage 2 (chunk/embed/store) reads the JSONL and processes each record.
If Stage 1 crashes (e.g. PaddleOCR segfault), already-parsed documents survive.

Usage:
    # Stage 1: write
    with JsonlCheckpointWriter(jsonl_path) as writer:
        record = serialize_parse_result(doc_id, fname, path, hash, parse_result)
        writer.write_record(record)

    # Stage 2: read
    for record, parse_result in JsonlCheckpointReader(jsonl_path):
        pipeline.ingest(raw, parse_result=parse_result)
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .document_parser import ParseResult

logger = logging.getLogger(__name__)

JSONL_VERSION = 1


@dataclass
class ParsedDocumentRecord:
    """One parsed document record in the JSONL file."""

    version: int = JSONL_VERSION
    doc_id: str = ""
    filename: str = ""
    source_path: str = ""
    content_hash: str = ""
    parsed_at: str = ""
    text: str = ""
    tables: list[list[list[str]]] = field(default_factory=list)
    ocr_text: str = ""
    images_processed: int = 0
    visual_analyses: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


def serialize_parse_result(
    doc_id: str,
    filename: str,
    source_path: str,
    content_hash: str,
    parse_result: ParseResult,
    metadata: dict[str, Any] | None = None,
) -> str:
    """Convert a ParseResult into a single JSONL line."""
    record = ParsedDocumentRecord(
        version=JSONL_VERSION,
        doc_id=doc_id,
        filename=filename,
        source_path=source_path,
        content_hash=content_hash,
        parsed_at=datetime.now(timezone.utc).isoformat(),
        text=parse_result.text,
        tables=parse_result.tables,
        ocr_text=parse_result.ocr_text,
        images_processed=parse_result.images_processed,
        visual_analyses=parse_result.visual_analyses,
        metadata=metadata or {},
    )
    return json.dumps(asdict(record), ensure_ascii=False)


def deserialize_record(line: str) -> tuple[ParsedDocumentRecord, ParseResult]:
    """Parse a JSONL line back into record + ParseResult."""
    data = json.loads(line)
    record = ParsedDocumentRecord(
        version=data.get("version", 1),
        doc_id=data["doc_id"],
        filename=data["filename"],
        source_path=data.get("source_path", ""),
        content_hash=data.get("content_hash", ""),
        parsed_at=data.get("parsed_at", ""),
        text=data.get("text", ""),
        tables=data.get("tables", []),
        ocr_text=data.get("ocr_text", ""),
        images_processed=data.get("images_processed", 0),
        visual_analyses=data.get("visual_analyses", []),
        metadata=data.get("metadata", {}),
    )
    parse_result = ParseResult(
        text=record.text,
        tables=record.tables,
        ocr_text=record.ocr_text,
        images_processed=record.images_processed,
        visual_analyses=record.visual_analyses,
    )
    return record, parse_result


def get_jsonl_path(kb_id: str) -> Path:
    """Return the standard JSONL checkpoint path for a KB."""
    base = os.getenv("KNOWLEDGE_PIPELINE_RUNTIME_BASE_DIR", "/tmp/knowledge-local")
    path = Path(base) / "uploads" / kb_id / "parsed_documents.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def get_already_parsed_ids(jsonl_path: str | Path) -> set[str]:
    """Read existing JSONL and return set of doc_ids already parsed."""
    ids: set[str] = set()
    path = Path(jsonl_path)
    if not path.exists():
        return ids
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                if data.get("doc_id"):
                    ids.add(data["doc_id"])
            except json.JSONDecodeError:
                continue  # truncated line from crash
    return ids


class JsonlCheckpointWriter:
    """Append-mode JSONL writer with fsync for crash safety."""

    def __init__(self, jsonl_path: str | Path) -> None:
        self._path = Path(jsonl_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._file = open(self._path, "a", encoding="utf-8")

    def write_record(self, json_line: str) -> None:
        self._file.write(json_line + "\n")
        self._file.flush()
        os.fsync(self._file.fileno())

    def close(self) -> None:
        self._file.close()

    def __enter__(self) -> JsonlCheckpointWriter:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()


class JsonlCheckpointReader:
    """Reads JSONL checkpoint file, skipping malformed lines."""

    def __init__(self, jsonl_path: str | Path) -> None:
        self._path = Path(jsonl_path)

    def __iter__(self) -> Iterator[tuple[ParsedDocumentRecord, ParseResult]]:
        if not self._path.exists():
            return
        with open(self._path, encoding="utf-8") as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    yield deserialize_record(line)
                except (json.JSONDecodeError, KeyError) as e:
                    logger.warning("Skipping malformed JSONL line %d: %s", line_num, e)
                    continue

    def count(self) -> int:
        """Count valid records without full deserialization."""
        n = 0
        if self._path.exists():
            with open(self._path, encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        n += 1
        return n
