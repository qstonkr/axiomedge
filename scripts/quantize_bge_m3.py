"""BGE-M3 ONNX INT8 양자화 스크립트.

FP32 모델을 INT8로 양자화하여 2-3x 빠른 추론 + 75% 모델 크기 감소.
정확도 손실: ~1% (거의 체감 불가).

사용법:
    # 1. FP32 모델 다운로드
    huggingface-cli download BAAI/bge-m3 --local-dir ./models/bge-m3

    # 2. INT8 양자화 실행
    python scripts/quantize_bge_m3.py

    # 3. .env에 양자화 모델 경로 설정
    KNOWLEDGE_BGE_ONNX_MODEL_PATH=./models/bge-m3-int8
    KNOWLEDGE_BGE_ONNX_FILE_NAME=model_quantized.onnx
"""

import os
import sys
from pathlib import Path


def quantize():
    try:
        from onnxruntime.quantization import quantize_dynamic, QuantType
    except ImportError:
        print("ERROR: onnxruntime 설치 필요: pip install onnxruntime")
        sys.exit(1)

    # 소스 모델 경로
    source_dir = os.getenv("KNOWLEDGE_BGE_ONNX_MODEL_PATH", "./models/bge-m3")
    source_model = Path(source_dir) / "model.onnx"

    if not source_model.exists():
        print(f"ERROR: FP32 모델을 찾을 수 없습니다: {source_model}")
        print("먼저 다운로드하세요: huggingface-cli download BAAI/bge-m3 --local-dir ./models/bge-m3")
        sys.exit(1)

    # 출력 경로
    output_dir = Path(source_dir).parent / "bge-m3-int8"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_model = output_dir / "model_quantized.onnx"

    print(f"소스:  {source_model} ({source_model.stat().st_size / 1e9:.1f} GB)")
    print(f"출력:  {output_model}")
    print("양자화 중... (1-2분 소요)")

    quantize_dynamic(
        model_input=str(source_model),
        model_output=str(output_model),
        weight_type=QuantType.QInt8,
    )

    # tokenizer 파일 복사 (양자화 모델 디렉토리에도 필요)
    import shutil
    for fname in ["tokenizer.json", "tokenizer_config.json", "special_tokens_map.json",
                   "vocab.txt", "sentencepiece.bpe.model", "config.json"]:
        src = Path(source_dir) / fname
        if src.exists():
            shutil.copy2(src, output_dir / fname)

    output_size = output_model.stat().st_size
    source_size = source_model.stat().st_size
    ratio = (1 - output_size / source_size) * 100

    print(f"\n완료!")
    print(f"  FP32: {source_size / 1e9:.1f} GB")
    print(f"  INT8: {output_size / 1e9:.1f} GB ({ratio:.0f}% 감소)")
    print(f"\n.env에 추가:")
    print(f"  KNOWLEDGE_BGE_ONNX_MODEL_PATH={output_dir}")
    print(f"  KNOWLEDGE_BGE_ONNX_FILE_NAME=model_quantized.onnx")


if __name__ == "__main__":
    quantize()
