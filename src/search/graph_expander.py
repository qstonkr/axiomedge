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

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any, Protocol

from src.config_weights import weights as _w

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

    async def search_entities(
        self,
        keywords: list[str],
        *,
        max_facts: int = 20,
    ) -> list[dict[str, Any]]:
        """Search entities in graph."""
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
        graph_boost: float = 0.08,
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
        existing_docs: set[str] = set()
        for chunk in chunks:
            uri = chunk.get("source_uri") or ""
            doc = chunk.get("document_name") or ""
            if uri:
                existing_uris.add(uri)
            if doc:
                existing_docs.add(doc)

        # Person-based expansion: find documents where Person is MENTIONED_IN
        person_uris = await self._find_person_mentioned_docs(
            entity_names, query=query, scope_kb_ids=scope_kb_ids,
        )
        if person_uris:
            new_person_docs = person_uris - existing_uris - existing_docs
            if new_person_docs:
                logger.info(
                    "Person MENTIONED_IN expansion: %d new docs from query names",
                    len(new_person_docs),
                )
                existing_uris |= person_uris

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

            all_related = related_uris | cross_kb_uris | person_uris
            new_uris = all_related - existing_uris - existing_docs
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

    @staticmethod
    def _extract_date_tokens(query: str) -> list[str]:
        """Extract date-like tokens from query for document name filtering.

        Matches: "2024년 4월", "4월 2주차", "2024_04", "3월", "2026년" etc.
        """
        import re
        date_tokens = []
        # "2024년 4월" → "2024_04", "2024" + "04"
        m = re.search(r"(20\d{2})년\s*(\d{1,2})월", query)
        if m:
            date_tokens.append(f"{m.group(1)}_{m.group(2).zfill(2)}")
            date_tokens.append(m.group(1))
            date_tokens.append(f"{m.group(2)}월")
        # "4월 2주차" → "04", "4월"
        m2 = re.search(r"(\d{1,2})월\s*(\d)주차", query)
        if m2:
            date_tokens.append(f"{m2.group(1)}월")
            date_tokens.append(f"{m2.group(1).zfill(2)}")
        # "2024_04" already in query
        m3 = re.search(r"(20\d{2})[_\-](0[1-9]|1[0-2])", query)
        if m3:
            date_tokens.append(f"{m3.group(1)}_{m3.group(2)}")
        return date_tokens

    async def _find_person_mentioned_docs(
        self,
        entity_names: list[str],
        *,
        query: str = "",
        scope_kb_ids: list[str] | None = None,
        max_results: int = 10,
    ) -> set[str]:
        """Find documents where query entities are MENTIONED_IN.

        Uses date tokens from query to narrow down results when
        Person is mentioned in many documents.
        """
        if not hasattr(self._graph_repo, "_client") or not self._graph_repo._client:
            return set()

        korean_names = [
            n for n in entity_names
            if 2 <= len(n) <= 4 and all("\uac00" <= c <= "\ud7a3" for c in n)
        ]
        if not korean_names:
            return set()

        scope_filter = "AND d.kb_id IN $scope" if scope_kb_ids else ""

        # Extract date tokens for document name filtering
        date_tokens = self._extract_date_tokens(query) if query else []
        if date_tokens:
            # Narrow: Person + date in document name
            date_conditions = " OR ".join(
                f"COALESCE(d.name, d.title, '') CONTAINS $dt{i}"
                for i in range(len(date_tokens))
            )
            date_filter = f"AND ({date_conditions})"
        else:
            date_filter = ""

        try:
            cypher = f"""
            UNWIND $names AS person_name
            MATCH (p:Person)-[:MENTIONED_IN]->(d:Document)
            WHERE p.name = person_name
              {scope_filter}
              {date_filter}
            RETURN DISTINCT COALESCE(d.name, d.title, d.url) AS doc_name
            LIMIT $limit
            """
            params: dict[str, Any] = {"names": korean_names, "limit": max_results}
            if scope_kb_ids:
                params["scope"] = scope_kb_ids
            for i, dt in enumerate(date_tokens):
                params[f"dt{i}"] = dt

            records = await self._graph_repo._client.execute_query(cypher, params)
            doc_names = {r["doc_name"] for r in records if r.get("doc_name")}

            # Fallback: if date filter returned nothing, try without date
            if not doc_names and date_tokens:
                cypher_fallback = f"""
                UNWIND $names AS person_name
                MATCH (p:Person)-[:MENTIONED_IN]->(d:Document)
                WHERE p.name = person_name
                  {scope_filter}
                RETURN DISTINCT COALESCE(d.name, d.title, d.url) AS doc_name
                LIMIT $limit
                """
                params_fb: dict[str, Any] = {"names": korean_names, "limit": max_results}
                if scope_kb_ids:
                    params_fb["scope"] = scope_kb_ids
                records = await self._graph_repo._client.execute_query(cypher_fallback, params_fb)
                doc_names = {r["doc_name"] for r in records if r.get("doc_name")}

            if doc_names:
                logger.info(
                    "Person MENTIONED_IN: names=%s date=%s → %d docs: %s",
                    korean_names[:3], date_tokens[:2], len(doc_names), list(doc_names)[:3],
                    korean_names[:3], len(doc_names), list(doc_names)[:3],
                )
            return doc_names
        except Exception as e:
            logger.warning("Person MENTIONED_IN lookup failed: %s", e)
            return set()

    def boost_chunks(
        self,
        chunks: list[dict[str, Any]],
        expanded_uris: set[str],
        graph_distances: dict[str, int] | None = None,
    ) -> list[dict[str, Any]]:
        """Apply dynamic graph_boost to chunks whose source_uri matches expanded set.

        Boost is distance-based: closer relationships get higher boost.
        - Distance 1 (direct): graph_boost * 1.0
        - Distance 2 (2-hop):  graph_boost * 0.6
        - Distance 3+:         graph_boost * 0.3
        """
        if not expanded_uris:
            return chunks

        distances = graph_distances or {}
        boosted: list[dict[str, Any]] = []
        for chunk in chunks:
            uri = chunk.get("source_uri") or ""
            doc_name = chunk.get("document_name") or ""
            # Match by URI or document name
            matched = uri in expanded_uris or doc_name in expanded_uris
            if matched:
                chunk = dict(chunk)
                # Dynamic boost based on graph distance
                dist = distances.get(uri, distances.get(doc_name, 2))
                if dist <= 1:
                    boost = self._graph_boost * 2.0  # Direct relation: 2x boost
                elif dist == 2:
                    boost = self._graph_boost * 1.0  # 2-hop: normal
                else:
                    boost = self._graph_boost * _w.reranker.graph_hop3_multiplier  # 3+: reduced
                chunk["score"] = chunk.get("score", 0.0) + boost
                chunk["graph_boosted"] = True
                chunk["graph_distance"] = dist
                # Set traversal axis for reranker
                chunk.setdefault("metadata", {})
                chunk["metadata"]["graph_distance"] = dist
            boosted.append(chunk)
        return boosted

    async def expand_with_entities(
        self,
        query: str,
        chunks: list[dict[str, Any]],
        *,
        scope_kb_ids: list[str] | None = None,
    ) -> ExpandedSearchResult:
        """Enhanced expansion: also search GraphRAG entities and inject related docs.

        In addition to standard find_related_chunks, this:
        1. Searches __Entity__ nodes (Store, Person, Process) matching query
        2. Finds documents connected to those entities via source_document
        3. Returns those document titles as expanded URIs for boosting/injection
        """
        # Standard expansion first
        result = await self.expand(query, chunks, scope_kb_ids=scope_kb_ids)

        # Entity-based expansion: find documents via source_document property
        try:
            if hasattr(self._graph_repo, "search_entities"):
                tokens = _split_compound_words(query)
                entity_names = [t for t in tokens if len(t) >= 2]
                if entity_names:
                    entities = await self._graph_repo.search_entities(
                        entity_names, max_facts=30,
                    )
                    entity_doc_names: set[str] = set()
                    for e in entities:
                        # 1. Connected Document nodes
                        connected = e.get("connected_name", "")
                        connected_type = e.get("connected_type", "")
                        if connected_type == "Document" and connected:
                            entity_doc_names.add(connected)

                    # 2. source_document property on matched entities
                    # Search each keyword separately (OR logic)
                    if hasattr(self._graph_repo, "_client"):
                        import unicodedata
                        for kw in entity_names:
                            try:
                                kw_nfc = unicodedata.normalize("NFC", kw)
                                kw_nfd = unicodedata.normalize("NFD", kw)
                                src_docs = await self._graph_repo._client.execute_query(
                                    "MATCH (n:__Entity__) "
                                    "WHERE (n.id CONTAINS $kw_nfc OR n.id CONTAINS $kw_nfd) "
                                    "AND n.source_document IS NOT NULL "
                                    "RETURN DISTINCT n.source_document AS doc LIMIT 5",
                                    {"kw_nfc": kw_nfc, "kw_nfd": kw_nfd},
                                )
                                for row in src_docs:
                                    doc = row.get("doc", "")
                                    if doc:
                                        entity_doc_names.add(doc)
                            except Exception:
                                pass

                    logger.info(
                        "Entity expansion: %d entities found, %d doc names extracted",
                        len(entities), len(entity_doc_names),
                    )
                    if entity_doc_names:
                        result.expanded_source_uris |= entity_doc_names
                        result.graph_related_count += len(entity_doc_names)

                    # Person MENTIONED_IN: find docs where query Person is mentioned
                    person_docs = await self._find_person_mentioned_docs(
                        entity_names, query=query, scope_kb_ids=scope_kb_ids,
                    )
                    if person_docs:
                        new_person = person_docs - result.expanded_source_uris
                        result.expanded_source_uris |= new_person
                        result.graph_related_count += len(new_person)
        except Exception as e:
            logger.warning("Entity expansion failed: %s", e)

        return result


class NoOpGraphSearchExpander:
    """No-op expander for when Neo4j is unavailable."""

    async def expand(
        self,
        _query: str,
        chunks: list[dict[str, Any]],
        **_kwargs: Any,
    ) -> ExpandedSearchResult:
        await asyncio.sleep(0)
        return ExpandedSearchResult(
            original_chunks=chunks,
            expanded_source_uris=set(),
            graph_related_count=0,
        )

    def boost_chunks(
        self,
        chunks: list[dict[str, Any]],
        _expanded_uris: set[str],
    ) -> list[dict[str, Any]]:
        return chunks


__all__ = [
    "ExpandedSearchResult",
    "GraphSearchExpander",
    "IGraphRepository",
    "NoOpGraphSearchExpander",
]
