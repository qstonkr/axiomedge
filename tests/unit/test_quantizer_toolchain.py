"""Toolchain path resolution (PR4) — env var strict mode."""

from __future__ import annotations


import pytest


@pytest.fixture(autouse=True)
def _clear_toolchain_env(monkeypatch):
    """각 테스트 독립 — env var 초기화.

    ``quantizer.py`` 의 실제 env 이름은 ``DISTILL_LLAMA_*`` 접두 — 옛
    fixture 가 접두 누락된 이름만 cleanup 해서 단독 실행과 풀 스위트의 결과
    가 달랐다. 양쪽 모두 명시.
    """
    for var in (
        "DISTILL_CONVERT_SCRIPT",
        "DISTILL_QUANTIZE_BIN",
        "DISTILL_LLAMA_CONVERT_SCRIPT",
        "DISTILL_LLAMA_QUANTIZE_BIN",
        "DISTILL_LIB_LLAMA_PATH",
        "DISTILL_ALLOW_PATH_FALLBACK",
    ):
        monkeypatch.delenv(var, raising=False)
    yield


# ---------------------------------------------------------------------------
# _path_fallback_allowed
# ---------------------------------------------------------------------------


class TestPathFallbackFlag:
    def test_default_disabled(self):
        from src.distill.quantizer import _path_fallback_allowed
        assert _path_fallback_allowed() is False

    def test_enabled_with_1(self, monkeypatch):
        from src.distill.quantizer import _path_fallback_allowed
        monkeypatch.setenv("DISTILL_ALLOW_PATH_FALLBACK", "1")
        assert _path_fallback_allowed() is True

    def test_enabled_with_true(self, monkeypatch):
        from src.distill.quantizer import _path_fallback_allowed
        monkeypatch.setenv("DISTILL_ALLOW_PATH_FALLBACK", "true")
        assert _path_fallback_allowed() is True

    def test_disabled_with_0(self, monkeypatch):
        from src.distill.quantizer import _path_fallback_allowed
        monkeypatch.setenv("DISTILL_ALLOW_PATH_FALLBACK", "0")
        assert _path_fallback_allowed() is False

    def test_disabled_with_empty(self, monkeypatch):
        from src.distill.quantizer import _path_fallback_allowed
        monkeypatch.setenv("DISTILL_ALLOW_PATH_FALLBACK", "")
        assert _path_fallback_allowed() is False


# ---------------------------------------------------------------------------
# _resolve_convert_script
# ---------------------------------------------------------------------------


class TestResolveConvertScript:
    def test_env_var_set_and_exists(self, monkeypatch, tmp_path):
        fake_script = tmp_path / "convert_hf_to_gguf.py"
        fake_script.write_text("# fake")
        monkeypatch.setenv("DISTILL_CONVERT_SCRIPT", str(fake_script))

        from src.distill.quantizer import _resolve_convert_script
        assert _resolve_convert_script() == str(fake_script)

    def test_env_var_set_but_missing_returns_none(self, monkeypatch, tmp_path, caplog):
        missing = tmp_path / "nope.py"
        monkeypatch.setenv("DISTILL_CONVERT_SCRIPT", str(missing))

        from src.distill.quantizer import _resolve_convert_script
        import logging
        # 다른 test 가 logger propagate=False / disable 했을 수 있어 명시 reset.
        # caplog.at_level context 가 set_level 보다 robust.
        target_logger = logging.getLogger("src.distill.quantizer")
        target_logger.propagate = True
        target_logger.disabled = False
        with caplog.at_level(logging.ERROR, logger="src.distill.quantizer"):
            assert _resolve_convert_script() is None
            assert any("DISTILL_CONVERT_SCRIPT is set but not found" in r.message for r in caplog.records)

    def test_env_var_unset_and_fallback_disabled_returns_none(self, monkeypatch, caplog):
        # env var 없음 + fallback flag 없음 → 즉시 None + error log
        from src.distill.quantizer import _resolve_convert_script
        import logging
        target_logger = logging.getLogger("src.distill.quantizer")
        target_logger.propagate = True
        target_logger.disabled = False
        with caplog.at_level(logging.ERROR, logger="src.distill.quantizer"):
            assert _resolve_convert_script() is None
            assert any("DISTILL_CONVERT_SCRIPT is not set" in r.message for r in caplog.records)

    def test_env_var_unset_but_fallback_opt_in_uses_path(self, monkeypatch, tmp_path):
        """DISTILL_ALLOW_PATH_FALLBACK=1 이면 $PATH 탐색 허용."""
        fake_bin_dir = tmp_path / "bin"
        fake_bin_dir.mkdir()
        fake_script = fake_bin_dir / "convert_hf_to_gguf.py"
        fake_script.write_text("# fake")
        fake_script.chmod(0o755)

        monkeypatch.setenv("DISTILL_ALLOW_PATH_FALLBACK", "1")
        monkeypatch.setenv("PATH", str(fake_bin_dir))

        from src.distill.quantizer import _resolve_convert_script
        assert _resolve_convert_script() == str(fake_script)


