"""Query Preprocessor.

Purpose:
    Normalize and typo-correct user queries before retrieval/classification.

Features:
    - Whitespace/punctuation normalization.
    - Domain typo map correction (Korean + English operational terms).
    - Optional lightweight fuzzy correction for ASCII tokens.

Usage:
    preprocessor = QueryPreprocessor()
    normalized = preprocessor.preprocess("쿠버네티즈   pod  재시작?")

Examples:
    "쿠버네티즈 pod 재시작" -> "쿠버네티스 pod 재시작"

Extracted from oreo-ecosystem query_preprocessor.py.
"""

from __future__ import annotations

import difflib
import logging
import os
import re
from dataclasses import dataclass

from src.config_weights import weights as _w

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Domain synonyms (extracted from oreo query_rewriter.py)
# ---------------------------------------------------------------------------
DOMAIN_SYNONYMS: dict[str, list[str]] = {
    # Kubernetes
    "k8s": ["kubernetes", "쿠버네티스", "쿠베"],
    "kubernetes": ["k8s", "쿠버네티스", "쿠베"],
    "pod": ["파드", "포드"],
    "deployment": ["디플로이먼트", "배포"],
    "service": ["서비스", "svc"],
    "ingress": ["인그레스"],
    "configmap": ["컨피그맵", "cm"],
    "secret": ["시크릿"],
    "pvc": ["persistent volume claim", "영구볼륨클레임"],
    "hpa": ["horizontal pod autoscaler", "수평 파드 오토스케일러"],
    # Roles
    "담당자": ["담당", "책임자", "관리자", "owner", "오너"],
    "pm": ["프로젝트 매니저", "project manager"],
    "pl": ["프로젝트 리더", "project leader"],
    "devops": ["데브옵스", "dev ops"],
    # Infrastructure
    "aws": ["아마존 웹 서비스", "amazon web services"],
    "ec2": ["이씨투", "ec 2"],
    "s3": ["에스쓰리", "simple storage service"],
    "rds": ["알디에스", "relational database service"],
    "eks": ["elastic kubernetes service"],
    "ecr": ["elastic container registry"],
    # Data
    "dm": ["데이터마트", "data mart", "datamart"],
    "dw": ["데이터웨어하우스", "data warehouse"],
    "etl": ["이티엘", "extract transform load"],
    "bi": ["비아이", "business intelligence"],
    # Systems
    "api": ["에이피아이", "application programming interface"],
    "db": ["데이터베이스", "database"],
    "ci/cd": ["cicd", "지속적 통합 배포"],
    "jenkins": ["젠킨스"],
    "argocd": ["아르고시디", "argo cd"],
    # GS Retail specific
    "miso": ["미소", "미소연동"],
    "oreo": ["오레오"],
    "cvs": ["편의점", "convenience store"],
    "sm": ["슈퍼마켓", "supermarket"],
    "hs": ["홈쇼핑", "home shopping"],
}


@dataclass(frozen=True, slots=True)
class QueryCorrection:
    """Single token-level query correction."""

    original: str
    corrected: str
    reason: str


@dataclass(frozen=True, slots=True)
class PreprocessedQuery:
    """Normalized query output for retrieval/classification stages."""

    original_query: str
    normalized_query: str
    corrected_query: str
    detected_language: str = "unknown"
    corrections: tuple[QueryCorrection, ...] = ()

    @property
    def was_corrected(self) -> bool:
        return self.corrected_query != self.normalized_query


_HANGUL_PATTERN = re.compile(r"[\uac00-\ud7a3]")
_LATIN_PATTERN = re.compile(r"[A-Za-z]")


