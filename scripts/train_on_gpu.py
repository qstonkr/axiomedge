"""EC2 GPU에서 실행되는 학습 스크립트.

gpu_boot_train.sh에서 호출됨. S3에서 다운로드된 데이터로 학습 후
결과를 output/ 디렉토리에 저장.

Usage:
    python3 train_on_gpu.py --data-dir /opt/distill/jobs/{build_id} \
                            --output-dir /opt/distill/jobs/{build_id}/output \
                            --build-id {build_id}
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from pathlib import Path

# SSL 우회 (사내망)
os.environ.setdefault("HF_HUB_DISABLE_SSL_VERIFY", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("REQUESTS_CA_BUNDLE", "")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

S3_MODEL_BUCKET = os.getenv("DISTILL_S3_BUCKET", "gs-knowledge-models")
S3_MODEL_PREFIX = "models"
AWS_REGION = os.getenv("AWS_REGION", "ap-northeast-2")


def _resolve_model_path(base_model: str) -> str:
    """모델 경로 결정: S3에 있으면 다운로드, 없으면 HuggingFace ID 그대로 반환.

    S3 경로: s3://{bucket}/models/{base_model}/
    로컬 캐시: /opt/distill/models/{base_model}/
    """
    import subprocess

    local_path = f"/opt/distill/models/{base_model.replace('/', '_')}"
    s3_path = f"s3://{S3_MODEL_BUCKET}/{S3_MODEL_PREFIX}/{base_model}/"

    # 이미 로컬에 있으면 재사용
    if os.path.exists(local_path) and os.listdir(local_path):
        logger.info("Model cached locally: %s", local_path)
        return local_path

    # S3에서 다운로드 시도
    try:
        check = subprocess.run(
            ["aws", "s3", "ls", s3_path, "--region", AWS_REGION],
            capture_output=True, text=True, timeout=10,
        )
        if check.returncode == 0 and check.stdout.strip():
            logger.info("Downloading model from S3: %s", s3_path)
            os.makedirs(local_path, exist_ok=True)
            subprocess.run(
                ["aws", "s3", "sync", s3_path, local_path, "--region", AWS_REGION],
                check=True, timeout=600,
            )
            logger.info("Model downloaded to %s", local_path)
            return local_path
    except Exception as e:
        logger.warning("S3 model download failed: %s, falling back to HuggingFace", e)

    # S3에 없으면 HuggingFace ID 반환 (transformers가 자동 다운로드)
    return base_model


def train(data_dir: str, output_dir: str, build_id: str):
    """LoRA SFT 학습 + GGUF 양자화."""
    config_path = os.path.join(data_dir, "config.json")
    data_path = os.path.join(data_dir, "train.jsonl")

    with open(config_path) as f:
        config = json.load(f)

    base_model = config.get("base_model", "Qwen/Qwen2.5-0.5B-Instruct")
    lora_config = config.get("lora", {})
    training_config = config.get("training", {})
    quantize_method = config.get("quantize", "q4_k_m")

    logger.info("Config: model=%s, lora_r=%s, epochs=%s",
                base_model, lora_config.get("r"), training_config.get("epochs"))

    Path(output_dir).mkdir(parents=True, exist_ok=True)
    merged_dir = os.path.join(output_dir, "model_merged")
    t0 = time.time()

    # 0. 모델 다운로드 (S3 우선, 없으면 HuggingFace)
    model_path = _resolve_model_path(base_model)
    logger.info("Using model path: %s", model_path)

    # 1. LoRA SFT 학습
    logger.info("=== Step 1: LoRA SFT Training ===")
    from peft import LoraConfig, get_peft_model
    from transformers import AutoModelForCausalLM, AutoTokenizer, TrainingArguments
    from trl import SFTTrainer
    from datasets import load_dataset

    tokenizer = AutoTokenizer.from_pretrained(model_path, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    model = AutoModelForCausalLM.from_pretrained(
        model_path, trust_remote_code=True, device_map="auto",
    )

    peft_config = LoraConfig(
        r=lora_config.get("r", 8),
        lora_alpha=lora_config.get("alpha", 16),
        lora_dropout=lora_config.get("dropout", 0.05),
        target_modules=lora_config.get("target_modules", ["q_proj", "v_proj"]),
        task_type="CAUSAL_LM",
    )
    model = get_peft_model(model, peft_config)

    dataset = load_dataset("json", data_files=data_path, split="train")
    eval_dataset = None
    if len(dataset) > 20:
        split = dataset.train_test_split(test_size=0.1, seed=42)
        dataset = split["train"]
        eval_dataset = split["test"]

    max_seq = training_config.get("max_seq_length", 512)
    training_args = TrainingArguments(
        output_dir=os.path.join(output_dir, "checkpoints"),
        num_train_epochs=training_config.get("epochs", 3),
        per_device_train_batch_size=training_config.get("batch_size", 4),
        gradient_accumulation_steps=training_config.get("gradient_accumulation", 8),
        learning_rate=training_config.get("learning_rate", 2e-4),
        logging_steps=10,
        save_strategy="no",
        fp16=True,
        report_to="none",
    )

    trainer = SFTTrainer(
        model=model,
        args=training_args,
        train_dataset=dataset,
        eval_dataset=eval_dataset,
        processing_class=tokenizer,
        max_seq_length=max_seq,
    )

    train_result = trainer.train()
    train_loss = train_result.training_loss
    eval_loss = None
    if eval_dataset:
        eval_result = trainer.evaluate()
        eval_loss = eval_result.get("eval_loss")

    logger.info("Training done: loss=%.4f, eval_loss=%s", train_loss,
                f"{eval_loss:.4f}" if eval_loss else "N/A")

    # 2. Merge LoRA + Save
    logger.info("=== Step 2: Merge LoRA ===")
    model = model.merge_and_unload()
    model.save_pretrained(merged_dir)
    tokenizer.save_pretrained(merged_dir)

    # 3. GGUF 양자화
    logger.info("=== Step 3: GGUF Quantization (%s) ===", quantize_method)
    gguf_path = os.path.join(output_dir, "model.gguf")
    try:
        import subprocess
        subprocess.run([
            "python3", "-m", "llama_cpp.convert",
            merged_dir, "--outfile", gguf_path,
            "--outtype", quantize_method,
        ], check=True, capture_output=True)
    except Exception:
        # llama-cpp-python convert 실패 시 llama.cpp 직접 사용
        try:
            subprocess.run([
                "/opt/llama.cpp/build/bin/llama-quantize",
                os.path.join(merged_dir, "ggml-model-f16.gguf"),
                gguf_path, quantize_method,
            ], check=True, capture_output=True)
        except Exception as e:
            logger.warning("GGUF quantization failed: %s (merged model available)", e)
            gguf_path = None

    duration = int(time.time() - t0)

    # 4. SHA256
    gguf_sha256 = None
    gguf_size_mb = None
    if gguf_path and os.path.exists(gguf_path):
        import hashlib
        sha = hashlib.sha256()
        with open(gguf_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha.update(chunk)
        gguf_sha256 = sha.hexdigest()
        gguf_size_mb = round(os.path.getsize(gguf_path) / (1024 * 1024), 1)

    # 5. result.json 생성
    result = {
        "status": "completed",
        "build_id": build_id,
        "train_loss": round(train_loss, 4),
        "eval_loss": round(eval_loss, 4) if eval_loss else None,
        "duration_sec": duration,
        "gguf_size_mb": gguf_size_mb,
        "gguf_sha256": gguf_sha256,
        "quantize_method": quantize_method,
        "base_model": base_model,
    }

    result_path = os.path.join(output_dir, "result.json")
    with open(result_path, "w") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    logger.info("=== Complete: %ds, loss=%.4f, gguf=%sMB ===",
                duration, train_loss, gguf_size_mb or "N/A")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--build-id", required=True)
    args = parser.parse_args()
    train(args.data_dir, args.output_dir, args.build_id)
