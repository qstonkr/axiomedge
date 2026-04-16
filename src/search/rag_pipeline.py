"""Knowledge RAG Pipeline - Local standalone version.

Pipeline flow:
1. Query Classification
2. Owner query special handling (hallucination-free)
3. Vector Search (Qdrant hybrid)
4. LLM Response Generation (EXAONE via Ollama)
5. Source formatting with citations
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, AsyncIterator

from src.config_weights import weights

logger = logging.getLogger(__name__)


class QueryIntent(StrEnum):
    """Domain intent classification (distinct from query_classifier.QueryType)."""
    OWNER_QUERY = "owner_query"
    PROCEDURE = "procedure"
    TROUBLESHOOT = "troubleshoot"
    CONCEPT = "concept"
    GENERAL = "general"



@dataclass
class RAGRequest:
    query: str
    kb_id: str | None = None
    top_k: int = field(default_factory=lambda: weights.search.top_k)
    filter_stale: bool = False
    include_sources: bool = True
    stream: bool = False


@dataclass
class RAGResponse:
    query: str
    answer: str
    sources: list[dict] = field(default_factory=list)
    query_type: QueryIntent = QueryIntent.GENERAL
    confidence: float = 0.0
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "query": self.query,
            "answer": self.answer,
            "sources": self.sources,
            "query_type": self.query_type.value,
            "confidence": self.confidence,
            "metadata": self.metadata,
        }


class KnowledgeRAGPipeline:
    """Knowledge RAG pipeline with local EXAONE LLM and BGE-M3 embedding."""

    # Owner query patterns
    OWNER_PATTERNS = [
        "담당자", "누가 담당", "관리자", "연락처", "책임자",
        "who is responsible", "who manages",
    ]

    def __init__(
        self,
        search_engine: Any = None,
        llm_client: Any = None,
        graph_client: Any = None,
        embedder: Any = None,
        query_preprocessor: Any = None,
        query_expander: Any = None,
    ):
        self._search = search_engine
        self._llm = llm_client
        self._graph = graph_client
        self._embedder = embedder
        self._query_preprocessor = query_preprocessor
        self._query_expander = query_expander

    def _preprocess_query(self, query: str) -> str:
        """Apply query preprocessing (typo correction + expansion)."""
        preprocessed = query
        try:
            if self._query_preprocessor:
                pp_result = self._query_preprocessor.preprocess(query)
                preprocessed = pp_result.corrected_query
                if pp_result.was_corrected:
                    logger.info(
                        "RAG query preprocessed: '%s' -> '%s'",
                        query[:80],
                        preprocessed[:80],
                    )
        except Exception as e:  # noqa: BLE001
            logger.warning("QueryPreprocessor failed in RAG pipeline, using original: %s", e)
        return preprocessed

    async def _expand_query(self, query: str, kb_id: str | None) -> str:
        """Expand query via glossary synonyms."""
        if not self._query_expander:
            return query
        try:
            expanded = await self._query_expander.expand_query(
                kb_id=kb_id or "all",
                query=query,
            )
            expanded_text = getattr(expanded, "expanded_query", None)
            if expanded_text and expanded_text != query:
                return expanded_text
        except Exception as e:  # noqa: BLE001
            logger.warning("Query expansion failed: %s", e)
        return query

    async def _embed_and_search(
        self, preprocessed_query: str, kb_id: str, top_k: int,
    ) -> list[Any]:
        """Encode query and run hybrid search."""
        colbert_enabled = weights.hybrid_search.enable_colbert_reranking
        encoded = await asyncio.to_thread(
            self._embedder.encode, [preprocessed_query], True, True, colbert_enabled,
        )
        dense_vector = encoded["dense_vecs"][0]
        sparse_weights = encoded["lexical_weights"][0] if encoded.get("lexical_weights") else {}
        sparse_vector = (
            {int(k): float(v) for k, v in sparse_weights.items()} if sparse_weights else None
        )
        colbert_vectors = (
            encoded["colbert_vecs"][0]
            if colbert_enabled and encoded.get("colbert_vecs")
            else None
        )

        if colbert_enabled and colbert_vectors:
            return await self._search.search_with_colbert_rerank(
                kb_id=kb_id, dense_vector=dense_vector,
                sparse_vector=sparse_vector, colbert_vectors=colbert_vectors,
                top_k=top_k,
            )
        return await self._search.search(
            kb_id=kb_id, dense_vector=dense_vector,
            sparse_vector=sparse_vector, top_k=top_k,
        )

    @staticmethod
    def _build_context(
        result_dicts: list[dict], include_sources: bool,
    ) -> tuple[str, list[dict]]:
        """Build context text and source list from search results."""
        context_chunks = []
        sources = []
        for i, result in enumerate(result_dicts):
            context_chunks.append(f"[{i+1}] {result['content']}")
            if include_sources:
                sources.append({
                    "index": i + 1,
                    "document_name": result["metadata"].get("document_name", ""),
                    "source_uri": result["metadata"].get("source_uri", ""),
                    "score": result["score"],
                    "kb_id": result["metadata"].get("kb_id", ""),
                })
        return "\n\n".join(context_chunks), sources

    async def process(self, request: RAGRequest) -> RAGResponse:
        """Execute RAG pipeline."""
        query = request.query.strip()

        preprocessed_query = self._preprocess_query(query)
        preprocessed_query = await self._expand_query(preprocessed_query, request.kb_id)
        query_type = self._classify_query(preprocessed_query)

        if query_type == QueryIntent.OWNER_QUERY and self._graph:
            return await self._handle_owner_query(request, preprocessed_query)

        if not self._search:
            return RAGResponse(
                query=query, answer="검색 엔진이 초기화되지 않았습니다.",
                query_type=query_type,
            )
        if not self._embedder:
            return RAGResponse(
                query=query, answer="임베딩 프로바이더가 초기화되지 않았습니다.",
                query_type=query_type,
            )

        search_results = await self._embed_and_search(
            preprocessed_query, request.kb_id or "knowledge", request.top_k,
        )

        if not search_results:
            return RAGResponse(
                query=query,
                answer=f"'{query}'에 대한 관련 문서를 찾을 수 없습니다.",
                query_type=query_type,
                confidence=0.0,
            )

        result_dicts = [
            {"point_id": r.point_id, "score": r.score, "content": r.content, "metadata": r.metadata}
            for r in search_results
        ]

        context_text, sources = self._build_context(result_dicts, request.include_sources)

        if self._llm:
            answer = await self._llm.generate_with_context(
                query=preprocessed_query, context=context_text,
            )
        else:
            answer = f"검색 결과 {len(result_dicts)}건이 있습니다.\n\n" + context_text

        confidence = self._calculate_confidence(result_dicts)

        return RAGResponse(
            query=query,
            answer=answer,
            sources=sources,
            query_type=query_type,
            confidence=confidence,
            metadata={
                "search_count": len(result_dicts),
                "top_score": result_dicts[0]["score"] if result_dicts else 0,
            },
        )

    async def process_stream(self, request: RAGRequest) -> AsyncIterator[str]:
        """Streaming RAG pipeline."""
        query = request.query.strip()
        preprocessed_query = self._preprocess_query(query)

        query_type = self._classify_query(preprocessed_query)

        if query_type == QueryIntent.OWNER_QUERY and self._graph:
            response = await self._handle_owner_query(request, preprocessed_query)
            yield response.answer
            return

        if not self._search:
            yield "검색 엔진이 초기화되지 않았습니다."
            return

        if not self._embedder:
            yield "임베딩 프로바이더가 초기화되지 않았습니다."
            return

        search_results = await self._embed_and_search(
            preprocessed_query, request.kb_id or "knowledge", request.top_k,
        )

        if not search_results:
            yield f"'{query}'에 대한 관련 문서를 찾을 수 없습니다."
            return

        context_chunks = [
            f"[{i+1}] {r.content}"
            for i, r in enumerate(search_results)
        ]
        context_text = "\n\n".join(context_chunks)

        if self._llm and hasattr(self._llm, "generate_stream"):
            async for token in self._llm.generate_stream(
                query=preprocessed_query, context=context_text,
            ):
                yield token
        else:
            response = await self.process(request)
            yield response.answer

    def _classify_query(self, query: str) -> QueryIntent:
        query_lower = query.lower()
        for pattern in self.OWNER_PATTERNS:
            if pattern in query_lower:
                return QueryIntent.OWNER_QUERY
        if any(k in query_lower for k in ["절차", "방법", "순서", "가이드", "매뉴얼"]):
            return QueryIntent.PROCEDURE
        if any(k in query_lower for k in ["에러", "오류", "문제", "안됨", "실패"]):
            return QueryIntent.TROUBLESHOOT
        if any(k in query_lower for k in ["뭐", "무엇", "개념", "정의", "설명"]):
            return QueryIntent.CONCEPT
        return QueryIntent.GENERAL

    async def _handle_owner_query(self, request: RAGRequest, query: str) -> RAGResponse:
        """Handle owner queries via graph DB (hallucination-free)."""
        try:
            results = await self._graph.execute_query(
                """
                MATCH (p:Person)-[r:RESPONSIBLE_FOR|OWNS|MANAGES]->(t)
                WHERE toLower(t.name) CONTAINS toLower($topic)
                   OR toLower(t.title) CONTAINS toLower($topic)
                RETURN p.name AS person, type(r) AS role,
                       t.name AS topic, labels(t) AS labels
                LIMIT 10
                """,
                {"topic": query},
            )
            if results:
                lines = []
                for r in results:
                    lines.append(f"- {r['person']} ({r['role']}) → {r['topic']}")
                answer = "담당자 정보:\n" + "\n".join(lines)
            else:
                answer = f"'{query}' 관련 담당자 정보를 찾을 수 없습니다."
        except Exception as e:  # noqa: BLE001
            logger.warning("Owner query failed: %s", e)
            answer = f"담당자 조회 중 오류가 발생했습니다: {e}"

        return RAGResponse(
            query=request.query,
            answer=answer,
            query_type=QueryIntent.OWNER_QUERY,
            confidence=1.0 if "담당자 정보:" in answer else 0.0,
        )

    @staticmethod
    def _calculate_confidence(results: list[dict]) -> float:
        if not results:
            return 0.0
        scores = [r.get("score", 0) for r in results]
        top = max(scores) if scores else 0
        # SSOT: config_weights.ConfidenceConfig
        if top >= weights.confidence.high:
            return 0.9
        elif top >= weights.confidence.medium:
            return 0.7
        elif top >= weights.confidence.low:
            return 0.5
        return 0.3
