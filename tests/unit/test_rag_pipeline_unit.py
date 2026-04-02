"""Unit tests for RAG pipeline — QueryIntent, classify, confidence."""

from src.search.rag_pipeline import (
    QueryIntent,
    KnowledgeRAGPipeline,
    RAGRequest,
    RAGResponse,
)


class TestQueryIntent:
    def test_values(self) -> None:
        assert QueryIntent.OWNER_QUERY == "owner_query"
        assert QueryIntent.PROCEDURE == "procedure"
        assert QueryIntent.TROUBLESHOOT == "troubleshoot"
        assert QueryIntent.CONCEPT == "concept"
        assert QueryIntent.GENERAL == "general"

    def test_is_str_enum(self) -> None:
        assert isinstance(QueryIntent.GENERAL, str)
        assert QueryIntent.GENERAL == "general"


class TestRAGRequest:
    def test_defaults(self) -> None:
        req = RAGRequest(query="test")
        assert req.query == "test"
        assert req.kb_id is None
        assert req.stream is False

    def test_custom_top_k(self) -> None:
        req = RAGRequest(query="test", top_k=3)
        assert req.top_k == 3


class TestRAGResponse:
    def test_to_dict(self) -> None:
        resp = RAGResponse(
            query="q", answer="a",
            query_type=QueryIntent.GENERAL,
            confidence=0.9,
        )
        d = resp.to_dict()
        assert d["query"] == "q"
        assert d["answer"] == "a"
        assert d["query_type"] == "general"
        assert d["confidence"] == 0.9


class TestClassifyQuery:
    def setup_method(self) -> None:
        self.pipeline = KnowledgeRAGPipeline()

    def test_owner_query(self) -> None:
        assert self.pipeline._classify_query("이 시스템 담당자가 누구야?") == QueryIntent.OWNER_QUERY

    def test_procedure(self) -> None:
        result = self.pipeline._classify_query("서버 배포 절차를 알려줘")
        assert result == QueryIntent.PROCEDURE

    def test_troubleshoot(self) -> None:
        result = self.pipeline._classify_query("로그인 에러가 발생합니다")
        assert result == QueryIntent.TROUBLESHOOT

    def test_concept(self) -> None:
        result = self.pipeline._classify_query("쿠버네티스란 무엇인가")
        assert result == QueryIntent.CONCEPT

    def test_general_fallback(self) -> None:
        result = self.pipeline._classify_query("최근 매출 현황")
        assert result == QueryIntent.GENERAL


class TestCalculateConfidence:
    def test_empty_results(self) -> None:
        assert KnowledgeRAGPipeline._calculate_confidence([]) == 0.0

    def test_high_score(self) -> None:
        results = [{"score": 0.9}]
        assert KnowledgeRAGPipeline._calculate_confidence(results) == 0.9

    def test_medium_score(self) -> None:
        results = [{"score": 0.75}]
        assert KnowledgeRAGPipeline._calculate_confidence(results) == 0.7

    def test_low_score(self) -> None:
        results = [{"score": 0.55}]
        assert KnowledgeRAGPipeline._calculate_confidence(results) == 0.5

    def test_very_low_score(self) -> None:
        results = [{"score": 0.3}]
        assert KnowledgeRAGPipeline._calculate_confidence(results) == 0.3
