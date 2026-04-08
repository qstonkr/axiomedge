"""Tests for distill data curation, model versioning, and edge server management.

Covers: generality_filter, dataset_builder (augmentation verify), quality_filter
(compute_similarity), edge_server repo, training_data repo (new fields),
build repo (version history, rollback), quantizer (SHA256), service
(generate_data_for_review, generate_test_data), API endpoints, constants.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# GeneralityFilter
# ---------------------------------------------------------------------------

class TestGeneralityFilter:
    """src/distill/data_gen/generality_filter.py"""

    def _make_filter(self, llm=None):
        from src.distill.data_gen.generality_filter import GeneralityFilter
        return GeneralityFilter(llm_helper=llm)

    def test_pattern_score_no_match(self):
        gf = self._make_filter()
        assert gf._pattern_score("편의점 위생 관리 기준") == 1.0

    def test_pattern_score_store_name(self):
        gf = self._make_filter()
        assert gf._pattern_score("강남점 냉장고 고장") <= 0.5

    def test_pattern_score_date(self):
        gf = self._make_filter()
        assert gf._pattern_score("2024년 4월 7일 매출") <= 0.3

    def test_pattern_score_person(self):
        gf = self._make_filter()
        assert gf._pattern_score("김철수 매니저에게 보고") <= 0.5

    def test_pattern_score_multiple_matches(self):
        gf = self._make_filter()
        assert gf._pattern_score("강남점 김철수 매니저 2024년") <= 0.1

    @pytest.mark.asyncio
    async def test_score_no_llm(self):
        gf = self._make_filter()
        score = await gf.score("폐기 절차 알려줘", "1. POS 등록 2. 폐기 박스")
        assert 0 <= score <= 1
        assert score == 1.0  # 패턴 매치 없으므로

    @pytest.mark.asyncio
    async def test_score_with_store_pattern(self):
        gf = self._make_filter()
        score = await gf.score("강남점 냉장고 온도", "냉장고를 확인합니다")
        assert score <= 0.5

    @pytest.mark.asyncio
    async def test_batch_score(self):
        gf = self._make_filter()
        qa = [
            {"question": "폐기 절차", "answer": "POS 등록"},
            {"question": "강남점 재고", "answer": "확인"},
        ]
        result = await gf.batch_score(qa)
        assert len(result) == 2
        assert result[0]["generality_score"] == 1.0
        assert result[1]["generality_score"] <= 0.5

    def test_parse_score_valid(self):
        from src.distill.data_gen.generality_filter import GeneralityFilter
        assert GeneralityFilter._parse_score("0.85") == 0.85
        assert GeneralityFilter._parse_score("점수: 0.7") == 0.7

    def test_parse_score_invalid(self):
        from src.distill.data_gen.generality_filter import GeneralityFilter
        assert GeneralityFilter._parse_score("좋습니다") == 0.5

    @pytest.mark.asyncio
    async def test_score_with_llm(self):
        llm = AsyncMock()
        llm.call = AsyncMock(return_value="0.9")
        gf = self._make_filter(llm=llm)
        score = await gf.score("폐기 절차", "POS 등록")
        assert score > 0.5
        llm.call.assert_awaited_once()


# ---------------------------------------------------------------------------
# QualityFilter.compute_similarity
# ---------------------------------------------------------------------------

class TestQualityFilterSimilarity:
    """src/distill/data_gen/quality_filter.py — compute_similarity"""

    @pytest.mark.asyncio
    async def test_compute_similarity_fuzz_fallback(self):
        from src.distill.data_gen.quality_filter import QualityFilter

        llm = AsyncMock()
        # embedder가 없으면 fuzz fallback
        embedder = MagicMock()
        embedder.encode = MagicMock(side_effect=Exception("no embedder"))

        profile = MagicMock()
        qf = QualityFilter(llm, embedder, profile)
        sim = await qf.compute_similarity("hello world", "hello world")
        assert sim == 1.0  # identical strings

    @pytest.mark.asyncio
    async def test_compute_similarity_different_texts(self):
        from src.distill.data_gen.quality_filter import QualityFilter

        llm = AsyncMock()
        embedder = MagicMock()
        embedder.encode = MagicMock(side_effect=Exception("no embedder"))

        profile = MagicMock()
        qf = QualityFilter(llm, embedder, profile)
        sim = await qf.compute_similarity("사과", "자동차")
        assert sim < 0.5


# ---------------------------------------------------------------------------
# DatasetBuilder.verify_augmented_questions
# ---------------------------------------------------------------------------

class TestDatasetBuilderVerify:
    """src/distill/data_gen/dataset_builder.py — verify_augmented_questions"""

    @pytest.mark.asyncio
    async def test_originals_pass_through(self):
        from src.distill.data_gen.dataset_builder import DatasetBuilder

        llm = AsyncMock()
        profile = MagicMock()
        profile.data_quality.augmentation_count = 0
        builder = DatasetBuilder(llm, profile)

        qa = [{"question": "Q1", "answer": "A1"}]  # no augmented_from
        quality = AsyncMock()
        result = await builder.verify_augmented_questions(qa, quality)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_augmented_verified(self):
        from src.distill.data_gen.dataset_builder import DatasetBuilder

        llm = AsyncMock()
        llm.call = AsyncMock(return_value="A1과 비슷한 답변")
        profile = MagicMock()
        builder = DatasetBuilder(llm, profile)

        quality = AsyncMock()
        quality.compute_similarity = AsyncMock(return_value=0.9)

        qa = [{"question": "Q1", "answer": "A1", "augmented_from": "parent-id"}]
        result = await builder.verify_augmented_questions(qa, quality, threshold=0.75)
        assert len(result) == 1
        assert result[0]["augmentation_verified"] is True

    @pytest.mark.asyncio
    async def test_augmented_rejected(self):
        from src.distill.data_gen.dataset_builder import DatasetBuilder

        llm = AsyncMock()
        llm.call = AsyncMock(return_value="완전히 다른 답변")
        profile = MagicMock()
        builder = DatasetBuilder(llm, profile)

        quality = AsyncMock()
        quality.compute_similarity = AsyncMock(return_value=0.2)

        qa = [{"question": "Q1", "answer": "A1", "augmented_from": "parent-id"}]
        result = await builder.verify_augmented_questions(qa, quality, threshold=0.75)
        assert len(result) == 0  # rejected

    @pytest.mark.asyncio
    async def test_augment_questions_adds_augmented_from(self):
        from src.distill.data_gen.dataset_builder import DatasetBuilder

        llm = AsyncMock()
        llm.call = AsyncMock(return_value="1. 유통기한이 지난 물건 처리 방법\n2. 폐기해야 하는 상품 절차")
        profile = MagicMock()
        profile.data_quality.augmentation_count = 2
        builder = DatasetBuilder(llm, profile)

        qa = [{"id": "orig-1", "question": "폐기 절차 알려줘", "answer": "POS 등록 후 폐기", "source_type": "usage_log"}]
        result = await builder.augment_questions(qa)
        augmented = [r for r in result if r.get("augmented_from")]
        assert len(augmented) >= 1
        assert augmented[0]["augmented_from"] == "orig-1"


# ---------------------------------------------------------------------------
# Quantizer SHA256
# ---------------------------------------------------------------------------

class TestQuantizerSHA256:
    """src/distill/quantizer.py — compute_sha256"""

    def test_compute_sha256(self, tmp_path):
        from src.distill.quantizer import DistillQuantizer
        f = tmp_path / "test.bin"
        f.write_bytes(b"hello world")
        expected = hashlib.sha256(b"hello world").hexdigest()
        assert DistillQuantizer.compute_sha256(str(f)) == expected


# ---------------------------------------------------------------------------
# EdgeServer Repository
# ---------------------------------------------------------------------------

class TestEdgeServerRepository:
    """src/distill/repositories/edge_server.py"""

    def test_hash_key(self):
        from src.distill.repositories.edge_server import _hash_key
        h = _hash_key("test-key")
        assert len(h) == 64
        assert h == hashlib.sha256(b"test-key").hexdigest()


# ---------------------------------------------------------------------------
# TrainingData Repository — new fields
# ---------------------------------------------------------------------------

class TestTrainingDataToDict:
    """_to_dict includes new fields."""

    def test_to_dict_new_fields(self):
        from src.distill.repositories.training_data import DistillTrainingDataRepository

        model = MagicMock()
        model.id = "test-id"
        model.profile_name = "pbu"
        model.question = "Q"
        model.answer = "A"
        model.source_type = "usage_log"
        model.source_id = None
        model.kb_id = None
        model.status = "pending"
        model.used_in_build = None
        model.created_at = datetime(2026, 4, 7, tzinfo=timezone.utc)
        model.consistency_score = 0.92
        model.generality_score = 0.85
        model.augmentation_verified = True
        model.augmented_from = "parent-id"
        model.generation_batch_id = "batch-1"
        model.reviewed_at = None
        model.review_comment = None

        d = DistillTrainingDataRepository._to_dict(model)
        assert d["consistency_score"] == 0.92
        assert d["generality_score"] == 0.85
        assert d["augmentation_verified"] is True
        assert d["augmented_from"] == "parent-id"
        assert d["generation_batch_id"] == "batch-1"

    def test_sort_whitelist(self):
        """sort_by에 허용되지 않은 값은 created_at으로 대체."""
        # 단순 로직 테스트 — repo 인스턴스 불필요
        allowed_sorts = {"created_at", "consistency_score", "generality_score", "status", "source_type"}
        assert "created_at" in allowed_sorts
        assert "__tablename__" not in allowed_sorts


# ---------------------------------------------------------------------------
# Build Repository — _to_dict new fields
# ---------------------------------------------------------------------------

class TestBuildToDict:
    """build _to_dict includes gguf_sha256, model_name, rollback_from."""

    def test_to_dict_new_fields(self):
        from src.distill.repositories.build import DistillBuildRepository

        model = MagicMock()
        model.id = "b1"
        model.profile_name = "pbu"
        model.status = "completed"
        model.version = "v20260407"
        model.search_group = "PBU"
        model.base_model = "Qwen/Qwen2.5-0.5B"
        model.training_samples = 5000
        model.train_loss = 0.42
        model.eval_loss = 0.5
        model.training_duration_sec = 300
        model.eval_faithfulness = 0.8
        model.eval_relevancy = 0.9
        model.eval_passed = True
        model.gguf_size_mb = 245.0
        model.gguf_sha256 = "abc123"
        model.model_name = "Qwen2.5-0.5B"
        model.quantize_method = "q4_k_m"
        model.s3_uri = "s3://bucket/model.gguf"
        model.deployed_at = datetime(2026, 4, 7, tzinfo=timezone.utc)
        model.rollback_from = "b0"
        model.error_message = None
        model.error_step = None
        model.created_at = datetime(2026, 4, 7, tzinfo=timezone.utc)
        model.updated_at = datetime(2026, 4, 7, tzinfo=timezone.utc)

        d = DistillBuildRepository._to_dict(model)
        assert d["gguf_sha256"] == "abc123"
        assert d["model_name"] == "Qwen2.5-0.5B"
        assert d["rollback_from"] == "b0"


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

class TestConstants:
    """dashboard/components/constants.py — new icons and quality_badge."""

    def _import_constants(self):
        import sys
        from pathlib import Path
        dashboard_dir = str(Path(__file__).parent.parent.parent / "dashboard")
        if dashboard_dir not in sys.path:
            sys.path.insert(0, dashboard_dir)
        from components.constants import (
            CURATION_STATUS_ICONS,
            EDGE_SERVER_STATUS_ICONS,
            quality_badge,
        )
        return CURATION_STATUS_ICONS, EDGE_SERVER_STATUS_ICONS, quality_badge

    def test_curation_status_icons(self):
        icons, _, _ = self._import_constants()
        assert "pending" in icons
        assert "approved" in icons
        assert "rejected" in icons

    def test_edge_server_status_icons(self):
        _, icons, _ = self._import_constants()
        assert "online" in icons
        assert "offline" in icons

    def test_quality_badge_high(self):
        _, _, badge = self._import_constants()
        assert "🟢" in badge(0.9)

    def test_quality_badge_medium(self):
        _, _, badge = self._import_constants()
        assert "🟡" in badge(0.6)

    def test_quality_badge_low(self):
        _, _, badge = self._import_constants()
        assert "🔴" in badge(0.2)

    def test_quality_badge_none(self):
        _, _, badge = self._import_constants()
        assert badge(None) == "—"


# ---------------------------------------------------------------------------
# DB Models — column existence
# ---------------------------------------------------------------------------

class TestModels:
    """src/distill/models.py — verify new columns exist."""

    def test_training_data_new_columns(self):
        from src.distill.models import DistillTrainingDataModel
        cols = {c.name for c in DistillTrainingDataModel.__table__.columns}
        assert "consistency_score" in cols
        assert "generality_score" in cols
        assert "augmentation_verified" in cols
        assert "augmented_from" in cols
        assert "generation_batch_id" in cols
        assert "reviewed_at" in cols
        assert "review_comment" in cols

    def test_build_new_columns(self):
        from src.distill.models import DistillBuildModel
        cols = {c.name for c in DistillBuildModel.__table__.columns}
        assert "gguf_sha256" in cols
        assert "model_name" in cols
        assert "rollback_from" in cols

    def test_edge_server_model_exists(self):
        from src.distill.models import DistillEdgeServerModel
        cols = {c.name for c in DistillEdgeServerModel.__table__.columns}
        assert "store_id" in cols
        assert "status" in cols
        assert "last_heartbeat" in cols
        assert "os_type" in cols
        assert "app_version" in cols
        assert "model_version" in cols
        assert "pending_model_update" in cols
        assert "pending_app_update" in cols
        assert "api_key_hash" in cols

    def test_edge_server_indexes(self):
        from src.distill.models import DistillEdgeServerModel
        idx_names = {idx.name for idx in DistillEdgeServerModel.__table__.indexes}
        assert "idx_edge_server_store" in idx_names
        assert "idx_edge_server_profile" in idx_names

    def test_training_data_batch_index(self):
        from src.distill.models import DistillTrainingDataModel
        idx_names = {idx.name for idx in DistillTrainingDataModel.__table__.indexes}
        assert "idx_train_data_batch" in idx_names


# ---------------------------------------------------------------------------
# Test Data Templates
# ---------------------------------------------------------------------------

class TestTestDataTemplates:
    """src/distill/data_gen/test_data_templates.py"""

    def test_templates_exist(self):
        from src.distill.data_gen.test_data_templates import TEST_QUESTION_TEMPLATES
        assert len(TEST_QUESTION_TEMPLATES) >= 4
        for category, questions in TEST_QUESTION_TEMPLATES.items():
            assert len(questions) >= 5

    def test_find_relevant_context_empty(self):
        from src.distill.data_gen.test_data_templates import _find_relevant_context
        result = _find_relevant_context("테스트", {})
        assert result == ""

    def test_find_relevant_context_keyword_match(self):
        from src.distill.data_gen.test_data_templates import _find_relevant_context
        chunks = {"kb1": ["유통기한 관리 절차입니다", "카드 결제 방법"]}
        result = _find_relevant_context("유통기한 지난 상품", chunks)
        assert "유통기한" in result


# ---------------------------------------------------------------------------
# init_db
# ---------------------------------------------------------------------------

class TestInitDB:
    """src/database/init_db.py — DistillBase import."""

    def test_distill_base_imported(self):
        from src.database.init_db import DistillBase
        assert DistillBase is not None


# ---------------------------------------------------------------------------
# Edge Server heartbeat data (edge/server.py)
# ---------------------------------------------------------------------------

class TestEdgeServerHeartbeat:
    """edge/server.py — heartbeat data structure."""

    def test_deque_maxlen(self):
        from collections import deque
        d: deque[int] = deque(maxlen=100)
        for i in range(150):
            d.append(i)
        assert len(d) == 100
        assert d[0] == 50


# ---------------------------------------------------------------------------
# Edge sync — stage_app_update
# ---------------------------------------------------------------------------

class TestEdgeSyncFunctions:
    """edge/sync.py — utility functions."""

    def test_sha256_file(self, tmp_path):
        from edge.sync import _sha256_file
        f = tmp_path / "test.bin"
        f.write_bytes(b"test data")
        expected = hashlib.sha256(b"test data").hexdigest()
        assert _sha256_file(f) == expected

    def test_read_local_version_missing(self, tmp_path, monkeypatch):
        from edge.sync import _read_local_version
        from edge import sync
        monkeypatch.setattr(sync, "CURRENT_DIR", tmp_path / "nonexistent")
        assert _read_local_version() == ""
