"""Unit tests for src/domain/models.py

Tests domain models: RawDocument, ConnectorResult, SearchChunk, KBConfig,
KBTier, IngestionResult — creation, defaults, sha256, factory methods.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.domain.models import (
    ConnectorResult,
    IngestionResult,
    KBConfig,
    KBTier,
    RawDocument,
    SearchChunk,
)


# ---------------------------------------------------------------------------
# RawDocument
# ---------------------------------------------------------------------------

class TestRawDocument:
    def test_creation_minimal(self) -> None:
        doc = RawDocument(doc_id="d1", title="Test", content="body", source_uri="/f.pdf")
        assert doc.doc_id == "d1"
        assert doc.author == ""
        assert doc.updated_at is None
        assert doc.metadata == {}

    def test_frozen_immutability(self) -> None:
        doc = RawDocument(doc_id="d1", title="T", content="C", source_uri="/f")
        with pytest.raises(AttributeError):
            doc.title = "changed"  # type: ignore[misc]

    def test_sha256_deterministic(self) -> None:
        h1 = RawDocument.sha256("hello world")
        h2 = RawDocument.sha256("hello world")
        assert h1 == h2
        assert len(h1) == 64  # hex digest length

    def test_sha256_different_inputs(self) -> None:
        assert RawDocument.sha256("a") != RawDocument.sha256("b")

    def test_full_fields(self) -> None:
        now = datetime.now(timezone.utc)
        doc = RawDocument(
            doc_id="d2",
            title="Full",
            content="body",
            source_uri="/path",
            author="user1",
            updated_at=now,
            content_hash="abc123",
            metadata={"key": "val"},
        )
        assert doc.author == "user1"
        assert doc.updated_at == now
        assert doc.metadata["key"] == "val"


# ---------------------------------------------------------------------------
# ConnectorResult
# ---------------------------------------------------------------------------

class TestConnectorResult:
    def test_success_result(self) -> None:
        r = ConnectorResult(success=True, source_type="file_upload")
        assert r.success is True
        assert r.documents == []
        assert r.error is None
        assert r.skipped is False

    def test_skipped_flag(self) -> None:
        r = ConnectorResult(
            success=True,
            source_type="file_upload",
            metadata={"skipped": True},
        )
        assert r.skipped is True

    def test_error_result(self) -> None:
        r = ConnectorResult(success=False, source_type="crawl", error="timeout")
        assert r.success is False
        assert r.error == "timeout"


# ---------------------------------------------------------------------------
# SearchChunk
# ---------------------------------------------------------------------------

class TestSearchChunk:
    def test_creation(self) -> None:
        chunk = SearchChunk(
            chunk_id="c1", content="text", score=0.95, kb_id="kb1"
        )
        assert chunk.chunk_id == "c1"
        assert chunk.score == 0.95
        assert chunk.kb_name == ""
        assert chunk.metadata == {}

    def test_slots(self) -> None:
        chunk = SearchChunk(chunk_id="c1", content="t", score=0.5, kb_id="k")
        with pytest.raises(AttributeError):
            chunk.nonexistent = "x"  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# KBConfig
# ---------------------------------------------------------------------------

class TestKBConfig:
    def test_collection_name_defaults_to_kb_id(self) -> None:
        config = KBConfig(kb_id="my-kb", name="My KB")
        assert config.collection_name == "my-kb"

    def test_explicit_collection_name(self) -> None:
        config = KBConfig(kb_id="kb1", name="KB", collection_name="custom")
        assert config.collection_name == "custom"

    def test_default_tier(self) -> None:
        config = KBConfig(kb_id="kb1", name="KB")
        assert config.tier == KBTier.GLOBAL


# ---------------------------------------------------------------------------
# KBTier
# ---------------------------------------------------------------------------

class TestKBTier:
    def test_enum_values(self) -> None:
        assert KBTier.GLOBAL.value == "global"
        assert KBTier.BU.value == "bu"
        assert KBTier.TEAM.value == "team"

    def test_string_comparison(self) -> None:
        assert KBTier.GLOBAL == "global"


# ---------------------------------------------------------------------------
# IngestionResult
# ---------------------------------------------------------------------------

class TestIngestionResult:
    def test_success_factory(self) -> None:
        r = IngestionResult.success_result(chunks_stored=42, metadata={"kb": "x"})
        assert r.success is True
        assert r.blocked is False
        assert r.chunks_stored == 42
        assert r.metadata["kb"] == "x"

    def test_failure_factory(self) -> None:
        r = IngestionResult.failure_result(reason="parse error", stage="parse")
        assert r.success is False
        assert r.reason == "parse error"
        assert r.stage == "parse"

    def test_frozen(self) -> None:
        r = IngestionResult(success=True)
        with pytest.raises(AttributeError):
            r.success = False  # type: ignore[misc]
