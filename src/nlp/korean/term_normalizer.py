"""Term Normalizer Utility

용어 정규화 유틸리티.
LLM 추출 정확도 향상을 위한 일관된 용어 처리.

설계서 참조:
- KNOWLEDGE_EXTRACTION_SYSTEM_DESIGN.md: LLM 용어 도출 정확도 개선

Created: 2026-02-05
Extracted from: oreo-ecosystem (domain/knowledge/utils/term_normalizer.py)
"""

from __future__ import annotations

import re
import unicodedata


class TermNormalizer:
    """용어 정규화 유틸리티

    용어 비교 및 검색 시 일관된 형태로 정규화합니다.

    주요 기능:
    - Unicode 정규화 (NFD → NFC)
    - 공백 정규화
    - 하이픈/언더스코어 통일
    - 대소문자 정규화
    - 특수문자 처리

    Examples:
        >>> normalizer = TermNormalizer()
        >>> normalizer.normalize("  Graph-RAG  ")
        'graph-rag'
        >>> normalizer.normalize("데이터_마트")
        '데이터-마트'
        >>> normalizer.normalize_for_comparison("GraphRAG")
        'graphrag'
    """

    # 정규화에서 보존할 문자 패턴
    ALLOWED_CHARS_PATTERN = re.compile(r"[^a-zA-Z0-9가-힣\s\-_]")

    # 연속 공백 패턴
    MULTI_SPACE_PATTERN = re.compile(r"\s+")

    # 연속 하이픈/언더스코어 패턴
    MULTI_SEPARATOR_PATTERN = re.compile(r"[-_]+")

    @classmethod
    def normalize(cls, term: str) -> str:
        """표준 정규화

        용어를 일관된 형태로 정규화합니다.
        저장 및 표시용으로 사용됩니다.

        Args:
            term: 정규화할 용어

        Returns:
            정규화된 용어

        Examples:
            >>> TermNormalizer.normalize("  Graph-RAG  ")
            'graph-rag'
            >>> TermNormalizer.normalize("API_Gateway")
            'api-gateway'
        """
        if not term:
            return ""

        # 1. Unicode 정규화 (NFD → NFC)
        # 한글 자모 분리 문제 해결
        normalized = unicodedata.normalize("NFC", term)

        # 2. 공백 정규화 (strip + 연속 공백 제거)
        normalized = cls.MULTI_SPACE_PATTERN.sub(" ", normalized.strip())

        # 3. 언더스코어를 하이픈으로 통일
        normalized = normalized.replace("_", "-")

        # 4. 연속 하이픈 정리
        normalized = cls.MULTI_SEPARATOR_PATTERN.sub("-", normalized)

        # 5. 소문자 변환
        normalized = normalized.lower()

        # 6. 양끝 하이픈 제거
        normalized = normalized.strip("-")

        return normalized

    @classmethod
    def normalize_for_comparison(cls, term: str) -> str:
        """비교용 정규화

        두 용어가 동일한지 비교할 때 사용합니다.
        모든 구분자와 공백을 제거합니다.

        Args:
            term: 비교할 용어

        Returns:
            비교용 정규화된 용어

        Examples:
            >>> TermNormalizer.normalize_for_comparison("Graph-RAG")
            'graphrag'
            >>> TermNormalizer.normalize_for_comparison("Graph RAG")
            'graphrag'
            >>> TermNormalizer.normalize_for_comparison("GraphRAG")
            'graphrag'
        """
        if not term:
            return ""

        # 1. 기본 정규화 적용
        normalized = cls.normalize(term)

        # 2. 모든 하이픈과 공백 제거
        normalized = normalized.replace("-", "").replace(" ", "")

        return normalized

    @classmethod
    def normalize_for_search(cls, term: str) -> str:
        """검색용 정규화

        검색 쿼리에 사용할 용어를 정규화합니다.
        특수문자를 제거하고 검색에 최적화합니다.

        Args:
            term: 검색할 용어

        Returns:
            검색용 정규화된 용어

        Examples:
            >>> TermNormalizer.normalize_for_search("K8s/Pod")
            'k8s pod'
        """
        if not term:
            return ""

        # 1. Unicode 정규화
        normalized = unicodedata.normalize("NFC", term)

        # 2. 허용되지 않는 특수문자를 공백으로 변환
        normalized = cls.ALLOWED_CHARS_PATTERN.sub(" ", normalized)

        # 3. 하이픈/언더스코어를 공백으로 변환
        normalized = normalized.replace("-", " ").replace("_", " ")

        # 4. 연속 공백 정리
        normalized = cls.MULTI_SPACE_PATTERN.sub(" ", normalized.strip())

        # 5. 소문자 변환
        return normalized.lower()

    @classmethod
    def is_normalized_variant(cls, term: str, base_term: str) -> bool:
        """정규화 변형 여부 확인

        두 용어가 정규화 후 동일한지 확인합니다.
        동의어 검증 시 정규화 변형을 필터링하는 데 사용됩니다.

        Args:
            term: 확인할 용어
            base_term: 기준 용어

        Returns:
            정규화 변형이면 True

        Examples:
            >>> TermNormalizer.is_normalized_variant("graph-rag", "GraphRAG")
            True
            >>> TermNormalizer.is_normalized_variant("RAG", "GraphRAG")
            False
        """
        return cls.normalize_for_comparison(term) == cls.normalize_for_comparison(
            base_term
        )

    @classmethod
    def extract_abbreviation_candidates(cls, term: str) -> list[str]:
        """약어 후보 추출

        용어에서 가능한 약어 패턴을 추출합니다.

        Args:
            term: 용어

        Returns:
            약어 후보 목록

        Examples:
            >>> TermNormalizer.extract_abbreviation_candidates("Data Mart")
            ['DM', 'dm']
            >>> TermNormalizer.extract_abbreviation_candidates("API Gateway")
            ['AG', 'ag']
        """
        if not term:
            return []

        candidates: list[str] = []

        # 공백으로 분리된 단어들의 첫 글자 조합
        words = term.split()
        if len(words) >= 2:
            initials_upper = "".join(w[0].upper() for w in words if w)
            initials_lower = initials_upper.lower()
            candidates.extend([initials_upper, initials_lower])

        # 하이픈으로 분리된 단어들의 첫 글자 조합
        parts = term.replace("_", "-").split("-")
        if len(parts) >= 2:
            initials_upper = "".join(p[0].upper() for p in parts if p)
            initials_lower = initials_upper.lower()
            if initials_upper not in candidates:
                candidates.extend([initials_upper, initials_lower])

        return candidates

    @classmethod
    def is_likely_abbreviation(cls, term: str) -> bool:
        """약어 여부 추정

        용어가 약어일 가능성이 높은지 판단합니다.

        Args:
            term: 용어

        Returns:
            약어로 추정되면 True

        Examples:
            >>> TermNormalizer.is_likely_abbreviation("DM")
            True
            >>> TermNormalizer.is_likely_abbreviation("API")
            True
            >>> TermNormalizer.is_likely_abbreviation("데이터마트")
            False
        """
        if not term:
            return False

        # 2-5자의 영문 대문자만으로 구성
        if re.match(r"^[A-Z]{2,5}$", term):
            return True

        # 2-5자의 영문 소문자만으로 구성
        if re.match(r"^[a-z]{2,5}$", term):
            return True

        # 숫자가 포함된 약어 (K8s, S3, EC2 등)
        if re.match(r"^[A-Za-z][A-Za-z0-9]{1,4}$", term):
            # 숫자가 있어야 약어로 판단
            if any(c.isdigit() for c in term):
                return True

        return False
