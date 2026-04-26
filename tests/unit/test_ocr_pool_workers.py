"""OCR ProcessPool worker 수 설정 — PR-3 (F).

- 기본 (cfg=0) → min(4, cpu_count)
- cfg>0 → 명시값
- PADDLE_USE_GPU=1 → 1 강제 (OOM 방지)
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.connectors.confluence._ocr_manager import _OcrManagerMixin


@pytest.fixture(autouse=True)
def _clear_paddle_gpu(monkeypatch):
    monkeypatch.delenv("PADDLE_USE_GPU", raising=False)


class TestPoolWorkers:
    def test_default_zero_uses_min_4_cpucount(self, monkeypatch):
        # Mock settings: ocr_pool_workers=0 → resolve to min(4, cpu_count)
        fake_settings = type("S", (), {})()
        fake_settings.pipeline = type("P", (), {"ocr_pool_workers": 0})()
        monkeypatch.setattr(
            "src.config.get_settings", lambda: fake_settings,
        )
        with patch("os.cpu_count", return_value=8):
            workers = _OcrManagerMixin._resolve_pool_workers()
        assert workers == 4

    def test_default_with_low_cpu_count(self, monkeypatch):
        fake_settings = type("S", (), {})()
        fake_settings.pipeline = type("P", (), {"ocr_pool_workers": 0})()
        monkeypatch.setattr(
            "src.config.get_settings", lambda: fake_settings,
        )
        with patch("os.cpu_count", return_value=2):
            workers = _OcrManagerMixin._resolve_pool_workers()
        assert workers == 2

    def test_explicit_setting_overrides(self, monkeypatch):
        fake_settings = type("S", (), {})()
        fake_settings.pipeline = type("P", (), {"ocr_pool_workers": 6})()
        monkeypatch.setattr(
            "src.config.get_settings", lambda: fake_settings,
        )
        workers = _OcrManagerMixin._resolve_pool_workers()
        assert workers == 6

    def test_gpu_env_forces_single_worker(self, monkeypatch):
        monkeypatch.setenv("PADDLE_USE_GPU", "1")
        fake_settings = type("S", (), {})()
        fake_settings.pipeline = type("P", (), {"ocr_pool_workers": 8})()
        monkeypatch.setattr(
            "src.config.get_settings", lambda: fake_settings,
        )
        workers = _OcrManagerMixin._resolve_pool_workers()
        assert workers == 1

    def test_settings_failure_falls_back_to_default(self, monkeypatch):
        # Settings load 실패 → cfg=0 fallback → cpu_count 기반
        def _raising():
            raise RuntimeError("config dead")
        monkeypatch.setattr("src.config.get_settings", _raising)
        with patch("os.cpu_count", return_value=4):
            workers = _OcrManagerMixin._resolve_pool_workers()
        assert workers == 4
