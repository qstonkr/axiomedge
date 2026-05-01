"""quantizer._check_inference_output — 빈/degenerate 출력 검증 helper."""

from unittest.mock import MagicMock

import pytest

from src.distill.quantizer import _check_inference_output


def test_check_passes_normal_korean() -> None:
    _check_inference_output("한국어 정상 답변입니다.", prompt="Q")


def test_check_passes_english() -> None:
    _check_inference_output("Normal English response", prompt="Q")


def test_check_passes_numeric() -> None:
    _check_inference_output("1234 5678 90", prompt="Q")


def test_check_rejects_empty_output() -> None:
    with pytest.raises(ValueError, match="empty output"):
        _check_inference_output("", prompt="Q")


def test_check_rejects_whitespace_only() -> None:
    with pytest.raises(ValueError, match="empty output"):
        _check_inference_output("   \n\t  ", prompt="Q")


def test_check_rejects_single_char() -> None:
    """1글자 출력은 의미 없음."""
    with pytest.raises(ValueError, match="empty output"):
        _check_inference_output("a", prompt="Q")


def test_check_rejects_degenerate_repeating_char() -> None:
    """단일 글자 반복 = degenerate (loss explosion / bad quantization)."""
    with pytest.raises(ValueError, match="degenerate"):
        _check_inference_output("AAAAAAAAAAAAAA", prompt="Q")


def test_check_rejects_two_char_alternating() -> None:
    """ABABAB 패턴도 degenerate."""
    with pytest.raises(ValueError, match="degenerate"):
        _check_inference_output("ABABABABABAB", prompt="Q")


def test_check_passes_short_diverse() -> None:
    """짧지만 다양한 글자 — 통과."""
    _check_inference_output("네, 맞아요", prompt="Q")


def test_quantize_raises_when_binary_missing(monkeypatch, tmp_path) -> None:
    """llama-quantize 미설치 시 f16 fallback 제거됨 — RuntimeError."""
    from src.distill import quantizer as qmod

    f16 = tmp_path / "f16.gguf"
    f16.write_bytes(b"x")
    out = tmp_path / "out.gguf"

    # _resolve_quantize_bin 가 None 반환하도록 패치
    monkeypatch.setattr(qmod, "_resolve_quantize_bin", lambda: None)

    # DistillProfile 모킹 (quantize_method 만 있으면 됨)
    profile = MagicMock()
    profile.deploy.quantize = "q4_k_m"
    inst = qmod.DistillQuantizer(profile)

    with pytest.raises(RuntimeError, match="llama-quantize"):
        inst._quantize_gguf(f16, out)
