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

from src.pipelines.term_patterns import (
    CAMEL_CASE_PATTERN,
    ACRONYM_PATTERN,
    HYPHENATED_PATTERN,
    KOREAN_TECH_PATTERN,
    MIXED_PATTERN,
    WHITESPACE_RE as _WHITESPACE_RE,
    STOP_TERMS as _STOP_TERMS,
    _COMPOUND_STOP,
    is_noise_term as _is_noise_term,
    is_synonym_noise as _is_synonym_noise,
    is_code_artifact as _is_code_artifact,
    strip_korean_particles as _strip_korean_particles,
)

logger = logging.getLogger(__name__)





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

    async def get_by_term(self, _kb_id: str, _term: str) -> dict[str, Any] | None:
        """Return term dict if exists (check scope='global')."""
        ...

    async def save(self, _term_data: dict[str, Any]) -> None:
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

    # KiwiPy score threshold: words with score > this are too common/generic
    # score is log probability — closer to 0 = more common
    # -12.0 separates generic (상품-9.6, 개발-9.7) from domain (정산-13.0, 경영주-14.2)
    KIWI_GENERIC_SCORE_THRESHOLD = -12.0

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

    # Dense similarity threshold: pending terms more similar than this
    # to any approved term are considered duplicates and skipped.
    DENSE_SIM_THRESHOLD = 0.75

    def __init__(
        self,
        glossary_repo: Any | None = None,
        min_occurrences: int | None = None,
        embedder: Any | None = None,
    ) -> None:
        self._glossary_repo = glossary_repo
        self._min_occurrences = min_occurrences or self.MIN_OCCURRENCES
        self._embedder = embedder  # For dense similarity check vs approved terms
        self._kiwi = None
        self._kiwi_available: bool | None = None  # None = not checked yet
        self._approved_vecs_cache: Any | None = None  # numpy array cache
        self._approved_terms_cache: list[str] | None = None

    def _get_kiwi(self) -> Any:
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

        # Filter out global terms (string matching)
        if self._glossary_repo:
            candidates = await self._filter_global_terms(candidates, kb_id)

        # Filter out terms too similar to approved terms (dense embedding)
        if self._embedder and self._glossary_repo and candidates:
            candidates = await self._filter_by_dense_similarity(candidates, kb_id)

        logger.info(
            "Term extraction (%s): %d candidates from %d chunks (kb_id=%s)",
            "kiwi" if use_kiwi else "regex",
            len(candidates), len(chunks), kb_id,
        )
        return candidates

    @staticmethod
    def _flush_compound_buffer(
        buf_forms: list[str],
        buf_tags: list[str],
        buf_scores: list[float],
        compounds: list[tuple[str, str, float]],
        stop_nouns: frozenset[str],
        min_term_length: int,
        max_compound_chars: int,
        foreign_tag: str,
    ) -> None:
        """Flush the compound buffer, emitting compound + individual terms."""
        if not buf_forms:
            return
        merged = "".join(buf_forms)
        min_score = max(buf_scores) if buf_scores else 0.0

        all_parts_valid = all(len(f) >= 2 for f in buf_forms)
        no_stopwords = not any(f in stop_nouns or f in _COMPOUND_STOP for f in buf_forms)
        if len(buf_forms) > 1 and len(merged) <= max_compound_chars and all_parts_valid and no_stopwords:
            compounds.append((merged, "compound_noun", min_score))

        for form, t, sc in zip(buf_forms, buf_tags, buf_scores):
            if len(form) < min_term_length:
                continue
            if t == "NNP":
                compounds.append((form, "proper_noun", sc))
            elif t == foreign_tag:
                compounds.append((form, "foreign", sc))
            elif t == "NNG":
                compounds.append((form, "noun", sc))

        buf_forms.clear()
        buf_tags.clear()
        buf_scores.clear()

    def _build_compounds_from_tokens(
        self, tokens: list,
    ) -> list[tuple[str, str, float]]:
        """Merge adjacent noun tokens into compound terms (Pass 1)."""
        _MAX_COMPOUND_TOKENS = 3
        _MAX_COMPOUND_CHARS = 8

        compounds: list[tuple[str, str, float]] = []
        buf_forms: list[str] = []
        buf_tags: list[str] = []
        buf_scores: list[float] = []

        flush = lambda: self._flush_compound_buffer(  # noqa: E731
            buf_forms, buf_tags, buf_scores, compounds,
            self._STOP_NOUNS, self.MIN_TERM_LENGTH, _MAX_COMPOUND_CHARS, self._FOREIGN_TAG,
        )

        for token in tokens:
            tag = token.tag
            if tag not in self._NOUN_TAGS and tag != self._FOREIGN_TAG:
                flush()
                continue

            should_break = (
                (tag == "NNP" and buf_forms)
                or (tag == self._FOREIGN_TAG and buf_forms and buf_tags[-1] != self._FOREIGN_TAG)
                or len(buf_forms) >= _MAX_COMPOUND_TOKENS
            )
            if should_break:
                flush()

            buf_forms.append(token.form)
            buf_tags.append(tag)
            buf_scores.append(token.score)

            # NNP always flush immediately (standalone)
            if tag == "NNP":
                flush()

        flush()
        return compounds

    def _extract_terms_kiwi(
        self,
        text: str,
        kiwi: Any,
        counter: Counter[str],
        types: dict[str, str],
        contexts: dict[str, list[str]],
        original_case: dict[str, str],
    ) -> None:
        """Extract terms using KiwiPy morphological analysis."""
        try:
            tokens = kiwi.tokenize(text)
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError):
            return

        compounds = self._build_compounds_from_tokens(tokens)

        for term, tag_type, kiwi_score in compounds:
            if _is_noise_term(term, tag_type, kiwi_score):
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
                except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError):
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
            except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
                logger.debug(
                    "Failed to save term '%s': %s", term.term, exc
                )

        logger.info("Saved %d/%d terms for kb_id=%s", saved, len(terms), kb_id)
        return saved

    # -- Pattern extraction ------------------------------------------------

    @staticmethod
    def _normalize_match(raw_term: str, pattern_type: str) -> str | None:
        """Normalize a regex match. Returns None if the term should be skipped."""
        if _is_code_artifact(raw_term):
            return None
        if pattern_type == "mixed":
            raw_term = _strip_korean_particles(raw_term)
        if len(raw_term) < 2:
            return None
        if raw_term.lower() in _STOP_TERMS:
            return None
        return raw_term

    def _record_match(
        self,
        text: str,
        raw_term: str,
        counter: Counter[str],
        types: dict[str, str],
        contexts: dict[str, list[str]],
        _original_case: dict[str, str],
        pattern_type: str,
    ) -> None:
        """Record a single matched term into counters, types, contexts."""
        count_key = raw_term.lower()
        counter[count_key] += 1
        types.setdefault(count_key, pattern_type)
        if count_key not in _original_case:
            _original_case[count_key] = raw_term

        if count_key not in contexts:
            contexts[count_key] = []
        if len(contexts[count_key]) < 3:
            ctx = self._extract_context(text, raw_term)
            if ctx:
                contexts[count_key].append(ctx)

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
                raw_term = self._normalize_match(match.group().strip(), pattern_type)
                if raw_term is None:
                    continue
                self._record_match(
                    text, raw_term, counter, types, contexts, _original_case, pattern_type,
                )

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
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError):
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

    @staticmethod
    def _build_known_term_set(known_terms: list[dict[str, Any]]) -> set[str]:
        """Build a lowercase set of all known terms and their synonyms."""
        known_set: set[str] = set()
        for kt in known_terms:
            t = kt.get("term", "")
            if t:
                known_set.add(t.lower())
            for s in kt.get("synonyms", []):
                if s:
                    known_set.add(s.lower())
        return known_set

    @staticmethod
    def _is_valid_synonym_pair(term_a: str, term_b: str) -> bool:
        """Check if a synonym pair passes noise/length filters."""
        if len(term_a) < 2 or len(term_b) < 2:
            return False
        if _is_code_artifact(term_a) or _is_code_artifact(term_b):
            return False
        if len(term_a) > 30 or len(term_b) > 30:
            return False
        if _is_synonym_noise(term_a) or _is_synonym_noise(term_b):
            return False
        return True

    @staticmethod
    def _resolve_base_synonym(term_a: str, term_b: str, known_set: set[str]) -> tuple[str, str]:
        """Determine which term is the base and which is the synonym."""
        a_known = term_a.lower() in known_set
        b_known = term_b.lower() in known_set
        if a_known and not b_known:
            return term_a, term_b
        if b_known and not a_known:
            return term_b, term_a
        return term_a, term_b

    async def discover_synonyms(
        self,
        text: str,
        known_terms: list[dict[str, Any]],
    ) -> list[tuple[str, str, str]]:
        """Discover synonym candidates from text context.

        Returns:
            List of (term, synonym, pattern_type) tuples.
        """
        await asyncio.sleep(0)
        if not text:
            return []

        known_set = self._build_known_term_set(known_terms)
        discovered: list[tuple[str, str, str]] = []
        seen_pairs: set[tuple[str, str]] = set()

        for pattern, pattern_type in self._SYNONYM_PATTERNS:
            for match in pattern.finditer(text):
                term_a = match.group(1).strip()
                term_b = match.group(2).strip()

                if not self._is_valid_synonym_pair(term_a, term_b):
                    continue

                base, syn = self._resolve_base_synonym(term_a, term_b, known_set)
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

    async def _save_single_synonym(
        self, kb_id: str, base_term: str, synonym: str, pattern_type: str,
    ) -> bool:
        """Save a single discovered synonym. Returns True if saved."""
        existing = None
        get_fn = getattr(self._glossary_repo, "get_by_term", None)
        if get_fn and callable(get_fn):
            existing = await get_fn(kb_id, base_term)
            if not existing:
                existing = await get_fn("all", base_term)

        if existing:
            current_synonyms = existing.get("synonyms", [])
            if synonym not in current_synonyms:
                current_synonyms.append(synonym)
                await self._glossary_repo.save({
                    "id": existing["id"],
                    "kb_id": existing["kb_id"],
                    "term": existing["term"],
                    "synonyms": current_synonyms,
                })
                return True
            return False
        else:
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
            return True

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
                ok = await self._save_single_synonym(kb_id, base_term, synonym, pattern_type)
                if ok:
                    saved += 1
            except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
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

    @staticmethod
    def _is_word_exact_match(result: dict, term_str: str, normalized: str) -> bool:
        """Check if a word-type global term is an exact match."""
        term_lower = term_str.lower()
        return (
            result["term"].lower() == term_lower
            or (result.get("term_ko") or "").lower() == term_lower
            or normalized.lower() == result["term"].lower()
        )

    @staticmethod
    def _is_global_hit(result: dict | None, term_str: str, normalized: str) -> bool:
        """Check if a lookup result qualifies as a global term hit."""
        if result is None or result.get("scope") != "global":
            return False
        if result.get("term_type") == "word":
            return TermExtractor._is_word_exact_match(result, term_str, normalized)
        return True

    async def _filter_global_terms(
        self,
        candidates: list[ExtractedTerm],
        _kb_id: str,
    ) -> list[ExtractedTerm]:
        """Remove candidates that already exist as scope='global'."""
        from src.nlp.korean.term_normalizer import TermNormalizer

        get_fn = getattr(self._glossary_repo, "get_by_term", None)
        if not get_fn or not callable(get_fn):
            return candidates

        normalizer = TermNormalizer()

        async def _is_global(term_str: str) -> bool:
            try:
                normalized = normalizer.normalize_for_comparison(term_str)
                result = await get_fn("all", normalized)
                if self._is_global_hit(result, term_str, normalized):
                    return True
                if normalized != term_str:
                    result = await get_fn("all", term_str)
                    if self._is_global_hit(result, term_str, normalized):
                        return True
                return False
            except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError):
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

    async def _load_approved_term_embeddings(self, np) -> bool:
        """Load and embed approved terms into cache. Returns False if unavailable."""
        list_fn = getattr(self._glossary_repo, "list_by_kb", None)
        if not list_fn:
            return False
        approved_terms_data = await list_fn(
            kb_id="all", status="approved", limit=10000, offset=0,
        )
        if not approved_terms_data:
            return False
        self._approved_terms_cache = [t["term"] for t in approved_terms_data if t.get("term")]
        if not self._approved_terms_cache:
            return False

        encode_fn = getattr(self._embedder, "encode", None)
        if not encode_fn:
            return False
        approved_vecs = await asyncio.to_thread(encode_fn, self._approved_terms_cache)
        if isinstance(approved_vecs, dict):
            approved_vecs = approved_vecs.get("dense", [])
        self._approved_vecs_cache = np.array(approved_vecs)
        norms = np.linalg.norm(self._approved_vecs_cache, axis=1, keepdims=True)
        self._approved_vecs_cache = self._approved_vecs_cache / np.maximum(norms, 1e-8)
        logger.info(
            "Dense similarity filter: loaded %d approved term embeddings",
            len(self._approved_terms_cache),
        )
        return True

    async def _filter_by_dense_similarity(
        self,
        candidates: list[ExtractedTerm],
        _kb_id: str,
    ) -> list[ExtractedTerm]:
        """Remove candidates too similar to approved terms via dense embedding.

        Uses BGE-M3 embeddings to compute cosine similarity between candidate
        terms and approved (standard) terms. Candidates with similarity >= threshold
        are considered duplicates of existing standard terms and removed.
        """
        import numpy as np

        try:
            # Load approved terms (cached)
            if self._approved_vecs_cache is None:
                loaded = await self._load_approved_term_embeddings(np)
                if not loaded:
                    return candidates

            # Embed candidates
            candidate_terms = [c.term for c in candidates]
            encode_fn = getattr(self._embedder, "encode", None)
            if not encode_fn:
                return candidates
            cand_vecs = await asyncio.to_thread(encode_fn, candidate_terms)
            if isinstance(cand_vecs, dict):
                cand_vecs = cand_vecs.get("dense", [])
            cand_vecs = np.array(cand_vecs)
            norms = np.linalg.norm(cand_vecs, axis=1, keepdims=True)
            cand_vecs = cand_vecs / np.maximum(norms, 1e-8)

            # Compute max similarity
            sim_matrix = cand_vecs @ self._approved_vecs_cache.T
            max_sims = np.max(sim_matrix, axis=1)

            filtered = []
            removed = 0
            for i, candidate in enumerate(candidates):
                if max_sims[i] >= self.DENSE_SIM_THRESHOLD:
                    removed += 1
                    logger.debug(
                        "Dense sim filter: '%s' similar to approved (%.2f)",
                        candidate.term, max_sims[i],
                    )
                else:
                    filtered.append(candidate)

            if removed > 0:
                logger.info(
                    "Dense similarity filter: removed %d/%d terms (threshold=%.0f%%)",
                    removed, len(candidates), self.DENSE_SIM_THRESHOLD * 100,
                )
            return filtered

        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.warning("Dense similarity filter failed, skipping: %s", e)
            return candidates


__all__ = [
    "ExtractedTerm",
    "TermExtractor",
]
