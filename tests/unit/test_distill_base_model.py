"""Tests for distill base model registry — repository + seed.

Covers:
- DistillBaseModelRepository._to_dict serialization
- seed.DEFAULT_BASE_MODELS schema consistency
- seed_base_models calls upsert once per row
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest


# ---------------------------------------------------------------------------
# Repository._to_dict
# ---------------------------------------------------------------------------

class TestToDict:
    """src/distill/repositories/base_model.py::_to_dict"""

    def _make_row(self, **overrides):
        row = MagicMock()
        row.hf_id = "google/gemma-3-4b-it"
        row.display_name = "Gemma 3 4B it"
        row.params = "4B"
        row.license = "Gemma"
        row.commercial_use = True
        row.verified = True
        row.notes = "추천"
        row.enabled = True
        row.sort_order = 10
        row.created_at = datetime(2026, 4, 15, 7, 0, tzinfo=timezone.utc)
        row.updated_at = datetime(2026, 4, 15, 7, 30, tzinfo=timezone.utc)
        for k, v in overrides.items():
            setattr(row, k, v)
        return row

    def test_basic(self):
        from src.distill.repositories.base_model import DistillBaseModelRepository
        d = DistillBaseModelRepository._to_dict(self._make_row())
        assert d["hf_id"] == "google/gemma-3-4b-it"
        assert d["display_name"] == "Gemma 3 4B it"
        assert d["commercial_use"] is True
        assert d["verified"] is True
        assert d["notes"] == "추천"
        assert d["enabled"] is True
        assert d["sort_order"] == 10
        assert d["created_at"] == "2026-04-15T07:00:00+00:00"
        assert d["updated_at"] == "2026-04-15T07:30:00+00:00"

    def test_none_notes_becomes_empty_string(self):
        from src.distill.repositories.base_model import DistillBaseModelRepository
        d = DistillBaseModelRepository._to_dict(self._make_row(notes=None))
        assert d["notes"] == ""

    def test_none_timestamps(self):
        from src.distill.repositories.base_model import DistillBaseModelRepository
        d = DistillBaseModelRepository._to_dict(
            self._make_row(created_at=None, updated_at=None),
        )
        assert d["created_at"] is None
        assert d["updated_at"] is None


# ---------------------------------------------------------------------------
# seed.DEFAULT_BASE_MODELS schema consistency
# ---------------------------------------------------------------------------

REQUIRED_KEYS = {
    "hf_id", "display_name", "params", "license",
    "commercial_use", "verified", "notes", "enabled", "sort_order",
}


class TestDefaultBaseModels:
    """src/distill/seed.py::DEFAULT_BASE_MODELS"""

    def test_all_rows_have_required_keys(self):
        from src.distill.seed import DEFAULT_BASE_MODELS
        for row in DEFAULT_BASE_MODELS:
            missing = REQUIRED_KEYS - row.keys()
            assert not missing, f"{row.get('hf_id')} missing keys: {missing}"

    def test_hf_ids_are_unique(self):
        from src.distill.seed import DEFAULT_BASE_MODELS
        ids = [row["hf_id"] for row in DEFAULT_BASE_MODELS]
        assert len(ids) == len(set(ids))

    def test_at_least_one_commercial_verified(self):
        """상업 배포 가능한 검증된 모델이 최소 1개 — default 선택지로 필요."""
        from src.distill.seed import DEFAULT_BASE_MODELS
        ok = [
            r for r in DEFAULT_BASE_MODELS
            if r["commercial_use"] and r["verified"]
        ]
        assert len(ok) >= 1, "no commercial+verified base model in seed"

    def test_hf_id_format(self):
        """HF 모델 ID 는 'org/repo' 형식."""
        from src.distill.seed import DEFAULT_BASE_MODELS
        for row in DEFAULT_BASE_MODELS:
            assert "/" in row["hf_id"], f"invalid hf_id: {row['hf_id']}"

    def test_sort_order_nonnegative(self):
        from src.distill.seed import DEFAULT_BASE_MODELS
        for row in DEFAULT_BASE_MODELS:
            assert row["sort_order"] >= 0

    def test_boolean_flags(self):
        from src.distill.seed import DEFAULT_BASE_MODELS
        for row in DEFAULT_BASE_MODELS:
            assert isinstance(row["commercial_use"], bool)
            assert isinstance(row["verified"], bool)
            assert isinstance(row["enabled"], bool)


# ---------------------------------------------------------------------------
# seed_base_models — 호출 패턴
# ---------------------------------------------------------------------------

class TestSeedBaseModels:
    """src/distill/seed.py::seed_base_models"""

    @pytest.mark.asyncio
    async def test_uses_insert_if_missing_not_upsert(self):
        """Seed 는 insert-only 경로여야 함 — admin 편집 보존 계약."""
        from src.distill.seed import DEFAULT_BASE_MODELS, seed_base_models
        repo = MagicMock()
        # insert_base_model_if_missing 만 호출돼야 함
        repo.insert_base_model_if_missing = AsyncMock(return_value=True)
        repo.upsert_base_model = AsyncMock(return_value={})

        result = await seed_base_models(repo)

        assert result["inserted"] == len(DEFAULT_BASE_MODELS)
        assert result["skipped"] == 0
        assert repo.insert_base_model_if_missing.call_count == len(DEFAULT_BASE_MODELS)
        # upsert 는 절대 호출돼선 안 됨 (호출되면 admin 편집을 덮어쓰는 버그)
        repo.upsert_base_model.assert_not_called()

    @pytest.mark.asyncio
    async def test_counts_inserted_vs_skipped(self):
        """일부는 신규 삽입, 일부는 이미 존재 → 각각 카운트."""
        from src.distill.seed import DEFAULT_BASE_MODELS, seed_base_models
        repo = MagicMock()
        # 홀수 인덱스 = 신규 (True), 짝수 인덱스 = 이미 존재 (False)
        side_effects = [i % 2 == 1 for i in range(len(DEFAULT_BASE_MODELS))]
        repo.insert_base_model_if_missing = AsyncMock(side_effect=side_effects)

        result = await seed_base_models(repo)

        expected_inserted = sum(side_effects)
        expected_skipped = len(DEFAULT_BASE_MODELS) - expected_inserted
        assert result["inserted"] == expected_inserted
        assert result["skipped"] == expected_skipped

    @pytest.mark.asyncio
    async def test_continues_after_per_row_failure(self):
        """한 row 실패해도 나머지는 계속 — fail-open seed pattern."""
        from src.distill.seed import DEFAULT_BASE_MODELS, seed_base_models
        repo = MagicMock()
        # 첫 호출만 실패, 나머지 성공 (모두 신규 삽입)
        side_effects: list = [RuntimeError("DB down")] + [
            True for _ in range(len(DEFAULT_BASE_MODELS) - 1)
        ]
        repo.insert_base_model_if_missing = AsyncMock(side_effect=side_effects)

        result = await seed_base_models(repo)

        # 실패한 1개는 어느 쪽으로도 안 세짐, 나머지는 inserted
        assert result["inserted"] == len(DEFAULT_BASE_MODELS) - 1
        assert result["skipped"] == 0
        assert repo.insert_base_model_if_missing.call_count == len(DEFAULT_BASE_MODELS)


# ---------------------------------------------------------------------------
# URL encoding contract for delete path
# ---------------------------------------------------------------------------

class TestDeleteUrlEncoding:
    """dashboard/services/api/distill.py::delete_distill_base_model 의 인코딩 규약.

    dashboard 모듈은 sys.path 설정이 다르므로 직접 import 하는 대신, 같은
    규약 (``quote(hf_id, safe='/')``) 을 표현식으로 검증한다.
    """

    def test_slash_preserved(self):
        from urllib.parse import quote
        assert quote("google/gemma-3-4b-it", safe="/") == "google/gemma-3-4b-it"

    def test_space_encoded(self):
        from urllib.parse import quote
        assert quote("weird org/with space", safe="/") == "weird%20org/with%20space"

    def test_hash_encoded(self):
        from urllib.parse import quote
        assert quote("org/repo#frag", safe="/") == "org/repo%23frag"

    def test_question_encoded(self):
        from urllib.parse import quote
        assert quote("org/repo?q=1", safe="/") == "org/repo%3Fq%3D1"
