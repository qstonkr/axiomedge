# pyright: reportMissingImports=false, reportAttributeAccessIssue=false
"""GGUF 양자화.

HuggingFace 모델을 llama.cpp GGUF 포맷으로 변환 + 양자화.

툴체인 요구사항 (SSOT):
    convert_hf_to_gguf.py 와 llama-quantize 는 **반드시 같은 llama.cpp 커밋**
    에서 나와야 한다. 드리프트가 있으면 신규 아키텍처 (EXAONE, Kanana2 등)
    의 GGUF metadata 키 네이밍이 불일치해서 "key not found in model" 로
    파이프라인이 깨진다. 2026-04-16 EXAONE 실측 사례.

    경로 해결 순서 (strict):
      1. 환경변수 ``DISTILL_CONVERT_SCRIPT`` / ``DISTILL_QUANTIZE_BIN``
         필수. 설정 안 되면 에러 + setup 스크립트 안내.
      2. **Opt-in** ``DISTILL_ALLOW_PATH_FALLBACK=1`` 이 설정된 경우에만
         ``$PATH`` 탐색 (CI / ad-hoc 개발 환경용). 기본은 금지 — 드리프트 재발
         방지.
      3. 경로가 없으면 Python gguf 패키지 직접 사용 (fallback, 제한적).

    설치/업그레이드: ``make setup-distill-toolchain``
    상세: ``docs/DISTILL_TOOLCHAIN.md``
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import subprocess

from src.config.weights import weights as _w
from pathlib import Path

from src.distill.config import DistillProfile

logger = logging.getLogger(__name__)


_SETUP_HINT = (
    "Run `make setup-distill-toolchain` and export DISTILL_CONVERT_SCRIPT / "
    "DISTILL_QUANTIZE_BIN. See docs/DISTILL_TOOLCHAIN.md for details."
)


def _path_fallback_allowed() -> bool:
    """`$PATH` fallback 이 허용되는지 확인.

    기본: 금지 (env var 없으면 에러). CI / 로컬 ad-hoc 에서 명시적으로
    ``DISTILL_ALLOW_PATH_FALLBACK=1`` 을 설정해야만 허용.
    """
    flag = os.getenv("DISTILL_ALLOW_PATH_FALLBACK", "").strip().lower()
    return flag in ("1", "true", "yes", "on")


def _resolve_convert_script() -> str | None:
    """``DISTILL_CONVERT_SCRIPT`` env var 에서 convert_hf_to_gguf.py 찾기.

    env var 가 필수. Opt-in ``DISTILL_ALLOW_PATH_FALLBACK=1`` 이 설정된 경우에만
    ``$PATH`` 탐색을 허용한다 — 드리프트 재발 방지 차원.
    """
    env_path = os.getenv("DISTILL_CONVERT_SCRIPT")
    if env_path:
        if Path(env_path).exists():
            return env_path
        logger.error(
            "DISTILL_CONVERT_SCRIPT is set but not found: %s — %s",
            env_path, _SETUP_HINT,
        )
        return None

    if not _path_fallback_allowed():
        logger.error(
            "DISTILL_CONVERT_SCRIPT is not set. %s "
            "(Set DISTILL_ALLOW_PATH_FALLBACK=1 to allow `$PATH` lookup — "
            "not recommended, causes version drift.)",
            _SETUP_HINT,
        )
        return None

    which_path = shutil.which("convert_hf_to_gguf.py")
    if which_path:
        logger.warning(
            "DISTILL_CONVERT_SCRIPT not set — falling back to $PATH (%s) "
            "because DISTILL_ALLOW_PATH_FALLBACK is enabled. "
            "This may drift from llama-quantize version. %s",
            which_path, _SETUP_HINT,
        )
        return which_path
    logger.error("convert_hf_to_gguf.py not found on $PATH either. %s", _SETUP_HINT)
    return None


def _resolve_quantize_bin() -> str | None:
    """``DISTILL_QUANTIZE_BIN`` env var 에서 llama-quantize 찾기."""
    env_path = os.getenv("DISTILL_QUANTIZE_BIN")
    if env_path:
        if Path(env_path).exists() and os.access(env_path, os.X_OK):
            return env_path
        logger.error(
            "DISTILL_QUANTIZE_BIN is set but not executable: %s — %s",
            env_path, _SETUP_HINT,
        )
        return None

    if not _path_fallback_allowed():
        logger.error(
            "DISTILL_QUANTIZE_BIN is not set. %s "
            "(Set DISTILL_ALLOW_PATH_FALLBACK=1 to allow `$PATH` lookup — "
            "not recommended, causes version drift.)",
            _SETUP_HINT,
        )
        return None

    which_path = shutil.which("llama-quantize")
    if which_path:
        logger.warning(
            "DISTILL_QUANTIZE_BIN not set — falling back to $PATH (%s) "
            "because DISTILL_ALLOW_PATH_FALLBACK is enabled. "
            "This may drift from convert_hf_to_gguf.py version. %s",
            which_path, _SETUP_HINT,
        )
        return which_path
    logger.error("llama-quantize not found on $PATH either. %s", _SETUP_HINT)
    return None


class DistillQuantizer:
    """GGUF 양자화 래퍼."""

    def __init__(self, profile: DistillProfile) -> None:
        self.profile = profile
        self.quantize_method = profile.deploy.quantize or "q4_k_m"

    def quantize_to_gguf(self, model_path: str, output_path: str) -> str:
        """HuggingFace 모델 → GGUF 양자화.

        llama-cpp-python의 내장 변환 또는 llama.cpp CLI 사용.
        """
        model_path = Path(model_path)
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Step 1: HF → GGUF f16 변환
        f16_path = output_path.parent / "model-f16.gguf"
        self._convert_hf_to_gguf(model_path, f16_path)

        # Step 2: 양자화
        self._quantize_gguf(f16_path, output_path)

        # Step 3: f16 임시 파일 삭제
        if f16_path.exists():
            f16_path.unlink()

        size_mb = output_path.stat().st_size / (1024 * 1024)
        logger.info("Quantized model: %s (%.1f MB, %s)", output_path, size_mb, self.quantize_method)
        return str(output_path)

    def _convert_hf_to_gguf(self, model_path: Path, output_path: Path) -> None:
        """HuggingFace → GGUF f16 변환.

        경로는 ``DISTILL_CONVERT_SCRIPT`` env var 를 우선 사용 (SSOT).
        설정 안 돼 있으면 $PATH fallback 후 경고. 둘 다 실패 시 Python fallback.
        """
        # 방어선: convert_hf_to_gguf.py의 Gemma3 경로는 tokenizer.model 유무로
        # SentencePiece / gpt2 BPE 분기한다. 없으면 BPE fallback 되어 한국어가 깨짐.
        # 학습 파이프라인이 tokenizer.model을 빼먹었을 경우 여기서 마지막 복구.
        self._ensure_tokenizer_model(model_path)

        convert_script = _resolve_convert_script()
        if convert_script:
            cmd = [
                "python", convert_script,
                "--outfile", str(output_path),
                "--outtype", "f16",
                str(model_path),
            ]
            logger.info("Converting HF → GGUF f16: %s", " ".join(cmd))
            _timeout = _w.timeouts.subprocess_convert
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=_timeout,
            )
            if result.returncode == 0:
                return
            logger.warning(
                "CLI convert failed (%s), trying Python fallback: %s",
                convert_script, result.stderr[:200],
            )

        # Fallback: Python gguf 라이브러리 직접 사용
        self._python_convert_to_gguf(model_path, output_path)

    def _ensure_tokenizer_model(self, model_path: Path) -> None:
        tm_target = model_path / "tokenizer.model"
        if tm_target.exists():
            return
        base_ref = self.profile.base_model
        try:
            from huggingface_hub import try_to_load_from_cache
            cached = try_to_load_from_cache(base_ref, "tokenizer.model")
            # str | None | _CACHED_NO_EXIST — sentinel 방어
            if isinstance(cached, str) and Path(cached).exists():
                shutil.copy(cached, tm_target)
                logger.info("Recovered tokenizer.model from HF cache (%s)", base_ref)
                return
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.debug("HF cache lookup failed: %s", e)
        try:
            base_path = Path(base_ref)
            if base_path.is_dir() and (base_path / "tokenizer.model").exists():
                shutil.copy(base_path / "tokenizer.model", tm_target)
                logger.info("Recovered tokenizer.model from local base %s", base_path)
                return
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.debug("Local base path lookup failed: %s", e)
        logger.warning(
            "tokenizer.model missing in %s and not recoverable from base %s — "
            "GGUF conversion may produce broken Korean (gpt2 BPE fallback)",
            model_path, base_ref,
        )

    def _quantize_gguf(self, input_path: Path, output_path: Path) -> None:
        """GGUF f16 → 양자화 (Q4_K_M 등).

        경로는 ``DISTILL_QUANTIZE_BIN`` env var 를 우선 사용 (SSOT).
        """
        quantize_bin = _resolve_quantize_bin()
        if quantize_bin:
            cmd = [quantize_bin, str(input_path), str(output_path), self.quantize_method.upper()]
            logger.info("Quantizing: %s", " ".join(cmd))
            _timeout = _w.timeouts.subprocess_convert
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=_timeout,
            )
            if result.returncode != 0:
                raise RuntimeError(f"Quantization failed: {result.stderr[:200]}")
        else:
            # llama-quantize 바이너리 없으면 f16을 그대로 사용. 엣지 배포 품질
            # 떨어지지만 파이프라인은 진행. 사용자는 로그로 알 수 있음.
            logger.warning(
                "llama-quantize not resolved — using f16 as-is. "
                "Run `make setup-distill-toolchain` and export DISTILL_QUANTIZE_BIN.",
            )
            shutil.copy2(str(input_path), str(output_path))

    @staticmethod
    def _python_convert_to_gguf(model_path: Path, output_path: Path) -> None:
        """Python 기반 GGUF 변환 (fallback)."""
        try:
            # gguf 패키지가 있으면 사용
            import gguf  # noqa: F401
            logger.info("Using gguf Python package for conversion")
            # gguf 패키지의 변환 로직 사용
            subprocess.run(
                ["python", "-c", f"""
