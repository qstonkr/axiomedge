"""Korean Morpheme Analyzer

Kiwi 기반 한국어 형태소 분석기.
동의어 검증, 용어 추출 시 조사 분리 및 원형 복원에 사용.

설계서 참조:
- KNOWLEDGE_EXTRACTION_SYSTEM_DESIGN.md: 한국어 형태소 분석

주요 기능:
1. 조사 분리: "데이터마트는" → "데이터마트" + "는"
2. 원형 복원: "했습니다" → "하다"
3. 명사 추출: 복합명사 및 기술 용어 추출
4. 품사 태깅: NNG(일반명사), NNP(고유명사), SL(외국어)

Created: 2026-02-05
Extracted from: oreo-ecosystem (domain/knowledge/utils/korean_morpheme_analyzer.py)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from functools import lru_cache
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

# Lazy loading for kiwipiepy (optional dependency)
if TYPE_CHECKING:
    from kiwipiepy import Kiwi


@dataclass
class MorphemeToken:
    """형태소 토큰

    Attributes:
        form: 표층형 (원본 형태)
        tag: 품사 태그 (NNG, NNP, VV 등)
        start: 시작 위치
        end: 끝 위치
        lemma: 원형 (해당되는 경우)
    """
    form: str
    tag: str
    start: int = 0
    end: int = 0
    lemma: str | None = None


@dataclass
class AnalysisResult:
    """형태소 분석 결과

    Attributes:
        tokens: 형태소 토큰 목록
        nouns: 추출된 명사 목록 (NNG, NNP)
        stems: 원형 복원된 어간 목록
        original: 원본 텍스트
    """
    tokens: list[MorphemeToken]
    nouns: list[str]
    stems: list[str]
    original: str


# 한국어 조사 목록 (형태소 분석 없이 정규식 기반 처리용 폴백)
KOREAN_PARTICLES = frozenset({
    # 격조사
    "이", "가", "을", "를", "의", "에", "에서", "에게", "께", "한테",
    "로", "으로", "와", "과", "랑", "이랑",
    # 보조사
    "은", "는", "도", "만", "까지", "부터", "조차", "마저", "밖에",
    # 접속조사/인용조사
    "라고", "이라고", "라며", "이라며", "라는", "이라는", "라면", "이라면",
    "하고", "이라", "이야", "야",
    # 종결어미 (동사/형용사)
    "다", "입니다", "합니다", "됩니다", "습니다", "ㅂ니다",
})

# 품사 태그 매핑 (Kiwi 기준)
POS_TAGS = {
    "NNG": "일반명사",
    "NNP": "고유명사",
    "NNB": "의존명사",
    "NP": "대명사",
    "NR": "수사",
    "VV": "동사",
    "VA": "형용사",
    "VX": "보조용언",
    "VCP": "긍정지정사",  # 이다
    "VCN": "부정지정사",  # 아니다
    "MM": "관형사",
    "MAG": "일반부사",
    "MAJ": "접속부사",
    "IC": "감탄사",
    "JKS": "주격조사",
    "JKC": "보격조사",
    "JKG": "관형격조사",
    "JKO": "목적격조사",
    "JKB": "부사격조사",
    "JKV": "호격조사",
    "JKQ": "인용격조사",
    "JC": "접속조사",
    "JX": "보조사",
    "EP": "선어말어미",
    "EF": "종결어미",
    "EC": "연결어미",
    "ETN": "명사형전성어미",
    "ETM": "관형형전성어미",
    "XPN": "체언접두사",
    "XSN": "명사파생접미사",
    "XSV": "동사파생접미사",
    "XSA": "형용사파생접미사",
    "XR": "어근",
    "SF": "마침표/물음표/느낌표",
    "SP": "쉼표/가운뎃점/콜론/빗금",
    "SS": "따옴표/괄호/줄표",
    "SE": "줄임표",
    "SO": "붙임표",
    "SW": "기타기호",
    "SL": "외국어",
    "SH": "한자",
    "SN": "숫자",
    "W_URL": "URL",
    "W_EMAIL": "이메일",
    "W_HASHTAG": "해시태그",
    "W_MENTION": "멘션",
}


class KoreanMorphemeAnalyzer:
    """한국어 형태소 분석기

    Kiwi 기반 형태소 분석을 수행합니다.
    Kiwi가 설치되지 않은 경우 정규식 기반 폴백을 사용합니다.

    Examples:
        >>> analyzer = KoreanMorphemeAnalyzer()
        >>> result = analyzer.analyze("데이터마트는 분석용 저장소입니다")
        >>> result.nouns
        ['데이터마트', '분석', '저장소']
        >>> analyzer.strip_particles("데이터마트는")
        '데이터마트'
    """

    _instance: KoreanMorphemeAnalyzer | None = None
    _kiwi: Kiwi | None = None
    _kiwi_available: bool | None = None

    def __new__(cls) -> KoreanMorphemeAnalyzer:
        """싱글톤 패턴 (Kiwi 초기화 비용 절감)"""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        """초기화 (lazy loading)"""
        if KoreanMorphemeAnalyzer._kiwi_available is None:
            self._initialize_kiwi()

    def _initialize_kiwi(self) -> None:
        """Kiwi 초기화"""
        try:
            from kiwipiepy import Kiwi
            KoreanMorphemeAnalyzer._kiwi = Kiwi()
            KoreanMorphemeAnalyzer._kiwi_available = True
            logger.info("Kiwi morpheme analyzer initialized")
        except ImportError:
            KoreanMorphemeAnalyzer._kiwi_available = False
            logger.warning(
                "kiwipiepy not installed. Using regex-based fallback. "
                "Install with: pip install kiwipiepy"
            )

    @property
    def is_available(self) -> bool:
        """Kiwi 사용 가능 여부"""
        return KoreanMorphemeAnalyzer._kiwi_available or False

    def analyze(self, text: str) -> AnalysisResult:
        """형태소 분석 수행

        Args:
            text: 분석할 텍스트

        Returns:
            분석 결과 (토큰, 명사, 원형)
        """
        if not text.strip():
            return AnalysisResult(
                tokens=[],
                nouns=[],
                stems=[],
                original=text,
            )

        if self.is_available and KoreanMorphemeAnalyzer._kiwi is not None:
            return self._analyze_with_kiwi(text)
        else:
            return self._analyze_with_fallback(text)

    def _analyze_with_kiwi(self, text: str) -> AnalysisResult:
        """Kiwi 기반 분석"""
        kiwi = KoreanMorphemeAnalyzer._kiwi
        assert kiwi is not None

        tokens: list[MorphemeToken] = []
        nouns: list[str] = []
        stems: list[str] = []

        # Kiwi 분석 실행
        result = kiwi.tokenize(text)

        for token in result:
            morph = MorphemeToken(
                form=token.form,
                tag=token.tag,
                start=token.start,
                end=token.start + token.len,
                lemma=None,
            )
            tokens.append(morph)

            # 명사 추출 (NNG: 일반명사, NNP: 고유명사, SL: 외국어)
            if token.tag in ("NNG", "NNP", "SL", "SH"):
                if len(token.form) >= 2:  # 1글자 명사 제외
                    nouns.append(token.form)

            # 동사/형용사 어간 추출
            if token.tag in ("VV", "VA", "VX", "VCP", "VCN"):
                stems.append(token.form)

        return AnalysisResult(
            tokens=tokens,
            nouns=nouns,
            stems=stems,
            original=text,
        )

    def _analyze_with_fallback(self, text: str) -> AnalysisResult:
        """정규식 기반 폴백 분석"""
        import re

        # 단순한 공백/문장부호 기준 토큰화
        words = re.findall(r"[\w가-힣]+", text)

        tokens: list[MorphemeToken] = []
        nouns: list[str] = []

        for word in words:
            # 조사 분리
            stripped = self.strip_particles(word)

            token = MorphemeToken(
                form=word,
                tag="UN",  # Unknown
                start=0,
                end=len(word),
                lemma=stripped if stripped != word else None,
            )
            tokens.append(token)

            # 명사로 추정 (한글 2글자 이상 또는 영문)
            if len(stripped) >= 2:
                if re.match(r"^[\uac00-\ud7a3]+$", stripped):  # 한글
                    nouns.append(stripped)
                elif re.match(r"^[A-Za-z0-9]+$", stripped):  # 영문/숫자
                    nouns.append(stripped)

        return AnalysisResult(
            tokens=tokens,
            nouns=nouns,
            stems=[],
            original=text,
        )

    @lru_cache(maxsize=10000)
    def strip_particles(self, word: str) -> str:
        """조사 분리하여 원형 추출

        Args:
            word: 조사가 붙은 단어 (예: "데이터마트는", "API를")

        Returns:
            조사가 제거된 원형 (예: "데이터마트", "API")
        """
        if not word:
            return word

        # ASCII 또는 영숫자 혼합 단어는 정규식 폴백 사용
        # Kiwi가 "K8s" 같은 단어를 잘못 분리할 수 있음
        import re
        if re.match(r"^[A-Za-z0-9]+[은는이가을를의에서로와과도만]?$", word):
            return self._strip_particles_regex(word)

        # Kiwi 사용 가능하면 정확한 분석
        if self.is_available and KoreanMorphemeAnalyzer._kiwi is not None:
            kiwi = KoreanMorphemeAnalyzer._kiwi
            result = kiwi.tokenize(word)

            # 연속된 명사/외국어 토큰을 결합 (복합명사 지원)
            # 예: "데이터마트는" → ["데이터", "마트", "는"] → "데이터마트"
            noun_tags = ("NNG", "NNP", "SL", "SH", "NNB")
            noun_parts: list[str] = []
            for token in result:
                if token.tag in noun_tags:
                    noun_parts.append(token.form)
                elif noun_parts:
                    # 명사가 아닌 토큰(조사 등)을 만나면 중단
                    break

            if noun_parts:
                return "".join(noun_parts)

            # 조사만 있는 경우 원본 반환
            if result:
                return result[0].form

        # 폴백: 정규식 기반 조사 제거
        return self._strip_particles_regex(word)

    def _strip_particles_regex(self, word: str) -> str:
        """정규식 기반 조사 분리 (폴백)"""
        import re

        # 긴 조사부터 매칭 (라고, 이라고, 에서 등)
        long_particles = [
            "이라고", "라고", "이라며", "라며", "이라는", "라는",
            "이라면", "라면", "에서", "으로", "에게", "한테",
            "부터", "까지", "조차", "마저", "밖에", "처럼", "같이",
        ]

        for particle in long_particles:
            if word.endswith(particle):
                stripped = word[:-len(particle)]
                if stripped:
                    return stripped

        # 짧은 조사 (1글자)
        short_particles = "은는이가을를의에로와과도만"
        if word and word[-1] in short_particles:
            stripped = word[:-1]
            if stripped:
                return stripped

        return word

    def extract_nouns(self, text: str) -> list[str]:
        """텍스트에서 명사 추출

        Args:
            text: 분석할 텍스트

        Returns:
            명사 목록 (중복 제거)
        """
        result = self.analyze(text)
        # 중복 제거 및 순서 유지
        seen = set()
        unique_nouns = []
        for noun in result.nouns:
            if noun not in seen:
                seen.add(noun)
                unique_nouns.append(noun)
        return unique_nouns

    def extract_compound_nouns(self, text: str) -> list[str]:
        """복합명사 추출 (연속된 명사 결합)

        Args:
            text: 분석할 텍스트

        Returns:
            복합명사 목록
        """
        result = self.analyze(text)
        compounds: list[str] = []

        current_compound: list[str] = []

        for token in result.tokens:
            if token.tag in ("NNG", "NNP", "SL", "SH"):
                current_compound.append(token.form)
            else:
                if len(current_compound) >= 2:
                    compounds.append("".join(current_compound))
                current_compound = []

        # 마지막 복합명사 처리
        if len(current_compound) >= 2:
            compounds.append("".join(current_compound))

        return compounds

    def tokenize_for_search(self, text: str) -> list[str]:
        """검색용 토큰화 (명사 + 외국어 + 원형)

        Args:
            text: 분석할 텍스트

        Returns:
            검색 토큰 목록
        """
        result = self.analyze(text)
        tokens: list[str] = []

        for token in result.tokens:
            # 명사, 외국어, 숫자
            if token.tag in ("NNG", "NNP", "SL", "SH", "SN"):
                tokens.append(token.form.lower())
            # 동사/형용사 어간
            elif token.tag in ("VV", "VA"):
                tokens.append(token.form)

        return tokens


# 싱글톤 인스턴스
_analyzer: KoreanMorphemeAnalyzer | None = None


def get_analyzer() -> KoreanMorphemeAnalyzer:
    """형태소 분석기 인스턴스 반환 (싱글톤)"""
    global _analyzer
    if _analyzer is None:
        _analyzer = KoreanMorphemeAnalyzer()
    return _analyzer


class NoOpKoreanMorphemeAnalyzer:
    """NoOp 구현 (테스트용)

    형태소 분석 없이 단순 공백 분리만 수행.
    """

    def analyze(self, text: str) -> AnalysisResult:
        """단순 공백 분리"""
        words = text.split()
        tokens = [MorphemeToken(form=w, tag="UN") for w in words]
        return AnalysisResult(
            tokens=tokens,
            nouns=words,
            stems=[],
            original=text,
        )

    def strip_particles(self, word: str) -> str:
        """조사 분리 없이 원본 반환"""
        return word

    def extract_nouns(self, text: str) -> list[str]:
        """단순 공백 분리"""
        return text.split()
