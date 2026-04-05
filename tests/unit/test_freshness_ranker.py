"""Unit tests for FreshnessRanker."""

from __future__ import annotations

from datetime import datetime, timedelta

from src.pipeline.freshness_ranker import FreshnessConfig, FreshnessRanker, RankedResult


def _make_result(
    content: str = "test",
    similarity: float = 0.8,
    updated_at: str | None = None,
    version_count: int = 0,
) -> dict:
    meta = {}
    if updated_at is not None:
        meta["updated_at"] = updated_at
    if version_count:
        meta["version_count"] = version_count
    return {"content": content, "similarity": similarity, "metadata": meta}


def _days_ago(n: int) -> str:
    return (datetime.now() - timedelta(days=n)).strftime("%Y-%m-%d")


class TestFreshnessConfig:
    def test_default_config_values(self) -> None:
        cfg = FreshnessConfig()
        assert cfg.fresh_days > 0
        assert cfg.stale_days > cfg.fresh_days
        assert cfg.outdated_days > cfg.stale_days
        assert 0 < cfg.fresh_boost
        assert 0 < cfg.stale_penalty <= 1.0
        assert 0 < cfg.outdated_penalty <= cfg.stale_penalty


class TestComputeFreshnessScore:
    def setup_method(self) -> None:
        self.ranker = FreshnessRanker(FreshnessConfig(
            fresh_days=30,
            stale_days=180,
            outdated_days=365,
            fresh_boost=1.1,
            stale_penalty=0.9,
            outdated_penalty=0.7,
            warning_threshold_days=180,
        ))

    def test_fresh_document_boosted(self) -> None:
        results = self.ranker.rank([_make_result(updated_at=_days_ago(10))])
        assert results[0].adjusted_score > results[0].original_score

    def test_stale_document_penalized(self) -> None:
        results = self.ranker.rank([_make_result(updated_at=_days_ago(200))])
        assert results[0].adjusted_score < results[0].original_score

    def test_outdated_document_heavily_penalized(self) -> None:
        results = self.ranker.rank([_make_result(updated_at=_days_ago(500))])
        assert results[0].adjusted_score < results[0].original_score * 0.75

    def test_no_date_no_change(self) -> None:
        results = self.ranker.rank([_make_result()])
        assert results[0].adjusted_score == results[0].original_score

    def test_penalty_disabled(self) -> None:
        results = self.ranker.rank(
            [_make_result(updated_at=_days_ago(500))],
            apply_penalty=False,
        )
        assert results[0].adjusted_score == results[0].original_score


class TestRankByFreshness:
    def setup_method(self) -> None:
        self.ranker = FreshnessRanker(FreshnessConfig(
            fresh_days=30,
            stale_days=180,
            outdated_days=365,
            fresh_boost=1.1,
            stale_penalty=0.9,
            outdated_penalty=0.7,
            warning_threshold_days=180,
        ))

    def test_rank_sorts_by_adjusted_score(self) -> None:
        results = self.ranker.rank([
            _make_result("old", 0.9, _days_ago(400)),
            _make_result("new", 0.85, _days_ago(5)),
        ])
        assert results[0].content == "new"
        assert results[1].content == "old"

    def test_version_bonus_high(self) -> None:
        results = self.ranker.rank([
            _make_result("active", 0.8, _days_ago(100), version_count=15),
            _make_result("inactive", 0.8, _days_ago(100), version_count=1),
        ])
        assert results[0].content == "active"
        assert results[0].adjusted_score > results[1].adjusted_score

    def test_version_bonus_mid(self) -> None:
        results = self.ranker.rank([
            _make_result("mid", 0.8, _days_ago(100), version_count=7),
            _make_result("low", 0.8, _days_ago(100), version_count=1),
        ])
        assert results[0].adjusted_score > results[1].adjusted_score


class TestDateParsing:
    def setup_method(self) -> None:
        self.ranker = FreshnessRanker()

    def test_iso_date(self) -> None:
        days = self.ranker._calculate_days_old("2020-01-01")
        assert days is not None
        assert days > 1000

    def test_iso_datetime(self) -> None:
        days = self.ranker._calculate_days_old("2024-06-15T10:30:00")
        assert days is not None
        assert days >= 0

    def test_invalid_date(self) -> None:
        days = self.ranker._calculate_days_old("not-a-date")
        assert days is None

    def test_none_date(self) -> None:
        days = self.ranker._calculate_days_old(None)
        assert days is None


class TestWarningsAndFiltering:
    def setup_method(self) -> None:
        self.ranker = FreshnessRanker(FreshnessConfig(
            fresh_days=30,
            stale_days=180,
            outdated_days=365,
            fresh_boost=1.1,
            stale_penalty=0.9,
            outdated_penalty=0.7,
            warning_threshold_days=180,
        ))

    def test_warning_for_outdated(self) -> None:
        results = self.ranker.rank([_make_result(updated_at=_days_ago(500))])
        assert results[0].freshness_warning is not None
        assert "미수정" in results[0].freshness_warning

    def test_warning_for_stale(self) -> None:
        results = self.ranker.rank([_make_result(updated_at=_days_ago(200))])
        assert results[0].freshness_warning is not None
        assert "개월" in results[0].freshness_warning

    def test_no_warning_for_fresh(self) -> None:
        results = self.ranker.rank([_make_result(updated_at=_days_ago(10))])
        assert results[0].freshness_warning is None

    def test_filter_outdated(self) -> None:
        ranked = self.ranker.rank([
            _make_result("ok", 0.8, _days_ago(100)),
            _make_result("old", 0.8, _days_ago(500)),
        ])
        filtered = self.ranker.filter_outdated(ranked, max_days=365)
        assert len(filtered) == 1
        assert filtered[0].content == "ok"

    def test_format_result_with_warning(self) -> None:
        ranked = self.ranker.rank([_make_result("본문", 0.8, _days_ago(500))])
        formatted = self.ranker.format_result_with_warning(ranked[0])
        assert "본문" in formatted
        assert "미수정" in formatted
