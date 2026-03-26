"""Graph Chunk Expander at Search Time.

After vector search returns top-K chunks, use Neo4j to find related documents:
- Documents that REFERENCE the found document
- Documents that share the same AUTHOR
- Child/parent documents in the wiki hierarchy

Adds these related chunks to the result set with a graph_boost score.
Extracted from oreo-ecosystem GraphChunkExpander.

Usage:
    expander = GraphSearchExpander(graph_repo=my_neo4j_repo)
    expanded = await expander.expand(query, initial_chunks, scope_kb_ids=["my-kb"])
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Protocol

logger = logging.getLogger(__name__)

# Korean compound-word split: English/digits + Hangul boundary
_COMPOUND_SPLIT_PATTERN = re.compile(r"[a-zA-Z0-9]+|[가-힣]+")


# ---------------------------------------------------------------------------
# Protocol for graph repository (matches Neo4jGraphRepository)
# ---------------------------------------------------------------------------


class IGraphRepository(Protocol):
    """Minimal protocol for the graph backend."""

    async def find_related_chunks(
        self,
        entity_names: list[str],
        *,
        max_hops: int = 2,
        max_results: int = 50,
        scope_kb_ids: list[str] | None = None,
    ) -> set[str]:
        """Return source URIs of related documents."""
        ...


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _split_compound_words(query: str) -> list[str]:
    """Split Korean compound words at English/Hangul boundaries.

    "K8S담당자" -> ["K8S", "담당자"]
    "POS장애처리" -> ["POS", "장애처리"]
    "CI/CD 파이프라인 관리자" -> ["CI", "CD", "파이프라인", "관리자"]
    """
    tokens: list[str] = []
    for word in query.split():
        parts = _COMPOUND_SPLIT_PATTERN.findall(word)
        if parts:
            tokens.extend(parts)
        elif len(word) >= 2:
            tokens.append(word)
    return tokens


# ---------------------------------------------------------------------------
# Expander
# ---------------------------------------------------------------------------


@dataclass
class ExpandedSearchResult:
    """Result of graph expansion."""

    original_chunks: list[dict[str, Any]]
    expanded_source_uris: set[str]
    graph_related_count: int


class GraphSearchExpander:
    """Expand vector search results via Neo4j graph traversal.

    After initial vector search, extracts entity tokens from the query
    and finds structurally related documents through graph edges:
    - REFERENCES (document cross-references)
    - AUTHORED (same author)
    - CHILD_OF (wiki hierarchy)
    - BELONGS_TO (same space)
    - COVERS (shared topics)

    The found source_uris can be used to boost or inject related chunks
    from the vector store.
    """

    def __init__(
        self,
        graph_repo: IGraphRepository,
        *,
        max_hops: int = 2,
        max_expansion: int = 20,
        graph_boost: float = 0.05,
    ) -> None:
        self._graph_repo = graph_repo
        self._max_hops = max_hops
        self._max_expansion = max_expansion
        self._graph_boost = graph_boost

    async def expand(
        self,
        query: str,
        chunks: list[dict[str, Any]],
        *,
        scope_kb_ids: list[str] | None = None,
    ) -> ExpandedSearchResult:
        """Expand search results with graph-related documents.

        Args:
            query: User search query.
            chunks: Initial vector search result chunks.
            scope_kb_ids: Optional KB scope filter.

        Returns:
            ExpandedSearchResult with original chunks and related source URIs.
        """
        if not chunks:
            return ExpandedSearchResult(
                original_chunks=chunks,
                expanded_source_uris=set(),
                graph_related_count=0,
            )

        # Extract entity tokens from query
        tokens = _split_compound_words(query)
        entity_names = [t for t in tokens if len(t) >= 2]

        if not entity_names:
            return ExpandedSearchResult(
                original_chunks=chunks,
                expanded_source_uris=set(),
                graph_related_count=0,
            )

        # Existing source URIs from initial results
        existing_uris: set[str] = set()
        for chunk in chunks:
            uri = chunk.get("source_uri") or ""
            if uri:
                existing_uris.add(uri)

        try:
            # Single-KB expansion (scoped)
            related_uris = await self._graph_repo.find_related_chunks(
                entity_names,
                max_hops=self._max_hops,
                max_results=self._max_expansion,
                scope_kb_ids=scope_kb_ids,
            )

            # Cross-KB expansion (unscoped) — find relationships across all KBs
            cross_kb_uris: set[str] = set()
            try:
                cross_kb_uris = await self._graph_repo.find_related_chunks(
                    entity_names,
                    max_hops=self._max_hops,
                    max_results=self._max_expansion // 2,
                    scope_kb_ids=None,  # No KB scope = cross-KB
                )
                cross_kb_uris -= related_uris  # Remove already found
            except Exception as _xkb_err:
                logger.debug("Cross-KB graph expansion failed (best-effort): %s", _xkb_err)

            all_related = related_uris | cross_kb_uris
            new_uris = all_related - existing_uris
            graph_related_count = len(new_uris)

            logger.info(
                "Graph expansion: %d entities -> %d related (%d same-KB, %d cross-KB, %d new)",
                len(entity_names),
                len(all_related),
                len(related_uris),
                len(cross_kb_uris),
                graph_related_count,
            )

            return ExpandedSearchResult(
                original_chunks=chunks,
                expanded_source_uris=new_uris,
                graph_related_count=graph_related_count,
            )

        except Exception as e:
            logger.warning("Graph expansion failed: %s", e)
            return ExpandedSearchResult(
                original_chunks=chunks,
                expanded_source_uris=set(),
                graph_related_count=0,
            )

    def boost_chunks(
        self,
        chunks: list[dict[str, Any]],
        expanded_uris: set[str],
    ) -> list[dict[str, Any]]:
        """Apply graph_boost to chunks whose source_uri matches expanded set.

        This should be called BEFORE final sorting/reranking to give
        graph-related chunks a slight score boost.
        """
        if not expanded_uris:
            return chunks

        boosted: list[dict[str, Any]] = []
        for chunk in chunks:
            uri = chunk.get("source_uri") or ""
            if uri in expanded_uris:
                chunk = dict(chunk)
                chunk["score"] = chunk.get("score", 0.0) + self._graph_boost
                chunk["graph_boosted"] = True
            boosted.append(chunk)
        return boosted


class NoOpGraphSearchExpander:
    """No-op expander for when Neo4j is unavailable."""

    async def expand(
        self,
        query: str,
        chunks: list[dict[str, Any]],
        **kwargs: Any,
    ) -> ExpandedSearchResult:
        return ExpandedSearchResult(
            original_chunks=chunks,
            expanded_source_uris=set(),
            graph_related_count=0,
        )

    def boost_chunks(
        self,
        chunks: list[dict[str, Any]],
        expanded_uris: set[str],
    ) -> list[dict[str, Any]]:
        return chunks


__all__ = [
    "ExpandedSearchResult",
    "GraphSearchExpander",
    "IGraphRepository",
    "NoOpGraphSearchExpander",
]
