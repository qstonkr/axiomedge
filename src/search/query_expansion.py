"""Query Expansion Service.

Combined from oreo-ecosystem:
- query_expansion_service.py (glossary-based expansion, tokenization, stopwords)
- search_query_expander.py (compound splitting, orchestration)

Features:
- Tokenize with Korean/English stopwords
- Glossary-based term expansion
- LLM semantic fallback expansion (via OllamaClient)
- Korean compound word splitting (e.g., "K8S담당자" -> "K8S 담당자")
- Recall probe comparison (original vs expanded query)
"""

from __future__ import annotations

import asyncio

import hashlib
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Glossary repository interface (simplified from oreo IGlossaryRepository)
# ---------------------------------------------------------------------------

class IGlossaryRepository(Protocol):
    """Glossary repository interface for term lookup."""

    async def search(self, kb_id: str, term: str, limit: int = 1) -> list[Any]:
        ...


class NoOpGlossaryRepository:
    """No-op glossary repository that returns no matches."""

    async def search(self, kb_id: str, term: str, limit: int = 1) -> list[Any]:
        await asyncio.sleep(0)
        return []


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ExpandedQuery:
    """Expanded query result.

    Attributes:
        original_query: Original query
        expanded_query: Expanded query (OR combinations)
        expansion_terms: Terms used in expansion
        matched_glossary_ids: Matched glossary term IDs
    """

    original_query: str
    expanded_query: str
    expansion_terms: list[str] = field(default_factory=list)
    matched_glossary_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "original_query": self.original_query,
            "expanded_query": self.expanded_query,
            "expansion_terms": self.expansion_terms,
            "matched_glossary_ids": self.matched_glossary_ids,
        }


@dataclass
class TermExpansion:
    """Single term expansion result.

    Attributes:
        original_term: Original term
        expanded_terms: Expanded term list
        glossary_term_id: Matched glossary ID (None if no match)
        source: Expansion source (glossary, semantic_fallback, decomposition, original)
    """

    original_term: str
    expanded_terms: list[str]
    glossary_term_id: str | None = None
    source: str = "glossary"


@dataclass(frozen=True)
class QueryExpansionDecision:
    """Resolved query expansion payload + method metadata."""

    original_query: str
    expanded_query: str
    method: str

    @property
    def was_expanded(self) -> bool:
        return self.original_query != self.expanded_query


# ---------------------------------------------------------------------------
# Korean compound splitting regex
# ---------------------------------------------------------------------------

# Split at English/digit + Hangul boundaries
_COMPOUND_SPLIT_RE = re.compile(r"[a-zA-Z0-9]+|[가-힣]+")


# ---------------------------------------------------------------------------
# QueryExpansionService
# ---------------------------------------------------------------------------