# ---------------------------------------------------------------------------
# _resolve_quantize_bin
# ---------------------------------------------------------------------------


class TestResolveQuantizeBin:
    def test_env_var_set_executable(self, monkeypatch, tmp_path):
        fake_bin = tmp_path / "llama-quantize"
        fake_bin.write_text("#!/bin/sh\necho fake")
        fake_bin.chmod(0o755)
        monkeypatch.setenv("DISTILL_QUANTIZE_BIN", str(fake_bin))

        from src.distill.quantizer import _resolve_quantize_bin
        assert _resolve_quantize_bin() == str(fake_bin)

    def test_env_var_set_not_executable_returns_none(self, monkeypatch, tmp_path):
        fake_bin = tmp_path / "llama-quantize"
        fake_bin.write_text("not a real binary")
        fake_bin.chmod(0o644)  # not executable
        monkeypatch.setenv("DISTILL_QUANTIZE_BIN", str(fake_bin))

        from src.distill.quantizer import _resolve_quantize_bin
        assert _resolve_quantize_bin() is None

    def test_env_var_unset_and_fallback_disabled_returns_none(self):
        from src.distill.quantizer import _resolve_quantize_bin
        assert _resolve_quantize_bin() is None

    def test_env_var_unset_but_fallback_opt_in_uses_path(self, monkeypatch, tmp_path):
        fake_bin_dir = tmp_path / "bin"
        fake_bin_dir.mkdir()
        fake_bin = fake_bin_dir / "llama-quantize"
        fake_bin.write_text("#!/bin/sh\necho fake")
        fake_bin.chmod(0o755)

        monkeypatch.setenv("DISTILL_ALLOW_PATH_FALLBACK", "1")
        monkeypatch.setenv("PATH", str(fake_bin_dir))

        from src.distill.quantizer import _resolve_quantize_bin
        assert _resolve_quantize_bin() == str(fake_bin)


# ---------------------------------------------------------------------------
# DistillDefaults drift fix
# ---------------------------------------------------------------------------


class TestDistillDefaultsDriftFix:
    def test_no_build_timeout_sec_field(self):
        """build_timeout_sec 는 DistillSettings (인프라) 로 이관됨."""
        from src.distill.config import DistillDefaults
        defaults = DistillDefaults()
        assert not hasattr(defaults, "build_timeout_sec"), (
            "DistillDefaults.build_timeout_sec was removed — "
            "use src.config.DistillSettings.build_timeout_sec (env DISTILL_BUILD_TIMEOUT_SEC)"
        )

    def test_min_training_samples_matches_yaml(self):
        """기본값은 distill.yaml 과 일치 (200)."""
        from src.distill.config import DistillDefaults
        defaults = DistillDefaults()
        assert defaults.min_training_samples == 200

    def test_infrastructure_build_timeout_still_accessible(self):
        """DistillSettings 는 여전히 build_timeout_sec 보유."""
        from src.config import DistillSettings
        settings = DistillSettings()
        assert settings.build_timeout_sec >= 300  # ge constraint
