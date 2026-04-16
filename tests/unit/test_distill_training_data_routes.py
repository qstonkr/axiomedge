"""Tests for src/api/routes/distill_training_data.py (PR9 split)."""

from __future__ import annotations



class TestRouterRegistration:
    """분리된 router 가 동일 prefix + 기존 endpoint 경로를 유지하는지 검증."""

    def test_router_prefix_matches_distill_main(self):
        from src.api.routes.distill import router as main_router
        from src.api.routes.distill_training_data import router as td_router
        assert td_router.prefix == main_router.prefix == "/api/v1/distill"

    def test_all_training_data_routes_present(self):
        """분리 전 distill.py 에 있던 15개 training-data endpoint 가 모두 유지됨."""
        from src.api.routes.distill_training_data import router

        paths_methods = {
            (route.path, tuple(sorted(route.methods)))
            for route in router.routes
        }

        expected = {
            # Basic CRUD + review
            ("/api/v1/distill/training-data", ("GET",)),
            ("/api/v1/distill/training-data", ("POST",)),
            ("/api/v1/distill/training-data/review", ("PUT",)),
            ("/api/v1/distill/training-data/review-edit", ("PUT",)),
            ("/api/v1/distill/training-data/stats", ("GET",)),
            ("/api/v1/distill/training-data/batches/{batch_id}", ("GET",)),
            # Smart approve
            ("/api/v1/distill/training-data/smart-approve", ("POST",)),
            # Generation background tasks
            ("/api/v1/distill/training-data/generate", ("POST",)),
            ("/api/v1/distill/training-data/generate-test", ("POST",)),
            ("/api/v1/distill/training-data/augment", ("POST",)),
            ("/api/v1/distill/training-data/generate-term-qa", ("POST",)),
            # Cleanup / delete
            ("/api/v1/distill/training-data/cleanup-answers", ("POST",)),
            ("/api/v1/distill/training-data/by-source", ("DELETE",)),
            ("/api/v1/distill/training-data/batch/{batch_id}", ("DELETE",)),
        }
        missing = expected - paths_methods
        assert not missing, f"Missing endpoints after split: {missing}"

    def test_main_distill_router_no_longer_has_training_data(self):
        """분리 후 distill.py 에 training-data/* endpoint 가 남아 있으면 안 됨 (중복 방지)."""
        from src.api.routes.distill import router
        training_data_routes = [
            r for r in router.routes
            if hasattr(r, "path") and "/training-data" in r.path
        ]
        assert not training_data_routes, (
            f"training-data routes 가 distill.py 에 여전히 남아 있음: "
            f"{[r.path for r in training_data_routes]}"
        )


class TestRequestModels:
    """이동된 Pydantic request model 의 정상 동작."""

    def test_generate_data_request(self):
        from src.api.routes.distill_training_data import GenerateDataRequest
        m = GenerateDataRequest(profile_name="pbu-store")
        assert m.profile_name == "pbu-store"

    def test_generate_test_data_request_default_count(self):
        from src.api.routes.distill_training_data import GenerateTestDataRequest
        m = GenerateTestDataRequest(profile_name="pbu-store")
        assert m.count == 50

    def test_training_data_update_item_partial(self):
        from src.api.routes.distill_training_data import TrainingDataUpdateItem
        m = TrainingDataUpdateItem(id="abc", status="approved")
        assert m.id == "abc"
        assert m.status == "approved"
        assert m.question is None

    def test_training_data_edit_review_request(self):
        from src.api.routes.distill_training_data import (
            TrainingDataEditReviewRequest,
            TrainingDataUpdateItem,
        )
        m = TrainingDataEditReviewRequest(
            updates=[TrainingDataUpdateItem(id="a"), TrainingDataUpdateItem(id="b")],
        )
        assert len(m.updates) == 2

    def test_augment_request_default(self):
        from src.api.routes.distill_training_data import AugmentRequest
        m = AugmentRequest(profile_name="p1")
        assert m.max_variants == 3

    def test_generate_term_qa_default(self):
        from src.api.routes.distill_training_data import GenerateTermQARequest
        m = GenerateTermQARequest(profile_name="p1")
        assert m.top_n == 100

    def test_training_data_add_request(self):
        from src.api.routes.distill_training_data import TrainingDataAddRequest
        m = TrainingDataAddRequest(
            profile_name="p1",
            question="Q?",
            answer="A.",
        )
        assert m.source_type == "manual"
        assert m.kb_id is None


class TestSmartApproveConstants:
    """Smart approve 관련 상수가 module level 로 추출돼 재사용 가능."""

    def test_bad_patterns_defined(self):
        from src.api.routes.distill_training_data import _BAD_ANSWER_PATTERNS
        assert isinstance(_BAD_ANSWER_PATTERNS, tuple)
        assert len(_BAD_ANSWER_PATTERNS) >= 5
        assert "명시되어 있지 않" in _BAD_ANSWER_PATTERNS

    def test_min_answer_chars(self):
        from src.api.routes.distill_training_data import _MIN_ANSWER_CHARS
        assert _MIN_ANSWER_CHARS == 20

    def test_bad_pattern_threshold(self):
        from src.api.routes.distill_training_data import _BAD_PATTERN_MATCH_THRESHOLD
        assert _BAD_PATTERN_MATCH_THRESHOLD == 2


class TestAllowedDeleteSourceTypes:
    def test_allowed_types_frozen(self):
        from src.api.routes.distill_training_data import _ALLOWED_DELETE_SOURCE_TYPES
        assert isinstance(_ALLOWED_DELETE_SOURCE_TYPES, frozenset)
        assert "test_seed" in _ALLOWED_DELETE_SOURCE_TYPES
        assert "term_qa" in _ALLOWED_DELETE_SOURCE_TYPES
        assert "manual" in _ALLOWED_DELETE_SOURCE_TYPES
        # 허용되지 않은 타입 확인
        assert "reformatted" not in _ALLOWED_DELETE_SOURCE_TYPES
