"""graph_expander._is_likely_person_name — person 후보 필터링 검증.

조사 결합 토큰 ('이행에', '대하여') 이 Neo4j Person 조회를 유발해 노이즈가
생기던 문제를 차단하는 헬퍼.
"""

from __future__ import annotations

from src.search.graph_expander import _is_likely_person_name


class TestPersonNameFilter:
    def test_typical_korean_person_name_passes(self) -> None:
        assert _is_likely_person_name("홍길동") is True

    def test_two_char_korean_passes(self) -> None:
        assert _is_likely_person_name("김철") is True

    def test_four_char_company_name_passes(self) -> None:
        # 4글자 토큰은 점포/회사명 케이스가 많아 통과
        assert _is_likely_person_name("삼성전자") is True

    def test_too_short_rejected(self) -> None:
        assert _is_likely_person_name("김") is False

    def test_too_long_rejected(self) -> None:
        assert _is_likely_person_name("매우긴이름이름") is False

    def test_non_hangul_rejected(self) -> None:
        assert _is_likely_person_name("Apple") is False
        assert _is_likely_person_name("김ABC") is False

    def test_stopword_rejected(self) -> None:
        # 명백한 비-인명 토큰
        assert _is_likely_person_name("이행에") is False
        assert _is_likely_person_name("대하여") is False
        assert _is_likely_person_name("그러나") is False

    def test_short_token_with_particle_suffix_rejected(self) -> None:
        # 길이 ≤ 3 + 조사 suffix → 조사 결합 토큰
        assert _is_likely_person_name("이름에") is False
        assert _is_likely_person_name("점포의") is False

    def test_four_char_with_particle_suffix_passes(self) -> None:
        # 4글자는 조사 필터 면제 (회사명/매장명 보호)
        assert _is_likely_person_name("도시락의") is True

    def test_empty_string_rejected(self) -> None:
        assert _is_likely_person_name("") is False
