"""edge/pii.py PII 마스킹 단위 테스트.

매장 쿼리 로그에 PII (전화/카드/이메일/주민번호) 가 평문 저장되지 않도록.
"""

from __future__ import annotations

from src.edge.pii import mask_pii


class TestPhone:
    def test_dashes(self) -> None:
        assert mask_pii("연락처는 010-1234-5678 입니다") == "연락처는 [PHONE] 입니다"

    def test_no_dashes(self) -> None:
        assert mask_pii("010 1234 5678") == "[PHONE]"

    def test_other_carrier_prefix(self) -> None:
        # 011/016/017/018/019 도 매칭
        assert mask_pii("019-1234-5678") == "[PHONE]"


class TestCard:
    def test_card_with_dashes(self) -> None:
        assert mask_pii("카드 1234-5678-9012-3456") == "카드 [CARD]"

    def test_card_no_dashes(self) -> None:
        assert mask_pii("1234567890123456") == "[CARD]"


class TestEmail:
    def test_simple(self) -> None:
        assert mask_pii("a@b.com 으로 보내주세요") == "[EMAIL] 으로 보내주세요"

    def test_dotted_user(self) -> None:
        assert mask_pii("john.doe@example.co.kr 발송") == "[EMAIL] 발송"


class TestSSN:
    def test_ssn_with_dash(self) -> None:
        assert mask_pii("주민 990101-1234567") == "주민 [SSN]"

    def test_ssn_no_dash(self) -> None:
        assert mask_pii("9901011234567") == "[SSN]"


class TestPreservation:
    def test_normal_text_unchanged(self) -> None:
        assert (
            mask_pii("영업시간은 9시부터 22시까지입니다")
            == "영업시간은 9시부터 22시까지입니다"
        )

    def test_short_number_not_masked(self) -> None:
        # 짧은 숫자는 PII 아님
        assert mask_pii("재고 50개") == "재고 50개"

    def test_year_not_masked(self) -> None:
        assert mask_pii("2026년") == "2026년"


class TestMultiplePII:
    def test_multiple_in_one_string(self) -> None:
        result = mask_pii("연락처 010-1234-5678 이메일 a@b.com")
        assert "[PHONE]" in result
        assert "[EMAIL]" in result
        assert "010" not in result
        assert "@b.com" not in result
