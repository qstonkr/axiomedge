"""Ingestion Gate (Stage 0).

Pre-ingestion quality, security, and metadata validation.
Adapted from oreo-ecosystem IngestionGateService.

Gate policy (from CLAUDE.md):
- Security FAIL (IG-06, IG-07) -> QUARANTINE
- Hard-reject (IG-05, IG-11..14, IG-16, IG-17) -> REJECT
- Core FAIL >= 2 -> REJECT
- Core FAIL == 1 -> HOLD
- Core WARN -> PROCEED (log only, do not block)
- Non-core WARN -> PROCEED

Gates implemented (simplified for local):
- IG-01: Source validation (source_type must be known)
- IG-02: Freshness check (updated_at must exist)
- IG-03: Content validity (min length, not empty)
- IG-04: Lifecycle check (not archived/deleted)
- IG-05: Exact dedup pre-filter (hash check)
- IG-06: File type eligibility (.pdf, .pptx, .docx, .txt, .md, .csv)
- IG-07: Content size limit (from config_weights max_file_size_mb)
- IG-10: Structure quality (has headings/sections for large docs)
- IG-11: Language detection (Korean/English only)
- IG-12: Snippet detection (reject very short content)
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from ..config_weights import weights
from ..domain.models import RawDocument

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

class GateVerdict(str, Enum):
    """Per-check verdict."""
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    SKIP = "skip"


class GateAction(str, Enum):
    """Document-level action."""
    PROCEED = "proceed"
    HOLD = "hold"
    REJECT = "reject"
    QUARANTINE = "quarantine"


@dataclass(frozen=True)
class CheckResult:
    """Single gate check result."""
    check_id: str
    check_name: str
    verdict: GateVerdict
    message: str
    details: dict[str, Any] = field(default_factory=dict)
    duration_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "check_name": self.check_name,
            "verdict": self.verdict.value,
            "message": self.message,
            "details": self.details,
            "duration_ms": round(float(self.duration_ms), 3),
        }


@dataclass
class GateResult:
    """Aggregate gate result."""
    action: GateAction
    checks: list[CheckResult] = field(default_factory=list)
    total_duration_ms: float = 0.0

    @property
    def passed_count(self) -> int:
        return sum(1 for c in self.checks if c.verdict == GateVerdict.PASS)

    @property
    def warned_count(self) -> int:
        return sum(1 for c in self.checks if c.verdict == GateVerdict.WARN)

    @property
    def failed_count(self) -> int:
        return sum(1 for c in self.checks if c.verdict == GateVerdict.FAIL)

    @property
    def is_blocked(self) -> bool:
        return self.action in (GateAction.REJECT, GateAction.QUARANTINE)

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action.value,
            "total_duration_ms": round(float(self.total_duration_ms), 3),
            "passed_count": self.passed_count,
            "warned_count": self.warned_count,
            "failed_count": self.failed_count,
            "checks": [c.to_dict() for c in self.checks],
        }


# ---------------------------------------------------------------------------
# Policy constants
# ---------------------------------------------------------------------------

# Core checks that matter for HOLD/REJECT decisions
CORE_CHECK_IDS: set[str] = {"IG-01", "IG-02", "IG-03", "IG-06", "IG-07", "IG-10", "IG-12"}
# Security hard-block checks -> QUARANTINE
SECURITY_HARD_BLOCK: set[str] = {"IG-06", "IG-07"}
# Hard-reject pre-filters
HARD_REJECT_ON_FAIL: set[str] = {"IG-05", "IG-11", "IG-12", "IG-16", "IG-17"}

# Known source types
KNOWN_SOURCE_TYPES: set[str] = {
    "confluence", "jira", "git", "teams_webhook", "file",
    "sharepoint", "bitbucket", "manual", "crawl",
}

# Eligible file extensions for knowledge ingestion
ELIGIBLE_EXTENSIONS: set[str] = {
    ".pdf", ".pptx", ".docx", ".xlsx", ".txt", ".md", ".csv",
    ".html", ".htm", ".json", ".yaml", ".yml",
}

# Blocked file extensions
BLOCKED_EXTENSIONS: set[str] = {
    ".exe", ".zip", ".tar", ".gz", ".rar", ".7z",
    ".dll", ".so", ".bin", ".iso", ".dmg",
    ".mp3", ".mp4", ".avi", ".mov", ".wav",
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".svg",
}

# Allowed languages
ALLOWED_LANGUAGES: set[str] = {"ko", "en"}

# Deprecated content markers
_DEPRECATED_KEYWORDS = re.compile(
    r"(?:\bDeprecated\b|\bObsolete\b|폐기|중단|사용\s*금지)", re.IGNORECASE
)

# Word pattern for word count
_WORD_RE = re.compile(r"[A-Za-z0-9_]+|[가-힣]+")


# ---------------------------------------------------------------------------
# In-memory dedup index
# ---------------------------------------------------------------------------

class _ExactDedupIndex:
    """In-memory exact dedup index for a single gate run."""

    def __init__(self) -> None:
        self._seen: dict[str, str] = {}  # content_hash -> doc_id

    def check_and_add(self, content_hash: str, doc_id: str) -> tuple[bool, str]:
        """Check if hash seen before and add it.

        Returns:
            (is_duplicate, duplicate_of_doc_id)
        """
        prev = self._seen.get(content_hash)
        if prev is not None:
            return True, prev
        self._seen[content_hash] = doc_id
        return False, ""


# ---------------------------------------------------------------------------
# Individual gate checks
# ---------------------------------------------------------------------------

def _check_ig01_source_validation(document: RawDocument, kb_id: str) -> CheckResult:
    """IG-01: Source validation (source_type must be known)."""
    start = time.perf_counter()
    source_type = (document.metadata.get("source_type", "") or "").strip().lower()

    if not source_type:
        verdict = GateVerdict.FAIL
        msg = "Missing source_type"
    elif source_type in KNOWN_SOURCE_TYPES:
        verdict = GateVerdict.PASS
        msg = "Source type recognized"
    else:
        verdict = GateVerdict.WARN
        msg = f"Unknown source type: {source_type}"

    return CheckResult(
        check_id="IG-01",
        check_name="Source validation",
        verdict=verdict,
        message=msg,
        details={"source_type": source_type},
        duration_ms=(time.perf_counter() - start) * 1000,
    )


def _check_ig02_freshness(document: RawDocument) -> CheckResult:
    """IG-02: Freshness check (updated_at must not be too old)."""
    start = time.perf_counter()

    if document.updated_at is None:
        return CheckResult(
            check_id="IG-02",
            check_name="Freshness check",
            verdict=GateVerdict.WARN,
            message="updated_at missing; freshness cannot be evaluated",
            details={"updated_at": None},
            duration_ms=(time.perf_counter() - start) * 1000,
        )

    try:
        now = datetime.now(timezone.utc)
        updated = document.updated_at
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        days = (now - updated).days
        max_age = weights.quality.stale_threshold_days

        if days > max_age:
            verdict = GateVerdict.FAIL
            msg = f"Document too old ({days}d > {max_age}d)"
        elif days > max_age // 2:
            verdict = GateVerdict.WARN
            msg = f"Document is stale ({days}d)"
        else:
            verdict = GateVerdict.PASS
            msg = "Document freshness acceptable"

        return CheckResult(
            check_id="IG-02",
            check_name="Freshness check",
            verdict=verdict,
            message=msg,
            details={"days_since_update": days, "max_age_days": max_age},
            duration_ms=(time.perf_counter() - start) * 1000,
        )
    except Exception as e:
        return CheckResult(
            check_id="IG-02",
            check_name="Freshness check",
            verdict=GateVerdict.FAIL,
            message=f"Freshness evaluation failed: {e}",
            details={},
            duration_ms=(time.perf_counter() - start) * 1000,
        )


def _check_ig03_content_validity(document: RawDocument) -> CheckResult:
    """IG-03: Content validity (min length, not empty)."""
    start = time.perf_counter()
    content = (document.content or "").strip()
    length = len(content)
    min_length = weights.quality.bronze_min_chars

    if length == 0:
        verdict = GateVerdict.FAIL
        msg = "Content is empty"
    elif length < min_length:
        verdict = GateVerdict.WARN
        msg = f"Content too short ({length} chars < {min_length})"
    else:
        verdict = GateVerdict.PASS
        msg = "Content length acceptable"

    return CheckResult(
        check_id="IG-03",
        check_name="Content validity",
        verdict=verdict,
        message=msg,
        details={"content_length": length, "min_length": min_length},
        duration_ms=(time.perf_counter() - start) * 1000,
    )


def _check_ig04_lifecycle(document: RawDocument) -> CheckResult:
    """IG-04: Lifecycle check (not archived/deleted)."""
    start = time.perf_counter()
    status = (document.metadata.get("status", "") or "").strip().lower()
    content = document.content or ""

    blocked_statuses = {"archived", "deleted", "deprecated", "obsolete", "retired"}

    if status in blocked_statuses:
        verdict = GateVerdict.FAIL
        msg = f"Lifecycle status blocked: {status}"
    elif status in ("under_review", "draft"):
        verdict = GateVerdict.WARN
        msg = f"Lifecycle status: {status}"
    elif _DEPRECATED_KEYWORDS.search(content):
        verdict = GateVerdict.FAIL
        msg = "Deprecated marker detected in content"
    else:
        verdict = GateVerdict.PASS
        msg = "Lifecycle OK"

    return CheckResult(
        check_id="IG-04",
        check_name="Lifecycle check",
        verdict=verdict,
        message=msg,
        details={"lifecycle_status": status},
        duration_ms=(time.perf_counter() - start) * 1000,
    )


def _check_ig05_exact_dedup(
    document: RawDocument,
    dedup_index: _ExactDedupIndex,
) -> CheckResult:
    """IG-05: Exact dedup pre-filter (SHA-256 hash check)."""
    start = time.perf_counter()
    content_hash = document.content_hash or RawDocument.sha256(document.content or "")

    is_dup, dup_of = dedup_index.check_and_add(content_hash, document.doc_id)

    if not is_dup:
        verdict = GateVerdict.PASS
        msg = "No exact duplicate detected"
        details = {"content_hash": content_hash[:16]}
    else:
        verdict = GateVerdict.FAIL
        msg = f"Exact duplicate detected (duplicate_of={dup_of})"
        details = {"content_hash": content_hash[:16], "duplicate_of": dup_of}

    return CheckResult(
        check_id="IG-05",
        check_name="Exact duplicate detection",
        verdict=verdict,
        message=msg,
        details=details,
        duration_ms=(time.perf_counter() - start) * 1000,
    )


def _check_ig06_file_type_eligibility(document: RawDocument) -> CheckResult:
    """IG-06: File type eligibility (.pdf, .pptx, .docx, .txt, .md, .csv etc)."""
    start = time.perf_counter()
    filename = (document.metadata.get("filename", "") or "").strip()

    if not filename:
        # No filename -> probably text/confluence content, pass
        return CheckResult(
            check_id="IG-06",
            check_name="File type eligibility",
            verdict=GateVerdict.PASS,
            message="No filename; assumed text content",
            details={"filename": None},
            duration_ms=(time.perf_counter() - start) * 1000,
        )

    # Extract extension
    ext = ""
    dot_idx = filename.rfind(".")
    if dot_idx >= 0:
        ext = filename[dot_idx:].lower()

    if ext in ELIGIBLE_EXTENSIONS:
        verdict = GateVerdict.PASS
        msg = "File type eligible"
    elif ext in BLOCKED_EXTENSIONS:
        verdict = GateVerdict.FAIL
        msg = f"Blocked file type: {ext}"
    elif ext:
        verdict = GateVerdict.WARN
        msg = f"Unknown file type: {ext}"
    else:
        verdict = GateVerdict.WARN
        msg = "No file extension detected"

    return CheckResult(
        check_id="IG-06",
        check_name="File type eligibility",
        verdict=verdict,
        message=msg,
        details={"filename": filename, "extension": ext},
        duration_ms=(time.perf_counter() - start) * 1000,
    )


def _check_ig07_content_size_limit(document: RawDocument) -> CheckResult:
    """IG-07: Content size limit (from config_weights max_file_size_mb)."""
    start = time.perf_counter()
    max_size_mb = weights.pipeline.max_file_size_mb
    max_size_bytes = max_size_mb * 1024 * 1024

    content_bytes = len((document.content or "").encode("utf-8"))
    file_bytes = document.metadata.get("file_size_bytes", 0) or 0
    actual_size = max(content_bytes, int(file_bytes))
    actual_mb = actual_size / (1024 * 1024)

    if actual_size > max_size_bytes:
        verdict = GateVerdict.FAIL
        msg = f"Content too large ({actual_mb:.1f}MB > {max_size_mb}MB)"
    else:
        verdict = GateVerdict.PASS
        msg = f"Content size acceptable ({actual_mb:.1f}MB)"

    return CheckResult(
        check_id="IG-07",
        check_name="Content size limit",
        verdict=verdict,
        message=msg,
        details={"size_bytes": actual_size, "max_size_mb": max_size_mb},
        duration_ms=(time.perf_counter() - start) * 1000,
    )


def _check_ig10_structure_quality(document: RawDocument) -> CheckResult:
    """IG-10: Structure quality (headings/sections for large docs)."""
    start = time.perf_counter()
    content = (document.content or "").strip()
    length = len(content)
    words = _WORD_RE.findall(content)
    word_count = len(words)
    min_content_length = weights.quality.silver_min_chars

    has_headers = bool(re.search(r"^#{1,6}\s+", content, re.MULTILINE))
    has_tables = "|" in content and "\n" in content
    paragraphs = [p for p in re.split(r"\n\s*\n+", content) if p.strip()]

    if length < 25:
        verdict = GateVerdict.FAIL
        msg = "Content too short / empty"
    elif length < min_content_length or word_count < 10:
        verdict = GateVerdict.WARN
        msg = "Weak structure (short content or low word count)"
    else:
        verdict = GateVerdict.PASS
        msg = "Structure metrics OK"

    return CheckResult(
        check_id="IG-10",
        check_name="Structure quality",
        verdict=verdict,
        message=msg,
        details={
            "content_length": length,
            "word_count": word_count,
            "paragraph_count": len(paragraphs),
            "has_headers": has_headers,
            "has_tables": has_tables,
        },
        duration_ms=(time.perf_counter() - start) * 1000,
    )


def _check_ig11_language_detection(document: RawDocument) -> CheckResult:
    """IG-11: Language detection (Korean/English only)."""
    start = time.perf_counter()
    content = (document.content or "").strip()

    if len(content) < 20:
        return CheckResult(
            check_id="IG-11",
            check_name="Language detection",
            verdict=GateVerdict.SKIP,
            message="Content too short for language detection",
            details={"content_length": len(content)},
            duration_ms=(time.perf_counter() - start) * 1000,
        )

    lang = None
    lang_err = None
    try:
        from langdetect import detect  # type: ignore[import-not-found]
        lang = detect(content[:2000])
    except ImportError:
        # langdetect not installed - use heuristic
        korean_chars = len(re.findall(r"[가-힣]", content[:2000]))
        ascii_chars = len(re.findall(r"[A-Za-z]", content[:2000]))
        if korean_chars > ascii_chars:
            lang = "ko"
        elif ascii_chars > 0:
            lang = "en"
        else:
            lang = "unknown"
    except Exception as e:
        lang_err = str(e)

    if lang is None:
        verdict = GateVerdict.WARN
        msg = f"Language detection failed: {lang_err}"
    elif lang.lower() in ALLOWED_LANGUAGES:
        verdict = GateVerdict.PASS
        msg = f"Language allowed: {lang}"
    else:
        verdict = GateVerdict.FAIL
        msg = f"Language not allowed: {lang}"

    return CheckResult(
        check_id="IG-11",
        check_name="Language detection",
        verdict=verdict,
        message=msg,
        details={"language": lang, "language_error": lang_err},
        duration_ms=(time.perf_counter() - start) * 1000,
    )


def _check_ig12_snippet_detection(document: RawDocument) -> CheckResult:
    """IG-12: Snippet detection (reject very short content < min_content_length)."""
    start = time.perf_counter()
    content = (document.content or "").strip()
    length = len(content)
    min_length = weights.quality.min_content_length

    if length < min_length:
        verdict = GateVerdict.FAIL
        msg = f"Noise content (below min length: {length} < {min_length})"
    else:
        verdict = GateVerdict.PASS
        msg = "Content length sufficient"

    return CheckResult(
        check_id="IG-12",
        check_name="Snippet detection",
        verdict=verdict,
        message=msg,
        details={"content_length": length, "min_content_length": min_length},
        duration_ms=(time.perf_counter() - start) * 1000,
    )


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

class IngestionGate:
    """Ingestion gate orchestrator.

    Runs all gate checks on a document and returns an aggregate action.
    """

    def __init__(self, enabled: bool = True) -> None:
        self._enabled = enabled
        self._dedup_index = _ExactDedupIndex()

    def run_gates(self, document: RawDocument, kb_id: str) -> GateResult:
        """Run all ingestion gates on a document.

        Args:
            document: Raw document to validate
            kb_id: Knowledge base ID

        Returns:
            GateResult with action (PROCEED/HOLD/REJECT/QUARANTINE) and check details
        """
        if not self._enabled:
            return GateResult(
                action=GateAction.PROCEED,
                checks=[],
                total_duration_ms=0.0,
            )

        start_total = time.perf_counter()
        checks: list[CheckResult] = []

        # Run all checks
        checks.append(_check_ig01_source_validation(document, kb_id))
        checks.append(_check_ig02_freshness(document))
        checks.append(_check_ig03_content_validity(document))
        checks.append(_check_ig04_lifecycle(document))
        checks.append(_check_ig05_exact_dedup(document, self._dedup_index))
        checks.append(_check_ig06_file_type_eligibility(document))
        checks.append(_check_ig07_content_size_limit(document))
        checks.append(_check_ig10_structure_quality(document))
        checks.append(_check_ig11_language_detection(document))
        checks.append(_check_ig12_snippet_detection(document))

        action = self._decide_action(checks)
        total_ms = (time.perf_counter() - start_total) * 1000

        return GateResult(
            action=action,
            checks=checks,
            total_duration_ms=total_ms,
        )

    def _decide_action(self, checks: list[CheckResult]) -> GateAction:
        """Decide document-level action based on check results.

        Policy:
        - Security FAIL (IG-06, IG-07) -> QUARANTINE
        - Hard-reject pre-filters -> REJECT
        - Core FAIL >= 2 -> REJECT
        - Core FAIL == 1 -> HOLD
        - Core WARN -> PROCEED (log only)
        - Non-core WARN -> PROCEED
        """
        # Security hard-block
        security_failed = [
            c for c in checks
            if c.check_id in SECURITY_HARD_BLOCK and c.verdict == GateVerdict.FAIL
        ]
        if security_failed:
            return GateAction.QUARANTINE

        # Hard-reject pre-filters
        hard_reject = [
            c for c in checks
            if c.check_id in HARD_REJECT_ON_FAIL and c.verdict == GateVerdict.FAIL
        ]
        if hard_reject:
            return GateAction.REJECT

        # Core checks
        core_fail = [
            c for c in checks
            if c.check_id in CORE_CHECK_IDS and c.verdict == GateVerdict.FAIL
        ]

        if core_fail:
            # Core fail count >= 2 -> reject; else hold
            return GateAction.REJECT if len(core_fail) >= 2 else GateAction.HOLD

        # Core WARN -> PROCEED (log only, do not block)
        # Non-core WARN -> PROCEED
        return GateAction.PROCEED

    def reset_dedup_index(self) -> None:
        """Reset the in-memory dedup index (call between ingestion runs)."""
        self._dedup_index = _ExactDedupIndex()