class QueryExpansionService:
    """Glossary-based query expansion service.

    Expansion strategy:
    1. Tokenize query
    2. Match each token against glossary
    3. Expand matched terms with synonyms/abbreviations via OR
    4. Combine expanded query
    """

    # Stopwords (excluded from expansion)
    STOPWORDS = {
        "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
        "have", "has", "had", "do", "does", "did", "will", "would", "could",
        "should", "may", "might", "must", "shall", "can", "need",
        "to", "of", "in", "for", "on", "with", "at", "by", "from", "up",
        "about", "into", "over", "after",
        "and", "or", "but", "not",
        "this", "that", "these", "those", "it", "its",
        "what", "which", "who", "whom", "when", "where", "why", "how",
        # Korean stopwords
        "은", "는", "이", "가", "을", "를", "의", "에", "에서", "로", "으로",
        "와", "과", "도", "만", "뿐", "부터", "까지",
        "하다", "되다", "있다", "없다", "것", "수", "등",
        # Korean conversational stopwords
        "대해", "대해서", "대한", "관한", "관해", "관해서",
        "해서", "하고", "합니다", "입니다", "습니다", "에요", "이에요",
        "같은", "어떤", "어떻게", "무엇",
        "알고", "싶어", "알고싶어", "알려", "알려줘", "알려주세요", "궁금",
    }

    def __init__(
        self,
        glossary_repository: IGlossaryRepository | None = None,
        max_expansions_per_term: int = 5,
        enable_semantic_fallback: bool = True,
        llm_expander: Any | None = None,
        decomposition_service: Any | None = None,
    ):
        """Initialize.

        Args:
            glossary_repository: Glossary repository for term lookup
            max_expansions_per_term: Max expansions per term (default: 5)
            enable_semantic_fallback: Use semantic expansion when glossary has no match
            llm_expander: Semantic expander (e.g., OllamaClient with expand method)
            decomposition_service: Compound word decomposition service
        """
        self._glossary_repo = glossary_repository or NoOpGlossaryRepository()
        self._max_expansions = max_expansions_per_term
        self._enable_semantic = enable_semantic_fallback
        self._llm_expander = llm_expander
        self._decomposition = decomposition_service

    def tokenize(self, query: str) -> list[str]:
        """Tokenize query, excluding stopwords."""
        tokens = re.findall(r"[A-Za-z0-9]+|[\uac00-\ud7a3]+", query)
        filtered = [t for t in tokens if t.lower() not in self.STOPWORDS]
        return filtered

    async def expand_term(self, kb_id: str, term: str) -> TermExpansion:
        """Expand a single term.

        Uses decomposition service (memory O(1) lookup) first if available,
        then falls back to DB-based glossary search.
        """
        # 1. Decomposition service expansion (memory O(1), no DB call)
        if self._decomposition and getattr(self._decomposition, "is_loaded", False):
            decomp_expansions = self._decomposition.expand_for_query(term)
            if len(decomp_expansions) > 1:
                if len(decomp_expansions) > self._max_expansions:
                    decomp_expansions = decomp_expansions[: self._max_expansions]
                return TermExpansion(
                    original_term=term,
                    expanded_terms=decomp_expansions,
                    source="decomposition",
                )

        # 2. DB-based glossary search (fallback)
        matched_terms = await self._glossary_repo.search(kb_id, term, limit=1)

        if not matched_terms:
            semantic_expansion = await self._expand_semantic_term(term)
            if semantic_expansion is not None:
                return semantic_expansion

            return TermExpansion(
                original_term=term,
                expanded_terms=[term],
                source="original",
            )

        glossary_term = matched_terms[0]

        # Differentiate expansion strategy by term_type:
        # - "word": Simple 1:1 bidirectional mapping (term <-> term_ko).
        #   No fuzzy expansion with synonyms/abbreviations.
        # - "term": Full expansion with synonyms, abbreviations, term_ko.
        if glossary_term.get("term_type") == "word":
            all_variants = [glossary_term["term"]]
            if glossary_term.get("term_ko"):
                all_variants.append(glossary_term["term_ko"])
            # Deduplicate: remove the original query term if it's already
            # present so we get a clean bidirectional mapping.
            all_variants = [v for v in all_variants if v]
        else:
            # Full expansion: term + synonyms + abbreviations + term_ko
            all_variants = [glossary_term["term"]]
            all_variants.extend(glossary_term.get("synonyms", []))
            all_variants.extend(glossary_term.get("abbreviations", []))
            if glossary_term.get("term_ko"):
                all_variants.append(glossary_term["term_ko"])

        if len(all_variants) > self._max_expansions:
            all_variants = all_variants[: self._max_expansions]

        return TermExpansion(
            original_term=term,
            expanded_terms=all_variants,
            glossary_term_id=glossary_term.get("id"),
            source="glossary",
        )

    async def _expand_semantic_term(self, term: str) -> TermExpansion | None:
        """Expand term with semantic fallback when glossary has no match."""
        if not self._enable_semantic or self._llm_expander is None:
            return None

        try:
            expanded = await self._llm_expander.expand(term)
        except Exception as exc:
            logger.warning("Semantic fallback expansion failed for term '%s': %s", term, exc)
            return None

        candidate_terms = [
            getattr(expanded, "rewrite_query", None),
            getattr(expanded, "preprocess_query", None),
            *list(getattr(expanded, "variations", []) or []),
        ]

        deduplicated: list[str] = [term]
        seen = {term.lower()}
        for candidate in candidate_terms:
            normalized = str(candidate or "").strip()
            if not normalized:
                continue
            lowered = normalized.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            deduplicated.append(normalized)

        if len(deduplicated) == 1:
            return None

        return TermExpansion(
            original_term=term,
            expanded_terms=deduplicated[: self._max_expansions],
            source="semantic_fallback",
        )

    async def expand_query(self, kb_id: str, query: str) -> ExpandedQuery:
        """Expand full query.

        Args:
            kb_id: KB ID
            query: Original query

        Returns:
            Expanded query
        """
        tokens = self.tokenize(query)

        if not tokens:
            return ExpandedQuery(original_query=query, expanded_query=query)

        expansions: list[TermExpansion] = []
        all_expansion_terms: list[str] = []
        matched_ids: list[str] = []

        for token in tokens:
            expansion = await self.expand_term(kb_id, token)
            expansions.append(expansion)

            all_expansion_terms.extend(expansion.expanded_terms)
            if expansion.glossary_term_id:
                matched_ids.append(expansion.glossary_term_id)

        # Combine expanded query
        expanded_parts: list[str] = []
        for expansion in expansions:
            if len(expansion.expanded_terms) > 1:
                or_group = " OR ".join(expansion.expanded_terms)
                expanded_parts.append(f"({or_group})")
            else:
                expanded_parts.append(expansion.expanded_terms[0])

        expanded_query = " AND ".join(expanded_parts) if expanded_parts else query

        logger.debug(f"Query expanded: '{query}' -> '{expanded_query}'")

        return ExpandedQuery(
            original_query=query,
            expanded_query=expanded_query,
            expansion_terms=list(set(all_expansion_terms)),
            matched_glossary_ids=matched_ids,
        )

    async def expand_for_vector_search(self, kb_id: str, query: str) -> str:
        """Expand query for vector search (space-separated, no OR/AND operators).

        Args:
            kb_id: KB ID
            query: Original query

        Returns:
            Expanded query string
        """
        result = await self.expand_query(kb_id, query)

        all_terms = [result.original_query]
        all_terms.extend(result.expansion_terms)

        # Deduplicate preserving order
        unique_terms = list(dict.fromkeys(all_terms))

        return " ".join(unique_terms)


