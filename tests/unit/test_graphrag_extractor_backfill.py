"""Coverage backfill — GraphRAG extractor entity filtering.

Tests _is_corrupted_entity, _is_invalid_person, _reclassify_person,
and _validate_entity from src/pipeline/graphrag/extractor.py.
"""

from src.pipelines.graphrag.extractor import (
    _is_corrupted_entity,
    _is_invalid_person,
    _reclassify_person,
    _validate_entity,
)


class TestIsCorruptedEntity:
    """Tests for placeholder and OCR corruption detection."""

    def test_korean_placeholder(self) -> None:
        assert _is_corrupted_entity("미상") is True
        assert _is_corrupted_entity("없음") is True
        assert _is_corrupted_entity("해당 없음") is True

    def test_english_placeholder(self) -> None:
        assert _is_corrupted_entity("unknown") is True
        assert _is_corrupted_entity("Unknown") is True  # case insensitive
        assert _is_corrupted_entity("n/a") is True

    def test_valid_entity(self) -> None:
        assert _is_corrupted_entity("김철수") is False
        assert _is_corrupted_entity("GS리테일") is False

    def test_lone_jamo_corruption(self) -> None:
        """Lone jamo (ㄱ~ㅎ) indicates OCR corruption."""
        assert _is_corrupted_entity("테스ㅌ") is True
        assert _is_corrupted_entity("ㄱ테스트") is True

    def test_repeated_syllable_corruption(self) -> None:
        """3+ repeated chars indicate OCR artifact."""
        assert _is_corrupted_entity("가가가") is True
        assert _is_corrupted_entity("aaa") is True

    def test_normal_repeated_ok(self) -> None:
        """2 repeats are OK."""
        assert _is_corrupted_entity("가가") is False

    def test_empty_and_whitespace(self) -> None:
        assert _is_corrupted_entity("-") is True
        assert _is_corrupted_entity("?") is True


class TestIsInvalidPerson:
    """Tests for person entity validation."""

    def test_company_in_blocklist(self) -> None:
        assert _is_invalid_person("구글", "구글") is True
        assert _is_invalid_person("카카오", "카카오") is True

    def test_location_in_blocklist(self) -> None:
        assert _is_invalid_person("강남", "강남") is True
        assert _is_invalid_person("서울", "서울") is True

    def test_concept_in_blocklist(self) -> None:
        assert _is_invalid_person("데이터", "데이터") is True
        assert _is_invalid_person("보안", "보안") is True

    def test_valid_person(self) -> None:
        assert _is_invalid_person("김철수", "김철수") is False

    def test_too_long_name(self) -> None:
        """Names > 15 chars are role descriptions, not person names."""
        long_name = "개발자교육담당자관리팀장보좌관역할담당"
        assert _is_invalid_person(long_name, long_name) is True

    def test_name_with_digits(self) -> None:
        assert _is_invalid_person("user_123", "user_123") is True
        assert _is_invalid_person("Person001", "Person001") is True

    def test_too_short_name(self) -> None:
        """Single char names are too ambiguous."""
        assert _is_invalid_person("김", "김") is True

    def test_role_suffix(self) -> None:
        assert _is_invalid_person("김담당자", "김담당자") is True
        assert _is_invalid_person("이매니저", "이매니저") is True

    def test_bracket_prefix(self) -> None:
        assert _is_invalid_person("[미정]", "[미정]") is True
        assert _is_invalid_person("(담당)", "(담당)") is True

    def test_corporate_marker(self) -> None:
        assert _is_invalid_person("GS(주)", "GS(주)") is True


class TestReclassifyPerson:
    """Tests for person → correct type reclassification."""

    def test_company_suffix_to_store(self) -> None:
        result = _reclassify_person("신한카드")
        assert result == ("신한카드", "Store")

    def test_insurance_to_store(self) -> None:
        result = _reclassify_person("삼성생명")
        assert result == ("삼성생명", "Store")

    def test_location_suffix(self) -> None:
        result = _reclassify_person("강남구")
        assert result == ("강남구", "Location")

    def test_team_suffix(self) -> None:
        result = _reclassify_person("개발팀")
        assert result == ("개발팀", "Team")

    def test_system_name(self) -> None:
        result = _reclassify_person("레디스")
        assert result == ("레디스", "System")

    def test_valid_person_unchanged(self) -> None:
        result = _reclassify_person("김철수")
        assert result == ("김철수", None)  # None type = no reclassification needed

    def test_name_cleaning_with_parens(self) -> None:
        """'김철수(PM)' should be cleaned to '김철수'."""
        result = _reclassify_person("김철수(PM)")
        name, type_ = result
        assert name == "김철수"
        assert type_ is None  # Stays Person (None = no reclassification)


class TestValidateEntity:
    """Tests for the main validation orchestration."""

    def test_corrupted_entity_rejected(self) -> None:
        result = _validate_entity("미상", "Person")
        assert result[0] is None  # ID is None = skip

    def test_person_reclassified_to_store(self) -> None:
        result = _validate_entity("신한카드", "Person")
        assert result is not None
        name, type_ = result
        assert type_ == "Store"

    def test_platform_reclassified_to_system(self) -> None:
        result = _validate_entity("쿠팡", "Store")
        assert result is not None
        _, type_ = result
        assert type_ == "System"

    def test_product_filtered_from_store(self) -> None:
        result = _validate_entity("500ML음료", "Store")
        assert result[0] is None  # ID is None = skip

    def test_valid_person_passes(self) -> None:
        result = _validate_entity("김철수", "Person")
        assert result is not None
        name, type_ = result
        assert name == "김철수"
        assert type_ == "Person"

    def test_valid_store_passes(self) -> None:
        result = _validate_entity("강남점", "Store")
        assert result is not None
        _, type_ = result
        assert type_ == "Store"

    def test_non_person_type_skips_person_checks(self) -> None:
        """System type should not go through person validation."""
        result = _validate_entity("Redis", "System")
        assert result is not None
        _, type_ = result
        assert type_ == "System"
