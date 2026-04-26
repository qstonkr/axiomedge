"""Ingest metrics 4종 — PR-10 (I).

- inc_ingest 카운터
- observe_ingest_stage 히스토그램 + sum/count
- inc_ingest_failure (stage, error_class)
- inc/dec_ingest_in_flight gauge
- Prometheus 렌더에 4종 시리즈 등장
"""

from __future__ import annotations

import pytest

import src.api.routes.metrics as M


@pytest.fixture(autouse=True)
def _reset_metrics():
    """각 테스트 전후로 ingest metric state 초기화."""
    M._ingest_documents_total.clear()
    M._ingest_stage_duration_buckets.clear()
    M._ingest_stage_duration_sum.clear()
    M._ingest_stage_duration_count.clear()
    M._ingest_failures_total.clear()
    M._ingest_in_flight = 0
    yield
    M._ingest_documents_total.clear()
    M._ingest_stage_duration_buckets.clear()
    M._ingest_stage_duration_sum.clear()
    M._ingest_stage_duration_count.clear()
    M._ingest_failures_total.clear()
    M._ingest_in_flight = 0


class TestIncIngest:
    def test_increment_per_kb_status(self):
        M.inc_ingest("kb-a", "success")
        M.inc_ingest("kb-a", "success")
        M.inc_ingest("kb-a", "failed")
        M.inc_ingest("kb-b", "skipped")
        assert M._ingest_documents_total[("kb-a", "success")] == 2
        assert M._ingest_documents_total[("kb-a", "failed")] == 1
        assert M._ingest_documents_total[("kb-b", "skipped")] == 1

    def test_kb_id_truncated_to_64(self):
        long_kb = "x" * 200
        M.inc_ingest(long_kb, "success")
        keys = list(M._ingest_documents_total.keys())
        assert all(len(k[0]) <= 64 for k in keys)

    def test_invalid_status_falls_back(self):
        M.inc_ingest("kb", "weird_status")
        assert ("kb", "unknown") in M._ingest_documents_total


class TestObserveIngestStage:
    def test_records_sum_and_count(self):
        M.observe_ingest_stage("stage1_parse", 0.5)
        M.observe_ingest_stage("stage1_parse", 1.5)
        assert M._ingest_stage_duration_count["stage1_parse"] == 2
        assert M._ingest_stage_duration_sum["stage1_parse"] == 2.0


class TestIngestFailures:
    def test_increments_per_stage_error(self):
        # P1-7: 라벨이 canonical 6-stage 로 정규화됨.
        # ``embed`` → ``stage2_embed``, ``store`` → ``stage2_store``.
        M.inc_ingest_failure("embed", "RuntimeError")
        M.inc_ingest_failure("embed", "RuntimeError")
        M.inc_ingest_failure("store", "TimeoutError")
        assert M._ingest_failures_total[("stage2_embed", "RuntimeError")] == 2
        assert M._ingest_failures_total[("stage2_store", "TimeoutError")] == 1

    def test_unknown_stage_falls_back(self):
        """Cardinality 가드: 매핑되지 않은 stage 는 'unknown' 으로."""
        M.inc_ingest_failure("totally_unknown_stage", "X")
        assert ("unknown", "X") in M._ingest_failures_total


class TestInFlightGauge:
    def test_inc_dec_balance(self):
        assert M._ingest_in_flight == 0
        M.inc_ingest_in_flight()
        M.inc_ingest_in_flight()
        assert M._ingest_in_flight == 2
        M.dec_ingest_in_flight()
        assert M._ingest_in_flight == 1


class TestPrometheusRendering:
    def test_renders_all_four_metric_families(self):
        M.inc_ingest("kb-1", "success")
        M.observe_ingest_stage("stage2_embed", 0.3)
        # P1-7: ``"store"`` → ``"stage2_store"`` (canonical normalize)
        M.inc_ingest_failure("store", "OSError")
        M.inc_ingest_in_flight()

        text = M._render_prometheus()
        assert "ingest_documents_total_v2" in text
        assert "ingest_duration_seconds" in text
        assert "ingest_failures_total" in text
        assert "ingest_in_flight" in text
        # 라벨 정합성 — canonical 6-stage
        assert 'kb_id="kb-1"' in text
        assert 'status="success"' in text
        assert 'stage="stage2_store"' in text
        assert 'error_class="OSError"' in text