# ---------------------------------------------------------------------------
# SearchQueryExpander (orchestrator)
# ---------------------------------------------------------------------------

def _stable_percent_bucket(*parts: str) -> int:
    """Build deterministic 0-99 bucket from stable input keys."""
    key = "||".join(str(part or "").strip().lower() for part in parts)
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % 100


class SearchQueryExpander:
    """Query expansion orchestrator.

    Responsibilities:
    - Glossary-based expansion via QueryExpansionService
    - Korean compound word splitting fallback
    - Recall probe sampling and execution
    """

    def __init__(
        self,
        query_expansion_service: QueryExpansionService | None = None,
        recall_probe_rate: float = 0.0,
    ) -> None:
        self._query_expansion = query_expansion_service
        self._recall_probe_rate = recall_probe_rate

    async def expand_query(
        self,
        query: str,
        query_type: str | None = None,
        query_language: str | None = None,
        kb_id: str | None = None,
    ) -> str:
        """Back-compatible query expansion wrapper returning only query text."""
        decision = await self.expand_query_with_metadata(
            query=query,
            query_type=query_type,
            query_language=query_language,
            kb_id=kb_id,
        )
        return decision.expanded_query

    async def expand_query_with_metadata(
        self,
        query: str,
        query_type: str | None = None,
        query_language: str | None = None,
        kb_id: str | None = None,
    ) -> QueryExpansionDecision:
        """Expand query using glossary-based QueryExpansionService.

        Fallback: If no glossary match, apply Korean compound word splitting.
        Example: "K8S담당자" -> "K8S 담당자" (improves vector search precision)
        """
        expanded = query

        if self._query_expansion:
            try:
                expansion_result = await self._query_expansion.expand_query(
                    kb_id=kb_id or "all",
                    query=query,
                )
                if (
                    expansion_result.expanded_query
                    and expansion_result.expanded_query != query
                ):
                    logger.info(
                        "Knowledge query expanded",
                        extra={
                            "original_query": query[:100],
                            "expanded_query": expansion_result.expanded_query[:200],
                            "matched_terms": len(
                                expansion_result.matched_glossary_ids
                            ),
                        },
                    )
                    return QueryExpansionDecision(
                        original_query=query,
                        expanded_query=expansion_result.expanded_query,
                        method="glossary",
                    )
            except Exception as e:
                logger.warning(
                    "Knowledge query expansion failed, using original query",
                    extra={
                        "error": str(e),
                        "query_preview": query[:100],
                    },
                )

        # Fallback: Korean compound splitting (split at English/digit + Hangul boundary)
        # "K8S담당자" -> "K8S 담당자", "POS장애처리" -> "POS 장애처리"
        tokens = _COMPOUND_SPLIT_RE.findall(query)
        if tokens:
            split_query = " ".join(tokens)
            if split_query != query:
                logger.info(
                    "Knowledge query compound-split",
                    extra={
                        "original_query": query[:100],
                        "split_query": split_query[:200],
                    },
                )
                expanded = split_query

        return QueryExpansionDecision(
            original_query=query,
            expanded_query=expanded,
            method="compound_split" if expanded != query else "none",
        )

    def should_run_recall_probe(
        self,
        *,
        user_id: str,
        organization_id: str,
        query: str,
        expansion_decision: QueryExpansionDecision,
    ) -> bool:
        """Return True when sampled recall probe should compare original vs expanded query."""
        if self._recall_probe_rate <= 0.0:
            return False
        if not expansion_decision.was_expanded:
            return False
        if self._recall_probe_rate >= 1.0:
            return True

        bucket = _stable_percent_bucket(
            user_id,
            organization_id,
            query,
            "query_expansion_recall_probe",
        )
        return (bucket / 100.0) < self._recall_probe_rate


__all__ = [
    "ExpandedQuery",
    "IGlossaryRepository",
    "NoOpGlossaryRepository",
    "QueryExpansionDecision",
    "QueryExpansionService",
    "SearchQueryExpander",
    "TermExpansion",
]
