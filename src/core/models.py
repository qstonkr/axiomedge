"""Domain models for knowledge-local.

Extracted and simplified from oreo-ecosystem:
- RawDocument, ConnectorResult, IKnowledgeConnector from connector.py
- SearchChunk, KBConfig, KBTier added for local use
- IngestionResult from ingestion_coordinator.py
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, AsyncIterator, Protocol


# ---------------------------------------------------------------------------
# KBTier
# ---------------------------------------------------------------------------

class KBTier(str, Enum):
    """Knowledge base access tier."""

    GLOBAL = "global"
    BU = "bu"
    TEAM = "team"


class FeedbackType(str, Enum):
    """Feedback type for knowledge quality tracking."""

    UPVOTE = "upvote"
    DOWNVOTE = "downvote"
    GENERAL = "general"
    CORRECTION = "correction"
    SUGGESTION = "suggestion"
    REPORT = "report"


class FeedbackStatus(str, Enum):
    """Feedback review status."""

    PENDING = "pending"
    REVIEWED = "reviewed"
    RESOLVED = "resolved"
    REJECTED = "rejected"


# ---------------------------------------------------------------------------
# RawDocument
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RawDocument:
    """Standardized raw document container.

    Mirrors the common "Document(text, metadata)" pattern in major RAG stacks.
    """

    doc_id: str
    title: str
    content: str
    source_uri: str
    author: str = ""
    updated_at: datetime | None = None
    content_hash: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def sha256(text: str) -> str:
        """Compute SHA-256 hex digest for the given text."""
        return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# ConnectorResult
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ConnectorResult:
    """Connector fetch result.

    Attributes:
        success: Whether fetch completed without fatal errors.
        source_type: Connector source type string (e.g., "confluence").
        documents: Returned documents (may be empty on skip/no changes).
        version_fingerprint: Deterministic fingerprint for change detection.
        metadata: Additional connector-specific metadata.
        error: Error message (if any).
    """

    success: bool
    source_type: str
    documents: list[RawDocument] = field(default_factory=list)
    version_fingerprint: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    @property
    def skipped(self) -> bool:
        """True if connector intentionally skipped fetching documents."""
        return bool(self.metadata.get("skipped", False))


# ---------------------------------------------------------------------------
# IKnowledgeConnector Protocol
# ---------------------------------------------------------------------------

class IKnowledgeConnector(Protocol):
    """Industry-standard connector interface.

    Simplified from oreo-ecosystem. Uses a plain config dict instead of
    SyncSource to avoid importing the full KB config system.
    """

    @property
    def source_type(self) -> str:
        """Source type string (e.g. 'file_upload', 'crawl_result')."""
        ...

    async def health_check(self) -> bool:
        """Check connector availability."""
        ...

    async def fetch(
        self,
        config: dict[str, Any],
        *,
        force: bool = False,
        last_fingerprint: str | None = None,
    ) -> ConnectorResult:
        """Fetch documents from the source."""
        ...

    async def lazy_fetch(
        self,
        config: dict[str, Any],
        *,
        force: bool = False,
        last_fingerprint: str | None = None,
    ) -> AsyncIterator[RawDocument]:
        """Stream documents for large sources (optional)."""
        ...


# ---------------------------------------------------------------------------
# SearchChunk
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class SearchChunk:
    """A chunk returned from vector search."""

    chunk_id: str
    content: str
    score: float
    kb_id: str
    kb_name: str = ""
    document_name: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# KBConfig
# ---------------------------------------------------------------------------

@dataclass
class KBConfig:
    """Knowledge base configuration."""

    kb_id: str
    name: str
    tier: KBTier = KBTier.GLOBAL
    description: str = ""
    collection_name: str = ""
    organization_id: str | None = None
    department_id: str | None = None
    settings: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.collection_name:
            self.collection_name = self.kb_id


# ---------------------------------------------------------------------------
# IngestionResult
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class IngestionResult:
    """Pipeline ingestion result."""

    success: bool
    blocked: bool = False
    reason: str | None = None
    stage: str | None = None
    chunks_stored: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def success_result(
        cls,
        *,
        chunks_stored: int,
        metadata: dict[str, Any] | None = None,
    ) -> IngestionResult:
        return cls(
            success=True,
            blocked=False,
            chunks_stored=chunks_stored,
            metadata=metadata or {},
        )

    @classmethod
    def failure_result(
        cls,
        *,
        reason: str,
        stage: str,
        metadata: dict[str, Any] | None = None,
    ) -> IngestionResult:
        return cls(
            success=False,
            blocked=False,
            reason=reason,
            stage=stage,
            metadata=metadata or {},
        )
