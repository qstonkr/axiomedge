"""Unit tests for src/search/query_classifier.py."""

from src.search.query_classifier import QueryClassifier, QueryType, ClassificationResult, resolve_query_type_tag


class TestQueryTypeEnum:
    """Test QueryType enum values."""

    def test_all_values(self) -> None:
        assert QueryType.CHITCHAT.value == "chitchat"
        assert QueryType.FACTUAL.value == "factual"
        assert QueryType.ANALYTICAL.value == "analytical"
        assert QueryType.ADVISORY.value == "advisory"
        assert QueryType.COMPARATIVE.value == "comparative"
        assert QueryType.MULTI_HOP.value == "multi_hop"
        assert QueryType.UNKNOWN.value == "unknown"

    def test_is_string_enum(self) -> None:
        # QueryType inherits from str
        assert isinstance(QueryType.FACTUAL, str)
        assert QueryType.FACTUAL == "factual"


class TestQueryClassifier:
    """Test rule-based query classification."""

    def setup_method(self) -> None:
        self.classifier = QueryClassifier()

    def test_empty_query_returns_unknown(self) -> None:
        result = self.classifier.classify("")
        assert result.query_type == QueryType.UNKNOWN
        assert result.confidence == 0.0

    def test_whitespace_query_returns_unknown(self) -> None:
        result = self.classifier.classify("   ")
        assert result.query_type == QueryType.UNKNOWN

    def test_chitchat_greeting(self) -> None:
        result = self.classifier.classify("안녕하세요")
        assert result.query_type == QueryType.CHITCHAT
        assert result.confidence >= 0.7

    def test_chitchat_hello_english(self) -> None:
        result = self.classifier.classify("hello")
        assert result.query_type == QueryType.CHITCHAT

    def test_chitchat_emoji_like(self) -> None:
        result = self.classifier.classify("ㅋㅋ")
        assert result.query_type == QueryType.CHITCHAT

    def test_factual_question(self) -> None:
        result = self.classifier.classify("VPN 담당자 누구인가요?")
        assert result.query_type == QueryType.FACTUAL
        assert result.confidence >= 0.7
        assert len(result.matched_patterns) >= 1

    def test_factual_procedure(self) -> None:
        result = self.classifier.classify("비밀번호 변경 절차 뭐예요?")
        assert result.query_type == QueryType.FACTUAL

    def test_analytical_question(self) -> None:
        result = self.classifier.classify("왜 서버가 다운되었나요?")
        assert result.query_type == QueryType.ANALYTICAL
        assert result.confidence >= 0.7

    def test_analytical_cause(self) -> None:
        result = self.classifier.classify("장애 원인 뭐야")
        assert result.query_type == QueryType.ANALYTICAL

    def test_advisory_question(self) -> None:
        result = self.classifier.classify("성능 개선 방법 추천해주세요")
        assert result.query_type == QueryType.ADVISORY

    def test_comparative_question(self) -> None:
        result = self.classifier.classify("AWS vs Azure 차이 뭐야")
        assert result.query_type == QueryType.COMPARATIVE

    def test_multi_hop_question(self) -> None:
        result = self.classifier.classify("먼저 A를 하고 다음 B를 해야 하나요?")
        assert result.query_type == QueryType.MULTI_HOP

    def test_unknown_fallback_to_factual(self) -> None:
        # Queries that match no pattern default to FACTUAL with low confidence
        result = self.classifier.classify("블루베리 스무디 레시피")
        assert result.query_type == QueryType.FACTUAL
        assert result.confidence == 0.5
        assert result.reasoning == "default fallback"

    def test_confidence_increases_with_more_matches(self) -> None:
        # "담당자 누구" matches multiple factual patterns
        result = self.classifier.classify("담당자 누구인가요?")
        assert result.confidence > 0.7  # base 0.7 + 0.1 per extra match

    def test_classification_result_dataclass(self) -> None:
        result = ClassificationResult(
            query_type=QueryType.FACTUAL,
            confidence=0.8,
            matched_patterns=["pattern1"],
            reasoning="test",
        )
        assert result.query_type == QueryType.FACTUAL
        assert result.confidence == 0.8
        assert result.reasoning == "test"


class TestResolveQueryTypeTag:
    """Test resolve_query_type_tag helper."""

    def test_returns_enum_value(self) -> None:
        assert resolve_query_type_tag(QueryType.FACTUAL) == "factual"
        assert resolve_query_type_tag(QueryType.CHITCHAT) == "chitchat"
        assert resolve_query_type_tag(QueryType.UNKNOWN) == "unknown"
