"""GGUF 양자화.

HuggingFace 모델을 llama.cpp GGUF 포맷으로 변환 + 양자화.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

from src.distill.config import DistillProfile

logger = logging.getLogger(__name__)


class DistillQuantizer:
    """GGUF 양자화 래퍼."""

    def __init__(self, profile: DistillProfile):
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
        """HuggingFace → GGUF f16 변환."""
        # llama-cpp-python 내장 변환 가용성 확인
        import importlib.util
        _has_llama_quantize = importlib.util.find_spec("llama_cpp") is not None

        # llama.cpp CLI 사용 (convert_hf_to_gguf.py)
        convert_script = shutil.which("convert_hf_to_gguf.py")
        if convert_script:
            cmd = [
                "python", convert_script,
                "--outfile", str(output_path),
                "--outtype", "f16",
                str(model_path),
            ]
        else:
            # pip install llama-cpp-python[server] 또는 직접 변환
            cmd = [
                "python", "-m", "llama_cpp.llama_convert",
                "--outfile", str(output_path),
                "--outtype", "f16",
                str(model_path),
            ]

        logger.info("Converting HF → GGUF f16: %s", " ".join(cmd))
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            # Fallback: transformers + gguf 라이브러리
            logger.warning("CLI convert failed, trying Python fallback: %s", result.stderr[:200])
            self._python_convert_to_gguf(model_path, output_path)

    def _quantize_gguf(self, input_path: Path, output_path: Path) -> None:
        """GGUF f16 → 양자화 (Q4_K_M 등)."""
        quantize_bin = shutil.which("llama-quantize")
        if quantize_bin:
            cmd = [quantize_bin, str(input_path), str(output_path), self.quantize_method.upper()]
            logger.info("Quantizing: %s", " ".join(cmd))
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            if result.returncode != 0:
                raise RuntimeError(f"Quantization failed: {result.stderr[:200]}")
        else:
            # llama-quantize 바이너리 없으면 f16을 그대로 사용
            logger.warning("llama-quantize not found, using f16 model as-is")
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
                capture_output=True, text=True, timeout=300,
            )
        except ImportError:
            pass

        raise RuntimeError(
            "GGUF conversion failed. Install llama.cpp tools:\n"
            "  pip install llama-cpp-python\n"
            "  # or build llama.cpp and add convert_hf_to_gguf.py to PATH"
        )

    def validate_gguf(self, gguf_path: str) -> dict:
        """GGUF 파일 유효성 검증 (로드 + 테스트 추론)."""
        from llama_cpp import Llama

        path = Path(gguf_path)
        if not path.exists():
            return {"valid": False, "error": "File not found"}

        size_mb = path.stat().st_size / (1024 * 1024)

        try:
            llm = Llama(model_path=gguf_path, n_ctx=128, n_threads=2, verbose=False)
            output = llm.create_chat_completion(
                messages=[{"role": "user", "content": "테스트"}],
                max_tokens=10,
            )
            test_output = output["choices"][0]["message"]["content"]
            del llm

            return {
                "valid": True,
                "size_mb": round(size_mb, 1),
                "test_output": test_output,
                "quantize_method": self.quantize_method,
            }
        except Exception as e:
            return {"valid": False, "error": str(e), "size_mb": round(size_mb, 1)}