class QueryPreprocessor:
    """Normalize and typo-correct knowledge search queries."""

    _TOKEN_PATTERN = re.compile(r"[A-Za-z0-9]+|[\uac00-\ud7a3]+")
    _MULTISPACE_PATTERN = re.compile(r"\s+")
    _SPACE_BEFORE_PUNCT_PATTERN = re.compile(r"\s+([,.:;!?])")

    _DEFAULT_TYPO_MAP: dict[str, str] = {
        # Kubernetes / infra domain typos
        "kuberenetes": "kubernetes",
        "kubernates": "kubernetes",
        "kuberentes": "kubernetes",
        "kubernets": "kubernetes",
        "argocdc": "argocd",
        "argoc": "argocd",
        "confluance": "confluence",
        "conflunce": "confluence",
        "jenkin": "jenkins",
        # Korean domain typos
        "쿠버네티즈": "쿠버네티스",
        "큐버네티스": "쿠버네티스",
        "쿠버네테스": "쿠버네티스",
        "쿠베네티스": "쿠버네티스",
        "파드드": "파드",
        "컨플루언스": "콘플루언스",
        "콘플루언쓰": "콘플루언스",
        "쉐어포인트": "셰어포인트",
        "셰어포인": "셰어포인트",
    }

    def __init__(
        self,
        *,
        typo_map: dict[str, str] | None = None,
        fuzzy_enabled: bool | None = None,
        fuzzy_cutoff: float | None = None,
    ) -> None:
        source_typo_map = self._DEFAULT_TYPO_MAP if typo_map is None else typo_map
        self._typo_map = {
            key.lower(): value
            for key, value in source_typo_map.items()
            if key and value
        }

        if fuzzy_enabled is None:
            env_val = os.getenv("KNOWLEDGE_QUERY_PREPROCESS_FUZZY_ENABLED")
            fuzzy_enabled = (
                env_val.lower() == "true" if env_val is not None
                else _w.preprocessor.fuzzy_enabled
            )
        self._fuzzy_enabled = fuzzy_enabled

        if fuzzy_cutoff is None:
            fuzzy_cutoff = self._read_fuzzy_cutoff_env()
        self._fuzzy_cutoff = max(0.0, min(float(fuzzy_cutoff), 1.0))
        self._fuzzy_vocabulary = self._build_fuzzy_vocabulary()

    def preprocess(self, query: str) -> PreprocessedQuery:
        """Normalize and typo-correct a user query."""
        original_query = str(query or "")
        normalized_query = self._normalize_query(original_query)
        if not normalized_query:
            return PreprocessedQuery(
                original_query=original_query,
                normalized_query=normalized_query,
                corrected_query=normalized_query,
                detected_language="unknown",
            )

        corrected_query, corrections = self._apply_token_corrections(normalized_query)
        if corrections:
            logger.info(
                "Knowledge query preprocessed with corrections",
                extra={
                    "original_query_preview": original_query[:120],
                    "normalized_query_preview": normalized_query[:120],
                    "corrected_query_preview": corrected_query[:120],
                    "correction_count": len(corrections),
                },
            )

        return PreprocessedQuery(
            original_query=original_query,
            normalized_query=normalized_query,
            corrected_query=corrected_query,
            detected_language=self._detect_language(corrected_query),
            corrections=tuple(corrections),
        )

    def _normalize_query(self, query: str) -> str:
        text = self._MULTISPACE_PATTERN.sub(" ", str(query or "").strip())
        text = self._SPACE_BEFORE_PUNCT_PATTERN.sub(r"\1", text)
        return text

    def _apply_token_corrections(
        self, normalized_query: str
    ) -> tuple[str, list[QueryCorrection]]:
        parts: list[str] = []
        corrections: list[QueryCorrection] = []
        last_index = 0

        for match in self._TOKEN_PATTERN.finditer(normalized_query):
            parts.append(normalized_query[last_index : match.start()])
            token = match.group(0)
            corrected_token, reason = self._correct_token(token)
            parts.append(corrected_token)
            if corrected_token != token and reason:
                corrections.append(
                    QueryCorrection(
                        original=token,
                        corrected=corrected_token,
                        reason=reason,
                    )
                )
            last_index = match.end()

        parts.append(normalized_query[last_index:])
        return "".join(parts), corrections

    def _correct_token(self, token: str) -> tuple[str, str | None]:
        lowered = token.lower()
        if lowered in self._typo_map:
            return self._typo_map[lowered], "typo_map"

        if not self._fuzzy_enabled:
            return token, None

        # Fuzzy correction is intentionally limited to ASCII words to avoid
        # aggressive Korean token rewrites.
        if not token.isascii() or not token.isalnum() or len(token) < _w.preprocessor.fuzzy_min_token_length:
            return token, None

        matches = difflib.get_close_matches(
            lowered,
            self._fuzzy_vocabulary,
            n=1,
            cutoff=self._fuzzy_cutoff,
        )
        if not matches:
            return token, None

        candidate = matches[0]
        if candidate == lowered:
            return token, None
        return candidate, "fuzzy"

    def _build_fuzzy_vocabulary(self) -> tuple[str, ...]:
        candidates: set[str] = set()
        candidates.update(self._typo_map.values())
        for term, synonyms in DOMAIN_SYNONYMS.items():
            candidates.add(term)
            for synonym in synonyms:
                candidates.add(str(synonym))
        # Fuzzy stage only handles ASCII tokens.
        return tuple(
            sorted(
                item.lower()
                for item in candidates
                if item and item.isascii() and item.replace("/", "").isalnum()
            )
        )

    @staticmethod
    def _read_fuzzy_cutoff_env() -> float:
        raw = os.getenv("KNOWLEDGE_QUERY_PREPROCESS_FUZZY_CUTOFF")
        if raw is None:
            return _w.preprocessor.fuzzy_cutoff
        try:
            parsed = float(raw)
        except (TypeError, ValueError):
            return _w.preprocessor.fuzzy_cutoff
        return max(0.0, min(parsed, 1.0))

    @staticmethod
    def _detect_language(text: str) -> str:
        """Best-effort language detection for Korean/English/mixed queries."""
        content = str(text or "").strip()
        if not content:
            return "unknown"

        hangul_count = len(_HANGUL_PATTERN.findall(content))
        latin_count = len(_LATIN_PATTERN.findall(content))
        alpha_total = hangul_count + latin_count
        if alpha_total <= 0:
            return "other"

        if hangul_count > 0 and latin_count > 0:
            hangul_ratio = hangul_count / alpha_total
            if hangul_ratio >= 0.7:
                return "ko"
            if hangul_ratio <= 0.3:
                return "en"
            return "mixed"
        if hangul_count > 0:
            return "ko"
        return "en"


class NoOpQueryPreprocessor:
    """No-op query preprocessor for tests and controlled fallback usage."""

    def preprocess(self, query: str) -> PreprocessedQuery:
        normalized_query = str(query or "").strip()
        return PreprocessedQuery(
            original_query=str(query or ""),
            normalized_query=normalized_query,
            corrected_query=normalized_query,
            detected_language=QueryPreprocessor._detect_language(normalized_query),
            corrections=(),
        )


__all__ = [
    "DOMAIN_SYNONYMS",
    "NoOpQueryPreprocessor",
    "PreprocessedQuery",
    "QueryCorrection",
    "QueryPreprocessor",
]