import sys
sys.path.insert(0, '.')
from transformers import AutoModelForCausalLM, AutoTokenizer
print('Loading model from {model_path}...')
# gguf 변환은 llama.cpp의 convert script가 필요
# 여기서는 에러를 발생시켜 상위에서 처리
raise NotImplementedError('Manual GGUF conversion needed')
"""],
                capture_output=True, text=True, timeout=_w.timeouts.subprocess_validate,
            )
        except ImportError:
            pass

        raise RuntimeError(
            "GGUF conversion failed. Install llama.cpp tools:\n"
            "  pip install llama-cpp-python\n"
            "  # or build llama.cpp and add convert_hf_to_gguf.py to PATH"
        )

    @staticmethod
    def compute_sha256(file_path: str) -> str:
        """파일 SHA256 해시 계산."""
        h = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()

    def validate_gguf(self, gguf_path: str) -> dict:
        """GGUF 파일 유효성 검증 (로드 + 테스트 추론 + SHA256)."""
        from llama_cpp import Llama

        path = Path(gguf_path)
        if not path.exists():
            return {"valid": False, "error": "File not found"}

        size_mb = path.stat().st_size / (1024 * 1024)
        sha256 = self.compute_sha256(gguf_path)

        try:
            llm = Llama(model_path=gguf_path, n_ctx=128, n_threads=2, verbose=False)
            # Gemma 3: <end_of_turn> (106) 는 GGUF eos 에 안 들어가서
            # 명시 차단 필요. edge/server.py 와 동일 방어선.
            output = llm.create_chat_completion(
                messages=[{"role": "user", "content": "테스트"}],
                max_tokens=10,
                stop=["<end_of_turn>", "<start_of_turn>"],
            )
            test_output = output["choices"][0]["message"]["content"]
            del llm

            return {
                "valid": True,
                "size_mb": round(size_mb, 1),
                "sha256": sha256,
                "test_output": test_output,
                "quantize_method": self.quantize_method,
            }
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            return {
                "valid": False, "error": str(e),
                "size_mb": round(size_mb, 1), "sha256": sha256,
            }
