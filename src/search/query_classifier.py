"""Query Classifier - 질의 유형 분류 서비스.

규칙 기반 패턴 매칭으로 질의 유형을 분류하여 응답 전략을 결정.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class QueryType(str, Enum):
    CHITCHAT = "chitchat"
    FACTUAL = "factual"
    ANALYTICAL = "analytical"
    ADVISORY = "advisory"
    COMPARATIVE = "comparative"
    MULTI_HOP = "multi_hop"
    UNKNOWN = "unknown"


@dataclass
class ClassificationResult:
    query_type: QueryType
    confidence: float
    matched_patterns: list[str]
    reasoning: str | None = None


class QueryClassifier:
    """규칙 기반 질의 유형 분류기."""

    CHITCHAT_PATTERNS = [
        r"^안녕\s*(?:하세요|하십니까|히|요|!|$)",
        r"^(?:하이|헬로|hello|hi|hey)\b",
        r"^반갑습니다",
        r"^좋은\s*(?:아침|오후|저녁)",
        r"^고마워|^감사합니다|^감사해",
        r"^잘\s*(?:지내|있)",
        r"^수고하세요|^수고했",
        r"^오랜만",
        r"^뭐해\s*$|^심심\s*$",
        r"^ㅎㅎ\s*$|^ㅋㅋ\s*$|^ㅎ+\s*$|^ㅋ+\s*$",
        r"^네\s*$|^응\s*$|^아니\s*$|^아뇨\s*$",
        r"^(?:넌|너)\s*(?:누구|뭐야|뭐)",
        r"^자기\s*소개",
    ]

    FACTUAL_PATTERNS = [
        r"담당자.*누구", r"연락처.*알려", r".*절차.*뭐", r".*방법.*어떻게",
        r".*정책.*무엇", r".*규정.*뭐", r"누가.*담당", r"어디서.*신청",
        r"언제.*까지", r"몇.*일", r"얼마.*인가", r"무슨.*이", r"어떤.*있",
        r"담당자", r"관리자", r"정책", r"절차", r"프로세스",
    ]

    ANALYTICAL_PATTERNS = [
        r"왜\s", r"원인.*뭐", r"이유.*뭐", r"분석.*해",
        r"장단점", r"영향.*뭐",
    ]

    ADVISORY_PATTERNS = [
        r"추천.*해", r"제안.*해", r"어떻게\s*하면", r"방안.*뭐",
        r"개선.*방법", r".*좋을까",
    ]

    COMPARATIVE_PATTERNS = [
        r".*vs\s", r"차이.*뭐", r"비교.*해", r".*다른\s*점",
    ]

    MULTI_HOP_PATTERNS = [
        r"먼저.*다음", r".*한\s*후에", r"순서.*뭐", r"단계.*뭐",
    ]

    def classify(self, query: str) -> ClassificationResult:
        query_stripped = query.strip()
        if not query_stripped:
            return ClassificationResult(QueryType.UNKNOWN, 0.0, [])

        # Check patterns in priority order
        for patterns, qtype in [
            (self.CHITCHAT_PATTERNS, QueryType.CHITCHAT),
            (self.MULTI_HOP_PATTERNS, QueryType.MULTI_HOP),
            (self.COMPARATIVE_PATTERNS, QueryType.COMPARATIVE),
            (self.ANALYTICAL_PATTERNS, QueryType.ANALYTICAL),
            (self.ADVISORY_PATTERNS, QueryType.ADVISORY),
            (self.FACTUAL_PATTERNS, QueryType.FACTUAL),
        ]:
            matched = [p for p in patterns if re.search(p, query_stripped, re.IGNORECASE)]
            if matched:
                confidence = min(0.95, 0.7 + 0.1 * len(matched))
                return ClassificationResult(qtype, confidence, matched)

        return ClassificationResult(QueryType.FACTUAL, 0.5, [], "default fallback")


def resolve_query_type_tag(query_type: QueryType) -> str:
    return query_type.value
