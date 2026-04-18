# pyright: reportGeneralTypeIssues=false
"""Entity Resolver (simplified for local).

Multi-stage entity resolution:
1. Glossary exact match (query glossary_terms table)
2. Embedding similarity (cosine > 0.85 = same entity)
3. Rule-based fallback (skip LLM coreference - too expensive for local)

py.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

from src.config.weights import weights as _w
from typing import Any

logger = logging.getLogger(__name__)


class ResolutionStage(str, Enum):
    """Resolution stage."""

    GLOSSARY = "glossary"      # Stage 1: glossary exact match
    EMBEDDING = "embedding"    # Stage 2: embedding similarity (>=0.85)
    RULE_BASED = "rule_based"  # Stage 3: rule-based fallback (local only)


class EntityType(str, Enum):
    """Entity type."""

    PERSON = "Person"
    TEAM = "Team"
    TOPIC = "Topic"
    SYSTEM = "System"
    DOCUMENT = "Document"


@dataclass
class ResolvedEntity:
    """Resolved entity."""

    canonical_name: str    # Normalized name
    original_name: str     # Original name
    entity_type: EntityType
    resolution_stage: ResolutionStage
    confidence: float
    source_id: str | None = None     # Glossary term ID (stage 1)
    matched_term: str | None = None  # Matched term


# Normalization rules (abbreviation -> full name)
NORMALIZATION_RULES: dict[str, str] = {
    "k8s": "Kubernetes",
    "k3s": "K3s",
    "js": "JavaScript",
    "ts": "TypeScript",
    "py": "Python",
    "pg": "PostgreSQL",
    "psql": "PostgreSQL",
    "es": "Elasticsearch",
    "gcp": "Google Cloud Platform",
    "aws": "Amazon Web Services",
}

# Embedding similarity threshold (SSOT: config_weights.ConfidenceConfig)
EMBEDDING_THRESHOLD = _w.confidence.entity_embedding_threshold


class EntityResolver:
    """Multi-stage entity resolver (simplified for local).

    3-stage resolution strategy:
    1. Glossary exact match (free, instant)
    2. Embedding similarity (~50ms, cheap)
    3. Rule-based fallback (no LLM)
    """

    def __init__(
        self,
        glossary_repo: Any | None = None,
        embedding_service: Any | None = None,
    ) -> None:
        """Initialize entity resolver.

        Args:
            glossary_repo: Glossary repository (for stage 1 matching)
            embedding_service: Embedding service (for stage 2 matching)
        """
        self.glossary = glossary_repo
        self.embedder = embedding_service

    async def resolve(
        self,
        entity_name: str,
        kb_id: str,
        entity_type: EntityType = EntityType.TOPIC,
        context: str | None = None,
    ) -> ResolvedEntity:
        """Resolve an entity name to its canonical form.

        Args:
            entity_name: Entity name to resolve
            kb_id: Knowledge base ID
            entity_type: Entity type hint
            context: Additional context (unused in local, kept for API compat)

        Returns:
            Resolved entity with canonical name
        """
        # Stage 0: Basic normalization (abbreviation -> full name)
        normalized = _basic_normalize(entity_name)
        if normalized != entity_name:
            return ResolvedEntity(
                canonical_name=normalized,
                original_name=entity_name,
                entity_type=entity_type,
                resolution_stage=ResolutionStage.GLOSSARY,
                confidence=1.0,
                matched_term=normalized,
            )

        # Stage 1: Glossary exact match
        if self.glossary:
            glossary_result = await self._match_by_glossary(entity_name, kb_id, entity_type)
            if glossary_result:
                return glossary_result

        # Stage 2: Embedding similarity
        if self.embedder:
            embedding_result = await self._match_by_embedding(entity_name, kb_id, entity_type)
            if embedding_result and embedding_result.confidence >= EMBEDDING_THRESHOLD:
                return embedding_result

        # Stage 3: Rule-based fallback (no LLM for local)
        rule_result = self._rule_based_resolve(entity_name, entity_type)
        if rule_result:
            return rule_result

        # Resolution failed - return original
        return ResolvedEntity(
            canonical_name=entity_name,
            original_name=entity_name,
            entity_type=entity_type,
            resolution_stage=ResolutionStage.RULE_BASED,
            confidence=0.5,
        )

    async def resolve_batch(
        self,
        entities: list[tuple[str, EntityType]],
        kb_id: str,
    ) -> list[ResolvedEntity]:
        """Batch resolve entities.

        Args:
            entities: List of (entity_name, entity_type) tuples
            kb_id: Knowledge base ID

        Returns:
            List of resolved entities
        """
        resolved = []
        for name, etype in entities:
            result = await self.resolve(name, kb_id, entity_type=etype)
            resolved.append(result)
        return resolved

    @staticmethod
    def _term_to_resolved(
        term: Any, name: str, confidence: float,
    ) -> ResolvedEntity:
        """Convert a glossary term object to a ResolvedEntity."""
        term_name = getattr(term, "term", None) or getattr(term, "name", str(term))
        term_id = getattr(term, "id", None)
        return ResolvedEntity(
            canonical_name=str(term_name),
            original_name=name,
            entity_type=EntityType.TOPIC,
            resolution_stage=ResolutionStage.GLOSSARY,
            confidence=confidence,
            source_id=str(term_id) if term_id else None,
            matched_term=str(term_name),
        )

    async def _try_exact_glossary_match(self, name: str, kb_id: str) -> ResolvedEntity | None:
        """Attempt exact glossary term match."""
        get_by_term = getattr(self.glossary, "get_by_term", None)
        if not (get_by_term and callable(get_by_term)):
            return None
        term = await get_by_term(name, kb_id)
        if not term:
            return None
        return self._term_to_resolved(term, name, 1.0)

    async def _try_variant_glossary_match(self, name: str, kb_id: str) -> ResolvedEntity | None:
        """Attempt variant (synonym/abbreviation) glossary match."""
        list_fn = getattr(self.glossary, "list_by_kb", None)
        if not (list_fn and callable(list_fn)):
            return None
        terms = await list_fn(kb_id=kb_id, limit=500, offset=0)
        if not terms:
            return None
        name_lower = name.lower()
        for term in terms:
            all_variants_fn = getattr(term, "get_all_variants", None)
            if not (all_variants_fn and callable(all_variants_fn)):
                continue
            all_variants = all_variants_fn()
            if name_lower in [v.lower() for v in all_variants]:
                return self._term_to_resolved(
                    term, name, _w.confidence.glossary_match_confidence,
                )
        return None

    async def _match_by_glossary(
        self,
        name: str,
        kb_id: str,
        _entity_type: EntityType,
    ) -> ResolvedEntity | None:
        """Glossary-based matching (exact + variant matching)."""
        try:
            result = await self._try_exact_glossary_match(name, kb_id)
            if result:
                return result
            return await self._try_variant_glossary_match(name, kb_id)
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.warning("Glossary matching failed: %s", e)
            return None

    async def _match_by_embedding(
        self,
        name: str,
        kb_id: str,
        entity_type: EntityType,
    ) -> ResolvedEntity | None:
        """Embedding-based similar entity search."""
        try:
            # Use embedding service to search similar terms
            search_fn = getattr(self.embedder, "search_similar", None)
            if search_fn and callable(search_fn):
                similar = await search_fn(name, kb_id, top_k=1)
                if similar and len(similar) > 0:
                    top = similar[0]
                    score = getattr(top, "score", 0.0)
                    matched_name = getattr(top, "name", getattr(top, "term", ""))
                    if score >= EMBEDDING_THRESHOLD:
                        return ResolvedEntity(
                            canonical_name=str(matched_name),
                            original_name=name,
                            entity_type=entity_type,
                            resolution_stage=ResolutionStage.EMBEDDING,
                            confidence=float(score),
                            matched_term=str(matched_name),
                        )
            return None

        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.warning("Embedding matching failed: %s", e)
            return None

    def _rule_based_resolve(
        self,
        name: str,
        entity_type: EntityType,
    ) -> ResolvedEntity | None:
        """Rule-based fallback resolution.

        Simple heuristics:
        - Case normalization for known patterns
        - Suffix stripping for Korean entity names
        """
        lower = name.lower().strip()

        # Check normalization rules
        if lower in NORMALIZATION_RULES:
            return ResolvedEntity(
                canonical_name=NORMALIZATION_RULES[lower],
                original_name=name,
                entity_type=entity_type,
                resolution_stage=ResolutionStage.RULE_BASED,
                confidence=_w.confidence.rule_based_confidence,
                matched_term=NORMALIZATION_RULES[lower],
            )

        return None


def _basic_normalize(name: str) -> str:
    """Basic normalization (abbreviation -> full name)."""
    lower_name = name.lower().strip()
    return NORMALIZATION_RULES.get(lower_name, name)
