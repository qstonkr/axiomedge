"""Search services factory — initializes all search/RAG pipeline components.

Extracted from ``app.py::_init_search_services`` for testability and SRP.
Each component is initialized independently; failure in one does not block others.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class SearchServicesFactory:
    """Initialize search pipeline services into an AppState-compatible dict.

    Usage::

        factory = SearchServicesFactory(state)
        await factory.initialize()
    """

    def __init__(self, state: dict[str, Any]) -> None:
        self._state = state

    async def initialize(self) -> None:
        """Initialize all search services. Failures are logged, not raised."""
        self._init_query_preprocessor()
        self._init_composite_reranker()
        self._warmup_cross_encoder()
        self._init_query_classifier()
        self._init_tiered_response()
        self._init_answer_service()
        self._init_crag_evaluator()
        self._init_query_expander()
        self._init_rag_pipeline()

    def _init_query_preprocessor(self) -> None:
        try:
            from src.search.query_preprocessor import QueryPreprocessor
            self._state["query_preprocessor"] = QueryPreprocessor()
            logger.info("QueryPreprocessor initialized")
        except Exception as e:  # noqa: BLE001
            logger.warning("QueryPreprocessor init failed: %s", e)

    def _init_composite_reranker(self) -> None:
        try:
            from src.search.composite_reranker import CompositeReranker
            self._state["composite_reranker"] = CompositeReranker()
            logger.info("CompositeReranker initialized")
        except Exception as e:  # noqa: BLE001
            logger.warning("CompositeReranker init failed: %s", e)

    def _warmup_cross_encoder(self) -> None:
        try:
            from src.search.cross_encoder_reranker import warmup as ce_warmup
            ce_warmup()
            logger.info("Cross-encoder warmup started")
        except Exception as e:  # noqa: BLE001
            logger.warning("Cross-encoder warmup failed: %s", e)

    def _init_query_classifier(self) -> None:
        try:
            from src.search.query_classifier import QueryClassifier
            self._state["query_classifier"] = QueryClassifier()
            logger.info("QueryClassifier initialized")
        except Exception as e:  # noqa: BLE001
            logger.warning("QueryClassifier init failed: %s", e)

    def _init_tiered_response(self) -> None:
        if not self._state.get("llm"):
            return
        try:
            from src.search.tiered_response import TieredResponseGenerator
            self._state["tiered_response_generator"] = TieredResponseGenerator(
                llm_client=self._state["llm"],
            )
            logger.info("TieredResponseGenerator initialized")
        except Exception as e:  # noqa: BLE001
            logger.warning("TieredResponseGenerator init failed: %s", e)

    def _init_answer_service(self) -> None:
        if not self._state.get("llm"):
            return
        try:
            from src.search.answer_service import AnswerService
            self._state["answer_service"] = AnswerService(llm_client=self._state["llm"])
            logger.info("AnswerService initialized")
        except Exception as e:  # noqa: BLE001
            logger.warning("AnswerService init failed: %s", e)

    def _init_crag_evaluator(self) -> None:
        try:
            from src.search.crag_evaluator import CRAGRetrievalEvaluator
            self._state["crag_evaluator"] = CRAGRetrievalEvaluator()
            logger.info("CRAGRetrievalEvaluator initialized")
        except Exception as e:  # noqa: BLE001
            logger.warning("CRAGRetrievalEvaluator init failed: %s", e)

    def _init_query_expander(self) -> None:
        try:
            from src.search.query_expansion import QueryExpansionService
            self._state["query_expander"] = QueryExpansionService(
                glossary_repository=self._state.get("glossary_repo"),
            )
            logger.info("QueryExpansionService initialized")
        except Exception as e:  # noqa: BLE001
            logger.warning("QueryExpansionService init failed: %s", e)

    def _init_rag_pipeline(self) -> None:
        from src.search.rag_pipeline import KnowledgeRAGPipeline

        self._state["rag_pipeline"] = KnowledgeRAGPipeline(
            search_engine=self._state.get("qdrant_search"),
            llm_client=self._state.get("llm"),
            graph_client=self._state.get("neo4j"),
            embedder=self._state.get("embedder"),
            query_preprocessor=self._state.get("query_preprocessor"),
            query_expander=self._state.get("query_expander"),
        )

        missing = [k for k in ["qdrant_search", "embedder", "llm"] if k not in self._state]
        if missing:
            logger.warning("RAG pipeline initialized without: %s", missing)
