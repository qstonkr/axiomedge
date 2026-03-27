"""Term Extraction during Ingestion.

Extracts domain terms from document chunks using NLP pattern matching.
Extracted terms are scoped to KB level (scope="kb"), NOT global.
During extraction, terms already in global scope are skipped.

Patterns:
- CamelCase (e.g., KnowledgeBase, GraphRAG)
- ACRONYM (e.g., KB, RAG, LLM, API)
- Hyphenated (e.g., micro-service, multi-tenant)
- Korean technical (e.g., 지식베이스, 벡터검색)
- Mixed Korean-English (e.g., K8s클러스터, Redis캐시)

Noise filters:
- CSS property artifacts (border-radius, flex-direction, etc.)
- Code fragments (permission strings, exception classes, MIME types)

Extracted from oreo-ecosystem glossary_extraction_service.py + term_extractor.py.

Usage:
    extractor = TermExtractor(glossary_repo=my_repo)
    terms = await extractor.extract_from_chunks(chunks, kb_id="my-kb")
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Pre-compiled whitespace normalization pattern
_WHITESPACE_RE = re.compile(r"\s+")


# ==========================================================================
# NLP Patterns
# ==========================================================================

# CamelCase (e.g., KnowledgeBase, GraphRAG)
CAMEL_CASE_PATTERN = re.compile(r"\b([A-Z][a-z]+(?:[A-Z][a-z]+)+)\b")

# ACRONYM (e.g., KB, RAG, LLM, API) -- 2-6 uppercase letters
ACRONYM_PATTERN = re.compile(r"\b([A-Z]{2,6})\b")

# Hyphenated (e.g., micro-service, multi-tenant)
HYPHENATED_PATTERN = re.compile(r"\b([a-z]+(?:-[a-z]+)+)\b", re.IGNORECASE)

# Korean technical terms (e.g., 지식베이스, 벡터검색)
KOREAN_TECH_PATTERN = re.compile(
    r"[\uac00-\ud7a3]{2,8}(?:베이스|시스템|서비스|검색|처리|관리)"
)

# Mixed Korean-English (e.g., K8s클러스터, Redis캐시)
MIXED_PATTERN = re.compile(r"[A-Za-z0-9]+[\uac00-\ud7a3]{2,}")


# ==========================================================================
# Noise Filters
# ==========================================================================

# CSS property prefixes -- hyphenated terms starting with these are artifacts
_CSS_PREFIXES = frozenset({
    "border", "margin", "padding", "font", "text", "background", "display",
    "flex", "grid", "align", "justify", "overflow", "position", "transform",
    "transition", "animation", "opacity", "visibility", "cursor", "outline",
    "box", "line", "letter", "word", "white", "list", "table", "vertical",
    "max", "min", "content", "counter", "resize", "user", "object",
    "pointer", "scroll", "clip", "filter", "mix", "backdrop", "isolation",
    "break", "page", "column", "gap", "place", "prefers",
})

# Code / technical noise regex patterns
_CODE_NOISE_RE = re.compile(
    r"(?:"
    r"^[d\-][rwx\-]{8,}$"        # Unix permissions: drwxr-xr-x
    r"|Exception$"                 # Java/Python exception classes
    r"|Error$"                     # Error classes
    r"|^x-www-"                    # HTTP content type headers
    r"|^[yYMmdDHhSs]{2,}[-/.][yYMmdDHhSs]"  # Date format strings
    r"|^application[-/]"           # MIME types
    r"|^content[-/]"               # HTTP headers
    r"|^text[-/]"                  # MIME types
    r")",
    re.IGNORECASE,
)

# Korean particles (조사) -- ordered long-to-short for greedy stripping
_KOREAN_PARTICLES_LONG = [
    "에서", "으로", "까지", "부터", "처럼", "같이", "에게", "한테", "보다"
]
_KOREAN_PARTICLES_SHORT = [
    "가", "를", "에", "의", "는", "은", "도", "와", "과", "이", "로", "만", "서"
]

# Boundary particle between English prefix and Korean body
_BOUNDARY_PARTICLE_RE = re.compile(
    r"^([A-Za-z0-9]+)"
    r"([가를에의는은도와과이])"
    r"([\uac00-\ud7a3]{2,})"
)

# Known acronyms to keep (not filtered as noise)
_KNOWN_ACRONYMS = frozenset({
    "API", "KB", "RAG", "LLM", "OFC", "DW", "FAQ", "GS", "CU",
    "POS", "ERP", "CRM", "HR", "IT", "AI", "ML", "DB", "SQL",
    "VM", "VPN", "SSL", "DNS", "CDN", "AWS", "GCP", "K8S",
    "CI", "CD", "QA", "UAT", "SLA", "KPI", "ROI", "MOU",
    "B2B", "B2C", "DM", "PM", "SM", "BI", "ETL", "RPA",
})

# English stop words for foreign token (SL) filtering
_ENGLISH_STOP = frozenset({
    "the", "and", "for", "with", "from", "this", "that", "have", "has",
    "are", "was", "were", "been", "will", "would", "could", "should",
    "not", "but", "can", "all", "any", "some", "more", "most", "other",
    "new", "old", "first", "last", "long", "great", "own", "same",
    "home", "page", "status", "type", "name", "data", "info", "list",
    "item", "value", "null", "true", "false", "none", "file", "test",
    "image", "text", "http", "https", "html", "json", "xml", "css",
    "div", "span", "class", "style", "width", "height", "color",
    "size", "title", "date", "time", "user", "admin", "server",
    "error", "success", "result", "total", "count", "index", "table",
})

# Default stop terms
_STOP_TERMS = frozenset({
    "the", "and", "for", "with", "from", "this", "that", "have", "has",
    "are", "was", "were", "been", "being", "will", "would", "could", "should",
    "not", "but", "can", "all", "any", "some", "more", "most", "other",
    "new", "old", "first", "last", "long", "great", "little", "own", "same",
    # Korean stopwords
    "있다", "하다", "되다", "이다", "없다", "않다", "같다", "보다", "위해",
    "통해", "대해", "따라", "경우", "때문", "이후", "이전", "현재", "내용",
})


# ==========================================================================
# Helpers
# ==========================================================================


def _is_noise_term(term: str, tag_type: str) -> bool:
    """Comprehensive noise filter for extracted terms.

    Consolidates all filtering rules discovered during DB cleanup:
    - Special chars, pure numbers, code patterns
    - SQL aliases (A.ACCM), camelCase (errorCode), UPPER_SNAKE (MAX_SIZE)
    - Short/generic English, pure ASCII compounds
    """
    # Length check
    if len(term) < 2:
        return True
    # Single-char Korean noun
    if len(term) == 1:
        return True
    # Repeated characters (손손, ㅋㅋㅋ)
    if len(set(term)) <= 1:
        return True
    # Special char prefix/suffix
    if term[0] in "#*@\\/$`~^:=+" or term[-1] in "#*@\\/$`~^":
        return True
    # Contains dots (SQL alias: A.ACCM, package: org.xxx)
    if "." in term:
        return True
    # Contains slash, comma, underscore (code patterns)
    if any(c in term for c in "/,_"):
        return True
    # Pure numbers or starts with digit (03월, 10시, 0원)
    if term[0].isdigit():
        return True
    stripped = term.strip(".-+%,")
    if stripped.isdigit():
        return True
    # Code/SQL/URL patterns (brackets, quotes, etc.)
    if any(c in term for c in "{}()[]\"'=<>;|"):
        return True
    # Too many special chars (>30% non-alnum, non-Korean)
    alnum_count = sum(1 for c in term if c.isalnum() or '\uac00' <= c <= '\ud7a3')
    if len(term) > 3 and alnum_count / len(term) < 0.7:
        return True
    # Pure ASCII: various code patterns
    if term.isascii():
        # Pure English ≤10 chars (Start, Query, bye) — no Korean mixed
        if term.isalpha() and len(term) <= 10:
            return True
        # camelCase (errorCode, requestId)
        if term[0].islower() and any(c.isupper() for c in term[1:]):
            return True
        # UPPER_SNAKE (MAX_SIZE) — already caught by underscore above
        # English + numbers only (BCRMA000, batch001)
        if term.isalnum() and not any('\uac00' <= c <= '\ud7a3' for c in term):
            return True
    # Foreign tokens (SL): additional filters
    if tag_type == "foreign":
        if len(term) < 3:
            return True
        if len(term) <= 4 and term.islower():
            return True
        if term.lower() in _ENGLISH_STOP:
            return True
        if len(term) <= 3 and term.isupper() and term not in _KNOWN_ACRONYMS:
            return True
    # Compound nouns: skip if all-ASCII (code concatenation)
    if tag_type == "compound_noun" and term.isascii() and term.isalpha():
        return True
    # Korean stopwords
    if term in TermExtractor._STOP_NOUNS:
        return True
    # Code artifact
    if _is_code_artifact(term):
        return True
    return False


def _is_synonym_noise(term: str) -> bool:
    """Check if a term is OCR/code noise for synonym discovery."""
    if not term:
        return True
    # Special char prefix
    if term[0] in "#*@\\/$`~^|{}<>":
        return True
    # Pure numbers
    if term.strip(".-+%,").isdigit():
        return True
    # Low alphanumeric ratio (>3 chars, <60% alnum+Korean)
    if len(term) > 3:
        alnum = sum(1 for c in term if c.isalnum() or '\uac00' <= c <= '\ud7a3')
        if alnum / len(term) < 0.6:
            return True
    return False


def _is_code_artifact(term: str) -> bool:
    """Check if term is a code/CSS/technical noise artifact."""
    if "-" in term:
        first_part = term.split("-")[0].lower()
        if first_part in _CSS_PREFIXES:
            return True
    return bool(_CODE_NOISE_RE.search(term))


def _strip_korean_particles(term: str) -> str:
    """Strip Korean particles (조사) from mixed Korean-English terms.

    Examples:
        GS가노출여부를 -> GS노출여부
        MD기획팀에 -> MD기획팀
    """
    # 1. Strip trailing particles
    changed = True
    while changed:
        changed = False
        for p in _KOREAN_PARTICLES_LONG:
            if term.endswith(p) and len(term) > len(p) + 2:
                term = term[: -len(p)]
                changed = True
                break
        if not changed:
            for p in _KOREAN_PARTICLES_SHORT:
                if term.endswith(p) and len(term) > len(p) + 2:
                    term = term[: -len(p)]
                    changed = True
                    break

    # 2. Strip particle at English-Korean boundary
    m = _BOUNDARY_PARTICLE_RE.match(term)
    if m:
        term = m.group(1) + m.group(3)

    return term


# ==========================================================================
# Data classes
# ==========================================================================


@dataclass
class ExtractedTerm:
    """A term extracted from chunk content."""

    term: str
    pattern_type: str  # camel_case, acronym, hyphenated, korean, mixed
    occurrences: int = 1
    contexts: list[str] = field(default_factory=list)
    category: str | None = None


# ==========================================================================
# Glossary Repository Protocol (duck-typed)
# ==========================================================================


class IGlossaryRepo:
    """Minimal glossary repository interface for global term lookup."""

    async def get_by_term(self, kb_id: str, term: str) -> dict[str, Any] | None:
        """Return term dict if exists (check scope='global')."""
        ...

    async def save(self, term_data: dict[str, Any]) -> None:
        """Save a term."""
        ...


# ==========================================================================
# TermExtractor
# ==========================================================================


class TermExtractor:
    """Extract domain terms from chunk content and store with scope='kb'.

    Uses KiwiPy morphological analysis to extract meaningful nouns and
    compound terms, with noise filtering. Falls back to regex patterns
    if KiwiPy is unavailable.

    During extraction:
    - Skip terms that already exist as scope='global' in the glossary.
    - Store extracted terms with scope='kb' and the source kb_id.
    """

    # Minimum occurrences to consider a term valid
    MIN_OCCURRENCES = 3

    # Minimum term length (chars)
    MIN_TERM_LENGTH = 2

    # Noun POS tags to extract from KiwiPy
    _NOUN_TAGS = frozenset({"NNG", "NNP"})  # 일반명사, 고유명사
    _FOREIGN_TAG = "SL"  # 외국어 (ESPA, OFC, GS25 등)

    # Single-char nouns to skip (too generic)
    _SKIP_SINGLE_NOUNS = frozenset({
        "것", "수", "등", "중", "때", "곳", "점", "건", "번", "일",
        "금", "명", "권", "부", "용", "내", "외", "상", "하", "전",
        "후", "간", "량", "율", "도", "망", "실", "손", "원",
    })

    # Generic nouns to skip (high frequency, low domain value)
    _STOP_NOUNS = frozenset({
        "경우", "내용", "사항", "관련", "기준", "사용", "이용", "확인",
        "처리", "진행", "안내", "문의", "요청", "변경", "등록", "삭제",
        "조회", "입력", "설정", "관리", "운영", "적용", "발생", "필요",
        "가능", "완료", "시작", "종료", "대상", "방법", "절차", "결과",
        "현황", "정보", "데이터", "시스템", "서비스", "기능", "항목",
        "화면", "페이지", "버튼", "메뉴", "탭", "표시", "출력",
    })

    # Pre-built regex pattern list (fallback when KiwiPy unavailable)
    _PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
        (CAMEL_CASE_PATTERN, "camel_case"),
        (ACRONYM_PATTERN, "acronym"),
        (HYPHENATED_PATTERN, "hyphenated"),
        (KOREAN_TECH_PATTERN, "korean"),
        (MIXED_PATTERN, "mixed"),
    )

    def __init__(
        self,
        glossary_repo: Any | None = None,
        min_occurrences: int | None = None,
    ) -> None:
        self._glossary_repo = glossary_repo
        self._min_occurrences = min_occurrences or self.MIN_OCCURRENCES
        self._kiwi = None
        self._kiwi_available: bool | None = None  # None = not checked yet

    def _get_kiwi(self):
        """Lazy-load KiwiPy tokenizer."""
        if self._kiwi_available is None:
            try:
                from kiwipiepy import Kiwi
                self._kiwi = Kiwi()
                self._kiwi_available = True
                logger.info("KiwiPy loaded for term extraction")
            except ImportError:
                self._kiwi_available = False
                logger.warning("KiwiPy not available, using regex fallback")
        return self._kiwi

    async def extract_from_chunks(
        self,
        chunks: list[str],
        *,
        kb_id: str,
    ) -> list[ExtractedTerm]:
        """Extract terms from a list of chunk texts.

        Uses KiwiPy morphological analysis (NNG/NNP + compound noun merging)
        with fallback to regex patterns if KiwiPy is unavailable.

        Args:
            chunks: List of chunk text strings.
            kb_id: KB identifier to scope the extracted terms.

        Returns:
            List of extracted terms (filtered, de-duped, scope='kb').
        """
        term_counter: Counter[str] = Counter()
        term_types: dict[str, str] = {}
        term_contexts: dict[str, list[str]] = {}
        original_case: dict[str, str] = {}

        kiwi = self._get_kiwi()
        use_kiwi = kiwi is not None

        for chunk_text in chunks:
            if not chunk_text:
                continue
            if use_kiwi:
                self._extract_terms_kiwi(
                    chunk_text, kiwi, term_counter, term_types,
                    term_contexts, original_case,
                )
            else:
                self._extract_patterns(
                    chunk_text, term_counter, term_types,
                    term_contexts, _original_case=original_case,
                )

        candidates = [
            ExtractedTerm(
                term=original_case.get(term, term),
                pattern_type=term_types.get(term, "unknown"),
                occurrences=count,
                contexts=term_contexts.get(term, [])[:3],
            )
            for term, count in term_counter.most_common()
            if count >= self._min_occurrences
        ]

        # Filter out global terms
        if self._glossary_repo:
            candidates = await self._filter_global_terms(candidates, kb_id)

        logger.info(
            "Term extraction (%s): %d candidates from %d chunks (kb_id=%s)",
            "kiwi" if use_kiwi else "regex",
            len(candidates), len(chunks), kb_id,
        )
        return candidates

    def _extract_terms_kiwi(
        self,
        text: str,
        kiwi: Any,
        counter: Counter[str],
        types: dict[str, str],
        contexts: dict[str, list[str]],
        original_case: dict[str, str],
    ) -> None:
        """Extract terms using KiwiPy morphological analysis.

        Strategy:
        1. Tokenize with KiwiPy
        2. Extract NNG (common nouns), NNP (proper nouns), SL (foreign words)
        3. Merge adjacent nouns into compound terms (경영+주→경영주, 정산+금→정산금)
        4. Filter single-char nouns and stopwords
        """
        try:
            tokens = kiwi.tokenize(text)
        except Exception:
            return

        # Pass 1: Merge adjacent noun tokens into compound terms
        # Rules:
        # - Max 3 tokens per compound (avoid 담배권망실점포)
        # - Max 8 chars per compound
        # - SL (foreign) tokens stand alone unless adjacent to SL/SN
        # - NNP (proper nouns) stand alone (사람 이름 분리)
        _MAX_COMPOUND_TOKENS = 3
        _MAX_COMPOUND_CHARS = 8

        compounds: list[tuple[str, str]] = []  # (term, tag_type)
        buf_forms: list[str] = []
        buf_tags: list[str] = []

        def _flush():
            if not buf_forms:
                return
            merged = "".join(buf_forms)
            has_nnp = any(t == "NNP" for t in buf_tags)
            has_sl = any(t == self._FOREIGN_TAG for t in buf_tags)

            # Emit compound (only if all parts are 2+ chars)
            all_parts_valid = all(len(f) >= 2 for f in buf_forms)
            if len(buf_forms) > 1 and len(merged) <= _MAX_COMPOUND_CHARS and all_parts_valid:
                tag = "compound_noun"
                compounds.append((merged, tag))
            # Always emit individual meaningful tokens
            for form, t in zip(buf_forms, buf_tags):
                if len(form) < self.MIN_TERM_LENGTH:
                    continue
                if t == "NNP":
                    compounds.append((form, "proper_noun"))
                elif t == self._FOREIGN_TAG:
                    compounds.append((form, "foreign"))
                elif t == "NNG":
                    compounds.append((form, "noun"))
            buf_forms.clear()
            buf_tags.clear()

        for token in tokens:
            tag = token.tag
            if tag in self._NOUN_TAGS or tag == self._FOREIGN_TAG:
                # NNP (proper nouns like 김철수) break compound
                if tag == "NNP" and buf_forms:
                    _flush()
                    buf_forms.append(token.form)
                    buf_tags.append(tag)
                    _flush()
                # SL (foreign) only compounds with adjacent SL
                elif tag == self._FOREIGN_TAG and buf_forms and buf_tags[-1] != self._FOREIGN_TAG:
                    _flush()
                    buf_forms.append(token.form)
                    buf_tags.append(tag)
                # Max compound size
                elif len(buf_forms) >= _MAX_COMPOUND_TOKENS:
                    _flush()
                    buf_forms.append(token.form)
                    buf_tags.append(tag)
                else:
                    buf_forms.append(token.form)
                    buf_tags.append(tag)
            else:
                _flush()
        _flush()

        # Pass 2: Filter and count
        for term, tag_type in compounds:
            if _is_noise_term(term, tag_type):
                continue

            count_key = term.lower()
            counter[count_key] += 1
            types.setdefault(count_key, tag_type)
            if count_key not in original_case:
                original_case[count_key] = term
            if count_key not in contexts:
                contexts[count_key] = []
            if len(contexts[count_key]) < 3:
                ctx = self._extract_context(text, term)
                if ctx:
                    contexts[count_key].append(ctx)

    async def save_extracted_terms(
        self,
        terms: list[ExtractedTerm],
        *,
        kb_id: str,
    ) -> int:
        """Save extracted terms to glossary with scope='kb'.

        Args:
            terms: Extracted terms to save.
            kb_id: KB identifier.

        Returns:
            Number of terms saved.
        """
        if not self._glossary_repo or not terms:
            return 0

        saved = 0

        # Batch dedup check: gather all existing-term lookups in parallel
        get_fn = getattr(self._glossary_repo, "get_by_term", None)
        existing_flags: list[bool] = []
        if get_fn and callable(get_fn):
            async def _exists(t: str) -> bool:
                try:
                    result = await get_fn(kb_id, t)
                    return result is not None
                except Exception:
                    return False
            existing_flags = await asyncio.gather(*[_exists(t.term) for t in terms])
        else:
            existing_flags = [False] * len(terms)

        for idx, term in enumerate(terms):
            if existing_flags[idx]:
                logger.debug("Skipping duplicate term '%s' for kb_id=%s", term.term, kb_id)
                continue
            try:
                term_data = {
                    "id": str(uuid.uuid4()),
                    "kb_id": kb_id,
                    "term": term.term,
                    "definition": "",  # placeholder - needs LLM enrichment
                    "source": "auto_extracted",
                    "status": "pending",
                    "occurrence_count": term.occurrences,
                    "category": term.category or term.pattern_type,
                    "scope": "kb",
                    "term_type": "term",  # auto-extracted = always "term" (words are CSV-import only)
                    "source_kb_ids": [kb_id],
                    "synonyms": [],
                    "abbreviations": [],
                    "related_terms": [],
                }
                await self._glossary_repo.save(term_data)
                saved += 1
            except Exception as exc:
                logger.debug(
                    "Failed to save term '%s': %s", term.term, exc
                )

        logger.info("Saved %d/%d terms for kb_id=%s", saved, len(terms), kb_id)
        return saved

    # -- Pattern extraction ------------------------------------------------

    def _extract_patterns(
        self,
        text: str,
        counter: Counter[str],
        types: dict[str, str],
        contexts: dict[str, list[str]],
        _original_case: dict[str, str] | None = None,
    ) -> None:
        """Extract all pattern types from text."""
        if _original_case is None:
            _original_case = {}

        for pattern, pattern_type in self._PATTERNS:
            for match in pattern.finditer(text):
                raw_term = match.group().strip()

                # Apply noise filter
                if _is_code_artifact(raw_term):
                    continue

                # Strip Korean particles for mixed terms
                if pattern_type == "mixed":
                    raw_term = _strip_korean_particles(raw_term)

                # Minimum length check
                if len(raw_term) < 2:
                    continue

                # Stop term check
                if raw_term.lower() in _STOP_TERMS:
                    continue

                # Keep original casing for the term (preserves acronyms like KB, RAG)
                canonical_term = raw_term
                # Use lowercased version only for counting/dedup
                count_key = raw_term.lower()
                counter[count_key] += 1
                types.setdefault(count_key, pattern_type)
                # Store original-cased canonical form (first occurrence wins)
                if count_key not in _original_case:
                    _original_case[count_key] = canonical_term

                # Collect context (surrounding text)
                if count_key not in contexts:
                    contexts[count_key] = []
                if len(contexts[count_key]) < 3:
                    ctx = self._extract_context(text, raw_term)
                    if ctx:
                        contexts[count_key].append(ctx)

    def _extract_context(
        self,
        content: str,
        term: str,
        context_size: int = 50,
    ) -> str | None:
        """Extract surrounding context for a term."""
        try:
            idx = content.lower().find(term.lower())
            if idx == -1:
                return None
            start = max(0, idx - context_size)
            end = min(len(content), idx + len(term) + context_size)
            context = _WHITESPACE_RE.sub(" ", content[start:end].strip())
            return f"...{context}..."
        except Exception:
            return None

    # -- Synonym discovery --------------------------------------------------

    # Regex patterns for Korean synonym detection
    # Pattern 1: term(synonym) or synonym(term)
    _PAREN_PATTERN = re.compile(r'(\S+)\s*[\(（]([^)）]+)[\)）]')
    # Pattern 2: "일명", "또는", "혹은", "즉"
    _AKA_PATTERN = re.compile(
        r'(\S+)\s*[,，]\s*(?:일명|또는|혹은|즉|일컫는|이른바)\s+(\S+)'
    )
    # Pattern 3: "이하 term"
    _ABBREV_INTRO_PATTERN = re.compile(
        r'(\S+)\s*[\(（]이하\s+([^)）]+)[\)）]'
    )
    # Pattern 4: "라고도 불리는", "로도 알려진"
    _ALSO_KNOWN_PATTERN = re.compile(
        r'(\S+)[이가]?\s*(?:라고도\s*불리는|로도\s*알려진|이라고도\s*하는)\s+(\S+)'
    )

    _SYNONYM_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
        (_ABBREV_INTRO_PATTERN, "abbreviation_intro"),
        (_PAREN_PATTERN, "parenthetical"),
        (_AKA_PATTERN, "aka"),
        (_ALSO_KNOWN_PATTERN, "also_known_as"),
    )

    async def discover_synonyms(
        self,
        text: str,
        known_terms: list[dict[str, Any]],
    ) -> list[tuple[str, str, str]]:
        """Discover synonym candidates from text context.

        Patterns to detect:
        1. Parenthetical: "K8s(쿠버네티스)" or "쿠버네티스(K8s)"
        2. Aka pattern: "K8s, 일명 쿠버네티스" or "K8s 또는 쿠버네티스"
        3. Abbreviation intro: "Kubernetes(이하 K8s)" or "데이터마트(이하 DM)"
        4. Also-known-as: "K8s라고도 불리는 쿠버네티스"

        Args:
            text: Full document text to scan.
            known_terms: List of glossary term dicts with at least "term" and
                optionally "synonyms" keys, used to match discovered pairs.

        Returns:
            List of (term, synonym, pattern_type) tuples.
        """
        if not text:
            return []

        # Build lookup set of known term strings (lowercased)
        known_set: set[str] = set()
        for kt in known_terms:
            t = kt.get("term", "")
            if t:
                known_set.add(t.lower())
            for s in kt.get("synonyms", []):
                if s:
                    known_set.add(s.lower())

        discovered: list[tuple[str, str, str]] = []
        seen_pairs: set[tuple[str, str]] = set()

        for pattern, pattern_type in self._SYNONYM_PATTERNS:
            for match in pattern.finditer(text):
                term_a = match.group(1).strip()
                term_b = match.group(2).strip()

                # Skip very short or noise matches
                if len(term_a) < 2 or len(term_b) < 2:
                    continue
                if _is_code_artifact(term_a) or _is_code_artifact(term_b):
                    continue
                # Skip if either side is too long (OCR sentence fragments)
                if len(term_a) > 30 or len(term_b) > 30:
                    continue
                # Skip noisy terms (special char prefix, pure numbers, low alnum ratio)
                if _is_synonym_noise(term_a) or _is_synonym_noise(term_b):
                    continue

                # Determine which is the base term and which is the synonym.
                # Prefer the one that already exists in the glossary as base.
                a_known = term_a.lower() in known_set
                b_known = term_b.lower() in known_set

                if a_known and not b_known:
                    base, syn = term_a, term_b
                elif b_known and not a_known:
                    base, syn = term_b, term_a
                else:
                    # Neither or both known -- use the first as base
                    base, syn = term_a, term_b

                pair_key = (base.lower(), syn.lower())
                if pair_key in seen_pairs or base.lower() == syn.lower():
                    continue
                seen_pairs.add(pair_key)
                discovered.append((base, syn, pattern_type))

        logger.info(
            "Synonym discovery: found %d candidates from %d chars",
            len(discovered), len(text),
        )
        return discovered

    async def save_discovered_synonyms(
        self,
        discoveries: list[tuple[str, str, str]],
        *,
        kb_id: str,
    ) -> int:
        """Save discovered synonym relationships to the glossary.

        For each (base_term, synonym, pattern_type):
        - If base_term exists in glossary, add synonym to its synonyms list
          with status="pending" for admin review.
        - If base_term does not exist, create a new pending term record with
          the synonym relationship stored.

        Args:
            discoveries: List of (term, synonym, pattern_type) tuples.
            kb_id: Knowledge base identifier.

        Returns:
            Number of synonym records saved.
        """
        if not self._glossary_repo or not discoveries:
            return 0

        saved = 0
        for base_term, synonym, pattern_type in discoveries:
            try:
                # Check if base term exists in glossary
                existing = None
                get_fn = getattr(self._glossary_repo, "get_by_term", None)
                if get_fn and callable(get_fn):
                    existing = await get_fn(kb_id, base_term)
                    if not existing:
                        existing = await get_fn("all", base_term)

                if existing:
                    # Add synonym to existing term (only if not already present)
                    current_synonyms = existing.get("synonyms", [])
                    if synonym not in current_synonyms:
                        current_synonyms.append(synonym)
                        await self._glossary_repo.save({
                            "id": existing["id"],
                            "kb_id": existing["kb_id"],
                            "term": existing["term"],
                            "synonyms": current_synonyms,
                        })
                        saved += 1
                else:
                    # Create a new discovered-synonym record for admin review
                    await self._glossary_repo.save({
                        "id": str(uuid.uuid4()),
                        "kb_id": kb_id,
                        "term": synonym,
                        "definition": f"Auto-discovered synonym of '{base_term}' ({pattern_type})",
                        "source": "auto_discovered",
                        "status": "pending",
                        "category": pattern_type,
                        "scope": "kb",
                        "synonyms": [base_term],
                        "related_terms": [],
                        "source_kb_ids": [kb_id],
                    })
                    saved += 1
            except Exception as exc:
                logger.debug(
                    "Failed to save discovered synonym '%s' -> '%s': %s",
                    base_term, synonym, exc,
                )

        logger.info(
            "Saved %d/%d discovered synonyms for kb_id=%s",
            saved, len(discoveries), kb_id,
        )
        return saved

    # -- Global term filtering ---------------------------------------------

    async def _filter_global_terms(
        self,
        candidates: list[ExtractedTerm],
        kb_id: str,
    ) -> list[ExtractedTerm]:
        """Remove candidates that already exist as scope='global'.

        Uses asyncio.gather to check all candidates in parallel instead of
        sequential N+1 lookups. Normalizes terms before comparison to avoid
        Unicode / particle mismatches.

        Matching strategy depends on the global term's ``term_type``:
        - **word** (term_type="word"): Exact match only after normalization.
          No fuzzy / similarity cascade.
        - **term** (term_type="term"): Full similarity cascade (L1-L3) via
          normalized + original form lookups.
        """
        from src.nlp.term_normalizer import TermNormalizer

        get_fn = getattr(self._glossary_repo, "get_by_term", None)
        if not get_fn or not callable(get_fn):
            return candidates

        normalizer = TermNormalizer()

        async def _is_global(term_str: str) -> bool:
            try:
                normalized = normalizer.normalize_for_comparison(term_str)
                result = await get_fn("all", normalized)
                if result is not None and result.get("scope") == "global":
                    # For word-type globals: exact match only
                    if result.get("term_type") == "word":
                        term_lower = term_str.lower()
                        if (
                            result["term"].lower() == term_lower
                            or (result.get("term_ko") or "").lower() == term_lower
                            or normalized.lower() == result["term"].lower()
                        ):
                            return True
                        # Not an exact match -- don't count as global hit
                        return False
                    # term-type: any match in the similarity cascade counts
                    return True
                # Also try the original form if different
                if normalized != term_str:
                    result = await get_fn("all", term_str)
                    if result is not None and result.get("scope") == "global":
                        if result.get("term_type") == "word":
                            term_lower = term_str.lower()
                            if (
                                result["term"].lower() == term_lower
                                or (result.get("term_ko") or "").lower() == term_lower
                            ):
                                return True
                            return False
                        return True
                return False
            except Exception:
                return False

        checks = await asyncio.gather(*[_is_global(c.term) for c in candidates])

        filtered: list[ExtractedTerm] = []
        for candidate, is_global in zip(candidates, checks):
            if is_global:
                logger.debug(
                    "Skipping global term '%s' during kb extraction",
                    candidate.term,
                )
                continue
            filtered.append(candidate)

        return filtered


__all__ = [
    "ExtractedTerm",
    "TermExtractor",
]
